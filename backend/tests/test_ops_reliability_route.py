"""The real-work progress surface must never dress a failed read as a clean one.

`GET /api/ops/reliability` is where an operator goes to answer "is the agent
actually doing the work, and what broke?". That question has exactly one
dangerous wrong answer: rendering an unreadable or absent ledger as "0
workloads, all good", which is indistinguishable from a healthy matrix that has
not run yet. These tests pin the distinction, plus the honesty rules the frontend
then renders verbatim.

The ledger is written by `scripts/reliability/alpha_workload_monitor.py`, an
external process. That makes every read here a read of a file that may be
missing, half-written, or from an older/newer monitor — three states the
surface has to name rather than absorb.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.gateway.routers import ops as ops_module
from app.gateway.routers.ops import (
    RELIABILITY_MAX_ROWS,
    _load_reliability_ledger,
    _reliability_ledger_path,
    _reliability_rows,
    ops_reliability,
)


def _write_ledger(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "workload-ledger.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _row(key: str, verdict: str, checked_at: str = "2026-10-04T00:00:00Z", **extra) -> dict:
    return {"workload": key, "title": f"workload {key}", "kind": "coding", "verdict": verdict, "detail": "d", "checked_at": checked_at, **extra}


@pytest.fixture
def ledger_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the route at a temporary ledger for the duration of a test."""

    def _use(payload: dict | None, *, raw: str | None = None) -> Path:
        path = tmp_path / "workload-ledger.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        elif payload is not None:
            path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv("ALPHA_RELIABILITY_LEDGER", str(path))
        return path

    return _use


