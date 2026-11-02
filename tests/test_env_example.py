from test_hooks import gen

OFF = {
    "use_auth": "n", "use_social_login": "n", "use_rbac": "n", "use_multitenancy": "n",
    "use_redis": "n", "use_sse": "n", "use_long_polling": "n", "use_websocket": "n",
    "use_graphql": "n", "use_grpc": "n", "use_celery": "n", "use_celery_beat": "n",
    "use_flower": "n",
}


def keys_of(tmp_path, **flags):
    r = gen(tmp_path, **flags)
    assert r.returncode == 0, r.stderr
    (project,) = (p for p in tmp_path.iterdir() if p.is_dir())
    lines = (project / ".env.example").read_text().splitlines()
    return {line.partition("=")[0] for line in lines if line and not line.startswith("#")}


def test_no_disabled_feature_keys(tmp_path):
    keys = keys_of(tmp_path, **OFF)
    assert keys == {
        "ENVIRONMENT", "DEBUG", "LOG_LEVEL", "DATABASE_URL", "CORS_ORIGINS",
        "ALLOWED_HOSTS", "RATE_LIMIT_PER_MINUTE",
    }


def test_grpc_keys_follow_the_flag(tmp_path):
    on = keys_of(tmp_path / "on", **{**OFF, "use_grpc": "y"})
    assert {"GRPC_PORT", "GRPC_TLS_CERT", "GRPC_TLS_KEY"} <= on
    assert not {k for k in keys_of(tmp_path / "off", **OFF) if k.startswith("GRPC")}


def test_flower_login_is_listed_only_with_docker(tmp_path):
    celery = {**OFF, "use_celery": "y", "use_flower": "y"}
    assert "FLOWER_BASIC_AUTH" in keys_of(tmp_path / "a", **celery, use_docker="y")
    assert "FLOWER_BASIC_AUTH" not in keys_of(tmp_path / "b", **celery, use_docker="n")
    assert "FLOWER_BASIC_AUTH" not in keys_of(tmp_path / "c", **OFF, use_docker="y")
