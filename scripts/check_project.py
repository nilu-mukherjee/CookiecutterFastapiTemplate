#!/usr/bin/env python
"""Generate the template into a tempdir and run lint/type/test checks.

Usage: python scripts/check_project.py [--preset minimal|full|NAME] [key=value ...]
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLAG_KEYS = [
    "use_docker", "use_github_actions", "use_auth", "use_auth_jwt_bearer",
    "use_auth_jwt_cookie", "use_auth_session", "use_auth_api_key", "use_auth_basic",
    "use_social_login", "use_rbac", "use_multitenancy", "use_redis", "use_celery",
    "use_celery_beat", "use_flower",
]
PRESETS = {
    "minimal": {**{k: "n" for k in FLAG_KEYS}, "database": "sqlite"},
    "full": {**{k: "y" for k in FLAG_KEYS}, "database": "sqlite"},
}


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default=None)
    ap.add_argument("overrides", nargs="*", help="key=value overrides")
    args = ap.parse_args()

    values: dict[str, str] = {}
    if args.preset:
        if args.preset not in PRESETS:
            print(f"unknown preset {args.preset!r}; choose from {sorted(PRESETS)}", file=sys.stderr)
            return 2
        values.update(PRESETS[args.preset])
    for item in args.overrides:
        key, sep, val = item.partition("=")
        if not sep:
            print(f"bad override {item!r}, expected key=value", file=sys.stderr)
            return 2
        values[key] = val

    uv = shutil.which("uv") or "uv"
    tmp = Path(tempfile.mkdtemp(prefix="cc-check-"))
    try:
        gen = [uv, "run", "--project", str(ROOT), "cookiecutter", "--no-input",
               str(ROOT), "-o", str(tmp)] + [f"{k}={v}" for k, v in values.items()]
        run(gen, ROOT)
        projects = [p for p in tmp.iterdir() if p.is_dir()]
        if len(projects) != 1:
            print(f"expected one generated project, found {projects}", file=sys.stderr)
            return 1
        project = projects[0]
        if not (project / "pyproject.toml").exists():
            print("NOTICE: generated project has no pyproject.toml yet; "
                  "skipping uv sync / ruff / mypy / pytest")
            return 0
        # Use the interpreter the project was rendered for, not uv's newest.
        py = tomllib.loads((project / "pyproject.toml").read_text())["tool"]["mypy"]["python_version"]
        run([uv, "sync", "--python", py], project)
        run([uv, "run", "--python", py, "python", "-c",
             "import sys; print('python', sys.version.split()[0])"], project)
        run([uv, "run", "--python", py, "ruff", "check", "."], project)
        run([uv, "run", "--python", py, "mypy", "app"], project)
        run([uv, "run", "--python", py, "pytest", "-q"], project)
    except subprocess.CalledProcessError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return e.returncode or 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
