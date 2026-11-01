import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_cookiecutter_key_documented():
    readme = (ROOT / "README.md").read_text()
    keys = json.loads((ROOT / "cookiecutter.json").read_text())
    missing = [key for key in keys if f"`{key}`" not in readme]
    assert missing == []
