"""Tests for demonstration-captured routines (alpha.routines)."""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `--noconftest` runs: make the harness importable without the project
# conftest (idempotent when the real conftest also adds this path).
_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.routines import (
    Routine,
    RoutineRecorder,
    RoutineStore,
    RoutineStoreUnreadable,
    RoutineValidationError,
    capture_trace,
    looks_like_input,
    replay,
)


def test_looks_like_input_detects_user_specific_values():
    assert looks_like_input("alice@example.com")
    assert looks_like_input("https://example.com/a")
    assert looks_like_input("C:/Users/me/file.txt")
    assert looks_like_input("/home/me/file.txt")
    assert not looks_like_input("just some text")
    assert not looks_like_input(42)


def test_record_finalize_and_replay_roundtrip():
    rec = RoutineRecorder("daily_digest", "Fetch and email a digest")
    rec.record_step("search_email", {"query": "invoice", "since": "2026-09-01"})
    rec.record_step("send_email", {"to": "alice@example.com", "subject": "Digest", "body": "Hi {{name}}"})
    routine = rec.finalize()

    # The email recipient was auto-detected as a required parameter.
    assert "to" in routine.required_parameters

    calls = replay(routine, {"to": "bob@example.com", "name": "Bob"})
    assert calls[0] == {"tool": "search_email", "args": {"query": "invoice", "since": "2026-09-01"}}
    assert calls[1]["args"]["to"] == "bob@example.com"
    assert calls[1]["args"]["body"] == "Hi Bob"


def test_replay_missing_required_parameter_raises():
    rec = RoutineRecorder("ping")
    rec.record_step("send_email", {"to": "alice@example.com"})
    routine = rec.finalize()
    with pytest.raises(RoutineValidationError):
        replay(routine, {})


def test_optional_parameter_uses_default():
    rec = RoutineRecorder("greet")
    rec.record_step("say", {"text": "hello {{who}}"})
    rec.set_parameter("who", "world")
    routine = rec.finalize()
    assert routine.required_parameters == []
    assert replay(routine)[0]["args"]["text"] == "hello world"


def test_capture_trace_builds_routine():
    routine = capture_trace("t", [{"tool": "a", "args": {}}, {"tool": "b", "args": {"path": "/tmp/x"}}])
    assert [s.tool for s in routine.steps] == ["a", "b"]
    assert "path" in routine.required_parameters


def test_store_persists_and_reloads(tmp_path):
    store = RoutineStore(tmp_path / "routines.json")
    rec = RoutineRecorder("task_one")
    rec.record_step("do", {"n": 1})
    store.save(rec.finalize())

    reloaded = RoutineStore(tmp_path / "routines.json")
    got = reloaded.get("task_one")
    assert got is not None
    assert got.steps[0].tool == "do"

    # Re-saving bumps the version.
    store.save(got)
    assert store.get("task_one").version == 2


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "routines.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RoutineStoreUnreadable):
        RoutineStore(path)


def test_validate_step_rejects_unknown_parameterize_key():
    with pytest.raises(RoutineValidationError):
        from alpha.routines.models import RoutineStep

        RoutineStep(tool="x", args={"a": 1}, parameterize=["b"])
