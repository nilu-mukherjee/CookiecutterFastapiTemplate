from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import get_settings
from app.middleware.access_log import AccessLogMiddleware
from app.middleware.deprecation import DeprecationMiddleware
from app.middleware.rate_limit import InMemoryRateLimiter, RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.timing import TimingMiddleware


def register_middleware(app: FastAPI) -> None:
    """The single place that sets middleware order.

    Starlette makes the LAST added middleware the OUTERMOST, so they are added
    here innermost first. Outermost first the stack is: request id, access log,
    timing, security headers, CORS, GZip, TrustedHost (non-dev), rate limit,
    deprecation.
    """
    settings = get_settings()
    is_dev = settings.environment == "dev"
    origins = settings.cors_origins

    app.add_middleware(DeprecationMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limiter=InMemoryRateLimiter(settings.rate_limit_per_minute),
    )
    if not is_dev:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=bool(origins) and "*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=not is_dev)
    app.add_middleware(TimingMiddleware)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
