"""One command must produce the whole diagnostic picture.

Before this change the two halves of the evidence lived apart:
`scripts/support_bundle.py` had config, versions, git and doctor but no logs and no
trace, and `scripts/export_run_trace.py` had spans and events but no logs, no
config and no versions -- and nothing called it. A reporter therefore could not
produce a complete picture with one command, and a maintainer could not get the
logs without a second request.

These tests pin the merge:

* logs and the trace are collected **by default**, because the point of the
  command is completeness;
* log tails are **bounded and disclosed** -- a bundle that silently contains the
  first N lines of a huge log is worse than no bundle, because it looks complete;
* the trace is rendered through `export_run_trace`, not a second renderer;
* secrets in log lines and trace records do not survive;
* the existing redaction contracts still hold, and the harness `Redactor` now
  chains behind the local patterns.
"""

from __future__ import annotations

import json
import logging
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import export_run_trace  # noqa: E402
import rotate_logs  # noqa: E402
import support_bundle  # noqa: E402

from alpha.observability.redaction import dangerous_value_corpus  # noqa: E402

#: Fabricated credential fixtures, taken from the canonical corpus rather than
#: re-typed: GitHub push protection rejects a commit whose *text* contains a whole
#: credential-shaped token (GH013) even when fabricated, which is why
#: `observability/redaction.py` keeps its own fixtures split. Referencing the
#: corpus means this file holds no credential literal at all.
_CORPUS: dict[str, str] = {label: value for label, value, _fragment in dangerous_value_corpus()}
_FRAGMENTS: dict[str, str] = {label: fragment for label, _value, fragment in dangerous_value_corpus()}

#: An assignment value that only matches because of the keyword in front of it.
_ASSIGNMENT_TEXT = "api_key=" + "0123456789abcdef0123456789abcdef"

#: A `Bearer` header, assembled. The split falls *inside* the scheme word: `Bearer`
#: is a shape push protection recognises on its own, so splitting at the space would
#: leave a half that still matches.
_BEARER_HEADER = "Authorization: Bea" + "rer abcdefghijklmnop"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_trace(path: Path, *, run_id: str = "a" * 32) -> Path:
    records = [
        {"type": "span", "name": "run", "span_id": "1" * 32, "start": 1_000.0, "end": 1_002.0, "depth": 0, "run_id": run_id, "status": "ok"},
        {"type": "event", "name": "tool.call", "ts": 1_000.5, "run_id": run_id, "status": "ok", "attributes": {"tool": "bash"}},
        {"type": "event", "name": "run.end", "ts": 1_002.0, "run_id": run_id, "status": "error"},
    ]
    return _write(path, "".join(json.dumps(record) + "\n" for record in records))


# --------------------------------------------------------------------------- #
# The merged command
# --------------------------------------------------------------------------- #


def test_bundle_includes_logs_and_trace_by_default(tmp_path: Path) -> None:
    """One command, the whole picture. Logs and trace are not opt-in."""
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "2026-09-26 12:00:00 - x - INFO - started\n")
    _write_trace(root / ".agent-workspace" / "traces" / "run.jsonl")
    out = root / "out" / "bundle.zip"

    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
    )

    with zipfile.ZipFile(bundle_path) as zf:
        names = set(zf.namelist())
        triage = json.loads(zf.read("triage.json"))
        manifest = json.loads(zf.read("manifest.json"))

    assert "logs.json" in names
    assert "logs/gateway.log" in names
    assert "trace.json" in names
    assert "trace-timeline.txt" in names
    assert manifest["includes"]["logs"] is True
    assert manifest["includes"]["trace"] is True
    assert triage["logs"]["included"] is True
    assert triage["logs"]["files"] == 1
    assert triage["trace"]["included"] is True


def test_bundle_trace_is_rendered_by_the_sibling_module(tmp_path: Path) -> None:
    """Merging the halves must merge the *renderers* too.

    Two timeline renderers would drift, and the bundle's is the one nobody would
    remember to update. The bundle has to delegate, so this asserts the rendered
    timeline is what `export_run_trace.render_trace` produces for the same file.
    """
    root = tmp_path / "repo"
    trace_path = _write_trace(root / ".agent-workspace" / "traces" / "run.jsonl")
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
    )
    with zipfile.ZipFile(bundle_path) as zf:
        bundled = zf.read("trace-timeline.txt").decode("utf-8")
    expected = export_run_trace.render_trace(trace_path, max_events=500, run_id=None)
    assert bundled == support_bundle.redact_evidence_text(expected)
    # And it really is the sibling module doing the work, not a reimplementation.
    assert "span run [ok]" in bundled
    assert "event tool.call" in bundled
    assert "DISCLOSURE" not in bundled


