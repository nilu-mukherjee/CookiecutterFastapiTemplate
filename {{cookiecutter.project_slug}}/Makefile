{%- set docker = cookiecutter.use_docker == "y" -%}
.PHONY: install run test lint migrate openapi{% if cookiecutter.use_grpc == "y" %} proto{% endif %}{% if docker %} up down{% endif %}

install:
	uv sync

run:
	uv run uvicorn app.main:app --reload --port 8001

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy app

migrate:
	uv run alembic upgrade head

# Write docs/openapi/openapi-v*.json. Commit them: CI fails when they are out of date.
openapi:
	uv run python -m scripts.export_openapi
{%- if cookiecutter.use_grpc == "y" %}

# Regenerate app/grpc/generated from proto/. A test fails when they are out of date.
proto:
	uv run python scripts/gen_proto.py
{%- endif %}
{%- if docker %}

up:
	docker compose up --build

down:
	docker compose down
{%- endif %}
