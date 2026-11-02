import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def gen(tmp_path, **flags):
    # gRPC rejects cookie/session/Basic-only auth builds, which many tests here create on
    # purpose; they are about other features, so gRPC is off unless a test asks for it.
    flags.setdefault("use_grpc", "n")
    cmd = [sys.executable, "-m", "cookiecutter", "--no-input", str(ROOT), "-o", str(tmp_path)]
    cmd += [f"{k}={v}" for k, v in flags.items()]
    return subprocess.run(cmd, capture_output=True, text=True)


def assert_aborts(tmp_path, flag, **flags):
    r = gen(tmp_path, **flags)
    assert r.returncode != 0
    assert flag in r.stderr


def test_use_rbac_without_auth_aborts(tmp_path):
    assert_aborts(tmp_path, "use_rbac", use_auth="n", use_rbac="y")


def test_auth_without_any_method_aborts(tmp_path):
    assert_aborts(
        tmp_path, "use_auth", use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="n",
        use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
        use_social_login="n",
    )


def test_flower_without_celery_aborts(tmp_path):
    assert_aborts(tmp_path, "use_flower", use_celery="n", use_celery_beat="n", use_flower="y")


def test_social_without_session_or_jwt_aborts(tmp_path):
    assert_aborts(
        tmp_path, "use_social_login", use_auth="y", use_auth_jwt_bearer="n",
        use_auth_jwt_cookie="n", use_auth_session="n", use_auth_api_key="y",
        use_auth_basic="n", use_social_login="y",
    )


def test_auth_off_ignores_method_flags_and_notices(tmp_path):
    r = gen(tmp_path, use_auth="n", use_rbac="n", use_multitenancy="n",
            use_social_login="n", use_auth_session="y")
    assert r.returncode == 0, r.stderr
    assert "ignored" in (r.stdout + r.stderr)


def test_defaults_generate(tmp_path):
    r = gen(tmp_path, use_grpc="y")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "my_project" / "README.md").exists()


def test_post_gen_prunes_from_full_context(tmp_path):
    # FLAGS in post_gen_project.py is the whole rendered context; PRUNE keys
    # must resolve without a KeyError and the paths be absent when flag is n.
    r = gen(tmp_path, use_docker="n", use_github_actions="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert (proj / "README.md").exists()
    assert not (proj / "Dockerfile").exists()
    assert not (proj / ".github").exists()
    # No prune-target paths exist yet for the use_docker=y case, so only
    # the n case is asserted at this stage.


def test_post_gen_survives_triple_single_quotes_in_author(tmp_path):
    r = gen(tmp_path, author="x'''y")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "my_project" / "README.md").exists()



AUTH_PATHS = [
    "app/core/auth", "app/core/security.py", "app/models/user.py",
    "app/models/refresh_token.py", "app/repositories/user.py",
    "app/repositories/refresh_token.py", "app/services/auth.py", "app/services/user.py",
    "app/schemas/v1/auth.py", "app/schemas/v1/user.py", "app/api/v1/endpoints/auth.py",
    "app/api/v1/endpoints/users.py", "alembic/versions/0002_auth.py", "tests/auth",
]


