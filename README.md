# 🚀 FastAPI Cookiecutter Template

> Generate a FastAPI project with only the parts you pick: authentication, multi-tenancy, realtime events and background workers.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/)

---

## ✨ Features

* **Pick your stack**: a database choice and 20 yes/no flags decide what is generated. Code for a feature you turn off is not in the project at all, so there is nothing to delete later.
* **Five authentication methods**: JWT Bearer, JWT in an HttpOnly cookie, server-side sessions, API keys and HTTP Basic, with CSRF protection for the cookie methods. Enable any combination.
* **Authorization and tenancy**: optional roles and permissions, and optional shared-schema multi-tenancy where repositories filter by tenant on every query.
* **Realtime item events**: Server-Sent Events, long polling, WebSocket and GraphQL subscriptions share one event broker, in memory or on Redis Streams.
* **gRPC**: an `items.v1` service with committed, drift-tested stubs, served by its own process and authenticated with Bearer tokens or API keys.
* **GraphQL**: queries and mutations over the same item service, with the same authentication, permissions and tenant scoping as REST.
* **Checked on every build**: the template ships a script that generates a project and runs ruff, mypy and pytest on it.

## 📦 Installation

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) for the generated project.

```bash
# Straight from GitHub
pipx run cookiecutter gh:nilu-mukherjee/CookiecutterFastapiTemplate

# Or from a local clone
git clone https://github.com/nilu-mukherjee/CookiecutterFastapiTemplate.git
pipx run cookiecutter CookiecutterFastapiTemplate
```

Cookiecutter asks for each variable in the table below. Pass `--no-input` and `key=value` pairs to skip the prompts.

## 🚀 Quick start

This generates a small API with Bearer login, RBAC, SSE and long polling on SQLite, then runs it.

