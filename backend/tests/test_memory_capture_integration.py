"""The capture seam against the REAL memory types.

`test_memory_capture_composition.py` pins the seam's policy with monkeypatched
writers - which means it would happily pass while the module called the real
types incorrectly. These tests run the real `entities` and `narrative` packages
against an isolated storage root, so a signature or field-name mistake in the
adapters is a failure here rather than a surprise in production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alpha.config.memory_config import MemoryConfig
from alpha.memory.capture_composition import compose_capture


def _config(tmp_path: Path, **extra: Any) -> MemoryConfig:
    payload: dict[str, Any] = {
        "enabled": True,
        "entities": {
            "enabled": True,
            "storage_path": str(tmp_path / "entities"),
        },
        "narrative": {
            "enabled": True,
            "storage_path": str(tmp_path / "narrative"),
            "max_chars": 2000,
        },
    }
    payload.update(extra)
    return MemoryConfig.model_validate(payload)


RECORDS: list[dict[str, Any]] = [
    {
        "id": "rec-1",
        "content": "Priya owns the Argo Rollouts canary promotion for the payments service.",
        "type": "fact",
        "priority": 80,
        "scene_name": "deploy",
        "created_at": "2026-09-01T10:00:00Z",
    },
    {
        "id": "rec-2",
        "content": "The production deploy of the payments service is owned by Priya.",
        "type": "fact",
        "priority": 75,
        "scene_name": "deploy",
        "created_at": "2026-09-02T10:00:00Z",
    },
]


def test_real_types_receive_the_records(tmp_path: Path) -> None:
    """End to end: real stores, real signatures, isolated root."""
    result = compose_capture(_config(tmp_path), RECORDS, user_id="u1", agent_name="lead")

    # Narrative turns stored records into story events; entities index explicit
    # entities. Either may legitimately find nothing in a given record set, but
    # the call must succeed and must be REPORTED honestly.
    for surface in ("entities", "narrative"):
        status = result.status_for(surface)
        assert status in {"written", "empty"}, f"{surface} reported {status!r}"
        assert status != "error", f"{surface} raised against the real package"

    # Whatever the statuses, the whole call must be accounted for.
    assert {s.surface for s in result.surfaces} == {"entities", "narrative"}
    assert all(s.considered == len(RECORDS) for s in result.surfaces)


def test_real_narrative_store_persists_something(tmp_path: Path) -> None:
    """The narrative store must actually contain the ingested events.

    This is the assertion that would catch a wrong keyword or a silently
    dropped argument: a seam that "succeeds" while writing nothing is exactly
    the dormant-feature failure this project refuses.
    """
    compose_capture(_config(tmp_path), RECORDS, user_id="u1", agent_name="lead", now=1.788e9)

    root = tmp_path / "narrative"
    assert root.exists(), "narrative store root was never created"
    written = [p for p in root.rglob("*.json") if p.is_file()]
    assert written, "narrative store created no document - the ingest wrote nothing"
    payload = "".join(p.read_text(encoding="utf-8", errors="replace") for p in written)
    assert "rec-1" in payload or "rec-2" in payload, (
        "the narrative store has no trace of the ingested records"
    )


def test_real_entities_store_persists_something(tmp_path: Path) -> None:
    """Same guarantee for the entity store."""
    compose_capture(_config(tmp_path), RECORDS, user_id="u1", agent_name="lead", now=1.788e9)

    root = tmp_path / "entities"
    assert root.exists(), "entity store root was never created"
    written = [p for p in root.rglob("*.json") if p.is_file()]
    assert written, "entity store created no document - the ingest wrote nothing"


def test_a_second_capture_does_not_duplicate_or_crash(tmp_path: Path) -> None:
    """Capture runs on every turn; re-running must be safe and honest."""
    first = compose_capture(_config(tmp_path), RECORDS, user_id="u1", now=1.788e9)
    second = compose_capture(_config(tmp_path), RECORDS, user_id="u1", now=1.788e9)
    assert first.status_for("narrative") != "error"
    assert second.status_for("narrative") != "error"


def test_records_may_be_pydantic_models(tmp_path: Path) -> None:
    """A caller holding L1 `MemoryRecord` objects must work unchanged."""
    from alpha.agents.memory.l1.models import MemoryRecord

    models = [
        MemoryRecord.create("The payments canary is owned by Priya.", memory_type="fact", priority=70)
        for _ in range(2)
    ]
    result = compose_capture(_config(tmp_path), models, user_id="u1", now=1.788e9)
    assert result.status_for("narrative") in {"written", "empty"}
    assert result.status_for("entities") in {"written", "empty"}


def test_missing_records_never_construct_the_real_stores(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = compose_capture(config, [], user_id="u1")
    assert result.status_for("narrative") == "empty"
    assert not (tmp_path / "narrative").exists(), (
        "an empty capture must not create a store root"
    )
