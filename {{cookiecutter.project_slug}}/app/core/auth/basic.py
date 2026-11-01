{%- set tenancy = cookiecutter.use_multitenancy == "y" -%}
import base64

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.base import Principal
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import MAX_PASSWORD_LENGTH
from app.services.user import {% if tenancy %}UserAuthentication{% else %}UserService{% endif %}


def _invalid() -> AppError:
    # Same wording as the login endpoint, and deliberately NO WWW-Authenticate header:
    # a ``Basic`` challenge makes browsers prompt for, and then cache and resend, the
    # credentials on every request, which would make them ambient (CSRF-able).
    return AppError(401, "invalid_credentials", "Invalid email or password")


class BasicAuthenticator:
    """``Authorization: Basic base64(email:password)``.

    No CSRF: since no challenge is ever sent, a browser never attaches these
    credentials by itself.

    Outside dev the credentials are only accepted over HTTPS. The scheme comes from
    ``request.url.scheme``; behind a TLS-terminating proxy run uvicorn with
    ``--proxy-headers`` and ``--forwarded-allow-ips`` set to the proxy, otherwise every
    request looks like plain http and Basic is rejected.
    """

    async def authenticate(self, request: Request, session: AsyncSession) -> Principal | None:
        header = request.headers.get("authorization")
        if not header:
            return None
        scheme, _, encoded = header.partition(" ")
        if scheme.lower() != "basic":
            return None  # another scheme (e.g. Bearer) is another authenticator's credential
        if get_settings().environment != "dev" and request.url.scheme != "https":
            raise AppError(
                401, "basic_requires_https", "HTTP Basic is only accepted over HTTPS"
            )
        email, password = self._decode(encoded.strip())
        # The login endpoint's own check: dummy-hash timing for unknown emails, one
        # outcome for unknown / wrong password / inactive, email normalisation.
        user = await {% if tenancy %}UserAuthentication{% else %}UserService{% endif %}(session).authenticate(email, password)
        if user is None:
            raise _invalid()
        return Principal(
            user_id=user.id,
{%- if tenancy %}
            tenant_id=user.tenant_id,
{%- endif %}
            is_superuser=user.is_superuser,
            auth_method="basic",
        )

    @staticmethod
    def _decode(encoded: str) -> tuple[str, str]:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except ValueError as exc:  # bad base64 (binascii.Error), non-ASCII text, non-UTF-8
            raise _invalid() from exc
        # First colon only: the email has none, the password may.
        email, sep, password = decoded.partition(":")
        if not sep or len(password) > MAX_PASSWORD_LENGTH:
            raise _invalid()
        return email, password