class TestLedgerResolution:
    def test_the_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "elsewhere.json"
        monkeypatch.setenv("ALPHA_RELIABILITY_LEDGER", str(target))
        assert _reliability_ledger_path() == target

    def test_a_blank_override_falls_back_to_runtime_home(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("ALPHA_RELIABILITY_LEDGER", "   ")
        resolved = _reliability_ledger_path()
        assert resolved.name == "workload-ledger.json"
        assert resolved.parent.name == "reliability"

    def test_the_default_lives_in_runtime_state_not_the_repo(self, monkeypatch: pytest.MonkeyPatch):
        """A ledger in the working tree would be an untracked file and a trap."""
        monkeypatch.delenv("ALPHA_RELIABILITY_LEDGER", raising=False)
        resolved = _reliability_ledger_path()
        assert "runtime" in str(resolved).lower() or ".alpha" in str(resolved).lower()


class TestFailedReadsAreNamed:
    def test_a_missing_ledger_is_not_an_empty_matrix(self, tmp_path: Path):
        payload, reason, reported = _load_reliability_ledger(tmp_path / "absent.json")
        assert reported is False
        assert reason == "ledger_not_found"
        assert payload == {}

    def test_corrupt_json_is_a_failed_read(self, tmp_path: Path):
        path = tmp_path / "broken.json"
        path.write_text("{not json at all", encoding="utf-8")
        payload, reason, reported = _load_reliability_ledger(path)
        assert reported is False
        assert reason == "ledger_unreadable"

    def test_a_json_array_is_not_a_ledger(self, tmp_path: Path):
        path = tmp_path / "array.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        _, reason, reported = _load_reliability_ledger(path)
        assert reported is False
        assert reason == "ledger_unreadable"

    def test_an_empty_file_is_a_failed_read_not_an_empty_matrix(self, tmp_path: Path):
        """`{}` and `` are indistinguishable from a monitor that never ran."""
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        _, reason, reported = _load_reliability_ledger(path)
        assert reported is False
        assert reason == "ledger_unreadable"


@pytest.mark.anyio
class TestSurfaceHonesty:
    async def test_absent_ledger_reports_nulls_never_zero(self, ledger_env):
        ledger_env(None)
        response = await ops_reliability()
        assert response.reported is False
        assert response.reason == "ledger_not_found"
        # The distinction this whole surface exists to keep: "I looked and
        # found nothing" (0) versus "I could not look" (null).
        assert response.total is None
        assert response.passed is None
        assert response.broken is None
        assert response.workloads == []

    async def test_an_empty_but_valid_ledger_is_still_not_reported(self, ledger_env):
        """A ledger with no outcomes means no wave has run — not a pass."""
        ledger_env({"outcomes": {}})
        response = await ops_reliability()
        assert response.reported is True
        assert response.total == 0
        assert response.passed == 0
        assert response.workloads == []

    async def test_counts_come_from_the_rows(self, ledger_env):
        ledger_env({"outcomes": {"A": _row("A", "PASS"), "B": _row("B", "FAIL"), "C": _row("C", "UNVERIFIED")}})
        response = await ops_reliability()
        assert response.reported is True
        assert response.total == 3
        assert response.passed == 1
        assert response.broken == 2, "FAIL and UNVERIFIED are both not-passed and neither is a pass"
        assert response.counts == {"PASS": 1, "FAIL": 1, "UNVERIFIED": 1}

    async def test_an_unknown_verdict_word_is_preserved_verbatim(self, ledger_env):
        """A newer monitor's verdict must reach the operator, not be snapped."""
        ledger_env({"outcomes": {"Z": _row("Z", "DEGRADED_BUT_RUNNING")}})
        response = await ops_reliability()
        assert [row.verdict for row in response.workloads] == ["DEGRADED_BUT_RUNNING"]
        # Not in the broken set, so it is neither counted as passed nor as broken.
        assert response.passed == 0
        assert response.broken == 0

    async def test_malformed_rows_are_skipped_without_failing_the_surface(self, ledger_env):
        ledger_env(
            {
                "outcomes": {
                    "A": _row("A", "PASS"),
                    "B": "not even a dict",
                    "C": {"title": "no verdict"},
                    "D": {"verdict": ""},
                }
            }
        )
        response = await ops_reliability()
        assert response.reported is True
        assert [row.workload for row in response.workloads] == ["A"]

    async def test_absent_optional_fields_stay_null(self, ledger_env):
        ledger_env({"outcomes": {"A": {"workload": "A", "verdict": "PASS"}}})
        row = (await ops_reliability()).workloads[0]
        assert row.title is None
        assert row.run_id is None
        assert row.thread_id is None
        assert row.elapsed_s is None
        assert row.model is None
        assert row.server_error is None

    async def test_a_wrongly_typed_field_is_null_not_coerced(self, ledger_env):
        ledger_env({"outcomes": {"A": _row("A", "PASS", elapsed_s="fast", run_id=7, title=3)}})
        row = (await ops_reliability()).workloads[0]
        assert row.elapsed_s is None
        assert row.run_id is None
        assert row.title is None

    async def test_rows_are_ordered_most_recently_checked_first(self, ledger_env):
        ledger_env(
            {
                "outcomes": {
                    "A": _row("A", "PASS", checked_at="2026-10-01T00:00:00Z"),
                    "B": _row("B", "FAIL", checked_at="2026-10-04T00:00:00Z"),
                    "C": _row("C", "PASS", checked_at="2026-10-02T00:00:00Z"),
                }
            }
        )
        response = await ops_reliability()
        assert [row.workload for row in response.workloads] == ["B", "C", "A"]

    async def test_a_row_with_no_timestamp_sorts_last_rather_than_first(self, ledger_env):
        ledger_env(
            {
                "outcomes": {
                    # Explicitly absent, not defaulted by the helper: an unknown
                    # time must not win the "most recent first" ordering.
                    "A": {"workload": "A", "verdict": "PASS"},
                    "B": _row("B", "FAIL", checked_at="2026-10-04T00:00:00Z"),
                }
            }
        )
        response = await ops_reliability()
        assert response.workloads[0].workload == "B"

    async def test_an_oversized_ledger_is_bounded_and_the_bound_is_disclosed(self, ledger_env):
        ledger_env({"outcomes": {f"W{index}": _row(f"W{index}", "PASS") for index in range(RELIABILITY_MAX_ROWS + 12)}})
        response = await ops_reliability()
        assert response.truncated is True
        assert response.returned == RELIABILITY_MAX_ROWS
        assert response.total == RELIABILITY_MAX_ROWS + 12, "the real count survives so the UI can say 'showing N of M'"
        assert len(response.workloads) == RELIABILITY_MAX_ROWS

    async def test_the_ledger_path_is_reported_so_a_missing_file_is_diagnosable(self, ledger_env):
        path = ledger_env(None)
        response = await ops_reliability()
        assert response.ledger_path == str(path)

    async def test_a_row_without_an_explicit_key_falls_back_to_the_ledger_key(self):
        rows = _reliability_rows({"outcomes": {"from-key": {"verdict": "PASS"}}})
        assert rows[0].workload == "from-key"

    async def test_a_ledger_with_no_outcomes_key_yields_no_rows_and_reports_read(self, ledger_env):
        ledger_env({"something": "else"})
        response = await ops_reliability()
        assert response.reported is True
        assert response.workloads == []
        assert response.total == 0


class TestRouteRegistration:
    def test_the_route_is_declared_on_the_ops_router_with_this_exact_path(self):
        paths = {route.path for route in ops_module.router.routes}
        assert "/api/ops/reliability" in paths

    def test_it_is_a_get_and_not_a_mutation(self):
        methods = {route.path: route.methods for route in ops_module.router.routes if route.path == "/api/ops/reliability"}
        assert methods["/api/ops/reliability"] == {"GET"}

    def test_the_broken_verdict_set_matches_the_monitor_s_own_words(self):
        """A typo here would silently reclassify a failure as a pass."""
        assert ops_module._BROKEN_VERDICTS == {"FAIL", "ERROR", "UNVERIFIED"}
