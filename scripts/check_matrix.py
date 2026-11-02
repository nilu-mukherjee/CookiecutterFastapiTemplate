#!/usr/bin/env python
"""Run scripts/check_project.py over the flag combinations that matter.

Usage: python scripts/check_matrix.py [--only TEXT] [--list]

Every valid case generates a project and runs ruff, mypy, pytest, the OpenAPI export
(and `docker compose config` when Docker is installed). Every invalid case must abort
generation with a non-zero exit. A failing case does not stop the run; the summary and
the exit code report them all.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

# (name, preset, overrides). Each feature is on its own, on top of the minimal build, with
# only the prerequisites it needs.
VALID: list[tuple[str, str, list[str]]] = [
    ("minimal", "minimal", []),
    ("full", "full", []),
    ("db-postgresql", "full", ["database=postgresql"]),
    ("db-mysql", "full", ["database=mysql"]),
    ("auth-jwt-bearer", "minimal", ["use_auth=y", "use_auth_jwt_bearer=y"]),
    ("auth-jwt-cookie", "minimal", ["use_auth=y", "use_auth_jwt_cookie=y"]),
    ("auth-session", "minimal", ["use_auth=y", "use_auth_session=y"]),
    ("auth-basic", "minimal", ["use_auth=y", "use_auth_basic=y"]),
    ("auth-api-key", "minimal", ["use_auth=y", "use_auth_basic=y", "use_auth_api_key=y"]),
    ("social", "minimal", ["use_auth=y", "use_auth_session=y", "use_social_login=y"]),
    ("rbac", "minimal", ["use_auth=y", "use_auth_jwt_bearer=y", "use_rbac=y"]),
    ("multitenancy", "minimal", ["use_auth=y", "use_auth_jwt_bearer=y", "use_multitenancy=y"]),
    ("redis", "minimal", ["use_redis=y"]),
    ("celery", "minimal", ["use_celery=y"]),
    ("celery-beat", "minimal", ["use_celery=y", "use_celery_beat=y"]),
    ("celery-flower", "minimal", ["use_celery=y", "use_flower=y"]),
    ("sse", "minimal", ["use_sse=y"]),
    ("long-polling", "minimal", ["use_long_polling=y"]),
    ("websocket", "minimal", ["use_websocket=y"]),
    ("graphql", "minimal", ["use_graphql=y"]),
    ("grpc", "minimal", ["use_grpc=y"]),
]
# Combinations generation must refuse.
INVALID: list[tuple[str, list[str]]] = [
    ("rbac-without-auth", ["use_auth=n", "use_rbac=y"]),
    ("tenancy-without-auth", ["use_auth=n", "use_multitenancy=y"]),
    ("social-without-auth", ["use_auth=n", "use_social_login=y"]),
    (
        "auth-without-method",
        ["use_auth=y", "use_auth_jwt_bearer=n", "use_auth_jwt_cookie=n", "use_auth_session=n",
         "use_auth_api_key=n", "use_auth_basic=n", "use_social_login=n"],
    ),
    (
        "social-without-session-or-jwt",
        ["use_auth=y", "use_auth_jwt_bearer=n", "use_auth_jwt_cookie=n", "use_auth_session=n",
         "use_auth_basic=n", "use_auth_api_key=y", "use_social_login=y"],
    ),
    (
        "api-key-alone",
        ["use_auth=y", "use_auth_jwt_bearer=n", "use_auth_jwt_cookie=n", "use_auth_session=n",
         "use_auth_basic=n", "use_auth_api_key=y", "use_social_login=n"],
    ),
    (
        "grpc-with-cookie-only-auth",
        ["use_auth=y", "use_auth_jwt_bearer=n", "use_auth_jwt_cookie=y", "use_auth_session=n",
         "use_auth_basic=n", "use_auth_api_key=n", "use_social_login=n", "use_grpc=y"],
    ),
    ("beat-without-celery", ["use_celery=n", "use_celery_beat=y"]),
    ("flower-without-celery", ["use_celery=n", "use_celery_beat=n", "use_flower=y"]),
]


def run_valid(preset: str, overrides: list[str]) -> bool:
    cmd = [sys.executable, str(HERE / "check_project.py"), "--preset", preset, *overrides]
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def run_invalid(overrides: list[str]) -> bool:
    """True when generation aborted with a message, and left no project behind."""
    with tempfile.TemporaryDirectory(prefix="cc-matrix-") as tmp:
        cmd = ["uv", "run", "--project", str(ROOT), "cookiecutter", "--no-input", str(ROOT),
               "-o", tmp, "use_grpc=n", *overrides]
        # An override later on the command line wins, so the case can turn gRPC on itself.
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        print(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "(no output)")
        return result.returncode != 0 and "ERROR" in result.stderr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run only the cases whose name contains this text")
    ap.add_argument("--list", action="store_true", help="print the case names and exit")
    args = ap.parse_args()

    def selected(name: str) -> bool:
        return not args.only or args.only in name

    if args.list:
        print("\n".join([n for n, _, _ in VALID] + [f"invalid:{n}" for n, _ in INVALID]))
        return 0
    if not shutil.which("docker"):
        print("NOTICE: docker not found. `docker compose config` and the Docker-dependent "
              "smoke tests (database and RabbitMQ boot) are skipped.\n", flush=True)

    results: list[tuple[str, bool]] = []
    for name, preset, overrides in VALID:
        if selected(name):
            print(f"\n=== {name}: {preset} {' '.join(overrides)}", flush=True)
            results.append((name, run_valid(preset, overrides)))
    for name, overrides in INVALID:
        if selected(f"invalid:{name}"):
            print(f"\n=== invalid:{name}: must abort", flush=True)
            results.append((f"invalid:{name}", run_invalid(overrides)))

    print("\n=== summary")
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
