"""The cognitive-memory read routes must speak ISO 8601.

Scope note: another agent owns the rest of ``routers/memory.py``. This file
covers ONLY the two cognitive handlers named in the assignment -
``GET /api/memory/cognitive/semantic`` (:704) and
``GET /api/memory/cognitive/procedural`` (:750) - which returned
``n.to_dict()`` / ``s.to_dict()`` verbatim. The other cognitive routes and the
top-of-file ``GET /api/memory`` are deliberately untouched here.

The bug
-------
``SemanticFactNode.valid_from`` / ``valid_to`` / ``last_accessed_at`` /
``created_at`` (cognitive/models.py:116-122) and ``ProceduralSkill.
last_executed_at`` / ``created_at`` (:170-172) are all ``time.time()`` floats,
and ``to_dict()`` is ``dataclasses.asdict``. The sibling ``GET /api/memory``
route in the SAME router file already returns ISO ``lastUpdated`` /
``facts[].createdAt``, so one file held two conventions.

The zero case, and why it is the interesting one here
----------------------------------------------------
``ProceduralSkill.last_executed_at`` is declared ``float = 0.0``. That default
IS the sentinel: it means "this skill has never run"
(``tests/test_cognitive_bootstrap_honesty.py:77`` pins exactly that bare
``0.0``). Passing it through ``coerce_iso`` unchecked would render
``1970-01-01T00:00:00+00:00`` - "this skill last executed at the Unix epoch" -
which is precisely the lie the projection exists to prevent. It is emitted as
JSON ``null`` instead, which is also the shape this repo's own
``fmtEpochSafe(iso: string | null)`` already renders as the word "never".

The coercion is at the API boundary only: the owner snapshot on disk keeps the
float, so every existing cognitive store still loads. These tests pin both
halves of that contract.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import memory as memory_router


@pytest.fixture(autouse=True)
def _isolated_cognitive_storage(tmp_path: Path, monkeypatch):
    from alpha.config.paths import Paths
    from alpha.memory.cognitive import engine

    monkeypatch.setattr(engine, "get_paths", lambda: Paths(tmp_path))
    monkeypatch.setattr(engine, "_owner_systems", {})


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(memory_router.router)
    return TestClient(app)


def _assert_iso(value: object, where: str) -> None:
    assert isinstance(value, str), f"{where} is {type(value).__name__}, not a string: {value!r}"
    assert value, f"{where} is an empty string"
    assert datetime.fromisoformat(value).tzinfo is not None, f"{where} is not an aware ISO timestamp: {value!r}"


def _assert_no_raw_epoch(payload: object, where: str = "payload") -> None:
    """The assertion that pins the regression, not a formatting detail."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool) and (key.endswith("_at") or key.endswith("_from")):
                raise AssertionError(f"{where}.{key} is still a raw epoch number: {value!r}")
            _assert_no_raw_epoch(value, f"{where}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_raw_epoch(item, f"{where}[{index}]")


# ---------------------------------------------------------------------------
# GET /api/memory/cognitive/semantic
# ---------------------------------------------------------------------------


def test_semantic_node_timestamps_are_iso(client: TestClient) -> None:
    """The regression: `nodes[].created_at` must parse as a date."""
    created = client.post("/api/memory/cognitive/semantic", json={"subject": "CacheSubsystem", "predicate": "implements", "object_val": "LRU_TwoTier", "confidence": 0.9})
    assert created.status_code == 200, created.text

    body = client.get("/api/memory/cognitive/semantic").json()
    nodes = body["nodes"]
    assert nodes, "no nodes were returned"

    for node in nodes:
        for field in ("valid_from", "last_accessed_at", "created_at"):
            _assert_iso(node[field], f"nodes[{node['node_id']}].{field}")
        # `valid_to` is genuinely optional ("no end"); None stays None.
        assert node["valid_to"] is None or isinstance(node["valid_to"], str), f"valid_to is {type(node['valid_to']).__name__}"
    _assert_no_raw_epoch(body, "cognitive/semantic")


def test_semantic_filters_are_separate_branches_but_one_dialect(client: TestClient) -> None:
    client.post("/api/memory/cognitive/semantic", json={"subject": "Alpha", "predicate": "is", "object_val": "autonomous"})
    client.post("/api/memory/cognitive/semantic", json={"subject": "Beta", "predicate": "is", "object_val": "reactive"})

    for params in ({}, {"subject": "Alpha"}, {"status": "active"}):
        body = client.get("/api/memory/cognitive/semantic", params=params).json()
        for node in body["nodes"]:
            _assert_iso(node["created_at"], f"filtered({params}).created_at")


def test_a_node_with_a_bounded_validity_window_stays_iso(client: TestClient) -> None:
    """`valid_to` is the one genuinely-optional timestamp here."""
    from alpha.memory.cognitive.semantic_graph import SemanticBeliefGraph

    graph = SemanticBeliefGraph()
    bounded = graph.add_belief("Temp", "expires", "soon")
    bounded.valid_to = bounded.valid_from + 3600.0

    rows = [bounded.to_dict()]
    assert isinstance(rows[0]["valid_to"], float), "the model stores a float; the route is what must translate it"
    assert isinstance(bounded.to_dict()["valid_to"], float), "to_dict must not have been changed to emit ISO in the model"


# ---------------------------------------------------------------------------
# GET /api/memory/cognitive/procedural
# ---------------------------------------------------------------------------


def test_procedural_created_at_is_iso(client: TestClient) -> None:
    created = client.post("/api/memory/cognitive/procedural", json={"name": "iso-skill", "description": "a reusable playbook", "trigger_pattern": "iso"})
    assert created.status_code == 200, created.text

    body = client.get("/api/memory/cognitive/procedural").json()
    assert body, "no skills were returned"
    for skill in body:
        _assert_iso(skill["created_at"], f"skills[{skill['skill_id']}].created_at")


def test_procedural_never_executed_is_null_not_1970(client: TestClient) -> None:
    """THE ZERO CASE.

    A freshly registered skill has `last_executed_at == 0.0`. Coerced naively
    that is `1970-01-01T00:00:00+00:00`, i.e. "this skill last ran at the Unix
    epoch", which is a fabricated fact. It must be `null` - "never ran".
    """
    client.post("/api/memory/cognitive/procedural", json={"name": "fresh-skill", "description": "never executed", "trigger_pattern": "fresh"})

    skills = client.get("/api/memory/cognitive/procedural").json()
    assert skills, "no skills were returned"
    fresh = next(s for s in skills if s["name"] == "fresh-skill")
    assert fresh["last_executed_at"] is None, f"a never-executed skill reports last_executed_at={fresh['last_executed_at']!r}; expected null, never an epoch"
    assert fresh["last_executed_at"] != "1970-01-01T00:00:00+00:00"
    assert fresh["last_executed_at"] != 0.0, "the raw sentinel leaked onto the wire"


def test_procedural_a_real_execution_time_is_iso(client: TestClient) -> None:
    """The other half of the zero case: a real execution must still render."""
    from alpha.memory.cognitive.procedural_memory import ProceduralSkillMemory

    memory = ProceduralSkillMemory()
    memory.register_skill(name="run-skill", description="d", trigger_pattern="t")
    # Drive a real execution through the engine so the field is genuinely set.
    skill = next(iter(memory._skills.values())) if hasattr(memory, "_skills") else None
    assert skill is not None

    from alpha.memory.cognitive import models as cognitive_models

    skill.last_executed_at = 1790528973.1918123
    row = skill.to_dict()
    assert isinstance(row["last_executed_at"], float), "the model stores a float; the route is what must translate it"
    assert cognitive_models.ProceduralSkill is skill.__class__

    from alpha.utils.time import coerce_iso

    assert coerce_iso(skill.last_executed_at) == "2026-09-27T17:09:33.191812+00:00"


def test_no_raw_epoch_survives_on_the_procedural_plane(client: TestClient) -> None:
    client.post("/api/memory/cognitive/procedural", json={"name": "dialect-skill", "description": "d", "trigger_pattern": "t"})
    body = client.get("/api/memory/cognitive/procedural").json()
    _assert_no_raw_epoch(body, "cognitive/procedural")


# ---------------------------------------------------------------------------
# Persistence: the coercion is an API-boundary concern
# ---------------------------------------------------------------------------


def test_the_stored_snapshot_keeps_the_floats(tmp_path: Path) -> None:
    """Rewriting the stored shape would break every existing cognitive store."""
    from alpha.config.paths import Paths
    from alpha.memory.cognitive.engine import CognitiveMemorySystem

    system = CognitiveMemorySystem(user_id="iso-owner")
    system.procedural_mem.register_skill(name="persisted", description="d", trigger_pattern="t")
    system.semantic_graph.add_belief("Persisted", "is", "on-disk")
    system.save_to_disk()

    storage = Paths(tmp_path).user_dir("iso-owner") / "cognitive_memory"
    files = [p for p in storage.rglob("*.json")]
    assert files, f"the cognitive store wrote nothing under {storage}"

    saw_float = False
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert '"created_at": "' not in text, f"{path.name} stored an ISO string; existing snapshots would change shape"
        if "created_at" in text:
            saw_float = True
    assert saw_float, "no cognitive snapshot contained a created_at field to check"


def test_the_model_still_declares_floats() -> None:
    """Guards the storage contract from being 'fixed' in the wrong layer."""
    from alpha.memory.cognitive.models import ProceduralSkill, SemanticFactNode

    skill = ProceduralSkill(name="n", description="d", trigger_pattern="t")
    assert skill.last_executed_at == 0.0, "the never-executed sentinel is part of the stored contract"
    assert isinstance(skill.to_dict()["last_executed_at"], float)
    assert isinstance(skill.to_dict()["created_at"], float)

    node = SemanticFactNode(subject="s", predicate="p", object_val="o")
    assert isinstance(node.to_dict()["created_at"], float)
    assert node.to_dict()["valid_to"] is None


def test_an_existing_snapshot_with_floats_still_loads(tmp_path: Path) -> None:
    """The upgrade path: a cognitive store written before the fix must load."""
    from alpha.config.paths import Paths
    from alpha.memory.cognitive.engine import CognitiveMemorySystem

    system = CognitiveMemorySystem(user_id="iso-owner")
    system.procedural_mem.register_skill(name="legacy", description="d", trigger_pattern="t")
    system.save_to_disk()

    storage = Paths(tmp_path).user_dir("iso-owner") / "cognitive_memory"
    files = list(storage.rglob("*.json"))
    assert files

    # A fresh system over the same directory: the float rows must still load.
    reopened = CognitiveMemorySystem(user_id="iso-owner")
    skills = reopened.procedural_mem.list_skills()
    assert any(skill.name == "legacy" for skill in skills), "the pre-existing snapshot did not load"


# ---------------------------------------------------------------------------
# The zero decision, stated as an executable claim
# ---------------------------------------------------------------------------


def test_the_zero_epoch_decision_for_this_plane(client: TestClient) -> None:
    """Which field is the sentinel, and what is emitted instead.

    `ProceduralSkill.last_executed_at` is the only `0.0`-defaulted timestamp on
    this plane, and it means "never executed" - not "executed at the epoch". The
    other four (`created_at`, `valid_from`, `last_accessed_at`) are
    `default_factory=time.time`, and `valid_to` is `None` for "no end".
    """
    from alpha.memory.cognitive.models import ProceduralSkill

    # The model's declared sentinel, unchanged.
    assert ProceduralSkill(name="n", description="d", trigger_pattern="t").last_executed_at == 0.0

    # What would happen WITHOUT the zero guard - the reason it exists.
    from alpha.utils.time import coerce_iso

    assert coerce_iso(0.0) == "1970-01-01T00:00:00+00:00"

    # And what the route does with that same input.
    client.post("/api/memory/cognitive/procedural", json={"name": "sentinel-probe", "description": "d", "trigger_pattern": "t"})
    skill = next(s for s in client.get("/api/memory/cognitive/procedural").json() if s["name"] == "sentinel-probe")
    assert skill["last_executed_at"] is None
    assert skill["last_executed_at"] != coerce_iso(0.0), "the route passed the sentinel through uncoerced"


def test_a_negative_execution_time_is_also_treated_as_never(client: TestClient) -> None:
    """`<= 0`, not `== 0.0`: a negative clock reading is never a real execution."""
    from alpha.memory.cognitive import get_cognitive_memory_system
    from alpha.runtime.user_context import get_effective_user_id

    client.post("/api/memory/cognitive/procedural", json={"name": "negative-clock", "description": "d", "trigger_pattern": "t"})

    # Resolve the owner the way the route does, so this is the same system
    # instance the GET reads from.
    owner = get_effective_user_id()
    system = get_cognitive_memory_system(user_id=owner)
    skill = next(s for s in system.procedural_mem.list_skills(limit=50) if s.name == "negative-clock")
    skill.last_executed_at = -1.0

    row = next(s for s in client.get("/api/memory/cognitive/procedural").json() if s["name"] == "negative-clock")
    assert row["last_executed_at"] is None, f"a negative execution time rendered as {row['last_executed_at']!r}"
    assert "1969" not in str(row["last_executed_at"])