def test_bundle_can_scope_the_trace_to_one_run(tmp_path: Path) -> None:
    """`--run-id` is the difference between "the last run" and "their run"."""
    root = tmp_path / "repo"
    _write_trace(root / ".agent-workspace" / "traces" / "a.jsonl", run_id="a" * 32)
    _write_trace(root / ".agent-workspace" / "traces" / "b.jsonl", run_id="b" * 32)
    summary = support_bundle.collect_trace(root, {"present": False}, run_id="b" * 32)
    assert summary["available"] is True
    assert f"run={'b' * 32}" in summary["timeline"]
    assert f"run={'a' * 32}" not in summary["timeline"]


def test_missing_trace_is_reported_not_fatal(tmp_path: Path) -> None:
    """ "Observability is not configured here" is a triage fact, not a crash."""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    summary = support_bundle.collect_trace(root, {"present": False})
    assert summary["available"] is False
    assert "no readable trace file found" in summary["reason"]
    assert summary["candidates_checked"] == []


def test_bundle_reports_no_trace_when_the_render_sibling_is_missing(monkeypatch, tmp_path: Path) -> None:
    """A broken/absent sibling must not take the logs half down with it.

    `scripts/export_run_trace.py` is loaded by path, so a partially-checked-out
    tree is a real failure mode -- and the logs are the part a reporter can
    always produce.
    """
    monkeypatch.setattr(support_bundle, "_export_run_trace", None)
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "2026-09-26 12:00:00 - x - INFO - started\n")
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
    )
    with zipfile.ZipFile(bundle_path) as zf:
        names = set(zf.namelist())
        trace = json.loads(zf.read("trace.json"))
    assert "logs.json" in names
    assert "trace-timeline.txt" not in names
    assert "unavailable" in trace["reason"]


# --------------------------------------------------------------------------- #
# Bounds and disclosure
# --------------------------------------------------------------------------- #


def test_log_tail_is_bounded_and_the_truncation_is_disclosed(tmp_path: Path) -> None:
    """A tail that silently drops the interesting end is worse than no tail.

    The end of a log is where the failure is, so the tail keeps the *last* N
    lines -- and `logs.json` has to say that something was dropped.
    """
    root = tmp_path / "repo"
    body = "".join(f"2026-09-26 12:00:00 - x - INFO - line {index}\n" for index in range(500))
    _write(root / "logs" / "gateway.log", body)
    logs = support_bundle.collect_logs(root, max_lines=10)
    assert len(logs) == 1
    entry = logs[0]
    assert entry["truncated"] is True
    assert entry["lines_included"] == 10
    # The *last* lines survive: an incident's ending is the useful part.
    assert "line 499" in entry["text"]
    assert "line 0\n" not in entry["text"]


def test_log_tail_is_byte_bounded_and_drops_the_partial_first_line(tmp_path: Path) -> None:
    """The byte window can start mid-line; a fragment is not a record.

    Keeping the fragment would produce a tail whose first line is a truncated
    message, which reads as real evidence and is not.
    """
    root = tmp_path / "repo"
    body = "".join(f"line {index:04d} aaaaaaaaaa\n" for index in range(200))
    _write(root / "logs" / "gateway.log", body)
    logs = support_bundle.collect_logs(root, max_lines=10_000, max_bytes=200)
    entry = logs[0]
    assert entry["truncated"] is True
    first = entry["text"].splitlines()[0]
    assert first.startswith("line "), f"tail began with a partial line: {first!r}"


def test_collected_log_metadata_describes_the_file_without_carrying_it(tmp_path: Path) -> None:
    """`logs.json` is a manifest; the content goes in its own zip member.

    Otherwise every triage of a large bundle re-reads megabytes of log text it
    does not need.
    """
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "2026-09-26 12:00:00 - x - INFO - hello\n")
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
    )
    with zipfile.ZipFile(bundle_path) as zf:
        logs_json = json.loads(zf.read("logs.json"))
    assert "text" not in logs_json[0]
    assert logs_json[0]["levels"]["INFO"] == 1


