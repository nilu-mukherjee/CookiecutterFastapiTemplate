import subprocess
import sys
from pathlib import Path

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

