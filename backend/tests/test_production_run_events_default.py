"""Fresh installs must keep run history across Gateway restarts."""

from pathlib import Path

import yaml


def test_config_example_uses_persistent_run_event_storage() -> None:
    """The shipped first-run config must not silently discard run history."""
    example = Path(__file__).resolve().parents[2] / "config.example.yaml"
    config = yaml.safe_load(example.read_text(encoding="utf-8"))

    assert config["database"]["backend"] in {"sqlite", "postgres"}
    assert config["run_events"]["backend"] == "db"