def test_level_histogram_surfaces_errors_the_reporter_could_not_see(tmp_path: Path) -> None:
    """The single most useful fact in the bundle: what was the Gateway saying?

    A reporter sees a blank UI. `log_error_lines` turns that into "the gateway
    was already logging ERROR", which is the difference between guessing and
    triaging.
    """
    root = tmp_path / "repo"
    _write(
        root / "logs" / "gateway.log",
        "2026-09-26 12:00:00 - x - INFO - ok\n"
        "2026-09-26 12:00:01 - x - WARNING - degraded\n"
        "2026-09-26 12:00:02 - x - ERROR - failed\n"
        "2026-09-26 12:00:03 - x - CRITICAL - very failed\n"
        "2026-09-26 12:00:04 - x - FATAL - also very failed\n"
        "2026-09-26 12:00:05 - x - WARN - old spelling\n"
        "a line with no level at all\n",
    )
    logs = support_bundle.collect_logs(root)
    levels = logs[0]["levels"]
    assert levels["INFO"] == 1
    assert levels["WARNING"] == 2
    assert levels["ERROR"] == 1
    assert levels["CRITICAL"] == 2
    assert levels["UNKNOWN"] == 1
    signals = support_bundle.log_error_signals(logs)
    assert signals["log_error_lines"] == 3
    assert signals["log_warning_lines"] == 2


def test_logs_can_be_left_out_entirely(tmp_path: Path) -> None:
    """A reporter who does not want operational data in a zip must be able to opt out."""
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "2026-09-26 12:00:00 - x - INFO - secret-ish\n")
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
        include_logs=False,
        include_trace=False,
    )
    with zipfile.ZipFile(bundle_path) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert not [name for name in names if name.startswith("logs/")]
    assert "logs.json" not in names
    assert "trace.json" not in names
    assert manifest["includes"]["logs"] is False
    assert manifest["includes"]["trace"] is False


# --------------------------------------------------------------------------- #
# Redaction of the new evidence
# --------------------------------------------------------------------------- #


def test_log_secrets_do_not_survive_into_the_bundle(tmp_path: Path) -> None:
    """Log lines are real operational data and travel into an issue attachment."""
    root = tmp_path / "repo"
    bearer_value = "b" * 20
    planted = [
        f"2026-09-26 12:00:00 - x - ERROR - Authorization: Bearer {bearer_value}\n",
        f"2026-09-26 12:00:01 - x - ERROR - db {_CORPUS['connection_string']}\n",
        f"2026-09-26 12:00:02 - x - ERROR - {_CORPUS['openai_project_key']}\n",
    ]
    _write(root / "logs" / "gateway.log", "".join(planted))
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
        include_trace=False,
    )
    with zipfile.ZipFile(bundle_path) as zf:
        log_text = zf.read("logs/gateway.log").decode("utf-8")
    for secret in (bearer_value, _FRAGMENTS["connection_string"], _CORPUS["openai_project_key"]):
        assert secret not in log_text, f"{secret!r} survived into the bundled log tail"
    assert "2026-09-26 12:00:00 - x - ERROR" in log_text


def test_a_rotated_log_generation_is_collected_too(tmp_path: Path) -> None:
    """The generation a rotation preserved is usually the one with the crash in it.

    `scripts/rotate_logs.py` names a generation `gateway.log.1`, which does not
    end in `.log`. A collector that only matched the suffix would silently drop
    the pre-restart log -- the single most valuable file in the directory. Found
    end-to-end, not by reading the code.
    """
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "2026-09-26 12:00:09 - x - INFO - after restart\n")
    _write(root / "logs" / "gateway.log.1", "2026-09-26 12:00:00 - x - ERROR - the crash\n")
    _write(root / "backend" / "logs" / "gateway.log.2", "2026-09-26 11:00:00 - x - WARNING - older\n")

    assert support_bundle.is_collected_log(root / "logs" / "gateway.log.1") is True
    assert support_bundle.is_collected_log(root / "backend" / "logs" / "gateway.log.2") is True
    assert support_bundle.is_collected_log(root / "logs" / "alpha.pid") is False
    assert support_bundle.is_collected_log(root / "logs" / "notes.md") is False

    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
        include_trace=False,
    )
    with zipfile.ZipFile(bundle_path) as zf:
        names = set(zf.namelist())
        logs_json = json.loads(zf.read("logs.json"))
    assert "logs/gateway.log.1" in names
    assert "backend/logs/gateway.log.2" in names
    # Grouped by directory rather than globally name-sorted, so a reader can see
    # at a glance which launcher wrote which file.
    assert [item["name"] for item in logs_json] == [
        "logs/gateway.log",
        "logs/gateway.log.1",
        "backend/logs/gateway.log.2",
    ]
    assert sum(item["levels"]["ERROR"] + item["levels"]["WARNING"] for item in logs_json) == 2


