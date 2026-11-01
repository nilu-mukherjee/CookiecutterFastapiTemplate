"""Prune files for disabled features, then git init."""
import shutil
import subprocess
from pathlib import Path

# Flag values are rendered by cookiecutter (this file is a Jinja template).
FLAGS = {
    "use_docker": "{{ cookiecutter.use_docker }}",
    "use_github_actions": "{{ cookiecutter.use_github_actions }}",
    "use_auth": "{{ cookiecutter.use_auth }}",
    "use_auth_jwt_bearer": "{{ cookiecutter.use_auth_jwt_bearer }}",
    "use_auth_jwt_cookie": "{{ cookiecutter.use_auth_jwt_cookie }}",
    "use_auth_session": "{{ cookiecutter.use_auth_session }}",
    "use_auth_api_key": "{{ cookiecutter.use_auth_api_key }}",
    "use_auth_basic": "{{ cookiecutter.use_auth_basic }}",
    "use_social_login": "{{ cookiecutter.use_social_login }}",
    "use_rbac": "{{ cookiecutter.use_rbac }}",
    "use_multitenancy": "{{ cookiecutter.use_multitenancy }}",
    "use_redis": "{{ cookiecutter.use_redis }}",
    "use_celery": "{{ cookiecutter.use_celery }}",
    "use_celery_beat": "{{ cookiecutter.use_celery_beat }}",
    "use_flower": "{{ cookiecutter.use_flower }}",
}

# PRUNE maps a key to the list of paths (relative to the generated project)
# removed when the key is off. Later tasks add entries here.
#   key: a flag name (str)          -> paths removed when that flag is "n"
#   key: a tuple of flag names      -> paths removed when ANY listed flag is "n"
#                                      (dependent-flag case, e.g. a path needing
#                                      both use_auth and use_rbac)
PRUNE: dict[str | tuple[str, ...], list[str]] = {
    "use_docker": ["Dockerfile", "docker-compose.yml", ".dockerignore"],
    "use_github_actions": [".github"],
}


def remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def is_off(key: str | tuple[str, ...]) -> bool:
    keys = (key,) if isinstance(key, str) else key
    return any(FLAGS[k] == "n" for k in keys)


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
