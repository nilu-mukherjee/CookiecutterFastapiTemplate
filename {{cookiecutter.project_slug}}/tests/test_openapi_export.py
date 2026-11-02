{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = auth and (cookiecutter.use_auth_jwt_cookie == "y" or cookiecutter.use_auth_session == "y") -%}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = auth and cookiecutter.use_auth_basic == "y" -%}
import importlib.util
import json
from pathlib import Path

from fastapi import FastAPI

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent


def load_exporter():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "export_openapi", ROOT / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version_apps(application: FastAPI) -> dict[str, FastAPI]:
    return {
        route.path.rsplit("/", 1)[-1]: route.app  # type: ignore[attr-defined]
        for route in application.routes
        if getattr(route, "path", "").startswith("/api/")
    }


def test_export_openapi_writes_both_versions(tmp_path: Path) -> None:
    written = load_exporter().export(create_app(), tmp_path)
    assert sorted(p.name for p in written) == ["openapi-v1.json", "openapi-v2.json"]
    for path in written:
        assert json.loads(path.read_text())["openapi"]


def test_exported_files_match_live_schema(tmp_path: Path) -> None:
    application = create_app()
    load_exporter().export(application, tmp_path)
    for name, sub in version_apps(application).items():
        exported = json.loads((tmp_path / f"openapi-{name}.json").read_text())
        assert exported == json.loads(json.dumps(sub.openapi()))


def test_export_is_stable(tmp_path: Path) -> None:
    exporter = load_exporter()
    exporter.export(create_app(), tmp_path / "a")
    exporter.export(create_app(), tmp_path / "b")
    for name in ("openapi-v1.json", "openapi-v2.json"):
        assert (tmp_path / "a" / name).read_text() == (tmp_path / "b" / name).read_text()


def test_v1_and_v2_schemas_differ_on_items_list_response() -> None:
    schemas = {
        name: sub.openapi()["paths"]["/items"]["get"]["responses"]["200"]
        for name, sub in version_apps(create_app()).items()
    }
    assert schemas["v1"] != schemas["v2"]


def test_security_schemes_only_for_enabled_methods() -> None:
    expected: set[tuple[str, str]] = set()
{%- if bearer %}
    expected.add(("http", "bearer"))
{%- endif %}
{%- if cookie %}
    expected.add(("apiKey", "cookie"))
{%- endif %}
{%- if api_key %}
    expected.add(("apiKey", "header"))
{%- endif %}
{%- if basic %}
    expected.add(("http", "basic"))
{%- endif %}
    for sub in version_apps(create_app()).values():
        schemes = sub.openapi().get("components", {}).get("securitySchemes", {})
        assert {(s["type"], s.get("scheme") or s["in"]) for s in schemes.values()} == expected
