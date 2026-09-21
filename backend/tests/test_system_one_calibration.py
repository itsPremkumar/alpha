"""Tests for System One shadow mode and the calibration harness.

The claim being tested is the one that justifies using these models at all: a
stated 0.9 should be right about 90% of the time. That claim is site-specific,
so the harness has to (a) capture decisions per site without ever disturbing
them and (b) be able to prove overconfidence when it happens.

The second property under test is that measurement is strictly non-invasive.
Shadow mode must return None so call sites keep their existing path, and a
broken log must never surface as an exception to a decision-maker.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from alpha.config.system_one_config import RiskTier, SystemOneConfig
from alpha.evaluation.system_one_calibration import (
    BUCKET_EDGES,
    MIN_BUCKET_COUNT,
    DecisionRecord,
    DecisionRecorder,
    calibration_report,
    configure_recorder,
    get_recorder,
    load_records,
    record_outcome,
    record_recent_outcome,
    reset_recorder,
)
from alpha.models.system_one import (
    BooleanQuestion,
    SystemOneClient,
    decide_boolean,
)

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _attach(client: SystemOneClient, handler) -> httpx.AsyncClient:
    fake = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    client._get_client = lambda: fake  # type: ignore[method-assign]
    return fake


def _boolean_payload(value: float = 0.9) -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {"q": {"type": "boolean", "boolean": value}},
        "usage": {"input_tokens": 10, "output_tokens": 2},
    }


def _ok_handler(payload: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload or _boolean_payload(), request=request)

    return handler


def _record(**kwargs) -> DecisionRecord:
    base = dict(
        ts=1.0,
        site="guardrail",
        tier="read",
        question_id="q",
        type="boolean",
        value=0.9,
        confidence=None,
        threshold=0.6,
        latency_ms=120.0,
    )
    base.update(kwargs)
    return DecisionRecord(**base)


@pytest.fixture(autouse=True)
def _isolated_recorder(tmp_path):
    """Point the process-wide recorder at a temp file for every test."""
    configure_recorder(tmp_path / "decisions.jsonl")
    yield
    reset_recorder()


# --------------------------------------------------------------------------
# DecisionRecord.acted
# --------------------------------------------------------------------------


def test_boolean_acts_when_strength_clears_threshold():
    # A boolean has no confidence, so "strength" is the distance from 0.5.
    assert _record(value=0.9).acted is True
    assert _record(value=0.1).acted is True  # confidently false still acts
    assert _record(value=0.55).acted is False  # |0.55-0.5|*2 = 0.1 < 0.6


def test_choice_and_score_act_on_confidence():
    assert _record(type="choice", value="allow", confidence=0.8).acted is True
    assert _record(type="choice", value="allow", confidence=0.5).acted is False
    assert _record(type="score", value=3.0, confidence=None).acted is False


def test_shadow_never_counts_as_acted():
    """Shadow decisions are recorded but explicitly did not drive behaviour."""
    assert _record(value=0.99, shadow=True).acted is False


def test_unparseable_value_does_not_act():
    assert _record(value="not-a-number").acted is False


# --------------------------------------------------------------------------
# Recorder
# --------------------------------------------------------------------------


def test_recorder_appends_jsonl(tmp_path):
    path = tmp_path / "a" / "decisions.jsonl"
    recorder = DecisionRecorder(path)
    recorder.record(_record(value=0.7))
    recorder.record(_record(value=0.8))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["site"] == "guardrail"


def test_recorder_without_path_is_a_noop():
    DecisionRecorder(None).record(_record())  # must not raise


def test_recorder_never_raises_on_unwritable_path(tmp_path):
    """A broken log must never surface as an exception to a decision-maker."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    DecisionRecorder(blocker / "nested" / "decisions.jsonl").record(_record())


def test_configure_and_reset_recorder(tmp_path):
    configure_recorder(tmp_path / "x.jsonl")
    assert get_recorder().path == tmp_path / "x.jsonl"
    reset_recorder()
    configure_recorder(None)
    assert get_recorder().path is None


