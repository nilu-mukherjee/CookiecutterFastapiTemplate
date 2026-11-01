"""The real Google/GitHub providers against a mocked HTTP transport (no network)."""
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.auth.oauth_providers import (
    GitHubProvider,
    GoogleProvider,
    ProviderError,
    ProviderName,
    SocialProfile,
)

GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"
SECRET = "the-client-secret"
REDIRECT = "https://api.example.com/api/v1/oauth/x/callback"
TOKEN = {"access_token": "access-123", "token_type": "bearer", "scope": "x"}

Routes = dict[str, httpx.Response | Callable[[httpx.Request], httpx.Response]]


class Recorder:
    """A MockTransport answering from ``routes`` (by URL) and recording every request."""

    def __init__(self, routes: Routes) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        answer = self.routes.get(url, httpx.Response(404))
        return answer(request) if callable(answer) else answer

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def _google(recorder: Recorder) -> GoogleProvider:
    return GoogleProvider("google-id", SECRET, transport=recorder.transport)


def _github(recorder: Recorder) -> GitHubProvider:
    return GitHubProvider("github-id", SECRET, transport=recorder.transport)


def _assert_token_request(request: httpx.Request, url: str, client_id: str) -> None:
    assert (request.method, str(request.url)) == ("POST", url)
    body = parse_qs(request.content.decode())
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["the-code"]
    # PKCE: the verifier goes to the token endpoint only.
    assert body["code_verifier"] == ["the-verifier"]
    assert body["redirect_uri"] == [REDIRECT]
    assert body["client_id"] == [client_id]
    assert body["client_secret"] == [SECRET]


def _assert_timeouts(recorder: Recorder) -> None:
    for request in recorder.requests:
        assert request.extensions["timeout"] == {
            "connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0
        }


# --- Google ------------------------------------------------------------------------


