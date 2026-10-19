"""Tests for the deterministic security audit (bot plan 4.4).

Every host-touching check is stubbed or patched at its source module so these
tests never read the developer's real config, bots, skills, MCP servers or
transcripts. The honesty invariants under test:

* ``scanned`` union ``not_checked`` always equals ``CHECK_IDS`` (disjoint).
* No score key exists anywhere in the report payload.
* A check that cannot run is ``not_checked`` with its real reason.
* Evidence never carries secret values, URLs or env-var values.
* Bounds are refused, never clamped.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from alpha.security import audit as audit_mod


def _stub_checks(monkeypatch, *, skip=frozenset(), **overrides) -> None:
    """Stub every real check with a clean stub, then apply per-check overrides.

    ``skill_prompt_injection`` has no ``_check_*`` function (it is always
    not_checked inline), so it is skipped. Check ids in ``skip`` keep their
    real implementation (for example to exercise a refusal against a tmp dir).
    Overrides are keyed by check id and must call the *captured* original if
    they forward to it — the module attribute is the override while patched.
    """
    for check_id in audit_mod.CHECK_IDS:
        if check_id == "skill_prompt_injection" or check_id in skip or check_id in overrides:
            continue
        monkeypatch.setattr(audit_mod, f"_check_{check_id}", lambda *a, **k: [])
    for check_id, fn in overrides.items():
        monkeypatch.setattr(audit_mod, f"_check_{check_id}", fn)


def _walk_keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


class TestReportShape:
    def test_report_payload_keys_and_no_score(self, monkeypatch):
        _stub_checks(monkeypatch)
        payload = audit_mod.run_security_audit().to_dict()
        assert set(payload) == {"audit", "generated_at", "scanned", "findings", "not_checked"}
        assert payload["audit"] == audit_mod.AUDIT_NAME
        assert "score" not in list(_walk_keys(payload))

    def test_scanned_and_not_checked_partition_check_ids(self, monkeypatch):
        _stub_checks(monkeypatch)
        payload = audit_mod.run_security_audit().to_dict()
        scanned = set(payload["scanned"])
        not_checked = {item["check"] for item in payload["not_checked"]}
        assert scanned | not_checked == set(audit_mod.CHECK_IDS)
        assert not scanned & not_checked

    def test_prompt_injection_is_always_not_checked(self, monkeypatch):
        _stub_checks(monkeypatch)
        report = audit_mod.run_security_audit()
        reasons = {item.check: item.reason for item in report.not_checked}
        assert reasons["skill_prompt_injection"] == audit_mod._PROMPT_INJECTION_NOT_CHECKED_REASON
        assert "skill_prompt_injection" not in report.scanned

    def test_run_check_converts_exception_to_not_checked(self):
        report = audit_mod.AuditReport(generated_at="t", findings=[], scanned=[], not_checked=[])

        def boom():
            raise ValueError("bad thing")

        audit_mod._run_check(report, "tool_allowlist_drift", boom)
        assert report.scanned == []
        assert [item.to_dict() for item in report.not_checked] == [{"check": "tool_allowlist_drift", "reason": "ValueError: bad thing"}]

    def test_finding_refuses_unknown_severity(self):
        with pytest.raises(ValueError, match="severity"):
            audit_mod.Finding(check="x", rule_id="y", severity="critical", message="", evidence="", remediation="")

    def test_audit_refuses_nonpositive_bounds(self):
        with pytest.raises(ValueError, match="max_threads"):
            audit_mod.run_security_audit(max_threads=0)
        with pytest.raises(ValueError, match="max_messages"):
            audit_mod.run_security_audit(max_messages=0)


class TestTranscriptCredentialLeakage:
    def test_leak_reports_pattern_and_hides_the_secret(self, monkeypatch):
        real_check = audit_mod._check_transcript_credential_leakage

        def fake_check(**_kwargs):
            reader = lambda **_k: {"t1": ["prefix sk-proj-abcdefghijklmnop suffix"]}  # noqa: E731
            return real_check(transcript_reader=reader)

        _stub_checks(monkeypatch, transcript_credential_leakage=fake_check)
        report = audit_mod.run_security_audit()
        leaks = [finding for finding in report.findings if finding.rule_id == "transcript.credential_leak"]
        assert len(leaks) == 1
        assert leaks[0].severity == "high"
        assert "t1" in leaks[0].evidence
        assert "openai_sk_key" in leaks[0].evidence
        assert "sk-proj-abcdefghijklmnop" not in leaks[0].evidence

    def test_clean_thread_reports_nothing(self, monkeypatch):
        real_check = audit_mod._check_transcript_credential_leakage

        def fake_check(**_kwargs):
            return real_check(transcript_reader=lambda **_k: {"t2": ["just ordinary chat text"]})

        _stub_checks(monkeypatch, transcript_credential_leakage=fake_check)
        report = audit_mod.run_security_audit()
        assert not [finding for finding in report.findings if finding.check == "transcript_credential_leakage"]

    def test_check_not_checked_under_a_running_loop(self, monkeypatch):
        _stub_checks(monkeypatch, skip={"transcript_credential_leakage"})

        async def probe():
            return audit_mod.run_security_audit()

        report = asyncio.run(probe())
        reasons = {item.check: item.reason for item in report.not_checked}
        assert "transcript_credential_leakage" in reasons
        assert "asyncio.to_thread" in reasons["transcript_credential_leakage"]
        assert "transcript_credential_leakage" not in report.scanned

    def test_collector_refuses_memory_run_events(self, monkeypatch):
        import alpha.config as alpha_config

        fake_config = SimpleNamespace(run_events=SimpleNamespace(backend="memory"))
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: fake_config)
        with pytest.raises(RuntimeError, match="run_events.backend memory"):
            asyncio.run(audit_mod._collect_transcript_rows(thread_ids=None, max_threads=1, max_messages=1))

    def test_collector_refuses_missing_session_factory(self, monkeypatch):
        import alpha.config as alpha_config
        import alpha.persistence.engine as engine

        fake_config = SimpleNamespace(run_events=SimpleNamespace(backend="db"))
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: fake_config)
        monkeypatch.setattr(engine, "get_session_factory", lambda: None)
        with pytest.raises(RuntimeError, match="database.backend memory"):
            asyncio.run(audit_mod._collect_transcript_rows(thread_ids=None, max_threads=1, max_messages=1))


class TestToolAllowlistDrift:
    def test_unresolvable_and_name_mismatch(self, monkeypatch):
        import alpha.config as alpha_config
        import alpha.reflection as reflection

        entries = [
            SimpleNamespace(name="good", group="g", use="m:Good"),
            SimpleNamespace(name="gone", group="g", use="m:Missing"),
            SimpleNamespace(name="drifted", group="g", use="m:Drift"),
        ]
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(tools=entries))

        def resolve(use, _expected):
            if use == "m:Missing":
                raise ImportError("no module named m")
            return SimpleNamespace(name="real_drifted_name" if use == "m:Drift" else "good")

        monkeypatch.setattr(reflection, "resolve_variable", resolve)
        findings = audit_mod._check_tool_allowlist_drift()
        assert {finding.rule_id for finding in findings} == {"tool.unresolvable", "tool.name_mismatch"}
        unresolvable = next(f for f in findings if f.rule_id == "tool.unresolvable")
        assert unresolvable.severity == "medium"
        assert "ImportError" in unresolvable.evidence
        mismatch = next(f for f in findings if f.rule_id == "tool.name_mismatch")
        assert "real_drifted_name" in mismatch.evidence

    def test_matching_entries_report_nothing(self, monkeypatch):
        import alpha.config as alpha_config
        import alpha.reflection as reflection

        entries = [SimpleNamespace(name="good", group="g", use="m:Good")]
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(tools=entries))
        monkeypatch.setattr(reflection, "resolve_variable", lambda use, _expected: SimpleNamespace(name="good"))
        assert audit_mod._check_tool_allowlist_drift() == []


class TestBotCapabilityCeiling:
    def test_over_ceiling_bot_is_a_high_finding(self, monkeypatch):
        import alpha.bots.authority_ceiling as authority
        import alpha.bots.registry as bots_registry

        monkeypatch.setattr(bots_registry, "get_bot_registry", lambda: SimpleNamespace(list_bots=lambda **_k: [SimpleNamespace(name="overreach", capabilities=["admin"])]))
        monkeypatch.setattr(authority, "get_ceiling", lambda: SimpleNamespace(within_ceiling=lambda _caps: (False, ["admin exceeds rank 2"])))
        findings = audit_mod._check_bot_capability_ceiling()
        assert len(findings) == 1
        assert findings[0].severity == "high"
        assert findings[0].rule_id == "bots.over_ceiling"
        assert "overreach" in findings[0].evidence
        assert "admin exceeds rank 2" in findings[0].evidence

    def test_within_ceiling_reports_nothing(self, monkeypatch):
        import alpha.bots.authority_ceiling as authority
        import alpha.bots.registry as bots_registry

        monkeypatch.setattr(bots_registry, "get_bot_registry", lambda: SimpleNamespace(list_bots=lambda **_k: [SimpleNamespace(name="ok", capabilities=[])]))
        monkeypatch.setattr(authority, "get_ceiling", lambda: SimpleNamespace(within_ceiling=lambda _caps: (True, [])))
        assert audit_mod._check_bot_capability_ceiling() == []


def _channel_connections(*, enabled=True, require_bound_identity=True, statuses=None):
    statuses = statuses or {}

    def provider_status(name):
        return statuses.get(name, {"enabled": False, "configured": False})

    return SimpleNamespace(enabled=enabled, require_bound_identity=require_bound_identity, provider_status=provider_status)


class TestChannelWebhookAuth:
    def test_unconfigured_provider_and_github_bypass(self, monkeypatch):
        import alpha.config as alpha_config

        monkeypatch.setenv("ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS", "1")
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
        conn = _channel_connections(statuses={"slack": {"enabled": True, "configured": False}})
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(channel_connections=conn))
        findings = audit_mod._check_channel_webhook_auth()
        assert {finding.rule_id for finding in findings} == {"channel.enabled_unconfigured", "github.unverified_webhooks"}
        unconfigured = next(f for f in findings if f.rule_id == "channel.enabled_unconfigured")
        assert unconfigured.severity == "medium"
        assert "slack" in unconfigured.evidence
        github = next(f for f in findings if f.rule_id == "github.unverified_webhooks")
        assert "GITHUB_WEBHOOK_SECRET" in github.evidence
        assert not any(char.isdigit() for char in github.evidence), "evidence must name env vars, never their values"

    def test_unbound_identity_is_high(self, monkeypatch):
        import alpha.config as alpha_config

        monkeypatch.delenv("ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS", raising=False)
        conn = _channel_connections(enabled=True, require_bound_identity=False)
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(channel_connections=conn))
        findings = audit_mod._check_channel_webhook_auth()
        assert [finding.rule_id for finding in findings] == ["channel.unbound_identity"]
        assert findings[0].severity == "high"

    def test_quiet_config_reports_nothing(self, monkeypatch):
        import alpha.config as alpha_config

        monkeypatch.delenv("ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS", raising=False)
        monkeypatch.setattr(alpha_config, "get_app_config", lambda: SimpleNamespace(channel_connections=_channel_connections(statuses={})))
        assert audit_mod._check_channel_webhook_auth() == []


class TestWorktreePathEscape:
    def test_missing_root_is_clean(self, tmp_path, monkeypatch):
        import alpha.config.runtime_paths as runtime_paths

        monkeypatch.setattr(runtime_paths, "runtime_home", lambda: tmp_path / "no-such-home")
        assert audit_mod._check_worktree_path_escape() == []

    def test_entries_inside_root_are_clean(self, tmp_path, monkeypatch):
        import alpha.config.runtime_paths as runtime_paths

        monkeypatch.setattr(runtime_paths, "runtime_home", lambda: tmp_path)
        worktrees = tmp_path / "swarm" / "worktrees"
        (worktrees / "wt-a").mkdir(parents=True)
        (worktrees / "wt-b").mkdir()
        assert audit_mod._check_worktree_path_escape() == []

    def test_symlink_escaping_root_is_flagged(self, tmp_path, monkeypatch):
        import alpha.config.runtime_paths as runtime_paths

        monkeypatch.setattr(runtime_paths, "runtime_home", lambda: tmp_path)
        worktrees = tmp_path / "swarm" / "worktrees"
        worktrees.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        try:
            (worktrees / "escape").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation requires privileges on this host")
        findings = audit_mod._check_worktree_path_escape()
        assert [finding.rule_id for finding in findings] == ["swarm.worktree_path_escape"]
        assert findings[0].severity == "high"
        assert "escape" in findings[0].evidence

    def test_over_bound_scan_is_refused_not_partial(self, tmp_path, monkeypatch):
        import alpha.config.runtime_paths as runtime_paths

        monkeypatch.setattr(runtime_paths, "runtime_home", lambda: tmp_path)
        worktrees = tmp_path / "swarm" / "worktrees"
        worktrees.mkdir(parents=True)
        for index in range(audit_mod._MAX_WORKTREE_ENTRIES + 1):
            (worktrees / f"wt-{index:04d}").mkdir()
        with pytest.raises(RuntimeError, match="scan bound"):
            audit_mod._check_worktree_path_escape()

    def test_over_bound_scan_surfaces_as_not_checked(self, tmp_path, monkeypatch):
        import alpha.config.runtime_paths as runtime_paths

        monkeypatch.setattr(runtime_paths, "runtime_home", lambda: tmp_path)
        worktrees = tmp_path / "swarm" / "worktrees"
        worktrees.mkdir(parents=True)
        for index in range(audit_mod._MAX_WORKTREE_ENTRIES + 1):
            (worktrees / f"wt-{index:04d}").mkdir()
        _stub_checks(monkeypatch, skip={"worktree_path_escape"})
        report = audit_mod.run_security_audit()
        reasons = {item.check: item.reason for item in report.not_checked}
        assert "worktree_path_escape" in reasons
        assert "scan bound" in reasons["worktree_path_escape"]
        assert "worktree_path_escape" not in report.scanned


class TestMcpTrust:
    def _patch_servers(self, monkeypatch, servers):
        import alpha.config.extensions_config as extensions_config

        fake = SimpleNamespace(get_enabled_mcp_servers=lambda: servers)
        monkeypatch.setattr(extensions_config.ExtensionsConfig, "from_file", classmethod(lambda cls, config_path=None: fake))

    def test_plaintext_transport_and_on_demand_runner(self, monkeypatch):
        self._patch_servers(
            monkeypatch,
            {
                "plain": SimpleNamespace(enabled=True, type="sse", url="http://secret-host.internal/mcp?token=abc", command=None),
                "runner": SimpleNamespace(enabled=True, type="stdio", url=None, command="npx"),
                "safe": SimpleNamespace(enabled=True, type="stdio", url=None, command="/usr/local/bin/my-server"),
            },
        )
        findings = audit_mod._check_mcp_trust()
        by_rule = {finding.rule_id: finding for finding in findings}
        assert set(by_rule) == {"mcp.plaintext_transport", "mcp.on_demand_runner"}
        assert by_rule["mcp.plaintext_transport"].severity == "medium"
        assert "secret-host.internal" not in by_rule["mcp.plaintext_transport"].evidence
        assert "scheme=http" in by_rule["mcp.plaintext_transport"].evidence
        assert "runner=npx" in by_rule["mcp.on_demand_runner"].evidence
        assert by_rule["mcp.on_demand_runner"].severity == "low"

    def test_secure_servers_report_nothing(self, monkeypatch):
        self._patch_servers(
            monkeypatch,
            {
                "tls": SimpleNamespace(enabled=True, type="http", url="https://mcp.example.com", command=None),
                "pinned": SimpleNamespace(enabled=True, type="stdio", url=None, command="my-server"),
            },
        )
        assert audit_mod._check_mcp_trust() == []


class TestSkillAstAudit:
    def test_violation_and_truncation_findings(self, tmp_path, monkeypatch):
        import alpha.skills.audit.ast_audit as ast_audit
        import alpha.skills.storage as skills_storage

        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "bad.py").write_text("import subprocess\n", encoding="utf-8")
        (skill_dir / "big.py").write_bytes(b"x" * (audit_mod._MAX_SKILL_PY_BYTES + 1))
        skill = SimpleNamespace(name="demo", skill_dir=skill_dir)
        monkeypatch.setattr(skills_storage, "get_or_new_skill_storage", lambda **_k: SimpleNamespace(load_skills=lambda: [skill]))

        class FakeAuditor:
            def audit_code(self, _code):
                return SimpleNamespace(is_safe=False, violations=["Prohibited module: subprocess"])

        monkeypatch.setattr(ast_audit, "get_skill_ast_auditor", lambda: FakeAuditor())
        findings = audit_mod._check_skill_ast_audit()
        assert {finding.rule_id for finding in findings} == {"skill.unsafe_python_ast", "skill.audit_truncated"}
        violation = next(f for f in findings if f.rule_id == "skill.unsafe_python_ast")
        assert violation.severity == "high"
        assert "demo" in violation.evidence and "bad.py" in violation.evidence
        truncated = next(f for f in findings if f.rule_id == "skill.audit_truncated")
        assert "files_oversize=1" in truncated.evidence

    def test_clean_skill_reports_nothing(self, tmp_path, monkeypatch):
        import alpha.skills.audit.ast_audit as ast_audit
        import alpha.skills.storage as skills_storage

        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "ok.py").write_text("VALUE = 1\n", encoding="utf-8")
        skill = SimpleNamespace(name="clean", skill_dir=skill_dir)
        monkeypatch.setattr(skills_storage, "get_or_new_skill_storage", lambda **_k: SimpleNamespace(load_skills=lambda: [skill]))

        class FakeAuditor:
            def audit_code(self, _code):
                return SimpleNamespace(is_safe=True, violations=[])

        monkeypatch.setattr(ast_audit, "get_skill_ast_auditor", lambda: FakeAuditor())
        assert audit_mod._check_skill_ast_audit() == []


class TestCli:
    def test_json_output_and_findings_exit(self, monkeypatch, capsys):
        canned = audit_mod.AuditReport(
            generated_at="2026-01-01T00:00:00+00:00",
            findings=[audit_mod.Finding(check="mcp_trust", rule_id="mcp.on_demand_runner", severity="low", message="m", evidence="e", remediation="r")],
            scanned=["mcp_trust"],
            not_checked=[audit_mod.NotChecked(check="skill_prompt_injection", reason="needs a model tier")],
        )
        monkeypatch.setattr(audit_mod, "run_security_audit", lambda **_k: canned)
        assert audit_mod.main(["--json"]) == audit_mod.EXIT_FINDINGS
        payload = json.loads(capsys.readouterr().out)
        assert payload["audit"] == audit_mod.AUDIT_NAME
        assert "score" not in json.dumps(payload)

    def test_clean_run_exits_zero_and_states_no_score(self, monkeypatch, capsys):
        canned = audit_mod.AuditReport(generated_at="t", findings=[], scanned=["mcp_trust"], not_checked=[])
        monkeypatch.setattr(audit_mod, "run_security_audit", lambda **_k: canned)
        assert audit_mod.main([]) == audit_mod.EXIT_CLEAN
        assert "No aggregate score is computed" in capsys.readouterr().out

    def test_nonpositive_bounds_are_refused_with_exit_two(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            audit_mod.main(["--max-threads", "0"])
        assert excinfo.value.code == 2
        with pytest.raises(SystemExit) as excinfo:
            audit_mod.main(["--max-messages", "not-a-number"])
        assert excinfo.value.code == 2


class TestHandlerBinding:
    def test_handle_security_audit_returns_report(self, monkeypatch):
        from alpha.commands.backend_handlers import handle_security_audit

        canned = audit_mod.AuditReport(generated_at="t", findings=[], scanned=["mcp_trust"], not_checked=[])
        monkeypatch.setattr(audit_mod, "run_security_audit", lambda **_k: canned)
        monkeypatch.setattr(audit_mod, "render_text", lambda _r: "TEXT")
        result = handle_security_audit("")
        assert result.status == "success"
        assert result.command == "/security audit"
        assert result.output == "TEXT"
        assert result.data["report"]["audit"] == audit_mod.AUDIT_NAME

    def test_command_is_registered_with_a_handler(self):
        from alpha.commands.backend_handlers import register_all_backend_handlers
        from alpha.commands.registry import command_registry

        register_all_backend_handlers()
        assert command_registry.get("/security audit") is not None
        assert command_registry.has_handler("/security audit")


class TestRoute:
    def test_route_is_declared_on_the_ops_router(self):
        from app.gateway.routers.ops import router

        declared = {(route.path, frozenset(route.methods or [])) for route in router.routes}
        assert ("/api/security/audit", frozenset({"POST"})) in declared

    def test_member_is_forbidden_before_the_audit_runs(self, monkeypatch):
        from fastapi import HTTPException

        from app.gateway.routers.ops import ops_security_audit

        def explode(**_kwargs):
            raise AssertionError("the audit must not run for a non-admin")

        monkeypatch.setattr(audit_mod, "run_security_audit", explode)
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="member")))
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(ops_security_audit(request, None))
        assert excinfo.value.status_code == 403

    def test_admin_success_runs_the_audit_off_loop(self, monkeypatch):
        import app.gateway.deps as deps
        from app.gateway.routers import ops as ops_router

        async def allow(_request, *, detail):
            return None

        monkeypatch.setattr(deps, "require_admin_user", allow)
        seen = {}

        def fake_audit(*, thread_ids, max_threads, max_messages):
            seen.update(thread_ids=thread_ids, max_threads=max_threads, max_messages=max_messages)
            return audit_mod.AuditReport(generated_at="t", findings=[], scanned=["mcp_trust"], not_checked=[])

        monkeypatch.setattr(audit_mod, "run_security_audit", fake_audit)
        request = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(system_role="admin")))
        body = ops_router.SecurityAuditRequest(thread_ids=["t1"], max_threads=3, max_messages_per_thread=7)
        payload = asyncio.run(ops_router.ops_security_audit(request, body))
        assert payload["audit"] == audit_mod.AUDIT_NAME
        assert seen == {"thread_ids": ["t1"], "max_threads": 3, "max_messages": 7}

    def test_request_bounds_are_refused(self):
        from app.gateway.routers.ops import SecurityAuditRequest

        with pytest.raises(ValidationError):
            SecurityAuditRequest(max_threads=0)
        with pytest.raises(ValidationError):
            SecurityAuditRequest(max_threads=201)
        with pytest.raises(ValidationError):
            SecurityAuditRequest(max_messages_per_thread=1001)
        defaults = SecurityAuditRequest()
        assert defaults.max_threads == 10
        assert defaults.max_messages_per_thread == 50
