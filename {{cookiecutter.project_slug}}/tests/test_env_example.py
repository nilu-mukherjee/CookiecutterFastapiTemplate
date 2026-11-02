from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parent.parent
# Read by docker compose, not by the app.
COMPOSE_ONLY = {"FLOWER_BASIC_AUTH"}


def example_keys() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key] = value
    return values


def test_env_keys_are_settings_fields() -> None:
    unknown = {k for k in example_keys() if k not in COMPOSE_ONLY} - {
        name.upper() for name in Settings.model_fields
    }
    assert not unknown


def test_required_settings_present_in_env_example() -> None:
    required = {name.upper() for name, f in Settings.model_fields.items() if f.is_required()}
    assert required <= set(example_keys())


def test_settings_load_from_env_example(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for key in example_keys():
        monkeypatch.delenv(key, raising=False)
    Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]
