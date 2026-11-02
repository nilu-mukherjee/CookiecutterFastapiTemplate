from test_hooks import gen

SECTIONS = ["## Authentication", "## Realtime item events", "## GraphQL", "## gRPC",
            "## Background tasks", "## Docker"]
OFF = {
    "use_auth": "n", "use_social_login": "n", "use_rbac": "n", "use_multitenancy": "n",
    "use_redis": "n", "use_sse": "n", "use_long_polling": "n", "use_websocket": "n",
    "use_graphql": "n", "use_grpc": "n", "use_celery": "n", "use_celery_beat": "n",
    "use_flower": "n", "use_docker": "n", "use_github_actions": "n",
}


def readme_of(tmp_path, **flags):
    r = gen(tmp_path, **flags)
    assert r.returncode == 0, r.stderr
    (project,) = (p for p in tmp_path.iterdir() if p.is_dir())
    return (project / "README.md").read_text()


def test_minimal_readme_describes_only_the_core(tmp_path):
    text = readme_of(tmp_path, **OFF)
    assert text.startswith("# My Project")
    for section in SECTIONS:
        assert section not in text
    assert "{{" not in text and "{%" not in text


def test_full_readme_has_every_section(tmp_path):
    text = readme_of(tmp_path, use_grpc="y")
    for section in SECTIONS:
        assert section in text
    assert "{{" not in text and "{%" not in text
    assert "Placeholder" not in text


def test_each_section_follows_its_own_flag(tmp_path):
    for i, (flag, section) in enumerate([
        ("use_auth", "## Authentication"), ("use_graphql", "## GraphQL"),
        ("use_celery", "## Background tasks"), ("use_docker", "## Docker"),
    ]):
        flags = {**OFF}
        flags[flag] = "y"
        if flag == "use_auth":
            flags["use_auth_jwt_bearer"] = "y"
        text = readme_of(tmp_path / str(i), **flags)
        assert section in text
        assert sum(s in text for s in SECTIONS) == 1