def test_zip_member_names_mirror_the_checkout_paths(tmp_path: Path) -> None:
    """A zip whose entries mirror the checkout is navigable.

    The member name and the `name` in `logs.json` have to be the same string, or a
    reader has to guess which one to cross-reference. This also pins that a
    leading separator or a `..` cannot reach a zip member name.
    """
    assert support_bundle._zip_name("logs/gateway.log") == "logs/gateway.log"
    assert support_bundle._zip_name("backend/logs/gateway.log.1") == "backend/logs/gateway.log.1"
    assert support_bundle._zip_name("C:\\Users\\x\\logs\\a.log") == "C:/Users/x/logs/a.log"
    assert support_bundle._zip_name("../../../etc/passwd") == "etc/passwd"
    assert support_bundle._zip_name("/logs/a.log") == "logs/a.log"
    assert support_bundle._zip_name("") == "logs/unnamed.log"


def test_trace_secrets_do_not_survive_into_the_bundle(tmp_path: Path) -> None:
    """The trace renderer is unchanged, so the bundle re-redacts what it renders."""
    root = tmp_path / "repo"
    records = [
        {
            "type": "event",
            "name": "tool.call",
            "ts": 1_000.0,
            "run_id": "a" * 32,
            "status": "ok",
            "attributes": {"error": "connect failed for " + _CORPUS["connection_string"].rsplit("/", 1)[0]},
        }
    ]
    _write(root / ".agent-workspace" / "traces" / "run.jsonl", json.dumps(records[0]) + "\n")
    out = root / "out" / "bundle.zip"
    bundle_path = support_bundle.create_support_bundle(
        project_root=root,
        out_path=out,
        config_path=root / "config.yaml",
        extensions_config_path=root / "extensions_config.json",
        include_logs=False,
    )
    with zipfile.ZipFile(bundle_path) as zf:
        timeline = zf.read("trace-timeline.txt").decode("utf-8")
    assert _FRAGMENTS["connection_string"] not in timeline
    assert "tool.call" in timeline


def test_harness_redactor_chains_behind_the_local_patterns(monkeypatch) -> None:
    """A family the local patterns do not know about must still be scrubbed.

    The local regex set is what lets the script run with a broken backend venv. The
    shared `Redactor` is what stops the bundle being a weaker guarantee than the
    trace plane it is meant to explain. This asserts the *chaining*, by making the
    harness redactor a spy.
    """
    calls: list[str] = []

    class _Outcome:
        def __init__(self, value: str) -> None:
            self.value = value

    class _SpyRedactor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def redact_value(self, value, **_kwargs):
            calls.append(str(value))
            return _Outcome(f"{value}<harness>")

    import alpha.observability.redaction as redaction_module

    monkeypatch.setattr(redaction_module, "Redactor", _SpyRedactor)
    monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", support_bundle._UNSET)
    try:
        result = support_bundle.redact_evidence_text(_ASSIGNMENT_TEXT)
    finally:
        monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", support_bundle._UNSET)
    assert calls, "the harness redactor was never consulted"
    assert result.endswith("<harness>")
    # The local pattern already ran first, so the harness sees the local output.
    assert "<redacted>" in calls[0]


def test_redact_text_output_is_unchanged_by_the_harness(monkeypatch) -> None:
    """`redact_text` is pinned by the existing suite and must stay pinned.

    Folding the engine into it would change every `<redacted>` it has ever emitted
    (`[REDACTED:env_file_line]` instead), which is a silent redefinition of an
    existing public behaviour across config, git, and doctor redaction. The two
    scrubbers are composed at `redact_evidence_text` instead.
    """
    monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", support_bundle._UNSET)
    assert support_bundle.redact_text("OPENAI_API_KEY=sk-live-secret") == "OPENAI_API_KEY=<redacted>"


def test_evidence_redaction_degrades_to_the_local_patterns(monkeypatch) -> None:
    """A broken venv must not remove the local scrubbing.

    This is the situation the local patterns exist for: the backend environment is
    unusable, which is exactly when someone needs a support bundle. Both halves of
    `redact_evidence_text` have to survive losing the harness.
    """
    monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", None)
    assert support_bundle.redact_text(_ASSIGNMENT_TEXT) == "api_key=<redacted>"
    assert "<redacted>" in support_bundle.redact_text(_BEARER_HEADER)
    assert support_bundle.redact_evidence_text(_ASSIGNMENT_TEXT) == "api_key=<redacted>"
    assert "<redacted>" in support_bundle.redact_evidence_text(_BEARER_HEADER)