def test_get_recorder_is_memoised(tmp_path):
    configure_recorder(tmp_path / "y.jsonl")
    assert get_recorder() is get_recorder()


# --------------------------------------------------------------------------
# load / outcome
# --------------------------------------------------------------------------


def test_load_records_skips_malformed_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(
        json.dumps(_record(value=0.9).to_dict()) + "\n" + "not json\n" + "\n" + '{"ts": "bad"}\n',
        encoding="utf-8",
    )
    records = load_records(path)
    assert len(records) == 1
    assert records[0].value == 0.9


def test_load_records_missing_file_is_empty(tmp_path):
    assert load_records(tmp_path / "nope.jsonl") == []


def test_record_outcome_matches_site_and_timestamp(tmp_path):
    path = tmp_path / "log.jsonl"
    ts = 1234.5
    records = [_record(ts=ts, site="guardrail", value=0.9), _record(ts=ts, site="browser", value=0.7)]
    with path.open("w", encoding="utf-8") as handle:
        for r in records:
            handle.write(json.dumps(r.to_dict()) + "\n")

    matched = record_outcome(path, ts, "guardrail", True)
    assert matched == 1

    reloaded = load_records(path)
    by_site = {r.site: r.outcome for r in reloaded}
    assert by_site["guardrail"] is True
    assert by_site["browser"] is None  # untouched


def test_record_outcome_returns_zero_when_nothing_matches(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text(json.dumps(_record(ts=1.0).to_dict()) + "\n", encoding="utf-8")
    assert record_outcome(path, 999.0, "guardrail", True) == 0


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def test_empty_report_is_safe():
    report = calibration_report([])
    assert report.total == 0
    assert report.brier is None
    assert "No outcomes" in report.render()


def test_coverage_and_latency_with_out_outcomes():
    """Most sites have no ground truth; they must still report coverage."""
    report = calibration_report([_record(latency_ms=100.0), _record(latency_ms=300.0)])
    assert report.total == 2
    assert report.scored == 0
    assert report.mean_latency_ms == pytest.approx(200.0)
    assert report.brier is None


def test_perfectly_calibrated_set_has_zero_gap():
    records = [_record(value=0.9, outcome=(i < 9)) for i in range(10)]
    report = calibration_report(records)
    assert report.scored == 10
    top = [b for b in report.buckets if b.low == 0.9][0]
    assert top.count == 10
    assert top.observed == pytest.approx(0.9)
    assert top.gap == pytest.approx(0.0, abs=1e-9)
    assert report.expected_calibration_error == pytest.approx(0.0, abs=1e-9)


def test_overconfident_set_reports_positive_gap():
    """This is the failure the harness exists to catch."""
    records = [_record(value=0.9, outcome=(i < 5)) for i in range(10)]
    report = calibration_report(records)
    top = [b for b in report.buckets if b.low == 0.9][0]
    assert top.observed == pytest.approx(0.5)
    assert top.gap == pytest.approx(0.4, abs=1e-9)  # said 0.9, was right 50%
    assert report.expected_calibration_error > 0.3


def test_brier_score_matches_mean_squared_error():
    records = [_record(value=1.0, outcome=True), _record(value=1.0, outcome=False)]
    report = calibration_report(records)
    assert report.brier == pytest.approx(0.5)


def test_buckets_cover_the_unit_interval():
    assert BUCKET_EDGES[0] == 0.0
    assert BUCKET_EDGES[-1] > 1.0  # so a stated 1.0 still lands in a bucket
    report = calibration_report([_record(value=1.0, outcome=True)])
    assert any(b.count == 1 for b in report.buckets)


def test_by_site_counts_are_separate():
    records = [
        _record(site="guardrail", value=0.9, outcome=True),
        _record(site="guardrail", value=0.2, outcome=False),
        _record(site="browser", value=0.95, outcome=True),
    ]
    report = calibration_report(records)
    assert report.by_site["guardrail"]["count"] == 2
    assert report.by_site["browser"]["count"] == 1
    assert report.by_site["browser"]["scored"] == 1


def test_unlabelled_records_are_grouped_not_dropped():
    report = calibration_report([_record(site="")])
    assert "(unlabelled)" in report.by_site


def test_render_includes_the_headline_numbers():
    report = calibration_report([_record(value=0.9, outcome=True)])
    text = report.render()
    assert "System One calibration" in text
    assert "Brier" in text
    assert "guardrail" in text


def test_report_to_dict_is_json_serialisable():
    report = calibration_report([_record(value=0.9, outcome=True)])
    assert json.loads(json.dumps(report.to_dict()))["total"] == 1


# --------------------------------------------------------------------------
# Client: shadow mode and recording
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_mode_returns_none_but_still_records(tmp_path):
    """The whole point: measure without changing behaviour."""
    path = tmp_path / "shadow.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", shadow_mode=True))
    _attach(cl, _ok_handler())

    result = await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail", tier=RiskTier.READ)

    assert result is None, "shadow mode must hand control back to the existing path"
    records = load_records(path)
    assert len(records) == 1
    assert records[0].site == "guardrail"
    assert records[0].tier == "read"
    assert records[0].type == "boolean"
    assert records[0].value == pytest.approx(0.9)
    assert records[0].shadow is True
    assert records[0].acted is False


