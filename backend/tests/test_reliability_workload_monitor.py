"""The reliability monitor must be trustworthy before its verdicts mean anything.

A monitor that reports PASS from a broken client is worse than no monitor: it
converts "I did not check" into "everything is fine". These tests pin the two
failure modes this harness has actually exhibited, plus the discrimination the
whole matrix rests on.

The first live wave of `scripts/reliability/alpha_workload_monitor.py` failed all
four workloads with "could not create a thread" while the Gateway was healthy
and answering. The cause was in the harness: its single base string omitted the
`/api` prefix, so it POSTed to `/threads`, and `/health` — a *root* route — kept
answering 200, so the preflight passed and the mistake only surfaced as four
identical opaque failures. The regression below pins the two-base split and the
reason-carrying thread creation so neither half can come back.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts/reliability/alpha_workload_monitor.py"


def _load_monitor():
    """Import the script by path; it is a repo script, not a package module."""
    spec = importlib.util.spec_from_file_location("alpha_workload_monitor", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


monitor = _load_monitor()


class TestRouteConstruction:
    """The exact bug: one base string cannot serve both route families."""

    def test_api_routes_are_addressed_under_api(self):
        gateway = monitor.Gateway("http://127.0.0.1:8001")
        assert gateway.api == "http://127.0.0.1:8001/api"

    def test_liveness_is_a_root_route_not_an_api_route(self):
        """`/health` is at the root, so health cannot be asked for under `/api`.

        If liveness moved under `/api`, this test is what should be updated to
        say so — silently keeping the root path is how the preflight kept
        passing over a broken API base.
        """
        gateway = monitor.Gateway("http://127.0.0.1:8001")
        assert not gateway.api.endswith("/api/health")
        assert gateway.origin == "http://127.0.0.1:8001"

    def test_a_trailing_slash_in_the_origin_cannot_double_up(self):
        gateway = monitor.Gateway("http://127.0.0.1:8001/")
        assert gateway.api == "http://127.0.0.1:8001/api"
        assert "//api" not in gateway.api


class TestThreadCreationReportsWhy:
    """A monitor that says "failed" without the server's words is its own defect."""

    def test_a_refusal_returns_the_servers_reason(self):
        class Refusing(monitor.Gateway):
            def call(self, method: str, path: str, payload: Any = None, *, timeout: int | None = None):
                return 403, {"detail": "Not allowed - admin rights required"}

        thread_id, reason = Refusing("http://127.0.0.1:8001").create_thread({})
        assert thread_id is None
        assert "403" in reason
        assert "admin rights" in reason

    def test_a_200_without_a_thread_id_is_a_failure_not_a_thread(self):
        class ThreadIdLess(monitor.Gateway):
            def call(self, method: str, path: str, payload: Any = None, *, timeout: int | None = None):
                return 200, {"status": "idle"}

        thread_id, reason = ThreadIdLess("http://127.0.0.1:8001").create_thread({})
        assert thread_id is None
        assert "no thread_id" in reason

    def test_a_real_thread_is_returned_with_its_id(self):
        class Creating(monitor.Gateway):
            def call(self, method: str, path: str, payload: Any = None, *, timeout: int | None = None):
                assert path == "/threads", "thread creation must address the /api base, not the root"
                return 200, {"thread_id": "t-1"}

        thread_id, reason = Creating("http://127.0.0.1:8001").create_thread({})
        assert thread_id == "t-1"
        assert reason == "created"


class TestServerErrorExtraction:
    def test_detail_string_is_preferred_and_bounded(self):
        text = "x" * (monitor.MAX_ERROR_CHARS * 2)
        assert monitor._server_error({"detail": text}) == text[: monitor.MAX_ERROR_CHARS]

    def test_a_structured_detail_is_reduced_to_its_message(self):
        payload = {"detail": {"code": "FORBIDDEN", "message": "needs admin rights"}}
        assert monitor._server_error(payload) == "needs admin rights"

    def test_a_body_with_no_reason_is_not_invented(self):
        assert monitor._server_error({"run_id": "r1", "status": "error"}) is None
        assert monitor._server_error(None) is None


