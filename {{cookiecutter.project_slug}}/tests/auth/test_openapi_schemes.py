{%- set bearer = cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set cookie = cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = cookiecutter.use_auth_session == "y" -%}
{%- set api_key = cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = cookiecutter.use_auth_basic == "y" -%}
from httpx import AsyncClient

EXPECTED = {
{%- if bearer %}
    "HTTPBearer": {"type": "http", "scheme": "bearer"},
{%- endif %}
{%- if api_key %}
    "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
{%- endif %}
{%- if session %}
    "SessionCookie": {"type": "apiKey", "in": "cookie", "name": "session_id"},
{%- endif %}
{%- if cookie %}
    "APIKeyCookie": {"type": "apiKey", "in": "cookie", "name": "access_token"},
{%- endif %}
{%- if basic %}
    "BasicAuth": {"type": "http", "scheme": "basic"},
{%- endif %}
}


async def test_openapi_lists_exactly_the_enabled_schemes_with_unique_names(
    anon_client: AsyncClient,
) -> None:
    for version in ("v1", "v2"):
        schema = (await anon_client.get(f"/api/{version}/openapi.json")).json()
        schemes = schema["components"]["securitySchemes"]
        assert schemes == EXPECTED
        # Every protected operation names a declared scheme.
        used = {
            name
            for item in schema["paths"].values()
            for op in item.values()
            for requirement in op.get("security", [])
            for name in requirement
        }
        assert used == set(EXPECTED)
