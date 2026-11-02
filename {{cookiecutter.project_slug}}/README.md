{%- set auth = cookiecutter.use_auth == "y" -%}
{%- set bearer = auth and cookiecutter.use_auth_jwt_bearer == "y" -%}
{%- set jwt_cookie = auth and cookiecutter.use_auth_jwt_cookie == "y" -%}
{%- set session = auth and cookiecutter.use_auth_session == "y" -%}
{%- set api_key = auth and cookiecutter.use_auth_api_key == "y" -%}
{%- set basic = auth and cookiecutter.use_auth_basic == "y" -%}
{%- set rbac = auth and cookiecutter.use_rbac == "y" -%}
{%- set tenancy = auth and cookiecutter.use_multitenancy == "y" -%}
{%- set social = auth and cookiecutter.use_social_login == "y" -%}
{%- set sse = cookiecutter.use_sse == "y" -%}
{%- set poll = cookiecutter.use_long_polling == "y" -%}
{%- set ws = cookiecutter.use_websocket == "y" -%}
{%- set realtime = sse or poll or ws -%}
{%- set graphql = cookiecutter.use_graphql == "y" -%}
{%- set grpc = cookiecutter.use_grpc == "y" -%}
{%- set celery = cookiecutter.use_celery == "y" -%}
{%- set docker = cookiecutter.use_docker == "y" -%}
# {{ cookiecutter.project_name }}

A FastAPI service on {{ {"postgresql": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}[cookiecutter.database] }} with async SQLAlchemy and Alembic migrations. It was generated from a Cookiecutter template; only the features chosen at generation are in this repository.

## Run it