```bash
pipx run cookiecutter CookiecutterFastapiTemplate --no-input \
  project_name="Orders API" database=sqlite \
  use_auth_jwt_cookie=n use_auth_session=n use_auth_api_key=n use_auth_basic=n \
  use_social_login=n use_multitenancy=n use_redis=n use_websocket=n \
  use_celery=n use_celery_beat=n use_flower=n use_graphql=n use_grpc=n

cd orders_api
uv sync
cp .env.example .env          # then set SECRET_KEY, see below
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

The API is at <http://localhost:8000>. Interactive docs for each API version are at `/api/v1/docs` and `/api/v2/docs`, and health checks are at `/health/live` and `/health/ready`. Run the project's own checks with `uv run pytest`, `uv run ruff check .` and `uv run mypy app`.

### Realtime events

With `use_sse`, `use_long_polling` and `use_websocket` on, creating, updating or deleting an item publishes `item.created`, `item.updated` or `item.deleted`. Each endpoint requires the same credentials as the rest of the API.

| Endpoint | Behaviour |
| :--- | :--- |
| `GET /api/v1/items/events` | Server-Sent Events. Resume with `Last-Event-ID`. Sends a comment every `SSE_HEARTBEAT_SECONDS`. |
| `GET /api/v1/items/poll?after=<id>&timeout=<s>` | Answers at once if events exist after `after`, otherwise waits and returns 204 on timeout. |
| `WS /api/v1/ws/items` | One JSON message per event. The text `ping` is answered with `pong`. |

For WebSocket, credentials are read from the handshake headers and cookies only. A token in the query string is never accepted. A handshake that carries a cookie must come from the server's own origin or one listed in `CORS_ORIGINS`, otherwise it is rejected (this stops other sites from using a visitor's cookies). With multi-tenancy on, a client only ever receives its own tenant's events.

### GraphQL

With `use_graphql`, `/graphql` serves `items(limit, offset)` and `item(id)` queries and `createItem`, `updateItem` and `deleteItem` mutations. All call the same `ItemService` as the REST endpoints and accept the same credentials, CSRF rules, permissions and tenant scoping. An unauthenticated request is a 401. A missing permission is a GraphQL error. An item outside the caller's tenant comes back as `null`.

When a realtime flag is on, the `itemEvents` subscription streams item changes over the `graphql-transport-ws` protocol. A subscription handshake must come from the server's own origin or one in `CORS_ORIGINS`. Queries nested deeper than 10 levels are rejected. In `prod`, introspection and the GraphiQL page are turned off.

### gRPC

With `use_grpc`, `proto/items/v1/items.proto` defines `ItemService` with `GetItem`, `ListItems`, `CreateItem`, `UpdateItem`, `DeleteItem` and the server-streaming `StreamItemEvents`. It calls the same `ItemService` as REST and GraphQL, so permissions and tenant scoping apply the same way. Run it as its own process:

```bash
uv run python -m app.grpc.server       # listens on GRPC_PORT, default 50051
```

Send `authorization: Bearer <token>` or `x-api-key: <key>` as call metadata. Cookie, session and Basic credentials are ignored, so those calls are UNAUTHENTICATED. Domain errors map to gRPC status codes (not found, permission denied, unauthenticated, invalid argument). Set `GRPC_TLS_CERT` and `GRPC_TLS_KEY` to serve TLS. Server reflection is on only when `ENVIRONMENT=dev`. `StreamItemEvents` answers UNIMPLEMENTED in a project generated without a realtime flag, because there is no event broker to read from.

The generated stubs in `app/grpc/generated` are committed. After editing the proto file run `uv run python scripts/gen_proto.py`; a test fails if the stubs and the proto disagree.

Without `use_redis` the broker lives in the process, so events reach only clients connected to that process. Turn on Redis, or run one worker, if you need more.

## 🛠️ Configuration and environment variables

### Template variables

Flags default to `y`. `project_slug` is derived from `project_name`.

| Variable | Choices | Requires or effect |
| :--- | :--- | :--- |
| `project_name` | text | Name of the project and of the OpenAPI docs. |
| `project_slug` | identifier | Directory name. Must be a lowercase Python identifier. |
| `author`, `author_email` | text | Written to the project metadata. |
| `python_version` | `3.12`, `3.13` | Minimum Python version of the generated project. |
| `database` | `postgresql`, `mysql`, `sqlite` | Picks the driver (asyncpg, aiomysql, aiosqlite) and the default `DATABASE_URL`. |
| `use_docker` | `y`, `n` | Not built yet. The flag exists but generates nothing. |
| `use_github_actions` | `y`, `n` | Not built yet. The flag exists but generates nothing. |
| `use_auth` | `y`, `n` | Needs at least one auth method. With `n` all routes are public and the method flags are ignored. |
| `use_auth_jwt_bearer` | `y`, `n` | Needs `use_auth`. Access and refresh tokens in the `Authorization` header. |
| `use_auth_jwt_cookie` | `y`, `n` | Needs `use_auth`. The same tokens in HttpOnly cookies, with CSRF protection. |
| `use_auth_session` | `y`, `n` | Needs `use_auth`. Sessions stored in the database, with CSRF protection. |
| `use_auth_api_key` | `y`, `n` | Needs `use_auth` and one of Bearer, cookie, session or Basic, because a person has to create the first key. |
| `use_auth_basic` | `y`, `n` | Needs `use_auth`. HTTP Basic against the same password hash. |
| `use_social_login` | `y`, `n` | Needs `use_auth` and one of Bearer, cookie or session. Google and GitHub. |
| `use_rbac` | `y`, `n` | Needs `use_auth`. Roles, permissions and `require_permission`. |
| `use_multitenancy` | `y`, `n` | Needs `use_auth`. Tenant model and tenant-scoped repositories. |
| `use_redis` | `y`, `n` | Redis rate limiter, Redis Streams broker and Celery result backend. Without it the rate limiter is per process. |
| `use_sse` | `y`, `n` | Server-Sent Events endpoint. |
| `use_long_polling` | `y`, `n` | Long polling endpoint. |
| `use_websocket` | `y`, `n` | WebSocket endpoint. |
| `use_graphql` | `y`, `n` | Strawberry GraphQL at `/graphql`, with a subscription when a realtime flag is on. |
| `use_grpc` | `y`, `n` | gRPC item service in its own process. With `use_auth` it needs Bearer or API key auth, because gRPC calls cannot carry cookies, sessions or Basic. |
| `use_celery` | `y`, `n` | Celery worker with RabbitMQ as the broker. |
| `use_celery_beat` | `y`, `n` | Needs `use_celery`. Periodic task scheduler. |
| `use_flower` | `y`, `n` | Needs `use_celery`. Declares the Flower dashboard credentials setting. The Flower service itself arrives with Docker support. |

An invalid combination, such as `use_rbac=y` with `use_auth=n`, stops generation with an error that names the missing flag.

### Generated project settings

The generated `.env.example` lists only the settings that apply to your flags. The ones most projects touch:

| Variable | Description | Type | Default |
| :--- | :--- | :--- | :--- |
| `SECRET_KEY` | Signs tokens and CSRF values. Outside `dev` the app refuses to start with the placeholder or a key under 32 characters. | String | `change-me` |
| `ENVIRONMENT` | `dev`, `staging` or `prod`. Changes HSTS, host checks and Basic over HTTP. | String | `dev` |
| `DATABASE_URL` | SQLAlchemy async URL. | String | depends on `database` |
| `CORS_ORIGINS` | Allowed browser origins, as a JSON list. Also used for WebSocket cookie handshakes. | JSON list | `[]` |
| `FIRST_SUPERUSER_EMAIL`, `FIRST_SUPERUSER_PASSWORD` | Create the first superuser at startup when both are set. | String | empty |
| `REDIS_URL` | Redis for rate limiting and the broker. | String | `redis://localhost:6379/0` |
| `REALTIME_MAX_CONNECTIONS` | Most streams, polls and sockets one process serves at once. Extra clients get 503, or close code 1013 on WebSocket. | Integer | `1000` |
| `SSE_HEARTBEAT_SECONDS` | Idle interval between SSE keepalive comments. | Number | `15` |
| `LONG_POLL_MAX_SECONDS` | Upper bound of a long poll's `timeout`. | Number | `30` |
| `COOKIE_SECURE` | Must be `true` outside `dev` (HTTPS only). | Boolean | `false` |