class TestInformationalDiscriminator:
    """The control case: an answer must be distinguishable from performed work.

    This is what stops the matrix from reporting a run that only *talked* about
    work as a successful execution.
    """

    def test_a_plain_answer_passes_as_informational(self):
        class G:
            def messages(self, thread_id: str, limit: int = 6):
                return [{"type": "ai", "content": "The campaign tracks open defects in KNOWN_ISSUES.md."}]

        ok, detail = monitor._evidence_informational("t1", G())
        assert ok is True
        assert "without performing work" in detail

    def test_a_completion_claim_fails_even_though_the_run_succeeded(self):
        class G:
            def messages(self, thread_id: str, limit: int = 6):
                return [{"type": "ai", "content": "I have fixed the bug and the tests now pass."}]

        ok, detail = monitor._evidence_informational("t1", G())
        assert ok is False
        assert "completion claim" in detail

    def test_no_answer_at_all_fails(self):
        class G:
            def messages(self, thread_id: str, limit: int = 6):
                return []

        ok, _ = monitor._evidence_informational("t1", G())
        assert ok is False


class TestEvidenceRequiresRealState:
    def test_a_run_with_no_run_record_cannot_be_adjudicated(self):
        class G:
            def runs(self, thread_id: str, limit: int = 5):
                return []

        ok, detail = monitor._evidence_no_run_error("t1", G())
        assert ok is False
        assert "no run record" in detail

    def test_a_non_success_terminal_status_fails_with_the_status_named(self):
        class G:
            def runs(self, thread_id: str, limit: int = 5):
                return [{"run_id": "r1", "status": "error"}]

        ok, detail = monitor._evidence_no_run_error("t1", G())
        assert ok is False
        assert "'error'" in detail

    def test_swarm_evidence_requires_a_real_delegation_not_a_claim(self):
        class Prose:
            def messages(self, thread_id: str, limit: int = 6):
                return [{"type": "ai", "content": "I delegated the audits to three subagents. " * 20}]

        ok, detail = monitor._evidence_swarm("t1", Prose())
        assert ok is False
        assert "no `task` delegation" in detail

        class Delegated:
            def messages(self, thread_id: str, limit: int = 6):
                return [
                    {"type": "ai", "content": "audit complete. " * 60},
                    {"type": "ai", "content": "", "tool_calls": [{"name": "task", "args": {}}]},
                ]

        ok, detail = monitor._evidence_swarm("t1", Delegated())
        assert ok is True
        assert "task" in detail


class TestMatrixIntegrity:
    """A workload with no evidence check would silently become decorative."""

    def test_every_workload_declares_evidence_a_prompt_and_a_key(self):
        for workload in monitor.build_workloads():
            assert workload.key.strip(), workload.title
            assert workload.prompt.strip(), f"workload {workload.key} has no prompt"
            assert callable(workload.evidence), f"workload {workload.key} has no evidence check"
            assert workload.timeout_s > 0, f"workload {workload.key} has no time budget"

    def test_workload_keys_are_unique(self):
        keys = [w.key for w in monitor.build_workloads()]
        assert len(keys) == len(set(keys)), "a duplicated key would make --workload ambiguous"

    def test_the_informational_case_is_marked_as_using_no_interaction(self):
        control = next(w for w in monitor.build_workloads() if w.key == "0")
        assert control.non_interactive is False, "the control case must not carry the proceed-without-asking suffix"


class TestFailureClassification:
    """A dependency outage must not be recorded as a broken workload.

    The first live wave hit this for real: the only free model provider was
    inside its own cooldown, and three of four workloads reported failure. Left
    unclassified the ledger blames the workloads, and the "fix" an operator
    attempts is to the agent instead of to the thing that actually broke.
    """

    def test_a_provider_cooldown_is_attributed_to_the_provider(self):
        assert monitor.classify_server_error("all 1 free provider attempt(s) failed (1 now cooling down)") == "model provider"

    def test_an_unavailable_model_is_attributed_to_the_provider(self):
        assert monitor.classify_server_error("model 'x' is not offered by any reachable free provider") == "model provider"

    def test_a_rate_limit_is_attributed_to_the_provider(self):
        assert monitor.classify_server_error('ProviderError: ovhcloud: {"message":"API rate limit exceeded"}') == "model provider"

    def test_a_genuine_workload_failure_is_not_reclassified(self):
        # The dishonest direction to get wrong: a real defect filed as an
        # infrastructure outage disappears and is never fixed.
        for error in [
            "the run produced no assistant message",
            "no project carries this attempt's token",
            "terminal status is 'error'",
            "evidence check raised KeyError: token",
        ]:
            assert monitor.classify_server_error(error) is None, error

    def test_no_error_is_not_a_dependency(self):
        assert monitor.classify_server_error(None) is None
        assert monitor.classify_server_error("") is None


