import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def gen(tmp_path, **flags):
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
    r = gen(tmp_path)
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