## Choosing authentication methods

| Method | Good for | Watch out for |
| :--- | :--- | :--- |
| JWT Bearer | Mobile apps, other servers, SPAs that store tokens themselves | Anything in JavaScript storage can be read by an injected script. |
| JWT in cookie | Browser apps on the same site | Needs the CSRF header on unsafe requests. |
| Session | Server-rendered or browser apps | Needs the CSRF header. Every request reads the session row. |
| API key | Scripts and integrations | A key has its owner's permissions and cannot create other keys. |
| Basic | Internal or service-to-service calls | Only over HTTPS. It is refused over plain HTTP outside `dev`, and never sends a browser login prompt. |

When a request carries more than one credential, the first one found decides, and an invalid credential is a 401 instead of falling through to the next method.

## Project structure

```
app/
  api/          versioned routers (v1, v2), dependencies, health checks
  core/         settings, security, logging, errors, middleware order, auth methods
  middleware/   request id, access log, security headers, timing, rate limit, deprecation
  models/       SQLAlchemy models
  repositories/ database access, tenant-scoped when multi-tenancy is on
  services/     business logic, written once and shared by every API version
  realtime/     event broker and connection limits
  worker/       Celery app and tasks
alembic/        async migrations for the enabled models
tests/          unit, API, auth, RBAC, tenancy, middleware and worker tests
```

To add an endpoint, add a router module under `app/api/v1/endpoints/` and include it in `app/api/v1/router.py`. To add an API version, copy the `v1` package, change what differs and mount it in `app/api/router.py`.

## Testing the template

```bash
uv sync
uv run pytest                                                   # hook and generation tests
uv run python scripts/check_project.py --preset minimal         # generate, lint, type-check, test
uv run python scripts/check_project.py --preset full
uv run python scripts/check_project.py --preset minimal use_websocket=y
```

`check_project.py` accepts `key=value` overrides after the preset, so any flag combination can be checked.

## 🤝 Contributing

Contributions are welcome. Run the checks above before opening a pull request.

1. **Fork** the project
2. **Create** your feature branch (`git checkout -b feature/my-change`)
3. **Commit** your changes (`git commit -m 'Add my change'`)
4. **Push** to the branch (`git push origin feature/my-change`)
5. **Open** a pull request

## 📄 License

Distributed under the MIT License.
