"""Prune files for disabled features, then git init."""
import json
import shutil
import subprocess
from pathlib import Path

# The whole rendered cookiecutter context, so every cookiecutter.json key is
# available to PRUNE without hand-maintained lines.
# (parsed via json.loads because the context can contain JSON null.)
FLAGS: dict[str, object] = json.loads(r"""{{ cookiecutter | jsonify }}""")

# PRUNE maps a key to the list of paths (relative to the generated project)
# removed when the key is off. Later tasks add entries here.
#   key: a flag name (str)          -> paths removed when that flag is "n"
#   key: a tuple of flag names      -> paths removed when ANY listed flag is "n"
#                                      (dependent-flag case, e.g. a path needing
#                                      both use_auth and use_rbac)
#   a nested tuple inside a tuple    -> an any-of group, "off" only when EVERY
#                                      member is "n" (code shared by several
#                                      methods, e.g. JWT for Bearer or cookie)
Key = str | tuple[str | tuple[str, ...], ...]
JWT_METHODS = ("use_auth_jwt_bearer", "use_auth_jwt_cookie")
COOKIE_METHODS = ("use_auth_jwt_cookie", "use_auth_session")
# Methods that log in through /auth/login (JWT pairs, or a server-side session).
LOGIN_METHODS = (*JWT_METHODS, "use_auth_session")
PRUNE: dict[Key, list[str]] = {
    "use_docker": ["Dockerfile", "docker-compose.yml", ".dockerignore"],
    "use_github_actions": [".github"],
    "use_redis": [
        "app/cache",
        "app/middleware/rate_limit_redis.py",
        "tests/middleware/test_rate_limit_redis.py",
    ],
    "use_auth": [
        "app/core/auth",
        "app/core/security.py",
        "app/models/user.py",
        "app/repositories/user.py",
        "app/services/user.py",
        "app/schemas/v1/user.py",
        "app/api/v1/endpoints/users.py",
        "alembic/versions/0002_auth.py",
        "tests/auth",
    ],
    # Shared by both JWT methods: token format, refresh tokens, login endpoints.
    ("use_auth", JWT_METHODS): [
        "app/core/auth/access_token.py",
        "app/models/refresh_token.py",
        "app/repositories/refresh_token.py",
        "app/services/auth.py",
    ],
    # Login/logout endpoints and their schemas, shared by JWT and session methods.
    ("use_auth", LOGIN_METHODS): [
        "app/schemas/v1/auth.py",
        "app/api/v1/endpoints/auth.py",
    ],
    ("use_auth", "use_auth_jwt_bearer"): [
        "app/core/auth/jwt_bearer.py",
        "tests/auth/test_jwt_bearer.py",
        # Uses Bearer tokens for its principals until other methods exist.
        "tests/auth/test_users.py",
    ],
    ("use_auth", "use_auth_jwt_cookie"): [
        "app/core/auth/jwt_cookie.py",
        "tests/auth/test_jwt_cookie.py",
    ],
    # Cookie names/attributes and CSRF are shared by every cookie method.
    ("use_auth", COOKIE_METHODS): [
        "app/core/auth/cookies.py",
        "app/core/auth/csrf.py",
    ],
    ("use_auth", "use_auth_session"): [
        "app/core/auth/session.py",
        "app/models/session.py",
        "app/repositories/session.py",
        "app/services/session.py",
        "alembic/versions/0003_sessions.py",
        "tests/auth/test_session.py",
    ],
    # The clock helper that tests patch, shared by the methods that compare timestamps.
    ("use_auth", ("use_auth_session", "use_auth_api_key")): ["app/core/time.py"],
    ("use_auth", "use_auth_api_key"): [
        "app/core/auth/api_key.py",
        "app/models/api_key.py",
        "app/repositories/api_key.py",
        "app/services/api_key.py",
        "app/schemas/v1/api_key.py",
        "app/api/v1/endpoints/api_keys.py",
        "alembic/versions/0004_api_keys.py",
        "tests/auth/test_api_key.py",
    ],
    ("use_auth", "use_auth_basic"): [
        "app/core/auth/basic.py",
        "tests/auth/test_basic.py",
    ],
    ("use_auth", "use_rbac"): [
        "app/models/permission.py",
        "app/models/role.py",
        "app/repositories/role.py",
        "app/services/rbac.py",
        "app/schemas/v1/role.py",
        "app/api/v1/endpoints/roles.py",
        "alembic/versions/0005_rbac.py",
        "tests/rbac",
    ],
    ("use_auth", "use_rbac", "use_auth_api_key"): ["tests/rbac/test_rbac_api_key.py"],
    ("use_auth", "use_multitenancy"): [
        "app/models/tenant.py",
        "app/repositories/tenant.py",
        "app/services/tenant.py",
        "app/schemas/v1/tenant.py",
        "app/api/v1/endpoints/tenants.py",
        "tests/tenancy",
    ],
    ("use_auth", "use_multitenancy", "use_rbac"): ["tests/tenancy/test_roles.py"],
    # Precedence between Bearer and the key/Basic methods needs Bearer and one of them.
    ("use_auth", "use_auth_jwt_bearer", ("use_auth_api_key", "use_auth_basic")): [
        "tests/auth/test_credential_precedence.py",
    ],
}


def remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def is_off(key: Key) -> bool:
    keys = (key,) if isinstance(key, str) else key
    return any(
        FLAGS[k] == "n" if isinstance(k, str) else all(FLAGS[g] == "n" for g in k)
        for k in keys
    )


def main() -> None:
    root = Path.cwd()
    for key, paths in PRUNE.items():
        if is_off(key):
            for rel in paths:
                remove(root / rel)
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q"], check=False)


if __name__ == "__main__":
    main()
