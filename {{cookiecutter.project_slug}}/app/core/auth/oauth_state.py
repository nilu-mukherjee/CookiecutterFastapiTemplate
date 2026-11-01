{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
"""The OAuth state carried from /login to /callback in a signed, short-lived cookie.

The cookie is an HS256 JWT signed with SECRET_KEY holding the state nonce, the PKCE code
verifier and the provider{% if tenancy %} (and the tenant slug the login named){% endif %}. Its
``typ`` claim is ``oauth_state``, so no other token of the app is accepted as one. Only the
nonce travels to the provider (as ``state``); the verifier never leaves the HttpOnly cookie.
"""
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Response

from app.core.auth.cookies import OAUTH_STATE_COOKIE, OAUTH_STATE_PATH, clear_cookie, set_cookie
from app.core.auth.oauth_providers import ProviderName
from app.core.config import get_settings

STATE_PURPOSE = "oauth_state"
STATE_LIFETIME = timedelta(minutes=10)
_REQUIRED_CLAIMS = ["exp", "iat", "typ", "nonce", "code_verifier", "provider"]


@dataclass(frozen=True, slots=True)
class OAuthState:
    nonce: str
    code_verifier: str
    provider: ProviderName
{%- if tenancy %}
    # The tenant (slug) a new user would join; None when the login named none.
    tenant: str | None
{%- endif %}

    @property
    def code_challenge(self) -> str:
        """PKCE S256: BASE64URL(SHA256(verifier)), unpadded."""
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def matches(self, presented: str) -> bool:
        """Constant-time comparison of the ``state`` sent back by the provider."""
        return hmac.compare_digest(self.nonce.encode(), presented.encode())


def new_state(provider: ProviderName{% if tenancy %}, tenant: str | None{% endif %}) -> OAuthState:
    return OAuthState(
        nonce=secrets.token_urlsafe(32),
        # 86 characters, within PKCE's 43-128.
        code_verifier=secrets.token_urlsafe(64),
        provider=provider,
{%- if tenancy %}
        tenant=tenant,
{%- endif %}
    )


def encode_state(state: OAuthState) -> str:
    now = datetime.now(UTC)
    claims = {
        "typ": STATE_PURPOSE,
        "nonce": state.nonce,
        "code_verifier": state.code_verifier,
        "provider": state.provider.value,
{%- if tenancy %}
        "tenant": state.tenant,
{%- endif %}
        "iat": now,
        "exp": now + STATE_LIFETIME,
    }
    return jwt.encode(claims, get_settings().secret_key, algorithm="HS256")


def decode_state(token: str) -> OAuthState | None:
    """The state, or None if the cookie is forged, tampered with, expired or not a state.

    The algorithm is pinned to HS256: the token's own ``alg`` header (``none`` included)
    is never trusted.
    """
    try:
        claims = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=["HS256"],
            options={"require": _REQUIRED_CLAIMS},
        )
    except jwt.PyJWTError:
        return None
    nonce, verifier = claims.get("nonce"), claims.get("code_verifier")
{%- if tenancy %}
    tenant = claims.get("tenant")
{%- endif %}
    if (
        claims.get("typ") != STATE_PURPOSE
        or not isinstance(nonce, str)
        or not nonce
        or not isinstance(verifier, str)
        or not verifier
{%- if tenancy %}
        or not (tenant is None or isinstance(tenant, str))
{%- endif %}
    ):
        return None
    try:
        provider = ProviderName(claims["provider"])
    except ValueError:
        return None
    return OAuthState(
        nonce=nonce,
        code_verifier=verifier,
        provider=provider,
{%- if tenancy %}
        tenant=tenant,
{%- endif %}
    )


# SameSite=Lax whatever COOKIE_SAMESITE says: the provider sends the browser back with a
# cross-site top-level GET, on which a Strict cookie would not be sent.


def set_state_cookie(response: Response, state: OAuthState) -> None:
    set_cookie(
        response,
        OAUTH_STATE_COOKIE,
        encode_state(state),
        max_age=int(STATE_LIFETIME.total_seconds()),
        path=OAUTH_STATE_PATH,
        samesite="lax",
    )


def clear_state_cookie(response: Response) -> None:
    clear_cookie(response, OAUTH_STATE_COOKIE, path=OAUTH_STATE_PATH, samesite="lax")