You need Python {{ cookiecutter.python_version }} or newer and [uv](https://docs.astral.sh/uv/).

```bash
make install                  # uv sync
cp .env.example .env
{%- if auth %}
# set SECRET_KEY in .env: python -c "import secrets; print(secrets.token_urlsafe(48))"
{%- endif %}
make migrate                  # alembic upgrade head
make run                      # http://localhost:8000
```

Interactive docs for each API version are at `/api/v1/docs` and `/api/v2/docs`. `/health/live` answers as long as the process is up; `/health/ready` also checks the database{% if cookiecutter.use_redis == "y" %} (and reports Redis, which only backs the rate limiter, as degraded rather than failing){% endif %}.

| Command | What it does |
| :--- | :--- |
| `make test` | pytest |
| `make lint` | ruff and mypy (strict) |
| `make migrate` | apply database migrations |
| `make openapi` | write `docs/openapi/openapi-v1.json` and `openapi-v2.json` |
{%- if grpc %}
| `make proto` | regenerate the gRPC stubs from `proto/` |
{%- endif %}
{%- if docker %}
| `make up` / `make down` | start / stop the Docker Compose stack |
{%- endif %}

## Settings

Every setting is an environment variable, read by `app/core/config.py` and listed in `.env.example`. Copy that file to `.env`; real environment variables win over it.
{%- if auth %} Outside `ENVIRONMENT=dev` the app refuses to start with the placeholder `SECRET_KEY` or one shorter than 32 characters.{% endif %}

{%- if auth %}

## Authentication

Enabled methods: {{ [
    "JWT Bearer" if bearer else "",
    "JWT in an HttpOnly cookie" if jwt_cookie else "",
    "server-side sessions" if session else "",
    "API keys" if api_key else "",
    "HTTP Basic" if basic else "",
] | select | join(", ") }}. A request that carries a credential for one of them and fails is rejected with 401; it is never retried against another method.

Set `FIRST_SUPERUSER_EMAIL` and `FIRST_SUPERUSER_PASSWORD` in `.env` to create the first administrator at startup.
{%- if bearer %}

- **Bearer:** `POST /api/v1/auth/login`, then send `Authorization: Bearer <access token>`. Refresh tokens are stored in the database and rotated on use; presenting an already-used one revokes all of that user's refresh tokens.
{%- endif %}
{%- if jwt_cookie %}

- **JWT cookie:** login sets an HttpOnly access cookie.
{%- endif %}
{%- if session %}

- **Session:** login sets an HttpOnly session cookie. Sessions live in the database and expire after `SESSION_IDLE_TIMEOUT_MINUTES` idle or `SESSION_ABSOLUTE_LIFETIME_HOURS` in total.
{%- endif %}
{%- if jwt_cookie or session %}

Cookie-authenticated `POST`, `PUT`, `PATCH` and `DELETE` requests must send the `X-CSRF-Token` header (the value is returned at login). Requests that use another method are not affected.
{%- endif %}
{%- if api_key %}

- **API key:** a signed-in user creates keys with `POST /api/v1/api-keys`; the key is shown once. Send it as `X-API-Key: <key>`. Revoked or expired keys get 401.
{%- endif %}
{%- if basic %}

- **Basic:** `Authorization: Basic ...` against the same password hash. The server never sends a `WWW-Authenticate: Basic` challenge, so browsers do not pop up a login box.
{%- endif %}
{%- if rbac %}

Endpoints require permissions such as `items:read` and `items:write`, granted through roles. Superusers pass every check.
{%- endif %}
{%- if tenancy %}

Each user belongs to one tenant, taken from the authenticated principal and never from a request header. Asking for another tenant's record answers 404.
{%- endif %}
{%- if social %}

Google and GitHub sign-in is at `/api/v1/oauth/<provider>/login`. A provider is off (404) until both its client id and secret are set. Register `<OAUTH_REDIRECT_BASE_URL>/api/v1/oauth/<provider>/callback` with the provider. Accounts are linked only when the provider reports the email as verified.
{%- endif %}
{%- endif %}
{%- if realtime %}

## Realtime item events

Creating, updating or deleting an item publishes an event{% if tenancy %} to the caller's tenant{% endif %}. Events reach clients through:
{%- if sse %}

- `GET /api/v1/items/events`: Server-Sent Events, resumable with `Last-Event-ID`.
{%- endif %}
{%- if poll %}

- `GET /api/v1/items/poll?after=<id>&timeout=<seconds>`: long polling; 204 when nothing arrives in time.
{%- endif %}
{%- if ws %}

- `WS /api/v1/ws/items`: one JSON message per event; send `ping` to get `pong`. Credentials are read from the handshake only, never the query string.
{%- endif %}

At most `REALTIME_MAX_CONNECTIONS` long-lived connections are served per process.{% if cookiecutter.use_redis == "y" %} Events travel through Redis Streams, so every process sees them.{% else %} Events are kept in the process, so they reach only clients connected to the same process.{% endif %}
{%- endif %}
{%- if graphql %}

## GraphQL

`/graphql` serves item queries and mutations{% if realtime %} and an `itemEvents` subscription{% endif %}, through the same service layer and the same credentials as REST. In `prod`, introspection and GraphiQL are off, and queries nested deeper than 10 levels are rejected.
{%- endif %}
{%- if grpc %}

## gRPC

`proto/items/v1/items.proto` defines `items.v1.ItemService`. Run the server as its own process:

```bash
uv run python -m app.grpc.server      # GRPC_PORT, default 50051
```

{% if auth %}Send `authorization: Bearer <token>` or `x-api-key: <key>` as call metadata; other credentials are ignored. {% endif %}Set `GRPC_TLS_CERT` and `GRPC_TLS_KEY` to serve TLS. Server reflection is on only when `ENVIRONMENT=dev`. After editing the proto file run `make proto`; a test fails when the committed stubs in `app/grpc/generated` are stale.
{%- endif %}
{%- if celery %}

## Background tasks

Celery with RabbitMQ as the broker. Tasks live in `app/worker/tasks`.

```bash
uv run celery -A app.worker.celery_app worker --loglevel=info
{%- if cookiecutter.use_celery_beat == "y" %}
uv run celery -A app.worker.celery_app beat --loglevel=info
{%- endif %}
```
{%- if cookiecutter.use_flower == "y" %}

Flower runs as `celery -A app.worker.celery_app flower --basic-auth=user:password`. There is no default login.
{%- endif %}
{%- endif %}
{%- if docker %}

## Docker

```bash
cp .env.example .env
{%- if auth %}
# set SECRET_KEY in .env
{%- endif %}
{%- if cookiecutter.use_flower == "y" %}
# set FLOWER_BASIC_AUTH=user:password in .env
{%- endif %}
make up
```

Compose starts the API{% if cookiecutter.database != "sqlite" %}, the database{% endif %}{% if cookiecutter.use_redis == "y" %}, Redis{% endif %}{% if celery %}, RabbitMQ and a worker{% endif %}{% if cookiecutter.use_celery_beat == "y" %}, the scheduler{% endif %}{% if cookiecutter.use_flower == "y" %}, Flower on `127.0.0.1:5555`{% endif %}{% if grpc %} and the gRPC server{% endif %}. The API applies migrations on start. The database and broker use fixed local credentials and are published on `127.0.0.1` only: change them before running this anywhere shared.
{%- endif %}

## API schemas

`docs/openapi/openapi-v1.json` and `openapi-v2.json` are generated by `make openapi` and should be committed.
{%- if cookiecutter.use_github_actions == "y" %} CI regenerates them and fails if they differ from what is committed.{% endif %}