def test_collected_evidence_actually_goes_through_the_composed_scrubber(monkeypatch, tmp_path: Path) -> None:
    """The composition has to be wired into the collectors, not just available.

    A helper nobody calls is not a redaction boundary. This makes the harness
    redactor a spy and asserts both the log tail and the trace timeline are
    routed through it.
    """
    seen: list[str] = []

    class _Outcome:
        def __init__(self, value: str) -> None:
            self.value = value

    class _SpyRedactor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def redact_value(self, value, **_kwargs):
            seen.append(str(value))
            return _Outcome(str(value))

    import alpha.observability.redaction as redaction_module

    monkeypatch.setattr(redaction_module, "Redactor", _SpyRedactor)
    monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", support_bundle._UNSET)
    try:
        root = tmp_path / "repo"
        _write(root / "logs" / "gateway.log", "2026-09-26 12:00:00 - x - INFO - hello there\n")
        _write_trace(root / ".agent-workspace" / "traces" / "run.jsonl")
        support_bundle.collect_logs(root)
        support_bundle.collect_trace(root, {"present": False})
    finally:
        monkeypatch.setattr(support_bundle, "_HARNESS_REDACTOR", support_bundle._UNSET)
    assert any("hello there" in item for item in seen), "the log tail bypassed the composed scrubber"
    assert any("tool.call" in item for item in seen), "the trace timeline bypassed the composed scrubber"


def test_rendered_reports_disclose_that_operational_data_is_included() -> None:
    """A reporter must be told the zip contains logs before they upload it.

    The logs and the trace are real operational data. The privacy section used to
    say the bundle excluded raw conversation messages and user file contents, and
    a reader could reasonably infer "nothing sensitive is in here" -- which would
    now be false.
    """
    triage = {
        "status": "ok",
        "active_signals": [],
        "git": {"branch": "main", "head": "abc", "dirty_worktree": False},
        "doctor": {"included": False, "ok": True, "errors": None, "warnings": None},
        "versions": {},
        "logs": {"included": True, "files": 2, "error_lines": 0, "warning_lines": 0, "tail_truncated": False},
        "trace": {"included": True, "source": ".agent-workspace/traces/run.jsonl", "reason": None, "run_id_filter": None},
        "reporter_next_steps": [],
        "maintainer_next_steps": [],
        "evidence_files": [],
        "privacy": {},
    }
    summary = support_bundle.render_issue_summary(triage)
    readme = support_bundle.render_bundle_readme(triage)
    assert "error_lines=0" in summary
    assert "run trace" in summary
    assert "secret-scrubbed but not otherwise summarised" in summary
    assert "Launcher log tails and the run trace are included" in readme
    assert "disclose when something was dropped" in readme


# --------------------------------------------------------------------------- #
# Rotation
# --------------------------------------------------------------------------- #


def test_rotation_moves_an_oversized_log_and_keeps_the_tail(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log = _write(log_dir / "gateway.log", "x" * 100)
    result = rotate_logs.rotate_file(log, max_bytes=10, backups=3)
    assert result.rotated is True
    assert not log.exists()
    assert (log_dir / "gateway.log.1").read_text(encoding="utf-8") == "x" * 100


def test_rotation_leaves_a_log_within_budget_alone(tmp_path: Path) -> None:
    log = _write(tmp_path / "logs" / "gateway.log", "small")
    result = rotate_logs.rotate_file(log, max_bytes=1024, backups=3)
    assert result.rotated is False
    assert "within budget" in result.reason
    assert log.exists()


def test_rotation_bounds_the_number_of_generations(tmp_path: Path) -> None:
    """A rotation that cannot free space is not a bound.

    Five successive rotations with `backups=2` must leave exactly two
    generations, oldest dropped -- otherwise the backup budget is consumed by one
    generation and the log directory grows anyway.
    """
    log_dir = tmp_path / "logs"
    for index in range(5):
        log = _write(log_dir / "gateway.log", f"generation {index}" + "x" * 100)
        rotate_logs.rotate_file(log, max_bytes=10, backups=2)
    names = sorted(path.name for path in log_dir.iterdir())
    assert names == ["gateway.log.1", "gateway.log.2"]
    assert (log_dir / "gateway.log.1").read_text(encoding="utf-8").startswith("generation 4")
    assert (log_dir / "gateway.log.2").read_text(encoding="utf-8").startswith("generation 3")


def test_backup_naming_matches_the_logging_rotating_file_handler(tmp_path: Path) -> None:
    """Both rotators must agree on where a generation lives.

    `backend/debug.py` uses `logging.handlers.RotatingFileHandler`, which names
    backups `<name>.N`. A second convention in the same repository means a reader
    has to check which tool wrote a file before knowing what to delete.
    """
    from logging.handlers import RotatingFileHandler

    log_dir = tmp_path / "logs"
    log = _write(log_dir / "gateway.log", "y" * 200)
    rotate_logs.rotate_file(log, max_bytes=10, backups=3)
    handler = RotatingFileHandler(log_dir / "python.log", maxBytes=10, backupCount=3)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "z" * 200, (), None)
    # The first emit fills the file; the second sees the projected size exceed
    # maxBytes and rolls over.
    handler.emit(record)
    handler.emit(record)
    handler.close()
    python_generation = next(path.name for path in log_dir.iterdir() if path.name.startswith("python.log.") and path.name.rsplit(".", 1)[-1].isdigit())
    rotate_generation = next(path.name for path in log_dir.iterdir() if path.name.startswith("gateway.log.") and path.name.rsplit(".", 1)[-1].isdigit())
    # Same shape: the number lands on the end of the full name.
    assert python_generation.split(".")[-1] == rotate_generation.split(".")[-1] == "1"