async def test_google_exchanges_the_code_with_pkce_and_reads_userinfo() -> None:
    def userinfo(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer access-123"
        return httpx.Response(
            200,
            json={"sub": "g-1", "email": "Someone@Example.com", "email_verified": True,
                  "name": "Someone"},
        )

    recorder = Recorder({GOOGLE_TOKEN: httpx.Response(200, json=TOKEN), GOOGLE_USERINFO: userinfo})
    profile = await _google(recorder).fetch_profile("the-code", "the-verifier", REDIRECT)
    assert profile == SocialProfile(
        provider=ProviderName.GOOGLE,
        subject="g-1",
        email="Someone@Example.com",
        email_verified=True,
        name="Someone",
    )
    _assert_token_request(recorder.requests[0], GOOGLE_TOKEN, "google-id")
    assert [str(r.url) for r in recorder.requests] == [GOOGLE_TOKEN, GOOGLE_USERINFO]
    _assert_timeouts(recorder)


@pytest.mark.parametrize("verified", [False, "true", None])
async def test_google_email_counts_as_verified_only_when_google_says_true(
    verified: object,
) -> None:
    info: dict[str, object] = {"sub": "g-1", "email": "a@example.com"}
    if verified is not None:
        info["email_verified"] = verified
    recorder = Recorder({
        GOOGLE_TOKEN: httpx.Response(200, json=TOKEN),
        GOOGLE_USERINFO: httpx.Response(200, json=info),
    })
    profile = await _google(recorder).fetch_profile("the-code", "the-verifier", REDIRECT)
    assert profile.email == "a@example.com"
    assert profile.email_verified is False


# --- GitHub ------------------------------------------------------------------------


def _github_routes(emails: list[dict[str, object]]) -> Routes:
    return {
        GITHUB_TOKEN: httpx.Response(200, json=TOKEN),
        GITHUB_USER: httpx.Response(200, json={"id": 4242, "login": "octo", "name": "Octo"}),
        GITHUB_EMAILS: httpx.Response(200, json=emails),
    }


async def test_github_takes_the_primary_verified_email_and_the_numeric_id() -> None:
    recorder = Recorder(_github_routes([
        {"email": "other@example.com", "primary": False, "verified": True},
        {"email": "Main@Example.com", "primary": True, "verified": True},
    ]))
    profile = await _github(recorder).fetch_profile("the-code", "the-verifier", REDIRECT)
    assert profile == SocialProfile(
        provider=ProviderName.GITHUB,
        subject="4242",
        email="Main@Example.com",
        email_verified=True,
        name="Octo",
    )
    _assert_token_request(recorder.requests[0], GITHUB_TOKEN, "github-id")
    assert recorder.requests[0].headers["accept"] == "application/json"
    for request in recorder.requests[1:]:
        assert request.headers["authorization"] == "Bearer access-123"
    _assert_timeouts(recorder)


async def test_github_primary_unverified_is_unverified_even_with_another_verified_email() -> None:
    recorder = Recorder(_github_routes([
        {"email": "verified-but-secondary@example.com", "primary": False, "verified": True},
        {"email": "main@example.com", "primary": True, "verified": False},
    ]))
    profile = await _github(recorder).fetch_profile("the-code", "the-verifier", REDIRECT)
    assert profile.email_verified is False
    # Never falls back to a non-primary address.
    assert profile.email == "main@example.com"


async def test_github_without_a_primary_email_is_unverified() -> None:
    recorder = Recorder(_github_routes([
        {"email": "secondary@example.com", "primary": False, "verified": True},
    ]))
    profile = await _github(recorder).fetch_profile("the-code", "the-verifier", REDIRECT)
    assert profile.email_verified is False
    assert profile.email is None


# --- failures ----------------------------------------------------------------------


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


@pytest.mark.parametrize(
    "routes",
    [
        # Token endpoint rejects the code (as an HTTP error, or GitHub-style 200 + error).
        {GITHUB_TOKEN: httpx.Response(400, json={"error": "bad_verification_code"})},
        {GITHUB_TOKEN: httpx.Response(200, json={"error": "bad_verification_code"})},
        {GITHUB_TOKEN: httpx.Response(502, text="<html>bad gateway</html>")},
        {GITHUB_TOKEN: _timeout},
        # Profile endpoints fail or answer nonsense.
        {**_github_routes([]), GITHUB_USER: httpx.Response(500)},
        {**_github_routes([]), GITHUB_USER: httpx.Response(200, json={"login": "no-id"})},
        {**_github_routes([]), GITHUB_EMAILS: httpx.Response(200, json={"not": "a list"})},
        {**_github_routes([]), GITHUB_EMAILS: httpx.Response(200, text="not json")},
    ],
)
async def test_github_failures_raise_provider_error_without_secrets(routes: Routes) -> None:
    with pytest.raises(ProviderError) as info:
        await _github(Recorder(routes)).fetch_profile("the-code", "the-verifier", REDIRECT)
    message = str(info.value)
    for secret in (SECRET, "the-code", "the-verifier", "access-123"):
        assert secret not in message


@pytest.mark.parametrize(
    "routes",
    [
        {GOOGLE_TOKEN: httpx.Response(401, json={"error": "invalid_client"})},
        {GOOGLE_TOKEN: httpx.Response(200, json=TOKEN), GOOGLE_USERINFO: httpx.Response(401)},
        {GOOGLE_TOKEN: httpx.Response(200, json=TOKEN),
         GOOGLE_USERINFO: httpx.Response(200, json={"email": "no-sub@example.com"})},
    ],
)
async def test_google_failures_raise_provider_error_without_secrets(routes: Routes) -> None:
    with pytest.raises(ProviderError) as info:
        await _google(Recorder(routes)).fetch_profile("the-code", "the-verifier", REDIRECT)
    for secret in (SECRET, "the-code", "the-verifier", "access-123"):
        assert secret not in str(info.value)


def test_provider_repr_does_not_show_the_secret() -> None:
    assert SECRET not in repr(GoogleProvider("google-id", SECRET))
    assert SECRET not in repr(GitHubProvider("github-id", SECRET))