def test_auth_off_leaves_no_auth_code(tmp_path):
    r = gen(tmp_path, use_auth="n", use_rbac="n", use_multitenancy="n", use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in AUTH_PATHS:
        assert not (proj / rel).exists(), rel
    needles = ("argon2", "pyjwt", "import jwt", "secret_key", "SECRET_KEY", "Principal",
               "email-validator", "refresh_token", "get_current_principal", "superuser")
    for path in proj.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            text = path.read_text(errors="ignore")
            for needle in needles:
                assert needle not in text, f"{needle!r} left in {path.relative_to(proj)}"


def test_auth_jwt_bearer_on_generates_auth_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in AUTH_PATHS:
        assert (proj / rel).exists(), rel


JWT_SHARED_PATHS = [
    "app/models/refresh_token.py", "app/repositories/refresh_token.py",
    "app/services/auth.py", "app/schemas/v1/auth.py", "app/api/v1/endpoints/auth.py",
    "app/core/auth/access_token.py",
]
COOKIE_PATHS = [
    "app/core/auth/jwt_cookie.py", "app/core/auth/csrf.py", "app/core/auth/cookies.py",
    "tests/auth/test_jwt_cookie.py",
]


def test_auth_jwt_cookie_only_generates_shared_jwt_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="y",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in JWT_SHARED_PATHS + COOKIE_PATHS:
        assert (proj / rel).exists(), rel
    assert not (proj / "app/core/auth/jwt_bearer.py").exists()
    assert not (proj / "tests/auth/test_jwt_bearer.py").exists()


def test_auth_jwt_cookie_off_leaves_no_cookie_or_csrf_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in JWT_SHARED_PATHS:
        assert (proj / rel).exists(), rel
    for rel in COOKIE_PATHS:
        assert not (proj / rel).exists(), rel
    for path in proj.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            text = path.read_text(errors="ignore").lower()
            # Code-level needles: the precedence-order comment may still name methods.
            for needle in ("csrf", "cookie_", "_cookie", "cookies", "apikeycookie",
                           "jwtcookie", "delivery"):
                assert needle not in text, f"{needle!r} left in {path.relative_to(proj)}"


SESSION_PATHS = [
    "app/core/auth/session.py", "app/core/time.py", "app/models/session.py",
    "app/repositories/session.py", "app/services/session.py",
    "alembic/versions/0003_sessions.py", "tests/auth/test_session.py",
]


def test_auth_session_only_generates_session_code_without_jwt_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="n",
            use_auth_session="y", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in SESSION_PATHS + ["app/core/auth/cookies.py", "app/core/auth/csrf.py",
                                "app/schemas/v1/auth.py", "app/api/v1/endpoints/auth.py"]:
        assert (proj / rel).exists(), rel
    for rel in JWT_SHARED_PATHS[:3] + ["app/core/auth/jwt_cookie.py",
                                       "app/core/auth/jwt_bearer.py"]:
        assert not (proj / rel).exists(), rel


def test_auth_session_off_leaves_no_session_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="y",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in SESSION_PATHS:
        assert not (proj / rel).exists(), rel
    needles = ("session_idle", "session_absolute", "sessionservice", "sessionauthenticator",
               "session_id", "session_cookie", "sessionlogin", "core.time", '"session"')
    for path in proj.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            text = path.read_text(errors="ignore").lower()
            for needle in needles:
                assert needle not in text, f"{needle!r} left in {path.relative_to(proj)}"


API_KEY_PATHS = [
    "app/core/auth/api_key.py", "app/models/api_key.py", "app/repositories/api_key.py",
    "app/services/api_key.py", "app/schemas/v1/api_key.py",
    "app/api/v1/endpoints/api_keys.py", "alembic/versions/0004_api_keys.py",
    "tests/auth/test_api_key.py",
]
BASIC_PATHS = ["app/core/auth/basic.py", "tests/auth/test_basic.py"]


CODE_SUFFIXES = {".py", ".toml", ".ini", ".example", ".yml", ".yaml", ".json"}
API_KEY_NEEDLES = ("api_key", "api-key", "use_auth_api_key", "apikeyauth", "apikeyheader",
                  "apikeyservice", "apikeyauthenticator")
BASIC_NEEDLES = ("basicauth", "httpbasic", "use_auth_basic", "basic_scheme", "basicscheme",
                 '"basic"', "'basic'", "basicauthenticator")


def scan(proj, needles):
    """Needles must not appear in any code/config file (README, docs and locks are exempt)."""
    for path in proj.rglob("*"):
        if (path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts
                and path.suffix in CODE_SUFFIXES and not path.name.endswith(".lock")):
            text = path.read_text(errors="ignore").lower()
            for needle in needles:
                assert needle not in text, f"{needle!r} left in {path.relative_to(proj)}"


def test_api_key_off_leaves_no_api_key_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="y", use_auth_api_key="n", use_auth_basic="y",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in API_KEY_PATHS:
        assert not (proj / rel).exists(), rel
    assert (proj / "alembic/versions/0003_sessions.py").exists()
    scan(proj, API_KEY_NEEDLES + ("x-api-key",))


def test_basic_off_leaves_no_basic_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="y", use_auth_api_key="y", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in BASIC_PATHS:
        assert not (proj / rel).exists(), rel
    scan(proj, BASIC_NEEDLES)


def test_bearer_only_build_contains_no_disabled_method_needles(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    scan(proj, API_KEY_NEEDLES + BASIC_NEEDLES + ("x-api-key", "sessionauthenticator",
                                                  "jwtcookieauthenticator"))
    order = (proj / "app/core/auth/__init__.py").read_text()
    assert "# Order: Bearer." in order


def test_api_key_without_human_method_aborts(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="y", use_auth_basic="n",
            use_social_login="n")
    assert r.returncode != 0
    assert "use_auth_api_key" in r.stderr


HUMAN_FLAGS = ["use_auth_jwt_bearer", "use_auth_jwt_cookie", "use_auth_session", "use_auth_basic"]


@pytest.mark.parametrize("human", HUMAN_FLAGS)
def test_api_key_with_a_human_method_ok(tmp_path, human):
    flags = {flag: ("y" if flag == human else "n") for flag in HUMAN_FLAGS}
    r = gen(tmp_path, use_auth="y", use_auth_api_key="y", use_social_login="n", **flags)
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert (proj / "alembic/versions/0004_api_keys.py").exists()
    assert (proj / "app/models/api_key.py").exists()
    previous = "0003" if human == "use_auth_session" else "0002"
    assert f'down_revision = "{previous}"' in (
        proj / "alembic/versions/0004_api_keys.py"
    ).read_text()


def test_api_key_with_auth_off_is_ignored(tmp_path):
    r = gen(tmp_path, use_auth="n", use_auth_api_key="y", use_rbac="n",
            use_multitenancy="n", use_social_login="n")
    assert r.returncode == 0, r.stderr
    assert "ignored" in (r.stdout + r.stderr)
    assert not (tmp_path / "my_project/app/models/api_key.py").exists()


RBAC_PATHS = [
    "app/models/permission.py", "app/models/role.py", "app/repositories/role.py",
    "app/services/rbac.py", "app/schemas/v1/role.py", "app/api/v1/endpoints/roles.py",
    "alembic/versions/0005_rbac.py", "tests/rbac",
]
RBAC_NEEDLES = ("require_permission", "rbacservice", "role_permissions", "permission_denied",
                "use_rbac", "user_roles", "unknown_role", "cannot_modify_own_roles",
                "app.models.role", "app.models.permission", "make_admin", "login_client")


def test_rbac_off_leaves_no_rbac_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="y",
            use_auth_session="y", use_auth_api_key="y", use_auth_basic="y",
            use_social_login="n", use_rbac="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in RBAC_PATHS:
        assert not (proj / rel).exists(), rel
    scan(proj, RBAC_NEEDLES)
    # Without RBAC the users endpoints stay superuser-only.
    users = (proj / "app/api/v1/endpoints/users.py").read_text()
    assert "SuperuserPrincipal" in users
    assert '"roles"' not in (proj / "app/schemas/v1/user.py").read_text()


def test_rbac_on_generates_rbac_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_api_key="n",
            use_rbac="y", use_social_login="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in RBAC_PATHS:
        if rel != "tests/rbac":
            assert (proj / rel).exists(), rel
    assert (proj / "tests/rbac/test_rbac.py").exists()
    # The API-key scenarios exist only when API keys do.
    assert not (proj / "tests/rbac/test_rbac_api_key.py").exists()


@pytest.mark.parametrize(
    ("session", "api_key", "previous"),
    [("n", "n", "0002"), ("y", "n", "0003"), ("n", "y", "0004"), ("y", "y", "0004")],
)
def test_rbac_migration_chains_onto_the_newest_existing_revision(
    tmp_path, session, api_key, previous
):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session=session, use_auth_api_key=api_key, use_auth_basic="n",
            use_social_login="n", use_rbac="y", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    migration = (tmp_path / "my_project/alembic/versions/0005_rbac.py").read_text()
    assert f'down_revision = "{previous}"' in migration
    assert f"Revises: {previous}" in migration


def test_rbac_with_api_key_includes_the_api_key_tests(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_api_key="y",
            use_rbac="y", use_social_login="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "my_project/tests/rbac/test_rbac_api_key.py").exists()


TENANCY_PATHS = [
    "app/models/tenant.py", "app/repositories/tenant.py", "app/services/tenant.py",
    "app/schemas/v1/tenant.py", "app/api/v1/endpoints/tenants.py", "tests/tenancy",
]
# "tenant" covers TenantScopedRepository, TenantMixin, Tenant, tenant_id, tenants, ...;
# the others are listed so a failure names the most specific leftover.
TENANCY_NEEDLES = ("tenantscopedrepository", "tenantmixin", "tenant_id", "tenants",
                   "use_multitenancy", "make_tenant", "client_b", "system_tenant",
                   "allow_social_signup", "tenant")


@pytest.mark.parametrize("rbac", ["y", "n"])
def test_multitenancy_off_leaves_no_tenancy_code(tmp_path, rbac):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="y",
            use_auth_session="y", use_auth_api_key="y", use_auth_basic="y",
            use_social_login="y", use_rbac=rbac, use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in TENANCY_PATHS:
        assert not (proj / rel).exists(), rel
    scan(proj, TENANCY_NEEDLES)


@pytest.mark.parametrize("rbac", ["y", "n"])
def test_multitenancy_on_generates_tenancy_code(tmp_path, rbac):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n", use_rbac=rbac, use_multitenancy="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in TENANCY_PATHS:
        assert (proj / rel).exists(), rel
    assert (proj / "tests/tenancy/test_isolation.py").exists()
    # Tenancy is folded into the existing revisions: no extra revision file.
    versions = sorted(p.name for p in (proj / "alembic/versions").glob("*.py"))
    expected = ["0001_initial.py", "0002_auth.py"] + (["0005_rbac.py"] if rbac == "y" else [])
    assert versions == expected
    # allow_social_signup exists only for social login (Task 11 consumes it).
    assert "allow_social_signup" not in (proj / "app/models/tenant.py").read_text()


def test_multitenancy_with_social_login_has_allow_social_signup(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_social_login="y",
            use_rbac="n", use_multitenancy="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in ("app/models/tenant.py", "app/schemas/v1/tenant.py",
                "alembic/versions/0001_initial.py"):
        assert "allow_social_signup" in (proj / rel).read_text(), rel


SOCIAL_PATHS = [
    "app/core/auth/oauth_providers.py", "app/core/auth/oauth_state.py",
    "app/models/social_account.py", "app/repositories/social_account.py",
    "app/services/social.py", "app/api/v1/endpoints/oauth.py",
    "alembic/versions/0006_social_accounts.py", "tests/auth/test_social.py",
    "tests/auth/test_social_providers.py",
]
SOCIAL_NEEDLES = ("socialloginservice", "social_accounts", "social_account", "oauth_state",
                  "oauthprovider", "oauth_providers", "google_client", "github_client",
                  "oauth_redirect", "oauth_success_redirect", "social_allow_signup",
                  "/oauth", "allow_social_signup", "use_social_login")
ALL_METHODS = dict(use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="y",
                   use_auth_session="y", use_auth_api_key="y", use_auth_basic="y")


def runtime_dependencies(proj):
    import tomllib
    return tomllib.loads((proj / "pyproject.toml").read_text())["project"]["dependencies"]


@pytest.mark.parametrize("tenancy", ["y", "n"])
def test_social_off_leaves_no_social_code(tmp_path, tenancy):
    r = gen(tmp_path, **ALL_METHODS, use_rbac="y", use_multitenancy=tenancy,
            use_social_login="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in SOCIAL_PATHS + ["tests/tenancy/test_social.py"]:
        assert not (proj / rel).exists(), rel
    scan(proj, SOCIAL_NEEDLES)
    # httpx stays a dev (test client) dependency only; no OAuth client library either.
    assert not any(d.startswith(("httpx", "authlib")) for d in runtime_dependencies(proj))


def test_bearer_only_without_social_has_no_cookie_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="n", use_rbac="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert not (proj / "app/core/auth/cookies.py").exists()
    scan(proj, SOCIAL_NEEDLES + ("cookie_secure", "cookie_samesite", "cookie_domain"))


def test_social_with_bearer_only_generates_social_and_state_cookie_code(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="y", use_rbac="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in SOCIAL_PATHS:
        assert (proj / rel).exists(), rel
    assert not (proj / "tests/tenancy").exists()
    # The OAuth state cookie needs the cookie helpers and settings, not CSRF.
    assert (proj / "app/core/auth/cookies.py").exists()
    assert not (proj / "app/core/auth/csrf.py").exists()
    assert "csrf" not in (proj / "app/core/auth/cookies.py").read_text().lower()
    assert "COOKIE_SECURE=" in (proj / ".env.example").read_text()
    deps = runtime_dependencies(proj)
    for name in ("httpx", "pyjwt"):
        assert any(d.startswith(name) for d in deps), name
    # Plain httpx: no OAuth client library (Authlib warned on every import).
    scan(proj, ("authlib",))


def test_social_with_session_only_needs_pyjwt_for_the_state_cookie(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="n",
            use_auth_session="y", use_auth_api_key="n", use_auth_basic="n",
            use_social_login="y", use_rbac="n", use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    deps = runtime_dependencies(tmp_path / "my_project")
    assert any(d.startswith("pyjwt") for d in deps)


def test_social_with_tenancy_generates_the_tenancy_social_tests(tmp_path):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_social_login="y",
            use_rbac="y", use_multitenancy="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert (proj / "tests/tenancy/test_social.py").exists()
    assert "social_allow_signup" not in (proj / "app/core/config.py").read_text()


@pytest.mark.parametrize(
    ("session", "api_key", "rbac", "previous"),
    [("n", "n", "n", "0002"), ("y", "n", "n", "0003"), ("y", "y", "n", "0004"),
     ("n", "y", "y", "0005"), ("n", "n", "y", "0005")],
)
def test_social_migration_chains_onto_the_newest_existing_revision(
    tmp_path, session, api_key, rbac, previous
):
    r = gen(tmp_path, use_auth="y", use_auth_jwt_bearer="y", use_auth_jwt_cookie="n",
            use_auth_session=session, use_auth_api_key=api_key, use_auth_basic="n",
            use_social_login="y", use_rbac=rbac, use_multitenancy="n")
    assert r.returncode == 0, r.stderr
    migration = (tmp_path / "my_project/alembic/versions/0006_social_accounts.py").read_text()
    assert f'down_revision = "{previous}"' in migration
    assert f"Revises: {previous}" in migration


CELERY_NEEDLES = ("celery", "app/worker", "app.worker", "queue_unavailable", "example_task", "kombu")
BEAT_NEEDLES = ("beat_schedule", "periodic_example", "periodic-example")


def test_celery_off_leaves_no_celery_code(tmp_path):
    r = gen(tmp_path, use_celery="n", use_celery_beat="n", use_flower="n", use_redis="y",
            use_auth="y", use_auth_jwt_bearer="y", use_rbac="y", use_multitenancy="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert not (proj / "app/worker").exists()
    assert not (proj / "tests/worker").exists()
    scan(proj, CELERY_NEEDLES)


def test_celery_on_generates_worker_code(tmp_path):
    r = gen(tmp_path, use_celery="y", use_celery_beat="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in ("app/worker/celery_app.py", "app/worker/tasks/example.py",
                "tests/worker/test_tasks.py", "tests/worker/test_beat.py"):
        assert (proj / rel).exists(), rel


def test_celery_without_beat_leaves_no_beat_code(tmp_path):
    r = gen(tmp_path, use_celery="y", use_celery_beat="n", use_flower="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert (proj / "app/worker/celery_app.py").exists()
    assert not (proj / "tests/worker/test_beat.py").exists()
    scan(proj, BEAT_NEEDLES)


# --- realtime (broker, SSE, long polling) ---------------------------------------------

REALTIME_FLAGS = ("use_sse", "use_long_polling", "use_websocket")
NEW_PROTOCOL_FLAGS = (*REALTIME_FLAGS, "use_graphql", "use_grpc")
NO_REALTIME = {flag: "n" for flag in REALTIME_FLAGS}
BROKER_NEEDLES = ("eventbroker", "inmemorybroker", "channel_for", "text/event-stream",
                  "sse_heartbeat_seconds", "long_poll_max_seconds", "app.realtime",
                  "get_broker", "brokerunavailable", "invalid_event_id", "realtimestate",
                  "realtime_max_connections", "too_many_connections")
SSE_PATHS = ["app/api/v1/endpoints/events.py", "tests/api/v1/streaming.py",
             "tests/api/v1/test_sse.py"]
POLL_PATHS = ["app/api/v1/endpoints/poll.py", "app/schemas/v1/event.py",
              "tests/api/v1/test_poll.py"]
BROKER_PATHS = ["app/realtime/__init__.py", "app/realtime/broker.py",
                "app/realtime/state.py", "app/realtime/follow.py", "tests/realtime/test_broker.py",
                "tests/realtime/test_publish.py", "tests/realtime/test_state.py"]
REDIS_BROKER_PATHS = ["app/realtime/redis_broker.py"]
WS_PATHS = ["app/api/v1/endpoints/ws.py", "tests/api/v1/test_ws.py"]
WS_CLIENT = "tests/api/v1/ws_client.py"  # also drives GraphQL subscriptions
GRAPHQL_PATHS = ["app/graphql/schema.py", "app/graphql/context.py", "tests/graphql/test_graphql.py"]


def load_check_project():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_project", ROOT / "scripts/check_project.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_flags_are_cookiecutter_choices_defaulting_to_y():
    import json

    context = json.loads((ROOT / "cookiecutter.json").read_text())
    keys = list(context)
    for flag in NEW_PROTOCOL_FLAGS:
        assert context[flag] == ["y", "n"], flag
    # Placed right after use_redis, in this order.
    start = keys.index("use_redis") + 1
    assert keys[start:start + len(NEW_PROTOCOL_FLAGS)] == list(NEW_PROTOCOL_FLAGS)


def test_presets_cover_the_protocol_flags():
    presets = load_check_project().PRESETS
    for flag in NEW_PROTOCOL_FLAGS:
        assert presets["minimal"][flag] == "n", flag
        assert presets["full"][flag] == "y", flag


@pytest.mark.parametrize("extra", [{}, {"use_graphql": "y", "use_grpc": "y"}])
def test_no_realtime_flag_leaves_no_broker_code(tmp_path, extra):
    # GraphQL and gRPC alone do not force a broker.
    r = gen(tmp_path, **NO_REALTIME, **extra)
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in ["app/realtime", "tests/realtime", *SSE_PATHS, *POLL_PATHS]:
        assert not (proj / rel).exists(), rel
    scan(proj, BROKER_NEEDLES + ("redisbroker", "get_stream_redis", "sse_heartbeat",
                                 "long_poll"))


@pytest.mark.parametrize("flag", REALTIME_FLAGS)
def test_any_realtime_flag_generates_the_broker(tmp_path, flag):
    r = gen(tmp_path, **{**NO_REALTIME, flag: "y"})
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in [*BROKER_PATHS, *REDIS_BROKER_PATHS]:
        assert (proj / rel).exists(), rel
    for rel in SSE_PATHS:
        assert (proj / rel).exists() == (flag == "use_sse"), rel
    for rel in POLL_PATHS:
        assert (proj / rel).exists() == (flag == "use_long_polling"), rel
    for rel in WS_PATHS:
        assert (proj / rel).exists() == (flag == "use_websocket"), rel


def test_sse_off_leaves_no_sse_code(tmp_path):
    r = gen(tmp_path, use_sse="n", use_long_polling="y", use_websocket="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in SSE_PATHS:
        assert not (proj / rel).exists(), rel
    assert (proj / "app/api/v1/endpoints/poll.py").exists()
    scan(proj, ("sse_heartbeat_seconds", "text/event-stream", "last-event-id"))


def test_long_polling_off_leaves_no_poll_code(tmp_path):
    r = gen(tmp_path, use_sse="y", use_long_polling="n", use_websocket="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in POLL_PATHS:
        assert not (proj / rel).exists(), rel
    assert (proj / "app/api/v1/endpoints/events.py").exists()
    scan(proj, ("long_poll_max_seconds", "/poll", "eventbatch"))


def test_websocket_off_leaves_no_websocket_code(tmp_path):
    r = gen(tmp_path, use_sse="y", use_long_polling="y", use_websocket="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in WS_PATHS:
        assert not (proj / rel).exists(), rel
    scan(proj, ("/ws/items", "answer_pings", "origin_allowed", "authenticate_connection(websocket"))


@pytest.mark.parametrize(
    ("websocket", "graphql", "client_kept"),
    [("n", "n", False), ("y", "n", True), ("n", "y", True)],
)
def test_websocket_test_client_is_kept_for_websocket_or_graphql(
    tmp_path, websocket, graphql, client_kept
):
    r = gen(tmp_path, use_websocket=websocket, use_graphql=graphql)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "my_project" / WS_CLIENT).exists() == client_kept


def test_graphql_off_leaves_no_graphql_code(tmp_path):
    r = gen(tmp_path, use_graphql="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert not (proj / "app/graphql").exists()
    assert not (proj / "tests/graphql").exists()
    scan(proj, ("strawberry", "create_graphql_router", "app.graphql"))


def test_graphql_on_generates_it_with_or_without_a_broker(tmp_path):
    r = gen(tmp_path, **NO_REALTIME, use_graphql="y")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in GRAPHQL_PATHS:
        assert (proj / rel).exists(), rel
    assert not (proj / "app/realtime").exists()
    assert "Subscription" not in (proj / "app/graphql/schema.py").read_text()


GRPC_PATHS = ["proto/items/v1/items.proto", "scripts/gen_proto.py", "app/grpc/server.py",
              "app/grpc/servicers.py", "app/grpc/interceptors.py",
              "app/grpc/generated/items_pb2.py", "app/grpc/generated/items_pb2_grpc.py",
              "tests/grpc/test_grpc.py"]
COOKIE_ONLY = dict(use_auth="y", use_auth_jwt_bearer="n", use_auth_jwt_cookie="y",
                   use_auth_session="n", use_auth_api_key="n", use_auth_basic="n",
                   use_social_login="n")


def test_pre_gen_rejects_grpc_with_only_cookie_auth(tmp_path):
    r = gen(tmp_path, use_grpc="y", **COOKIE_ONLY)
    assert r.returncode != 0
    assert "use_grpc=y" in r.stderr and "use_auth_jwt_bearer" in r.stderr


@pytest.mark.parametrize("auth", [
    dict(use_auth="n", use_rbac="n", use_multitenancy="n", use_social_login="n"),
    dict(use_auth_jwt_bearer="y", use_auth_jwt_cookie="n", use_auth_session="n"),
    dict(use_auth_jwt_bearer="n", use_auth_jwt_cookie="y", use_auth_api_key="y"),
])
def test_grpc_is_accepted_with_no_auth_bearer_or_api_key(tmp_path, auth):
    r = gen(tmp_path, use_grpc="y", **auth)
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in GRPC_PATHS:
        assert (proj / rel).exists(), rel


def test_grpc_off_leaves_no_grpc_code(tmp_path):
    r = gen(tmp_path, use_grpc="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    for rel in [*GRPC_PATHS, "proto", "app/grpc", "tests/grpc", "scripts/gen_proto.py"]:
        assert not (proj / rel).exists(), rel
    scan(proj, ("grpc", "protobuf", "items_pb2", "gen_proto"))


def test_redis_off_leaves_no_redis_broker(tmp_path):
    r = gen(tmp_path, use_redis="n", use_celery="n", use_celery_beat="n", use_flower="n")
    assert r.returncode == 0, r.stderr
    proj = tmp_path / "my_project"
    assert (proj / "app/realtime/broker.py").exists()
    for rel in REDIS_BROKER_PATHS:
        assert not (proj / rel).exists(), rel
    scan(proj, ("redisbroker", "redis_broker", "get_stream_redis", "xread", "xadd"))