@pytest.mark.asyncio
async def test_shadow_mode_without_record_decisions_still_logs(tmp_path):
    """Shadow without a log would measure nothing, so it implies recording."""
    path = tmp_path / "implied.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", shadow_mode=True, record_decisions=False))
    _attach(cl, _ok_handler())
    await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="browser")
    assert len(load_records(path)) == 1


@pytest.mark.asyncio
async def test_recording_on_and_not_shadow_returns_the_result(tmp_path):
    path = tmp_path / "live.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler())

    result = await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail")

    assert result is not None
    assert result.get("q").value == pytest.approx(0.9)
    records = load_records(path)
    assert len(records) == 1
    assert records[0].shadow is False
    # Booleans act on strength, not confidence: |0.9 - 0.5| * 2 = 0.8 >= 0.6.
    assert records[0].acted is True
    assert records[0].threshold == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_no_recording_when_both_flags_off(tmp_path):
    path = tmp_path / "off.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k"))
    _attach(cl, _ok_handler())
    await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail")
    assert not path.exists()


@pytest.mark.asyncio
async def test_recording_failure_does_not_break_the_decision(tmp_path):
    """A broken log must never turn a usable answer into a fallback."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    configure_recorder(blocker / "nested" / "decisions.jsonl")
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler())
    result = await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail")
    assert result is not None


@pytest.mark.asyncio
async def test_threshold_recorded_comes_from_the_tier(tmp_path):
    path = tmp_path / "tier.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler())
    await cl.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail", tier=RiskTier.DESTRUCTIVE)
    assert load_records(path)[0].threshold == pytest.approx(0.90)


@pytest.mark.asyncio
async def test_every_answer_in_a_batch_is_recorded(tmp_path):
    """All questions ride one request; all of them must be logged."""
    path = tmp_path / "batch.jsonl"
    configure_recorder(path)
    payload = {
        "model": "jev",
        "answers": {
            "a": {"type": "boolean", "boolean": 0.9},
            "b": {"type": "boolean", "boolean": 0.2},
        },
    }
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler(payload))
    await cl.evaluate({"x": 1}, {"a": BooleanQuestion("a?"), "b": BooleanQuestion("b?")}, site="trace")
    records = load_records(path)
    assert len(records) == 2
    assert {r.question_id for r in records} == {"a", "b"}


@pytest.mark.asyncio
async def test_site_label_threads_through_the_helper(tmp_path):
    path = tmp_path / "helper.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler())

    await decide_boolean({"x": 1}, "true?", site="rerank", client=cl)

    assert load_records(path)[0].site == "rerank"


@pytest.mark.asyncio
async def test_site_defaults_to_empty_when_not_given(tmp_path):
    path = tmp_path / "unlabelled.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler())
    await decide_boolean({"x": 1}, "true?", client=cl)
    assert load_records(path)[0].site == ""


# --------------------------------------------------------------------------
# Outcomes: attaching ground truth after the fact
# --------------------------------------------------------------------------


def test_recorder_tracks_the_newest_timestamp_per_site(tmp_path):
    recorder = DecisionRecorder(tmp_path / "ts.jsonl")
    recorder.record(_record(ts=1.0, site="browser"))
    recorder.record(_record(ts=2.0, site="browser"))
    recorder.record(_record(ts=3.0, site="guardrail"))
    assert recorder.last_ts("browser") == 2.0
    assert recorder.last_ts("guardrail") == 3.0
    assert recorder.last_ts("nothing") is None


def test_record_recent_outcome_marks_the_latest_decision(tmp_path):
    path = tmp_path / "recent.jsonl"
    configure_recorder(path)
    recorder = get_recorder()
    recorder.record(_record(ts=1.0, site="browser", value=0.9))
    recorder.record(_record(ts=2.0, site="browser", value=0.8))

    assert record_recent_outcome("browser", True) == 1

    marked = [r for r in load_records(path) if r.outcome is not None]
    assert len(marked) == 1
    assert marked[0].ts == 2.0  # the newest one, not the first
    assert marked[0].value == 0.8


def test_record_recent_outcome_without_a_log_is_a_noop():
    configure_recorder(None)
    assert record_recent_outcome("browser", True) == 0


def test_record_recent_outcome_with_no_decision_is_a_noop(tmp_path):
    configure_recorder(tmp_path / "empty.jsonl")
    assert record_recent_outcome("browser", True) == 0


def test_small_buckets_are_flagged_not_trusted():
    """Two lucky samples must not read as 'calibrated'."""
    records = [_record(value=0.9, outcome=True) for _ in range(2)]
    report = calibration_report(records)
    top = [b for b in report.buckets if b.count][-1]
    assert top.observed == 1.0
    # Stated 0.9, right 2/2 -> looks underconfident, but it is just noise.
    assert top.gap == pytest.approx(-0.1, abs=1e-9)
    assert top.reliable is False
    assert "too few to judge" in report.render()


def test_buckets_become_reliable_at_the_threshold():
    thin = [_record(value=0.9, outcome=True) for _ in range(MIN_BUCKET_COUNT - 1)]
    assert not [b for b in calibration_report(thin).buckets if b.count][-1].reliable
    enough = [_record(value=0.9, outcome=True) for _ in range(MIN_BUCKET_COUNT)]
    assert [b for b in calibration_report(enough).buckets if b.count][-1].reliable


def test_report_warns_about_sites_with_thin_evidence():
    records = [_record(site="browser", value=0.9, outcome=True) for _ in range(3)]
    rendered = calibration_report(records).render()
    assert "Thin evidence" in rendered
    assert "browser" in rendered


def test_reliable_flag_is_in_the_json_payload():
    records = [_record(value=0.9, outcome=True) for _ in range(MIN_BUCKET_COUNT)]
    payload = calibration_report(records).to_dict()
    top = [b for b in payload["buckets"] if b["count"]][-1]
    assert top["reliable"] is True
    assert payload["min_bucket_count"] == MIN_BUCKET_COUNT


def test_record_outcome_survives_a_concurrent_append(tmp_path):
    """The rewrite must not silently drop decisions appended while it ran.

    Losing decisions is the worst failure a measurement tool can have: it
    quietly biases the sample towards whatever survived.
    """
    from alpha.evaluation import system_one_calibration as module

    path = tmp_path / "race.jsonl"
    ts = 5.0
    path.write_text(json.dumps(_record(ts=ts, site="browser").to_dict()) + "\n", encoding="utf-8")

    # Append once, right after the first read — i.e. between read and write.
    original_load = module.load_records
    appended = {"done": False}

    def load_then_append(target, *args, **kwargs):
        result = original_load(target, *args, **kwargs)
        if not appended["done"]:
            appended["done"] = True
            with Path(target).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_record(ts=99.0, site="guardrail", value=0.7).to_dict()) + "\n")
        return result

    module.load_records = load_then_append
    try:
        assert record_outcome(path, ts, "browser", True) == 1
    finally:
        module.load_records = original_load

    records = load_records(path)
    assert len(records) == 2, f"the concurrent append must survive the rewrite, got {len(records)}"
    by_site = {r.site: r.outcome for r in records}
    assert by_site["browser"] is True
    assert by_site["guardrail"] is None  # preserved, just unjudged


def test_record_outcome_leaves_no_temp_files_behind(tmp_path):
    """The atomic replace must not litter the state dir with partial writes.

    The `.lock` file deliberately persists — it is the lock token, and removing
    it would race with another process holding it.
    """
    path = tmp_path / "clean.jsonl"
    path.write_text(json.dumps(_record(ts=1.0, site="browser").to_dict()) + "\n", encoding="utf-8")
    record_outcome(path, 1.0, "browser", False)
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == [], f"rewrite left stray files: {leftovers}"
    assert json.loads(path.read_text(encoding="utf-8").strip())["outcome"] is False


@pytest.mark.asyncio
async def test_selection_override_splits_skill_and_tool_ranking(tmp_path):
    """The shared ranker must not pool skill and tool calibration together."""
    from alpha.tools.selection import Candidate, rank_candidates

    path = tmp_path / "select.jsonl"
    configure_recorder(path)
    options = ("alpha", "beta")
    payload = {
        "model": "jev",
        "answers": {"pick": {"type": "choice", "choice": "alpha", "probabilities": {"alpha": 0.8, "beta": 0.2}, "confidence": 0.9}},
    }
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler(payload))

    candidates = [Candidate(id=o, title=o, summary=o) for o in options]
    await rank_candidates("do the thing", candidates, site="skill_select", client=cl)

    # The coarse pass and the refine pass both record, the latter suffixed.
    sites = {r.site for r in load_records(path)}
    assert sites == {"skill_select", "skill_select:refine"}, "the override must replace the default label"


@pytest.mark.asyncio
async def test_rerank_override_separates_session_search(tmp_path):
    from alpha.memory.rerank import rerank

    path = tmp_path / "rerank.jsonl"
    configure_recorder(path)
    payload = {
        "model": "jev",
        "answers": {
            "c0": {"type": "score", "score": 4, "probabilities": {"1": 0.1, "2": 0.1, "3": 0.2, "4": 0.6}, "confidence": 0.9},
            "c1": {"type": "score", "score": 2, "probabilities": {"1": 0.2, "2": 0.6, "3": 0.1, "4": 0.1}, "confidence": 0.9},
        },
    }
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler(payload))

    await rerank("find it", ["relevant hit", "unrelated"], site="session_search", client=cl)

    records = load_records(path)
    assert records, "rerank should have recorded its per-candidate scores"
    assert {r.site for r in records} == {"session_search"}


def test_selection_sync_wrapper_forwards_the_site_label(tmp_path):
    """A sync wrapper that dropped kwargs would silently unlabel every decision.

    Deliberately NOT an async test: the wrapper refuses to run inside a live
    loop by design, so it only does anything when there is no running loop.
    """
    from alpha.tools.selection import Candidate, rank_candidates_sync

    path = tmp_path / "sync.jsonl"
    configure_recorder(path)
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler({"model": "jev", "answers": {"pick": {"type": "choice", "choice": "alpha", "probabilities": {"alpha": 0.8, "beta": 0.2}, "confidence": 0.9}}}))

    candidates = [Candidate(id="alpha", title="alpha", summary="alpha"), Candidate(id="beta", title="beta", summary="beta")]
    ranking = rank_candidates_sync("do the thing", candidates, site="tool_select", client=cl)

    assert ranking is not None, "with no running loop the sync wrapper should run"
    sites = {r.site for r in load_records(path)}
    assert sites == {"tool_select", "tool_select:refine"}


@pytest.mark.asyncio
async def test_browser_agent_records_whether_its_chosen_step_worked(tmp_path):
    """The one site with immediate, unambiguous ground truth.

    The policy picks an action; the executor then reports whether it actually
    worked. That is the outcome that turns a coverage log into a real
    calibration measurement.
    """
    from alpha.browser.executor import ScriptedExecutor
    from alpha.browser.jev_agent import BrowserAgent

    path = tmp_path / "browser.jsonl"
    configure_recorder(path)

    operations = ("CLICK", "TYPE_TEXT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")
    share = 0.02 / (len(operations) - 1)
    probabilities = {op: share for op in operations if op != "CLICK"}
    probabilities["CLICK"] = 1.0 - sum(probabilities.values())
    payload = {
        "model": "jev",
        "answers": {
            "operation": {"type": "choice", "choice": "CLICK", "probabilities": probabilities, "confidence": 0.95},
            "click_target": {"type": "choice", "choice": "1", "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95},
        },
    }

    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler(payload))

    page = {
        "url": "https://example.com",
        "title": "Example",
        "text": "Welcome",
        "elements": [
            {"tag": "button", "text": "Submit", "selector": "#s", "coords": [10, 10]},
            {"tag": "input", "placeholder": "Email", "selector": "#e"},
        ],
    }
    agent = BrowserAgent(ScriptedExecutor(pages=[page, page]), client=cl, scan_injection=False, max_steps=2)
    run = await agent.run("submit the form")

    assert run.steps, "the agent should have executed at least one step"
    records = [r for r in load_records(path) if r.site == "browser"]
    assert records, "browser decisions should be labelled and recorded"
    # Every answer from that request shares a timestamp, so all get the outcome.
    assert all(r.outcome is True for r in records), "the step succeeded, so the outcome is True"


@pytest.mark.asyncio
async def test_outcome_recording_never_breaks_a_run(tmp_path):
    """Measurement must be strictly non-invasive, even when the log is broken."""
    from alpha.browser.executor import ScriptedExecutor
    from alpha.browser.jev_agent import BrowserAgent

    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    configure_recorder(blocker / "nested" / "decisions.jsonl")

    operations = ("CLICK", "TYPE_TEXT", "SCROLL_UP", "SCROLL_DOWN", "WAIT", "DONE", "BLOCKED")
    share = 0.02 / (len(operations) - 1)
    probabilities = {op: share for op in operations if op != "CLICK"}
    probabilities["CLICK"] = 1.0 - sum(probabilities.values())
    payload = {
        "model": "jev",
        "answers": {
            "operation": {"type": "choice", "choice": "CLICK", "probabilities": probabilities, "confidence": 0.95},
            "click_target": {"type": "choice", "choice": "1", "probabilities": {"1": 0.9, "2": 0.1}, "confidence": 0.95},
        },
    }
    cl = SystemOneClient(SystemOneConfig(api_key="k", record_decisions=True))
    _attach(cl, _ok_handler(payload))
    page = {
        "url": "https://example.com",
        "title": "Example",
        "text": "Welcome",
        "elements": [
            {"tag": "button", "text": "Submit", "selector": "#s", "coords": [10, 10]},
            {"tag": "input", "placeholder": "Email", "selector": "#e"},
        ],
    }
    agent = BrowserAgent(ScriptedExecutor(pages=[page, page]), client=cl, scan_injection=False, max_steps=2)
    run = await agent.run("submit the form")
    assert run.steps, "a broken log must not stop the agent from working"
