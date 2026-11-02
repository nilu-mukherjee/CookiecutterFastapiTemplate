import pytest
import yaml
from test_hooks import gen

NONE = {
    "use_auth": "n", "use_social_login": "n", "use_rbac": "n", "use_multitenancy": "n",
    "use_redis": "n", "use_sse": "n", "use_long_polling": "n", "use_websocket": "n",
    "use_graphql": "n", "use_grpc": "n", "use_celery": "n", "use_celery_beat": "n",
    "use_flower": "n",
}
# Each case: flags, then the services compose must define for them.
CASES = {
    "bare-sqlite": ({**NONE, "database": "sqlite"}, {"api"}),
    "postgres": ({**NONE, "database": "postgresql"}, {"api", "db"}),
    "mysql-redis": ({**NONE, "database": "mysql", "use_redis": "y"}, {"api", "db", "redis"}),
    "celery": ({**NONE, "database": "sqlite", "use_celery": "y"}, {"api", "rabbitmq", "worker"}),
    "celery-beat": (
        {**NONE, "database": "sqlite", "use_celery": "y", "use_celery_beat": "y"},
        {"api", "rabbitmq", "worker", "beat"},
    ),
    "everything": (
        {"database": "postgresql", "use_grpc": "y"},
        {"api", "db", "redis", "rabbitmq", "worker", "beat", "flower", "grpc"},
    ),
    "grpc": ({**NONE, "database": "sqlite", "use_grpc": "y"}, {"api", "grpc"}),
}


def compose_of(tmp_path, flags):
    # gen() turns gRPC off unless the case asks for it.
    r = gen(tmp_path, **flags)
    assert r.returncode == 0, r.stderr
    (project,) = (p for p in tmp_path.iterdir() if p.is_dir())
    return yaml.safe_load((project / "docker-compose.yml").read_text())


@pytest.mark.parametrize("flags,services", CASES.values(), ids=CASES.keys())
def test_compose_services_match_flags(tmp_path, flags, services):
    compose = compose_of(tmp_path, flags)
    assert set(compose["services"]) == services


def test_compose_wiring_in_full_build(tmp_path):
    services = compose_of(tmp_path, {"database": "postgresql", "use_grpc": "y"})["services"]
    # Everything that talks to a service waits until it is healthy.
    assert set(services["api"]["depends_on"]) == {"db", "redis", "rabbitmq"}
    assert all(d["condition"] == "service_healthy" for d in services["api"]["depends_on"].values())
    assert "healthcheck" in services["rabbitmq"]
    # Flower has no default credential: compose fails when FLOWER_BASIC_AUTH is unset.
    assert "${FLOWER_BASIC_AUTH:?" in services["flower"]["command"]
    assert services["flower"]["ports"] == ["127.0.0.1:5555:5555"]
    assert "${SECRET_KEY:?" in services["api"]["environment"]["SECRET_KEY"]
    # The brokers are reached by service name, not localhost.
    assert "@db:" in services["api"]["environment"]["DATABASE_URL"]
    assert services["worker"]["environment"]["CELERY_BROKER_URL"].endswith("@rabbitmq:5672//")


def test_sqlite_build_has_no_database_service(tmp_path):
    compose = compose_of(tmp_path, {**NONE, "database": "sqlite"})
    assert "depends_on" not in compose["x-app"]
    assert "app-data" in compose["volumes"]


def test_docker_and_ci_files_follow_their_flags(tmp_path):
    r = gen(tmp_path, use_docker="n", use_github_actions="n")
    assert r.returncode == 0, r.stderr
    (project,) = (p for p in tmp_path.iterdir() if p.is_dir())
    for name in ("Dockerfile", "docker-compose.yml", ".dockerignore", ".github"):
        assert not (project / name).exists(), name
    makefile = (project / "Makefile").read_text()
    assert "docker" not in makefile
    assert "\nup:" not in makefile


def test_makefile_has_the_documented_targets(tmp_path):
    r = gen(tmp_path)
    assert r.returncode == 0, r.stderr
    (project,) = (p for p in tmp_path.iterdir() if p.is_dir())
    makefile = (project / "Makefile").read_text()
    for target in ("install", "run", "test", "lint", "migrate", "openapi", "up", "down"):
        assert f"\n{target}:" in makefile, target
    ci = yaml.safe_load((project / ".github/workflows/ci.yml").read_text())
    steps = " ".join(s["run"] for s in ci["jobs"]["check"]["steps"] if "run" in s)
    for command in ("ruff check", "mypy", "pytest", "make openapi", "docs/openapi"):
        assert command in steps, command


def test_grpc_service_waits_for_the_api_and_publishes_its_port(tmp_path):
    services = compose_of(tmp_path, {"database": "postgresql", "use_grpc": "y"})["services"]
    assert services["grpc"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert services["grpc"]["command"] == "python -m app.grpc.server"
    assert services["grpc"]["ports"] == ["${GRPC_PORT:-50051}:${GRPC_PORT:-50051}"]
