"""Regression tests for the transactional Bot forge and its supporting modules.

Covers the four behaviours that separate a built Bot from a half-built one:

* a failed build rolls back to *nothing* (no ghost profile on the roster);
* a Bot is only reported alive after it answers a smoke test;
* a duplicate role is refused before anything is created;
* blockers raised by any Bot roll up into one ``waiting_on`` answer.

Plus the supporting machinery: the deterministic workspace survey, the
append-only work journal, and the secret-scanned share/import template.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import alpha.bots.registry as bot_reg
from alpha.bots.forge import (
    DEFAULT_APPROVALS,
    MIN_ROUTINE_INTERVAL_MINUTES,
    build_guardrail_soul,
    check_overlap,
    forge_bot,
    plan_routine_guard,
    probe_sandbox,
)
from alpha.bots.journal import BotJournal, format_waiting, waiting_on_you
from alpha.bots.portable import (
    TemplateError,
    export_template,
    import_template,
    scan_mapping,
    scan_text,
)
from alpha.bots.registry import BotRegistry
from alpha.bots.survey import clear_survey_cache, score_overlap, survey_workspace
from alpha.tools.builtins.bot_roster_tool import bot_roster_tool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    clear_survey_cache()
    yield
    bot_reg._global_registry = None
    bot_reg._global_registry_path = None
    clear_survey_cache()


@pytest.fixture()
def registry(tmp_path) -> BotRegistry:
    return BotRegistry(tmp_path / "roster.json")


# ---------------------------------------------------------------------------
# Overlap refusal
# ---------------------------------------------------------------------------


def test_score_overlap_is_high_for_same_role_low_for_disjoint():
    high, _ = score_overlap("write scheduled social posts", "write scheduled social posts daily")
    low, _ = score_overlap("write scheduled social posts", "reconcile quarterly invoices")
    assert high > 0.5
    assert low == 0.0


def test_check_overlap_refuses_a_second_bot_for_the_same_job():
    existing = {"inkwell": "write scheduled social posts\nYou write posts for the calendar."}
    offender, score = check_overlap(
        "",
        "write scheduled social posts",
        existing=existing,
        name="quill",
    )
    assert offender == "inkwell"
    assert score >= 0.34


def test_check_overlap_ignores_the_bot_updating_itself():
    offender, _ = check_overlap("", "write scheduled social posts", existing={"quill": "x"}, name="quill")
    assert offender is None


def test_check_overlap_does_not_refuse_on_a_thin_role():
    """A one-word role scores 1.0 against any SOUL sharing that word.

    Refusing on that would block perfectly legitimate creations (``role=
    "Security"`` would be refused because the ``alpha`` leader's SOUL mentions
    security), so a query with fewer than MIN_OVERLAP_TERMS distinct terms
    yields evidence only, never a refusal.
    """
    from alpha.bots.survey import MIN_OVERLAP_TERMS

    assert MIN_OVERLAP_TERMS == 3
    existing = {"sentinel": "Security Engineer\nYou run security reviews and audit everything."}
    offender, score = check_overlap("", "Security", existing=existing, name="newcomer")
    assert score >= 0.34, "the score is still reported as evidence"
    assert offender is None, "but a thin query must not refuse the build"


def test_check_overlap_refuses_once_the_role_is_specific_enough():
    existing = {"sentinel": "Security Engineer\nYou run security reviews and audit everything."}
    offender, _ = check_overlap(
        "",
        "run security reviews and audit everything",
        existing=existing,
        name="newcomer",
    )
    assert offender == "sentinel"


# ---------------------------------------------------------------------------
# Routine cost guard
# ---------------------------------------------------------------------------


def test_routine_guard_blocks_a_schedule_faster_than_the_floor():
    report = plan_routine_guard(["every 15 minutes"])
    assert report.allowed is False
    assert report.runs_per_day == pytest.approx(96.0)
    assert str(MIN_ROUTINE_INTERVAL_MINUTES) in report.reason
    assert "allow_frequent" in report.reason


def test_routine_guard_allows_the_floor_and_a_daily_cron():
    assert plan_routine_guard(["every 30 minutes"]).allowed is True
    assert plan_routine_guard(["0 7 * * *"]).allowed is True


def test_routine_guard_operator_override_is_explicit_and_reported():
    report = plan_routine_guard(["every 5 minutes"], allow_frequent=True)
    assert report.allowed is True
    assert "allow_frequent" in report.reason


def test_routine_guard_does_not_block_unparseable_schedules_silently():
    report = plan_routine_guard(["whenever the sun rises"])
    assert report.allowed is True
    assert report.interval_minutes is None


# ---------------------------------------------------------------------------
# Sandbox probe
# ---------------------------------------------------------------------------


def test_probe_sandbox_refuses_an_unknown_backend():
    available, reason = probe_sandbox("hypercube")
    assert available is False
    assert "unknown sandbox backend" in reason


def test_probe_sandbox_accepts_host_execution():
    available, reason = probe_sandbox(None)
    assert available is True
    assert "host" in reason


# ---------------------------------------------------------------------------
# Guardrail SOUL
# ---------------------------------------------------------------------------


def test_guardrail_soul_writes_approvals_escalation_and_survey_at_birth():
    soul = build_guardrail_soul(
        "inkwell",
        "X writer",
        approvals=DEFAULT_APPROVALS,
        reports_to="architect",
        sandbox="docker",
        survey_block="## Where you landed\n- fits: none",
    )
    assert "## Ask first" in soul
    for approval in DEFAULT_APPROVALS:
        assert f"- {approval}" in soul
    assert "## Escalate to" in soul
    assert "@architect" in soul
    assert "`docker` sandbox" in soul
    assert "## Where you landed" in soul
    assert "SOUL.md - Inkwell" in soul


def test_guardrail_soul_does_not_adopt_another_bots_identity():
    soul = build_guardrail_soul(
        "quill",
        "X writer",
        approvals=[],
        base_soul="# SOUL.md - Inkwell (X writer)\n\nYou are **Inkwell**. Write posts.",
    )
    assert "You are **Inkwell**" not in soul
    assert "Quill" in soul


# ---------------------------------------------------------------------------
# The forge: transactional build
# ---------------------------------------------------------------------------


def test_forge_builds_a_complete_bot(registry):
    result = forge_bot(
        name="inkwell",
        role="Write scheduled social posts",
        registry=registry,
        department="growth",
        reports_to="architect",
        routines=[{"name": "daily", "schedule": "0 9 * * *", "action": "draft the day's posts"}],
        workspace_survey=False,
        smoke_test=lambda bot: f"ready: {bot.name}",
    )

    assert result.ok is True, result.reply()
    assert result.smoke_tested is True
    bot = registry.get_bot("inkwell")
    assert bot is not None
    assert bot.department == "growth"
    assert bot.reports_to == "architect"
    assert "## Ask first" in bot.soul
    assert bot.metadata["approvals"] == list(DEFAULT_APPROVALS)
    assert bot.metadata["forged_by"] == "forge"
    assert len(bot.routines) == 1
    assert "journal" in result.step_names
    assert "is alive" in result.reply()


def test_forge_rolls_back_to_nothing_when_the_smoke_test_fails(registry):
    def _no_answer(bot):
        raise RuntimeError("did not answer")

    result = forge_bot(
        name="ghost",
        role="Never delivers",
        registry=registry,
        workspace_survey=False,
        routines=[{"name": "daily", "schedule": "0 9 * * *", "action": "x"}],
        smoke_test=_no_answer,
    )

    assert result.ok is False
    assert result.rolled_back is True
    assert "smoke test failed" in result.reason
    assert registry.get_bot("ghost") is None, "a failed build must leave no profile"
    assert result.rollback_report == [], f"rollback was not clean: {result.rollback_report}"
    assert "no partial Bot remains" in result.reply()


def test_forge_refuses_a_duplicate_role_before_creating_anything(registry):
    forge_bot(
        name="inkwell",
        role="Write scheduled social posts",
        registry=registry,
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )

    result = forge_bot(
        name="quill",
        role="Write scheduled social posts",
        registry=registry,
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )

    assert result.ok is False
    assert "inkwell" in result.reason
    assert "allow_overlap" in result.reason
    assert registry.get_bot("quill") is None


def test_forge_allow_overlap_override_builds_and_warns(registry):
    forge_bot(
        name="inkwell",
        role="Write scheduled social posts",
        registry=registry,
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )
    result = forge_bot(
        name="quill",
        role="Write scheduled social posts",
        registry=registry,
        allow_overlap=True,
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )
    assert result.ok is True
    assert any("allow_overlap" in w for w in result.warnings)
    assert registry.get_bot("quill") is not None


def test_forge_refuses_an_unusable_sandbox_before_creating_anything(registry):
    result = forge_bot(
        name="jail",
        role="Shell worker",
        registry=registry,
        sandbox="hypercube",
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )
    assert result.ok is False
    assert "sandbox refused" in result.reason
    assert registry.get_bot("jail") is None


def test_forge_refuses_an_unaffordable_routine_before_creating_anything(registry):
    result = forge_bot(
        name="chatty",
        role="Never stops talking",
        registry=registry,
        routines=[{"name": "nag", "schedule": "every 10 minutes", "action": "nag"}],
        workspace_survey=False,
        smoke_test=lambda bot: "ok",
    )
    assert result.ok is False
    assert "routine refused" in result.reason
    assert registry.get_bot("chatty") is None


def test_forge_without_a_smoke_test_reports_the_bot_as_unverified(registry):
    result = forge_bot(
        name="unproven",
        role="Unchecked build",
        registry=registry,
        workspace_survey=False,
    )
    assert result.ok is True
    assert result.smoke_tested is False
    assert any("UNVERIFIED" in w for w in result.warnings)


def test_forge_refuses_a_malformed_handle(registry):
    result = forge_bot(name="../escape", role="x", registry=registry, workspace_survey=False)
    assert result.ok is False
    assert "not a valid handle" in result.reason


def test_forge_refuses_to_rebuild_an_existing_bot(registry):
    forge_bot(name="inkwell", role="a", registry=registry, workspace_survey=False, smoke_test=lambda b: "ok")
    result = forge_bot(name="inkwell", role="a", registry=registry, workspace_survey=False)
    assert result.ok is False
    assert "already exists" in result.reason


# ---------------------------------------------------------------------------
# Workspace survey
# ---------------------------------------------------------------------------


def test_survey_is_deterministic_and_cached(tmp_path):
    clear_survey_cache()
    kwargs = dict(
        soul="Reconcile invoices against the ledger",
        role="Invoice reconciler",
        name="ledger",
        cwd=str(tmp_path),
        workspace_roots=[str(tmp_path)],
        skills=["accounting"],
        installed_skills=["accounting", "design"],
        existing_roles={},
    )
    first = survey_workspace(**kwargs)
    second = survey_workspace(**kwargs)
    assert first.cached is False
    assert second.cached is True
    assert first.to_dict()["findings"] == second.to_dict()["findings"]


def test_survey_reads_the_workspace_and_reports_a_fit(tmp_path):
    repo = tmp_path / "invoicing-service"
    repo.mkdir()
    (repo / "README.md").write_text("# Invoicing service\n\nReconciles invoices against the ledger.\n", encoding="utf-8")
    result = survey_workspace(
        soul="Reconcile invoices against the ledger",
        role="Invoice reconciler",
        cwd=str(tmp_path),
        workspace_roots=[str(tmp_path)],
    )
    assert result.fits == "invoicing-service"
    assert result.fits_score > 0
    assert any(f.path.endswith("invoicing-service") for f in result.findings)


def test_survey_scrubs_credentials_before_they_reach_bot_memory(tmp_path):
    (tmp_path / "README.md").write_text("Setup: export OPENAI_API_KEY=sk-live-abcdefghijklmnop\n", encoding="utf-8")
    result = survey_workspace(soul="configure the api key", cwd=str(tmp_path), workspace_roots=[str(tmp_path)])
    blob = result.memory_block()
    assert "sk-live" not in blob
    assert "[redacted]" in blob or "sk-live" not in json.dumps(result.to_dict())


def test_survey_reports_territory_an_existing_bot_already_covers(tmp_path):
    result = survey_workspace(
        soul="write scheduled social posts",
        role="X writer",
        cwd=str(tmp_path),
        workspace_roots=[str(tmp_path)],
        existing_roles={"inkwell": "write scheduled social posts for the calendar"},
    )
    assert result.covered_by == "inkwell"
    assert "inkwell" in result.already_here
    assert "coordinate with @inkwell" in " ".join(result.next_steps)


def test_survey_never_walks_dependency_trees(tmp_path):
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "README.md").write_text("helper utilities", encoding="utf-8")
    result = survey_workspace(soul="helper utilities", cwd=str(tmp_path), workspace_roots=[str(tmp_path)])
    assert not any("node_modules" in f.path for f in result.findings)


def test_survey_operator_kill_switch_stops_the_scan(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text("reconciles invoices against the ledger", encoding="utf-8")
    monkeypatch.setenv("AGENT_WORKSPACE_BOT_SURVEY", "0")
    result = survey_workspace(soul="reconcile invoices", cwd=str(tmp_path), workspace_roots=[str(tmp_path)])
    assert result.findings == []
    assert result.roots_scanned == []
    assert result.fits is None
    assert any("disabled by the operator" in step for step in result.next_steps)


def test_survey_kill_switch_is_not_cached_as_a_real_result(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_BOT_SURVEY", "0")
    disabled = survey_workspace(soul="x", cwd=str(tmp_path), workspace_roots=[str(tmp_path)])
    monkeypatch.delenv("AGENT_WORKSPACE_BOT_SURVEY")
    enabled = survey_workspace(soul="x", cwd=str(tmp_path), workspace_roots=[str(tmp_path)])
    assert any("disabled by the operator" in step for step in disabled.next_steps)
    assert not any("disabled by the operator" in step for step in enabled.next_steps)


# ---------------------------------------------------------------------------
# Work journal + blocker roll-up
# ---------------------------------------------------------------------------


def test_journal_opens_a_blocker_and_closes_it_when_completed(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()

    entry = journal.append("pull invoices", tried="called stripe", blocker="Need the Stripe key")
    assert not isinstance(entry, str)
    assert entry.state == "blocked"
    assert [b.title for b in journal.open_blockers()] == ["Need the Stripe key"]

    journal.append("pull invoices", tried="retried with key", completed="Need the Stripe key")
    assert journal.open_blockers() == []


def test_journal_refuses_credential_shaped_text(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    refusal = journal.append("leak", tried="key sk-live-abcdefghijklmnop")
    assert isinstance(refusal, str)
    assert refusal.startswith("refused")
    assert "credential" in refusal
    assert journal.read() == []


def test_journal_refuses_private_reasoning(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    refusal = journal.append("think", tried="chain of thought: first I did X then Y")
    assert isinstance(refusal, str)
    assert "reasoning" in refusal


def test_journal_refuses_an_empty_title(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    assert journal.append("") == "refused: entry requires a title"


def test_journal_persists_entries_across_instances(tmp_path):
    BotJournal(tmp_path, "inkwell").enable()
    first = BotJournal(tmp_path, "inkwell")
    first.append("did a thing", outcome="it worked")
    second = BotJournal(tmp_path, "inkwell")
    assert len(second.read()) == 1
    assert second.summary()["entries"] == 1


def test_journal_writes_a_dated_markdown_record(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    journal.append("did a thing", outcome="it worked", evidence=["log line"])
    day = journal.read()[0].day
    body = journal.read_day(day)
    assert "did a thing" in body
    assert "it worked" in body
    assert "log line" in body


def test_journal_day_path_rejects_a_traversing_day(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    with pytest.raises(ValueError):
        journal.read_day("../../etc/passwd")


def test_waiting_on_you_rolls_every_bot_up_into_one_answer(tmp_path):
    a = BotJournal(tmp_path, "inkwell")
    a.enable()
    a.append("blocked a", blocker="Waiting on the API key")
    b = BotJournal(tmp_path, "quill")
    b.enable()
    b.append("blocked b", blocker="Waiting on the calendar access")

    rows = waiting_on_you([a, b])
    assert {r["bot"] for r in rows} == {"inkwell", "quill"}
    summary = format_waiting(rows)
    assert "2 waiting on you" in summary
    assert "inkwell: Waiting on the API key" in summary


def test_waiting_on_you_says_so_when_nothing_is_blocked(tmp_path):
    journal = BotJournal(tmp_path, "inkwell")
    journal.enable()
    journal.append("clean run", outcome="done")
    assert waiting_on_you([journal]) == []
    assert "Nothing is waiting on you" in format_waiting([])


# ---------------------------------------------------------------------------
# Secret-scanned share / import
# ---------------------------------------------------------------------------


def _profile(**extra):
    base = {
        "name": "inkwell",
        "display_name": "Inkwell",
        "role": "X writer",
        "soul": "# SOUL.md - Inkwell (X writer)",
        "skills": ["x"],
        "department": "growth",
    }
    base.update(extra)
    return base


def test_export_excludes_every_private_field(tmp_path):
    out = export_template(
        _profile(
            chat_history="SECRET CHAT",
            user_facts="I like pizza",
            journal=["day1"],
            auth="Bearer abc",
        ),
        "inkwell",
        export_root=tmp_path,
    )
    assert out["verdict"] == "CLEAN"
    payload = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    for key in ("chat_history", "user_facts", "journal", "auth"):
        assert key not in payload
    assert set(out["excluded"]) >= {"chat_history", "user_facts", "journal"}


def test_export_is_blocked_by_a_secret_and_writes_nothing(tmp_path):
    with pytest.raises(TemplateError) as exc:
        export_template(
            _profile(soul="key: sk-live-abcdefghijklmnop"),
            "leaky",
            export_root=tmp_path,
        )
    assert "BLOCKED" in str(exc.value)
    assert not (tmp_path / "leaky.alphabot.json").exists()


def test_export_refuses_to_overwrite_an_existing_file(tmp_path):
    export_template(_profile(), "inkwell", export_root=tmp_path)
    with pytest.raises(TemplateError, match="overwrite"):
        export_template(_profile(), "inkwell", export_root=tmp_path)


def test_export_refuses_a_path_that_escapes_the_export_root(tmp_path):
    outside = tmp_path.parent / "outside-root.json"
    with pytest.raises(TemplateError, match="stay under"):
        export_template(_profile(), outside, export_root=tmp_path)
    assert not outside.exists()


def test_import_refuses_to_stack_a_second_identity_onto_an_existing_bot(tmp_path):
    out = export_template(_profile(), "inkwell", export_root=tmp_path)
    with pytest.raises(TemplateError, match="already exists"):
        import_template(out["path"], known_names=["inkwell"])


def test_import_renames_on_conflict_when_a_new_name_is_offered(tmp_path):
    out = export_template(_profile(), "inkwell", export_root=tmp_path)
    result = import_template(out["path"], known_names=["inkwell"], proposed_name="quill")
    assert result["renamed"] is True
    assert result["profile"]["name"] == "quill"


def test_import_blocks_a_hand_edited_secret(tmp_path):
    out = export_template(_profile(), "inkwell", export_root=tmp_path)
    payload = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    payload["soul"] = "password: hunter2secret1"
    bad = tmp_path / "bad.alphabot.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(TemplateError, match="BLOCKED"):
        import_template(bad, known_names=[])


def test_scan_text_and_scan_mapping_verdicts():
    assert scan_text("write the posts daily").verdict == "CLEAN"
    assert scan_text("token ghp_abcdefghijklmnopqrstuvwx").verdict == "BLOCK"
    assert scan_mapping({"soul": "hi"}).verdict == "CLEAN"
    assert scan_mapping({"soul": "hi", "api_key": "x"}).verdict == "BLOCK"
    assert scan_mapping({"soul": "hi", "note": "my long deadbeefdeadbeefdeadbeefdeadbeef hash"}).verdict == "WARN"


# ---------------------------------------------------------------------------
# Model-facing tool surface
# ---------------------------------------------------------------------------


def test_tool_forge_reports_a_complete_build(tmp_path, monkeypatch):
    out = bot_roster_tool.invoke(
        {
            "action": "forge",
            "name": "inkwell",
            "role": "Write scheduled social posts",
            "department": "growth",
            "schedule": "0 9 * * *",
            "objective": "draft the day's posts",
        }
    )
    assert "is alive" in out
    assert "smoke test: skipped" in out  # honest: no probe was supplied
    assert "UNVERIFIED" in out


def test_tool_forge_writes_default_approvals_into_the_soul():
    bot_roster_tool.invoke(
        {"action": "forge", "name": "inkwell", "role": "Write scheduled social posts"}
    )
    from alpha.bots.registry import get_bot_registry

    bot = get_bot_registry().get_bot("inkwell")
    assert "## Ask first" in bot.soul
    for approval in DEFAULT_APPROVALS:
        assert f"- {approval}" in bot.soul


def test_tool_forge_approvals_opt_out_is_explicit_not_implicit():
    """``approvals=""`` cannot mean opt-out (indistinguishable from unasked)."""
    bot_roster_tool.invoke(
        {"action": "forge", "name": "barebot", "role": "Reconcile invoices monthly", "approvals": "none"}
    )
    from alpha.bots.registry import get_bot_registry

    bot = get_bot_registry().get_bot("barebot")
    assert bot is not None
    assert "## Ask first" not in bot.soul
    assert bot.metadata["approvals"] == []


def test_tool_forge_refuses_a_frequent_routine_and_creates_nothing():
    out = bot_roster_tool.invoke(
        {
            "action": "forge",
            "name": "chatty",
            "role": "Never stops",
            "schedule": "every 10 minutes",
            "objective": "nag",
        }
    )
    assert "routine refused" in out
    assert bot_roster_tool.invoke({"action": "list"})  # roster still readable


def test_tool_journal_roundtrip_and_waiting_on_rollup():
    bot_roster_tool.invoke({"action": "create", "name": "inkwell", "role": "X writer", "soul": "Write posts."})
    blocked = bot_roster_tool.invoke(
        {
            "action": "journal",
            "name": "inkwell",
            "content": "pull invoices",
            "reason": "Need the Stripe API key",
            "objective": "called stripe",
        }
    )
    assert "blocker opened" in blocked

    rollup = bot_roster_tool.invoke({"action": "waiting_on"})
    assert "waiting on you" in rollup
    assert "inkwell" in rollup
    assert "Need the Stripe API key" in rollup

    read_back = bot_roster_tool.invoke({"action": "journal", "name": "inkwell"})
    assert "pull invoices" in read_back

    closed = bot_roster_tool.invoke(
        {
            "action": "journal",
            "name": "inkwell",
            "content": "pull invoices",
            "completed": "Need the Stripe API key",
            "goal": "retried and it worked",
        }
    )
    assert "outcome recorded" in closed
    assert "inkwell: Need the Stripe API key" not in bot_roster_tool.invoke({"action": "waiting_on"})


def test_tool_journal_refuses_a_bot_that_does_not_exist():
    out = bot_roster_tool.invoke({"action": "journal", "name": "ghost", "content": "note"})
    assert "not found" in out
    assert "create it before journaling" in out


def test_tool_waiting_on_survives_the_bot_being_retired():
    """A blocker must not vanish because its Bot left the roster."""
    bot_roster_tool.invoke({"action": "create", "name": "ephemeral", "role": "X writer", "soul": "Write posts."})
    bot_roster_tool.invoke(
        {
            "action": "journal",
            "name": "ephemeral",
            "content": "run the export",
            "reason": "Waiting on the export token",
        }
    )
    assert "Waiting on the export token" in bot_roster_tool.invoke({"action": "waiting_on"})

    import alpha.bots.registry as bot_reg_singleton

    bot_reg_singleton.get_bot_registry().retire_bot("ephemeral")

    rollup = bot_roster_tool.invoke({"action": "waiting_on"})
    assert "Waiting on the export token" in rollup
    assert "off the roster" in rollup


def test_tool_journal_refuses_a_secret_and_says_why():
    bot_roster_tool.invoke({"action": "create", "name": "inkwell", "role": "X writer", "soul": "Write posts."})
    out = bot_roster_tool.invoke(
        {
            "action": "journal",
            "name": "inkwell",
            "content": "leak",
            "objective": "key sk-live-abcdefghijklmnop",
        }
    )
    assert "refused" in out
    assert "credential" in out


def test_tool_share_and_import_roundtrip(tmp_path):
    bot_roster_tool.invoke({"action": "create", "name": "shareme", "role": "Data Engineer", "soul": "Build pipelines."})
    shared = bot_roster_tool.invoke({"action": "share", "name": "shareme"})
    assert "scan: CLEAN" in shared
    assert "never shared" in shared

    path = next(line for line in shared.splitlines() if line.startswith("Exported "))
    file_path = path.split(" to ", 1)[1].splitlines()[0]

    imported = bot_roster_tool.invoke({"action": "import", "path": file_path, "name": "shareme2"})
    assert "scan: CLEAN" in imported
    assert "shareme2" in imported


def test_tool_share_reports_the_scan_verdict_for_a_clean_profile():
    bot_roster_tool.invoke({"action": "create", "name": "leaky", "role": "Ops", "soul": "Run things."})
    out = bot_roster_tool.invoke({"action": "share", "name": "leaky"})
    assert "scan: CLEAN" in out
    assert "never shared" in out


def test_tool_share_names_a_bot_that_does_not_exist():
    out = bot_roster_tool.invoke({"action": "share", "name": "ghost"})
    assert "not found" in out


def test_tool_doctor_reports_an_healthy_installation():
    out = bot_roster_tool.invoke({"action": "doctor"})
    assert "=== Bot Forge Doctor ===" in out
    assert "status: OK" in out or "status: ISSUES FOUND" in out
    assert "routine frequency floor" in out
    assert "bots:" in out


def test_tool_doctor_flags_bots_carrying_open_blockers():
    bot_roster_tool.invoke({"action": "create", "name": "blockedbot", "role": "Data Engineer", "soul": "Build pipelines."})
    bot_roster_tool.invoke(
        {
            "action": "journal",
            "name": "blockedbot",
            "content": "run the load",
            "reason": "Waiting on warehouse credentials",
        }
    )
    out = bot_roster_tool.invoke({"action": "doctor"})
    assert "status: ISSUES FOUND" in out
    assert "bots with open blockers" in out
    assert "@blockedbot" in out


def test_tool_sandbox_reports_backend_availability():
    out = bot_roster_tool.invoke({"action": "sandbox"})
    assert "=== Sandbox backends on this machine ===" in out
    for backend in ("docker", "none"):
        assert backend in out
    assert "Per-Bot sandbox:" in out


def test_tool_teach_refuses_a_draft_without_a_verification_command():
    bot_roster_tool.invoke({"action": "create", "name": "inkwell", "role": "X writer", "soul": "Write posts."})
    out = bot_roster_tool.invoke(
        {
            "action": "teach",
            "name": "inkwell",
            "role": "X writer",
            "content": "Open the composer. Type the post. Press enter to send it.",
        }
    )
    assert "NOT taught" in out
    assert "Verification" in out


def test_tool_teach_queues_a_scanned_proposal_when_the_bar_is_met(tmp_path):
    bot_roster_tool.invoke({"action": "create", "name": "inkwell", "role": "X writer", "soul": "Write posts."})
    out = bot_roster_tool.invoke(
        {
            "action": "teach",
            "name": "inkwell",
            "role": "X writer",
            "content": ("After every edit run `python -m pytest tests/ -q`.\n- never skip the suite\n- never push on a red suite"),
        }
    )
    assert "queued for review" in out
    assert "NOT active yet" in out
    assert "proposal:" in out


def test_tool_teach_refuses_to_install_and_says_the_skill_is_not_active_yet():
    """The approve gate stays in charge: teach never claims an installed skill."""
    bot_roster_tool.invoke({"action": "create", "name": "inkwell", "role": "X writer", "soul": "Write posts."})
    out = bot_roster_tool.invoke(
        {
            "action": "teach",
            "name": "inkwell",
            "role": "X writer",
            "content": "Run `pytest -q` after every edit.",
        }
    )
    assert "installed" not in out.lower() or "NOT active" in out
    assert "admin approve gate" in out
