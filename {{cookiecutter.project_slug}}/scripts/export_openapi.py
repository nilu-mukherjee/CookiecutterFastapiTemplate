{%- set auth = cookiecutter.use_auth == "y" -%}
"""Write each API version's OpenAPI schema to docs/openapi/openapi-v{n}.json.

Building the schema connects to nothing: no database, no Redis, no broker.
Run it with ``make openapi`` (``python -m scripts.export_openapi``) and commit the result;
CI fails when the files drift.
"""
import json
{%- if auth %}
import os
{%- endif %}
import sys
from pathlib import Path
{%- if auth %}

# Settings are read when app.main is imported. The schema does not depend on the key.
os.environ.setdefault("SECRET_KEY", "openapi-export-only-" + "0" * 32)
{%- endif %}

from fastapi import FastAPI
from starlette.routing import Mount

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "openapi"


def schemas(application: FastAPI) -> dict[str, dict[str, object]]:
    """The OpenAPI schema of every mounted version app, keyed by name (``v1``, ``v2``)."""
    return {
        route.path.rsplit("/", 1)[-1]: route.app.openapi()
        for route in application.routes
        if isinstance(route, Mount) and isinstance(route.app, FastAPI)
    }


def render(schema: dict[str, object]) -> str:
    """Stable text: sorted keys, so a diff shows real changes only."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def export(application: FastAPI, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in schemas(application).items():
        path = out_dir / f"openapi-{name}.json"
        path.write_text(render(schema))
        written.append(path)
    return written


def main() -> int:
    from app.main import app

    for path in export(app, OUT_DIR):
        print(f"wrote {path.relative_to(OUT_DIR.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