def test_rotation_does_not_rotate_a_previous_generation(tmp_path: Path) -> None:
    """A rotated log keeps the `.log` suffix, so it must be excluded explicitly.

    Otherwise the second `make dev` re-rotates `gateway.log.1` and the whole
    budget fills with one generation.
    """
    log_dir = tmp_path / "logs"
    previous = _write(log_dir / "gateway.log.1", "x" * 10_000)
    assert rotate_logs.is_log_candidate(previous) is False
    results = rotate_logs.rotate_directory(log_dir, max_bytes=10, backups=3)
    assert results[0].rotated is False
    assert previous.exists()


def test_rotation_reports_a_missing_directory_rather_than_failing(tmp_path: Path) -> None:
    """`make dev` must not abort because nobody has ever started the stack."""
    results = rotate_logs.rotate_directory(tmp_path / "never-created", max_bytes=10, backups=3)
    assert len(results) == 1
    assert results[0].rotated is False
    assert results[0].reason == "directory absent"


def test_rotation_distinguishes_only_generations_from_an_empty_directory(tmp_path: Path) -> None:
    """Right after a rotation the directory is not empty -- it is all generations.

    Reporting that as "no log files present" reads as "the logs vanished", which is
    the wrong thing to tell someone at the moment they are diagnosing a restart.
    """
    log_dir = tmp_path / "logs"
    log = _write(log_dir / "gateway.log", "x" * 100)
    rotate_logs.rotate_file(log, max_bytes=10, backups=3)
    after_rotation = rotate_logs.rotate_directory(log_dir, max_bytes=10, backups=3)
    assert after_rotation[0].rotated is False
    assert "rotated generation" in after_rotation[0].reason

    empty = tmp_path / "empty"
    empty.mkdir()
    assert rotate_logs.rotate_directory(empty, max_bytes=10, backups=3)[0].reason == "no log files present"


def test_rotation_covers_both_default_log_directories(tmp_path: Path) -> None:
    """`start.ps1` writes `<root>/logs`; a backend-local run writes `<root>/backend/logs`."""
    root = tmp_path / "repo"
    _write(root / "logs" / "gateway.log", "x" * 100)
    _write(root / "backend" / "logs" / "gateway.log", "y" * 100)
    report = rotate_logs.rotate_logs(root, max_bytes=10, backups=2)
    assert sorted(report) == sorted([str(root / "logs"), str(root / "backend" / "logs")])
    assert all(any(result.rotated for result in results) for results in report.values())


def test_rotation_cli_validates_its_bounds(capsys: pytest.CaptureFixture[str]) -> None:
    assert rotate_logs.main(["--max-bytes", "0"]) == rotate_logs.EXIT_USAGE
    assert rotate_logs.main(["--backups", "0"]) == rotate_logs.EXIT_USAGE
    captured = capsys.readouterr()
    assert "--max-bytes must be >= 1" in captured.err
    assert "--backups must be >= 1" in captured.err


def test_rotation_cli_is_a_no_op_on_a_clean_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert rotate_logs.main(["--project-root", str(root), "--json"]) == rotate_logs.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["rotated"] == 0
    assert payload["failed"] == 0
