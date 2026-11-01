"""Social login providers: Google and GitHub, OAuth2 authorization code + PKCE.

The rest of the app only sees the ``OAuthProvider`` protocol and the normalised
``SocialProfile``, so tests substitute fake providers (see ``get_oauth_providers``).
A provider is enabled only when both its client id and secret are configured.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from app.core.config import Settings

# Every outbound call (token exchange, profile) gives up after this many seconds.
HTTP_TIMEOUT_SECONDS = 10.0


class ProviderName(StrEnum):
    """The closed set of supported providers; any other name is a 404."""

    GOOGLE = "google"
    GITHUB = "github"


@dataclass(frozen=True, slots=True)
class SocialProfile:
    """Who the provider says the user is, normalised across providers."""

    provider: ProviderName
    # The provider's stable, unique id for the account (never the email).
    subject: str
    email: str | None
    # True only when the provider itself has verified the address.
    email_verified: bool
    name: str | None


class ProviderError(Exception):
    """The code exchange or the profile fetch failed.

    The message is safe to log: it never contains a secret, code or token.
    """


class OAuthProvider(Protocol):
    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        """Where to send the browser to sign in (S256 PKCE challenge, never the verifier)."""
        ...

    async def fetch_profile(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> SocialProfile:
        """Exchange the code (with the PKCE verifier) and read the user's profile.

        Raises ProviderError on any failure.
        """
        ...


class _HttpxProvider:
    """Authorization URL, then the code exchange and profile calls over plain httpx."""

    name: ProviderName
    authorize_endpoint: str
    token_endpoint: str
    scope: str

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self._client_secret = client_secret
        # Tests pass an httpx.MockTransport; None means the real network.
        self._transport = transport

    def authorization_url(self, state: str, code_challenge: str, redirect_uri: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorize_endpoint}?{urlencode(params)}"

    async def fetch_profile(
        self, code: str, code_verifier: str, redirect_uri: str
    ) -> SocialProfile:
        try:
            async with httpx.AsyncClient(
                timeout=HTTP_TIMEOUT_SECONDS, transport=self._transport
            ) as client:
                access_token = await self._exchange(client, code, code_verifier, redirect_uri)
                return await self._profile(client, {"Authorization": f"Bearer {access_token}"})
        except Exception as exc:
            # The boundary to a third party: any failure (network, HTTP status, malformed or
            # unexpected JSON, a bug in the parsing) is a provider error. Only the exception
            # type is kept: messages can carry provider responses, codes or tokens.
            raise ProviderError(f"{self.name.value}: {type(exc).__name__}") from exc

    async def _exchange(
        self, client: httpx.AsyncClient, code: str, code_verifier: str, redirect_uri: str
    ) -> str:
        """Authorization code + PKCE verifier -> access token (client_secret_post)."""
        response = await client.post(
            self.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
            },
            # GitHub answers form-encoded unless JSON is asked for.
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = _json_object(response)
        # GitHub reports a bad code as 200 + {"error": ...}.
        if "error" in token:
            raise ValueError("token endpoint returned an error")
        access_token = _text(token.get("access_token"))
        if access_token is None:
            raise ValueError("no access token")
        return access_token

    async def _profile(self, client: httpx.AsyncClient, auth: dict[str, str]) -> SocialProfile:
        raise NotImplementedError


async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> Any:
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    return response.json()


def _json_object(response: httpx.Response) -> dict[str, Any]:
    data = response.json()
    if not isinstance(data, dict):
        raise TypeError("expected a JSON object")
    return data


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class GoogleProvider(_HttpxProvider):
    name = ProviderName.GOOGLE
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
    scope = "openid email profile"

    async def _profile(self, client: httpx.AsyncClient, auth: dict[str, str]) -> SocialProfile:
        info = await _get(client, self.userinfo_endpoint, auth)
        if not isinstance(info, dict):
            raise TypeError("userinfo is not an object")
        subject = _text(info.get("sub"))
        if subject is None:
            raise ValueError("no subject")
        return SocialProfile(
            provider=self.name,
            subject=subject,
            email=_text(info.get("email")),
            # Only a JSON true counts; anything else is unverified.
            email_verified=info.get("email_verified") is True,
            name=_text(info.get("name")),
        )


class GitHubProvider(_HttpxProvider):
    name = ProviderName.GITHUB
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    user_endpoint = "https://api.github.com/user"
    emails_endpoint = "https://api.github.com/user/emails"
    scope = "read:user user:email"

    async def _profile(self, client: httpx.AsyncClient, auth: dict[str, str]) -> SocialProfile:
        headers = {**auth, "Accept": "application/vnd.github+json"}
        user = await _get(client, self.user_endpoint, headers)
        if not isinstance(user, dict):
            raise TypeError("user is not an object")
        account_id = user.get("id")
        if not isinstance(account_id, int) or isinstance(account_id, bool):
            raise TypeError("id is not a number")
        emails = await _get(client, self.emails_endpoint, headers)
        if not isinstance(emails, list):
            raise TypeError("emails is not a list")
        # The PRIMARY address, and only if GitHub verified it: never fall back to another.
        primary = next(
            (e for e in emails if isinstance(e, dict) and e.get("primary") is True), None
        )
        return SocialProfile(
            provider=self.name,
            subject=str(account_id),
            email=_text(primary.get("email")) if primary else None,
            email_verified=primary is not None and primary.get("verified") is True,
            name=_text(user.get("name")),
        )


OAuthProviders = Mapping[ProviderName, OAuthProvider]


def configured_providers(settings: Settings) -> OAuthProviders:
    """The enabled providers: those with both a client id and a client secret."""
    providers: dict[ProviderName, OAuthProvider] = {}
    if settings.google_client_id and settings.google_client_secret:
        providers[ProviderName.GOOGLE] = GoogleProvider(
            settings.google_client_id, settings.google_client_secret.get_secret_value()
        )
    if settings.github_client_id and settings.github_client_secret:
        providers[ProviderName.GITHUB] = GitHubProvider(
            settings.github_client_id, settings.github_client_secret.get_secret_value()
        )
    return providers
