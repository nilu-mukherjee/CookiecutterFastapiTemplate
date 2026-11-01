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
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuthError

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


class _AuthlibProvider:
    """Authorization URL plus the code exchange through Authlib's async httpx client."""

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
            async with AsyncOAuth2Client(
                client_id=self.client_id,
                client_secret=self._client_secret,
                token_endpoint_auth_method="client_secret_post",
                redirect_uri=redirect_uri,
                timeout=HTTP_TIMEOUT_SECONDS,
                transport=self._transport,
            ) as client:
                await client.fetch_token(
                    self.token_endpoint,
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=code_verifier,
                )
                return await self._profile(client)
        except (OAuthError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Only the exception type: messages can carry provider responses.
            raise ProviderError(f"{self.name.value}: {type(exc).__name__}") from exc

    async def _profile(self, client: Any) -> SocialProfile:
        raise NotImplementedError


async def _get_json(client: Any, url: str, **kwargs: Any) -> Any:
    response = await client.get(url, **kwargs)
    response.raise_for_status()
    return response.json()


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


class GoogleProvider(_AuthlibProvider):
    name = ProviderName.GOOGLE
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    userinfo_endpoint = "https://openidconnect.googleapis.com/v1/userinfo"
    scope = "openid email profile"

    async def _profile(self, client: Any) -> SocialProfile:
        info = await _get_json(client, self.userinfo_endpoint)
        subject = _text(info["sub"])
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


class GitHubProvider(_AuthlibProvider):
    name = ProviderName.GITHUB
    authorize_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    user_endpoint = "https://api.github.com/user"
    emails_endpoint = "https://api.github.com/user/emails"
    scope = "read:user user:email"
    _headers = {"Accept": "application/vnd.github+json"}

    async def _profile(self, client: Any) -> SocialProfile:
        user = await _get_json(client, self.user_endpoint, headers=self._headers)
        account_id = user["id"]
        if not isinstance(account_id, int) or isinstance(account_id, bool):
            raise TypeError("id is not a number")
        emails = await _get_json(client, self.emails_endpoint, headers=self._headers)
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
