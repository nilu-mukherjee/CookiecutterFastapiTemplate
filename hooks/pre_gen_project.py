"""Validate cookiecutter flag combinations before anything is generated."""
import re
import sys

flags = {
    "project_slug": "{{ cookiecutter.project_slug }}",
    "use_auth": "{{ cookiecutter.use_auth }}",
    "use_auth_jwt_bearer": "{{ cookiecutter.use_auth_jwt_bearer }}",
    "use_auth_jwt_cookie": "{{ cookiecutter.use_auth_jwt_cookie }}",
    "use_auth_session": "{{ cookiecutter.use_auth_session }}",
    "use_auth_api_key": "{{ cookiecutter.use_auth_api_key }}",
    "use_auth_basic": "{{ cookiecutter.use_auth_basic }}",
    "use_social_login": "{{ cookiecutter.use_social_login }}",
    "use_rbac": "{{ cookiecutter.use_rbac }}",
    "use_multitenancy": "{{ cookiecutter.use_multitenancy }}",
    "use_celery": "{{ cookiecutter.use_celery }}",
    "use_celery_beat": "{{ cookiecutter.use_celery_beat }}",
    "use_flower": "{{ cookiecutter.use_flower }}",
}
METHODS = [
    "use_auth_jwt_bearer", "use_auth_jwt_cookie", "use_auth_session",
    "use_auth_api_key", "use_auth_basic",
]
HUMAN_METHODS = [
    "use_auth_jwt_bearer", "use_auth_jwt_cookie", "use_auth_session", "use_auth_basic",
]
SOCIAL_METHODS = ["use_auth_session", "use_auth_jwt_cookie", "use_auth_jwt_bearer"]


def on(name: str) -> bool:
    return flags[name] == "y"


def fail(msg: str) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(1)


if not re.fullmatch(r"[a-z_][a-z0-9_]*", flags["project_slug"]):
    fail(f"project_slug '{flags['project_slug']}' must be a valid Python identifier (lowercase)")

if on("use_auth"):
    if not any(on(m) for m in METHODS):
        fail("use_auth=y requires at least one of: " + ", ".join(METHODS))
    if on("use_social_login") and not any(on(m) for m in SOCIAL_METHODS):
        fail("use_social_login=y requires one of: " + ", ".join(SOCIAL_METHODS))
    # API keys can only be created by a signed-in person (an API key cannot mint keys).
    if on("use_auth_api_key") and not any(on(m) for m in HUMAN_METHODS):
        fail("use_auth_api_key=y requires one of: " + ", ".join(HUMAN_METHODS)
             + " (a key cannot create the first key)")
else:
    for dep in ("use_rbac", "use_multitenancy", "use_social_login"):
        if on(dep):
            fail(f"{dep}=y requires use_auth=y")
    ignored = [m for m in METHODS if on(m)]
    if ignored:
        print(f"NOTICE: use_auth=n, so auth method flags are ignored: {', '.join(ignored)}")

if not on("use_celery"):
    for dep in ("use_celery_beat", "use_flower"):
        if on(dep):
            fail(f"{dep}=y requires use_celery=y")
