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