class TestBudgetExhaustionIsNotAnOutage:
    """A client that stopped waiting must not claim the Gateway died.

    The first live wave reported "the Gateway was unreachable for the whole run
    window" for a run that was still working. That sends an operator to restart
    a healthy service, so the honest verdict is UNVERIFIED with the outcome
    unknown.
    """

    def _outcome(self, transport_error: str):
        class G:
            origin = "http://127.0.0.1:8001"
            api = "http://127.0.0.1:8001/api"
            verification_token = "t"

            def create_thread(self, metadata=None):
                return "t-1", "created"

            def run(self, thread_id, prompt, *, timeout_s, key):
                return {"http_status": 0, "body": {"transport_error": transport_error}}

        workload = monitor.Workload(key="X", title="t", prompt="p", evidence=lambda *a: (True, "ok"))
        return monitor.run_workload(G(), workload)

    def test_a_timeout_is_unverified_with_an_unknown_outcome(self):
        outcome = self._outcome("TimeoutError: timed out")
        assert outcome.verdict == "UNVERIFIED"
        assert "outcome is UNKNOWN" in outcome.detail
        assert "may still be running it" in outcome.detail
        assert "unreachable" not in outcome.detail

    def test_a_refused_connection_is_still_a_transport_error(self):
        outcome = self._outcome("URLError: ConnectionRefusedError")
        assert outcome.verdict == "ERROR"
        assert "unreachable" in outcome.detail


class TestLedgerAlerting:
    """A standing break must alert once, not every interval forever."""

    def test_transitions_are_new_then_changed_then_unchanged(self, tmp_path: Path):
        ledger = tmp_path / "ledger.json"
        first = monitor.Outcome(workload="A", title="t", kind="coding", verdict="FAIL", detail="broke")
        assert monitor._ledger_record(ledger, first) == "new"
        again = monitor.Outcome(workload="A", title="t", kind="coding", verdict="FAIL", detail="still broke")
        assert monitor._ledger_record(ledger, again) == "unchanged"
        fixed = monitor.Outcome(workload="A", title="t", kind="coding", verdict="PASS", detail="ok")
        assert monitor._ledger_record(ledger, fixed) == "changed"
        stored = json.loads(ledger.read_text(encoding="utf-8"))
        assert stored["outcomes"]["A"]["verdict"] == "PASS"

    def test_a_corrupt_ledger_is_rebuilt_rather_than_crashing_the_watch(self, tmp_path: Path):
        ledger = tmp_path / "ledger.json"
        ledger.write_text("{not json", encoding="utf-8")
        outcome = monitor.Outcome(workload="B", title="t", kind="tools", verdict="PASS", detail="ok")
        assert monitor._ledger_record(ledger, outcome) == "new"


class TestReportRendersBreaks:
    def test_the_report_names_the_break_and_never_claims_success(self):
        outcomes = [
            monitor.Outcome(workload="A", title="coding", kind="coding", verdict="PASS", detail="fixed + tested", elapsed_s=90),
            monitor.Outcome(workload="K", title="provider", kind="fault", verdict="FAIL", detail="no citations", run_id="abcdef123456", server_error="boom"),
        ]
        report = monitor.render_report(outcomes)
        assert "1/2 passed" in report
        assert "no citations" in report
        assert "boom" in report
        # A pipe inside a table cell would break the table itself.
        assert "|" not in outcomes[0].detail


def test_the_monitor_refuses_to_run_against_a_non_gateway_http_200():
    """Any process can answer /health with 200; the identity check is the gate."""

    class Impostor(monitor.Gateway):
        def _request(self, url: str, method: str, payload: Any, timeout: int | None):
            return 200, {"status": "ok"}

    healthy, detail = Impostor("http://127.0.0.1:8001").healthy()
    assert healthy is False
    assert "alpha-gateway" in detail


@pytest.mark.parametrize("url", ["http://127.0.0.1:8001", "http://127.0.0.1:8001/"])
def test_api_base_is_stable_for_a_given_origin(url: str):
    assert monitor.Gateway(url).api == "http://127.0.0.1:8001/api"
