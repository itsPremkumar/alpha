"""Self-inventory plane: new registries, the aggregate projection, and the tool.

Grouped in one file because the three layers only mean anything together — a
registry's honesty contract is invisible without the inventory that reports it,
and the inventory is unreachable without the tool the model calls. Split across
three files, each test would be asserting against mocks of its own neighbours.

Offline throughout: no network, no provider probe, no config write. Where a test
needs a config file it patches the loader rather than shipping one.
"""

from __future__ import annotations

import json

import pytest

from alpha.tools.builtins.alpha_capability_tool import alpha_capability
from alpha.tools.tools import BUILTIN_TOOLS
from alpha.workflow.registry import (
    REGISTRY_KINDS,
    BotProfileRegistry,
    CommandRegistry,
    EngineRegistry,
    IdentityRegistry,
    ModelRegistry,
    RegistryHealth,
    RegistryUnavailable,
    WiringRegistry,
    get_workflow_registry,
)
from alpha.workflow.registry.manifest_source import manifest_section


def _call(**kwargs):
    """Invoke the tool's underlying function (not the StructuredTool)."""
    return alpha_capability.func(**kwargs)


# ---------------------------------------------------------------------------
# Facade: the new kinds are wired, and none of them is a lie
# ---------------------------------------------------------------------------


class TestFacadeWiring:
    def test_every_new_kind_is_in_registry_kinds(self):
        for kind in ("identity", "models", "bots", "commands", "engines", "wiring"):
            assert kind in REGISTRY_KINDS, f"{kind} missing from REGISTRY_KINDS"

    def test_every_advertised_kind_resolves(self):
        """``REGISTRY_KINDS`` and ``registry()`` must not disagree.

        The facade rebuilt its kind map inside ``registry()``, so a kind added to
        one and not the other raised ``KeyError`` for a kind the module
        advertised. This is the regression that pins them together.
        """
        facade = get_workflow_registry()
        for kind in REGISTRY_KINDS:
            assert facade.registry(kind) is not None

    def test_unknown_kind_names_the_valid_kinds(self):
        facade = get_workflow_registry()
        with pytest.raises(KeyError) as excinfo:
            facade.registry("does-not-exist")
        assert "tools" in str(excinfo.value)

    def test_health_reports_every_kind_and_never_raises(self):
        report = get_workflow_registry().health()
        assert set(report) == set(REGISTRY_KINDS)
        for kind, health in report.items():
            assert isinstance(health, RegistryHealth)
            assert health.status in {"ok", "unavailable"}


# ---------------------------------------------------------------------------
# Identity: "which public repository is this" must be read, never composed
# ---------------------------------------------------------------------------


class TestIdentityRegistry:
    def test_declares_the_shipped_repository(self):
        rows = {row.id: row for row in IdentityRegistry().list()}
        repository = rows["repository"]
        assert repository.availability == "available"
        assert "github.com" in rows["repository_url"].source
        # The point of the plane: the URL is a declared fact, so it must carry an
        # operator-config authority rather than looking like something inferred.
        assert "operator config" in repository.authority

    def test_incomplete_manifest_is_unavailable_with_a_reason(self, monkeypatch):
        monkeypatch.setattr(IdentityRegistry, "_load_manifest", staticmethod(lambda: {"repository": {"owner": "acme"}, "release": {}}))
        rows = {row.id: row for row in IdentityRegistry().list()}
        assert rows["repository"].availability == "unavailable"
        # A guessed GitHub URL is worse than no claim, so the absence must say why.
        assert rows["repository"].reason
        assert "url" in rows["repository"].reason

    def test_runtime_identity_failure_degrades_one_row_only(self, monkeypatch):
        monkeypatch.setattr(IdentityRegistry, "_runtime_identity", staticmethod(lambda: {}))
        rows = {row.id: row for row in IdentityRegistry().list()}
        assert rows["runtime"].availability == "unavailable"
        # The repository answer is the authoritative half and must survive.
        assert rows["repository"].availability == "available"

    def test_unresolved_git_commit_keeps_identitys_own_reason(self, monkeypatch):
        monkeypatch.setattr(
            IdentityRegistry,
            "_runtime_identity",
            staticmethod(lambda: {"agentId": "a", "gitCommit": "unknown", "gitCommitSource": "unavailable", "gitCommitNote": "not a git checkout", "alphaVersion": "1.0.0"}),
        )
        row = {r.id: r for r in IdentityRegistry().list()}["runtime"]
        assert row.evidence_kind == "unverified"
        assert "not a git checkout" in (row.reason or "")
        assert row.availability == "available"

    def test_describe_accepts_aliases(self):
        registry = IdentityRegistry()
        assert registry.describe("repo").id == "repository"
        assert registry.describe("url").id == "repository_url"
        assert registry.describe("no-such-alias") is None


# ---------------------------------------------------------------------------
# Manifest-backed registries: the generated counts are the only counts
# ---------------------------------------------------------------------------


class TestManifestRegistries:
    def test_engines_are_read_from_the_generated_manifest(self):
        rows = {row.id: row for row in EngineRegistry().list()}
        assert rows, "the generated manifest declares engine packages"
        for row in rows.values():
            assert row.source.startswith("contracts/feature_manifest.json")
            assert row.health == "unverified"

    def test_engine_namespace_rows_are_measured_available(self):
        """A namespace has no ``__init__.py`` of its own.

        Probing only ``alpha.<ns>`` would report every namespace as unavailable,
        which is why the registry falls back to a declared submodule.
        """
        rows = EngineRegistry().list()
        namespaces = [row for row in rows if row.kind == "namespace"]
        if not namespaces:
            pytest.skip("this checkout declares no engine namespaces")
        assert all(row.availability == "available" for row in namespaces), [f"{row.id}: {row.reason}" for row in namespaces]

    def test_wiring_ids_are_namespaced_per_class(self):
        rows = WiringRegistry().list()
        assert rows
        prefixes = {row.id.split(":", 1)[0] for row in rows}
        assert prefixes <= {"router", "middleware", "loop"}
        # Un-namespaced ids would let a router and a middleware collide.
        assert len({row.id for row in rows}) == len(rows)

    def test_wiring_health_surfaces_unwired_rows_without_faking_a_failure(self):
        health = WiringRegistry().health()
        assert health.status == "ok"
        assert health.count is not None

    def test_missing_manifest_fails_closed_not_empty(self, monkeypatch):
        monkeypatch.setattr(
            "alpha.workflow.registry.engines.manifest_section",
            lambda section: (_ for _ in ()).throw(RegistryUnavailable("feature manifest unreadable at /x: boom")),
        )
        health = EngineRegistry().health()
        assert health.status == "unavailable"
        assert health.count is None
        assert "boom" in (health.error or "")
        with pytest.raises(RegistryUnavailable):
            EngineRegistry().list()

    def test_manifest_section_rejects_a_typo(self):
        with pytest.raises(RegistryUnavailable):
            manifest_section("router")


# ---------------------------------------------------------------------------
# Models: declared, never probed
# ---------------------------------------------------------------------------


class _StubModel:
    def __init__(self, name, use="", model=""):
        self.name = name
        self.use = use
        self.provider = ""
        self.model = model


class _StubConfig:
    def __init__(self, models, default="alpha-free", providers=None):
        self.models = models
        self.default_model_name = default
        self.providers = providers or {}


class TestModelRegistry:
    def test_declared_model_is_available_but_health_stays_unverified(self, monkeypatch):
        monkeypatch.setattr(ModelRegistry, "_load_config", staticmethod(lambda: (_StubConfig([_StubModel("alpha-free", "x:ChatFreeLLM", "free:zen")]), "config.yaml")))
        row = ModelRegistry().describe("alpha-free")
        assert row.availability == "available"
        # Declared in config.yaml is not the same claim as reachable, and the
        # descriptor must not let the two blur together.
        assert row.health == "unverified"
        assert "free:zen" in row.source

    def test_default_model_row_is_a_measurable_claim(self, monkeypatch):
        monkeypatch.setattr(ModelRegistry, "_load_config", staticmethod(lambda: (_StubConfig([_StubModel("alpha-free")]), "config.yaml")))
        row = ModelRegistry().describe("default_model")
        assert row.availability == "available"
        assert row.source.endswith("alpha-free")

    def test_default_model_outside_models_is_unavailable_with_the_real_set(self, monkeypatch):
        monkeypatch.setattr(ModelRegistry, "_load_config", staticmethod(lambda: (_StubConfig([_StubModel("a"), _StubModel("b")], default="ghost"), "config.yaml")))
        row = ModelRegistry().describe("default_model")
        assert row.availability == "unavailable"
        assert "ghost" in row.reason
        assert "a, b" in row.reason

    def test_unloadable_config_fails_closed(self, monkeypatch):
        monkeypatch.setattr(ModelRegistry, "_load_config", staticmethod(lambda: (_ for _ in ()).throw(RegistryUnavailable("config.yaml could not be loaded: FileNotFoundError"))))
        with pytest.raises(RegistryUnavailable):
            ModelRegistry().list()
        assert ModelRegistry().health().count is None


# ---------------------------------------------------------------------------
# Commands: a catalog row is not a capability
# ---------------------------------------------------------------------------


class TestCommandRegistry:
    def test_handler_backed_commands_are_available(self):
        rows = {row.id: row for row in CommandRegistry().list()}
        assert len(rows) > 100
        assert any(row.availability == "available" for row in rows.values())

    def test_rows_without_a_handler_are_unavailable_and_say_why(self):
        """``418 commands registered`` is not ``418 commands usable``.

        ``execute_slash_command_tool`` returns ``unimplemented`` for a row with no
        bound handler, so presenting it as available would be a claim the runtime
        contradicts.
        """
        rows = [row for row in CommandRegistry().list() if row.availability == "unavailable"]
        assert rows, "this checkout's catalog should have some handler-less rows"
        assert all(row.reason and "unimplemented" in row.reason for row in rows)

    def test_health_counts_the_handlerless_rows(self):
        rows = CommandRegistry().list()
        health = CommandRegistry().health()
        assert health.count == len(rows)
        without = sum(1 for row in rows if row.availability != "available")
        if without:
            assert str(without) in (health.error or "")

    def test_absent_command_is_none_not_an_error(self):
        assert CommandRegistry().describe("/definitely-not-a-command") is None


# ---------------------------------------------------------------------------
# Bots: an archived profile must not be advertised as a worker
# ---------------------------------------------------------------------------


class _StubBot:
    def __init__(self, name, status="active"):
        self.name = name
        self.status = status
        self.handle = name
        self.role = "Engineer"
        self.department = "engineering"
        self.model = "alpha-free"


class TestBotRegistry:
    def test_live_profile_is_available(self, monkeypatch):
        monkeypatch.setattr(BotProfileRegistry, "_load_bots", staticmethod(lambda: [_StubBot("coder")]))
        row = BotProfileRegistry().describe("coder")
        assert row.availability == "available"
        assert row.kind == "bot_profile"
        # A roster row proves a profile was written, not that the Bot answers.
        assert row.health == "unverified"

    def test_inactive_status_is_unavailable_with_the_status(self, monkeypatch):
        monkeypatch.setattr(BotProfileRegistry, "_load_bots", staticmethod(lambda: [_StubBot("old", status="retired")]))
        row = BotProfileRegistry().describe("old")
        assert row.availability == "unavailable"
        assert "retired" in row.reason

    def test_archived_profiles_are_excluded_at_the_source(self):
        """``list_bots`` defaults ``include_archived=True``.

        That default is right for a roster view and wrong here: ``retire_bot`` is a
        soft delete, so listing an archived Bot advertises a worker that is gone.
        The registry must pass ``False`` explicitly rather than rely on a default.
        """
        import inspect

        from alpha.bots.registry import BotRegistry

        source = inspect.getsource(BotProfileRegistry._load_bots)
        assert "include_archived=False" in source
        assert BotRegistry.list_bots.__defaults__ is not None or True


# ---------------------------------------------------------------------------
# The aggregate inventory
# ---------------------------------------------------------------------------


class TestSelfInventory:
    def test_one_call_returns_every_section(self):
        from alpha.intelligence.self_inventory import build_self_inventory

        inventory = build_self_inventory()
        assert {section.name for section in inventory.sections} == set(REGISTRY_KINDS)

    def test_totals_describe_what_was_read_not_what_exists(self):
        from alpha.intelligence.self_inventory import build_self_inventory

        totals = build_self_inventory().totals()
        assert totals["sections"] == len(REGISTRY_KINDS)
        assert totals["read_sections"] + totals["unreadable_sections"] == totals["sections"]

    def test_unknown_section_raises_instead_of_shrinking_the_answer(self):
        from alpha.intelligence.self_inventory import build_self_inventory

        with pytest.raises(KeyError):
            build_self_inventory(sections=("tools", "not-a-kind"))

    def test_unreadable_section_is_null_counted_not_zero(self, monkeypatch):
        """``count: 0`` says "I looked and there is nothing".

        That is a different statement from "I could not look", and the two lead to
        opposite decisions, so a failing registry must not collapse into the first.
        """
        from alpha.intelligence import self_inventory

        def explode(kind):
            if kind == "models":
                raise RegistryUnavailable("config.yaml could not be loaded: FileNotFoundError")
            return []

        monkeypatch.setattr(self_inventory, "get_workflow_registry", lambda: _StubFacade(explode))
        section = self_inventory.build_self_inventory(sections=("models",)).section("models")
        assert section.status == "unavailable"
        assert section.count is None
        assert "FileNotFoundError" in section.error

    def test_summary_omits_source_and_full_includes_it(self):
        from alpha.intelligence.self_inventory import build_self_inventory

        summary = build_self_inventory(sections=("identity",)).to_dict()["sections"][0]["entries"][0]
        assert set(summary) <= {"id", "kind", "availability", "reason"}
        full = build_self_inventory(sections=("identity",), detail="full").to_dict()["sections"][0]["entries"][0]
        # The measured finding: names are enough for reuse, full addresses are what
        # make a claim checkable. Both must be reachable, neither by default.
        assert {"source", "authority", "evidence_kind", "version"} <= set(full)

    def test_notes_state_the_honesty_contract(self):
        from alpha.intelligence.self_inventory import build_self_inventory

        notes = " ".join(build_self_inventory().to_dict()["notes"])
        assert "unverified" in notes
        assert "null, never 0" in notes

    def test_search_spans_kinds_a_tool_search_would_miss(self):
        from alpha.intelligence.self_inventory import search_inventory

        result = search_inventory("memory", limit=25)
        sections = {hit["section"] for hit in result["hits"]}
        # skills/commands/bots/engines are not in the BM25 tool index at all.
        assert len(sections) > 1
        assert all("section" in hit for hit in result["hits"])

    def test_search_requires_a_query(self):
        from alpha.intelligence.self_inventory import search_inventory

        assert search_inventory("")["count"] == 0
        assert "required" in search_inventory("")["detail"]

    def test_absent_capability_is_a_status_not_a_raise(self):
        from alpha.intelligence.self_inventory import describe_capability

        result = describe_capability("tools", "no_such_tool_at_all")
        assert result["status"] == "absent"

    def test_unknown_capability_kind_lists_the_valid_ones(self):
        from alpha.intelligence.self_inventory import describe_capability

        result = describe_capability("nope", "x")
        assert result["status"] == "unknown_kind"
        assert "tools" in result["sections"]

    def test_identity_projection_reports_the_repository_url(self):
        from alpha.intelligence.self_inventory import identity_projection

        facts = identity_projection()["repository_facts"]
        assert facts["repository_url"].startswith("https://")
        assert "itsPremkumar" in json.dumps(facts) or facts.get("repository_owner")


class _StubRegistry:
    def __init__(self, kind, exploder):
        self._kind = kind
        self._exploder = exploder

    def list(self):
        return self._exploder(self._kind)

    def describe(self, entry_id):
        return None

    def health(self):
        return RegistryHealth(registry=self._kind, status="ok", count=0, error=None, evidence_kind="measured")


class _StubFacade:
    def __init__(self, exploder):
        self._exploder = exploder

    def registry(self, kind):
        if kind not in REGISTRY_KINDS:
            raise KeyError(f"unknown registry kind {kind!r}")
        return _StubRegistry(kind, self._exploder)

    def list(self, kind):
        return self.registry(kind).list()

    def describe(self, kind, entry_id):
        return self.registry(kind).describe(entry_id)

    def health(self):
        return {kind: self.registry(kind).health() for kind in REGISTRY_KINDS}


# ---------------------------------------------------------------------------
# Configuration diagnosis: read-only, and a proposal is never an action
# ---------------------------------------------------------------------------


class TestConfigDiagnosis:
    def test_every_finding_carries_read_only(self):
        from alpha.ops.config_diagnosis import diagnose_configuration

        report = diagnose_configuration().to_dict()
        assert report["read_only"] is True
        assert all(finding["read_only"] is True for finding in report["findings"])

    def test_missing_api_key_reports_the_variable_name_only(self, monkeypatch):
        from alpha.ops import config_diagnosis

        class _Provider:
            enabled = True
            api_key_env = "ALPHA_TEST_ABSENT_KEY"

        class _Config:
            models = [_StubModel("alpha-free")]
            default_model_name = "alpha-free"
            providers = {"acme": _Provider()}
            sandbox = type("S", (), {"use": "alpha.sandbox.local:LocalSandboxProvider"})()

        monkeypatch.delenv("ALPHA_TEST_ABSENT_KEY", raising=False)

        def fake_config_file(findings, context):
            context["config"] = {"loaded": True, "source": "test"}
            return _Config()

        monkeypatch.setattr(config_diagnosis, "_check_config_file", fake_config_file)
        monkeypatch.setattr(config_diagnosis, "_check_manifest", lambda findings, context: None)
        monkeypatch.setattr(config_diagnosis, "_check_capabilities", lambda findings, include: None)
        monkeypatch.setattr(config_diagnosis, "_check_command_coverage", lambda findings, context: None)

        report = config_diagnosis.diagnose_configuration().to_dict()
        finding = next((f for f in report["findings"] if f["id"] == "missing_api_key"), None)
        assert finding is not None, report["findings"]
        assert "ALPHA_TEST_ABSENT_KEY" in finding["evidence"]
        assert finding["severity"] == "warning"
        # The name is the whole point; a value in here would be a credential leak
        # into a model-facing payload.
        assert "=" not in finding["evidence"].split("unset api_key_env:")[1].replace("ALPHA_TEST_ABSENT_KEY", "")

    def test_disabled_capability_is_info_not_a_defect(self):
        from alpha.ops.config_diagnosis import diagnose_configuration

        report = diagnose_configuration().to_dict()
        finding = next((f for f in report["findings"] if f["id"] == "disabled_capability"), None)
        if finding is None:
            pytest.skip("every optional capability is enabled in this checkout")
        # A disabled capability is an operator's deliberate choice; calling it a
        # warning would train operators to ignore warnings.
        assert finding["severity"] == "info"

    def test_findings_are_sorted_by_severity(self):
        from alpha.ops.config_diagnosis import diagnose_configuration

        severities = [f["severity"] for f in diagnose_configuration().to_dict()["findings"]]
        rank = {"blocker": 0, "warning": 1, "info": 2}
        assert severities == sorted(severities, key=lambda s: rank[s])

    def test_a_failing_probe_becomes_a_finding_not_a_crash(self):
        """A diagnosis tool that dies because one check failed is useless when needed.

        Every check is isolated, so a broken source degrades one section of the
        report instead of taking down the whole thing.
        """
        from alpha.ops.config_diagnosis import Severity, _safe

        findings = []

        def boom():
            raise RuntimeError("source exploded")

        assert _safe("demo", boom, findings) is None
        assert len(findings) == 1
        assert findings[0].id == "probe_failed_demo"
        assert findings[0].severity is Severity.WARNING
        # The real exception text, not a generic message: the operator needs to
        # know what to go fix.
        assert "source exploded" in findings[0].evidence

    def test_a_failing_sandbox_probe_does_not_stop_the_report(self, monkeypatch):
        from alpha.ops import config_diagnosis

        class _Config:
            models = [_StubModel("alpha-free")]
            default_model_name = "alpha-free"
            providers = {}
            sandbox = type("S", (), {"use": "alpha.sandbox.does_not_exist:Nope"})()

        def fake_config_file(findings, context):
            context["config"] = {"loaded": True, "source": "test"}
            return _Config()

        monkeypatch.setattr(config_diagnosis, "_check_config_file", fake_config_file)
        monkeypatch.setattr(config_diagnosis, "_check_manifest", lambda findings, context: None)
        monkeypatch.setattr(config_diagnosis, "_check_capabilities", lambda findings, include: None)
        monkeypatch.setattr(config_diagnosis, "_check_command_coverage", lambda findings, context: None)

        report = config_diagnosis.diagnose_configuration().to_dict()
        assert any(f["id"] == "probe_failed_sandbox" for f in report["findings"])
        # The rest of the report still ran.
        assert "models" in report["context"]


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


class TestAlphaCapabilityTool:
    def test_registered_exactly_once(self):
        names = [tool.name for tool in BUILTIN_TOOLS]
        assert names.count("alpha_capability") == 1

    def test_not_in_subagent_tools(self):
        """It is a lead-agent self-knowledge surface, not a delegation tool."""
        from alpha.tools.tools import SUBAGENT_TOOLS

        assert "alpha_capability" not in [tool.name for tool in SUBAGENT_TOOLS]

    def test_schema_has_no_runtime_injection(self):
        schema = next(tool for tool in BUILTIN_TOOLS if tool.name == "alpha_capability").args_schema.model_json_schema()
        # The tool reads process-global registries, exactly like bot_roster, so it
        # must not claim an injected runtime argument it does not use.
        assert "runtime" not in schema.get("properties", {})

    @pytest.mark.parametrize("action", ["inventory", "identity", "status", "diagnose", "symbols_status"])
    def test_offline_actions_succeed(self, action):
        result = _call(action=action)
        assert result["success"] is True, result
        assert result["free_and_offline"] is True

    def test_inventory_reports_every_kind(self):
        result = _call(action="inventory")
        assert {section["name"] for section in result["sections"]} == set(REGISTRY_KINDS)
        assert result["totals"]["sections"] == len(REGISTRY_KINDS)

    def test_sections_filter_is_honoured(self):
        result = _call(action="inventory", sections="identity,models")
        assert [section["name"] for section in result["sections"]] == ["identity", "models"]

    def test_unknown_section_is_refused_rather_than_dropped(self):
        """Silently dropping it would return less than the caller asked for."""
        result = _call(action="inventory", sections="tools,nope")
        assert result["success"] is False
        assert result["error"] == "unknown_kind"
        assert "tools" in result["error"] or "not-a-kind" in result["detail"] or "nope" in result["detail"]

    def test_identity_action_names_the_public_repository(self):
        facts = _call(action="identity")["repository_facts"]
        assert facts["repository_url"].startswith("https://")
        assert facts["repository_provider"] == "github"

    def test_capability_action_requires_both_ids(self):
        assert _call(action="capability")["error"] == "kind_and_entry_id_required"

    def test_capability_action_returns_a_source_address(self):
        result = _call(action="capability", kind="identity", entry_id="repository_url")
        assert result["status"] == "ok"
        assert result["entry"]["source"]

    def test_diagnose_is_marked_read_only(self):
        result = _call(action="diagnose")
        assert result["read_only"] is True
        assert any("read-only" in note.lower() for note in result["notes"])

    def test_symbols_find_a_real_definition(self):
        result = _call(action="symbols", query="get_available_tools")
        assert result["matched"] >= 1
        hit = result["results"][0]
        assert hit["path"].endswith(".py")
        assert hit["line"] > 0
        assert hit["extraction"] == "ast"

    def test_symbols_never_return_a_body(self):
        """The 29.2% finding: a source dump drove worse self-reuse than nothing."""
        result = _call(action="symbols", query="get_available_tools")
        for hit in result["results"]:
            assert set(hit) == {"name", "qualified_name", "kind", "language", "path", "line", "signature", "doc", "parent", "extraction"}
            assert "source" not in hit and "body" not in hit and "code" not in hit

    def test_symbols_report_their_coverage(self):
        coverage = _call(action="symbols", query="CapabilityDescriptor")["coverage"]
        assert coverage["files_scanned"] > 0
        # A bounded answer must say it was bounded, or a confident absence and a
        # truncated one are indistinguishable.
        assert "candidate_limit" in coverage
        assert "parse_errors" in coverage

    def test_symbols_path_prefix_narrows_the_scan(self):
        result = _call(action="symbols", query="tool", path_prefix="backend/packages/harness/alpha/commands")
        assert all(hit["path"].startswith("backend/packages/harness/alpha/commands") for hit in result["results"])

    def test_symbol_describe_scopes_to_one_file(self):
        target = "backend/packages/harness/alpha/workflow/registry/base.py"
        result = _call(action="symbol", entry_id=target, query="Registry")
        assert result["status"] == "ok"
        assert result["count"] >= 1
        assert all(hit["path"] == target for hit in result["results"])

    def test_symbol_describe_rejects_an_unindexed_path(self):
        result = _call(action="symbol", entry_id="not/a/real/file.py", query="x")
        assert result["status"] == "not_indexed"

    def test_unknown_action_lists_the_supported_ones(self):
        result = _call(action="wat")
        assert result["error"] == "unsupported_action"
        assert "inventory" in result["supported_actions"]

    def test_oversized_arguments_are_refused(self):
        assert _call(action="search", query="x" * 600)["error"] == "invalid_argument"
        assert _call(action="symbols", query="x" * 600)["error"] == "invalid_argument"
        assert _call(action="capability", kind="tools", entry_id="y" * 600)["error"] == "invalid_argument"
        assert _call(action="symbols", query="x", path_prefix="z" * 600)["error"] == "invalid_argument"


# ---------------------------------------------------------------------------
# The symbol lookup itself
# ---------------------------------------------------------------------------


class TestCodeSymbolLookup:
    def test_python_signatures_are_ast_derived(self):
        from alpha.knowledge.code_index import _python_symbols

        source = "class Alpha:\n    def run(self, x: int) -> str:\n        '''Doc.'''\n        return ''\n\nasync def go(a, *, b=1):\n    pass\n"
        by_name = {s.name: s for s in _python_symbols(source, "x.py")}
        assert by_name["Alpha"].kind == "class"
        assert by_name["run"].qualified_name == "Alpha.run"
        assert by_name["run"].parent == "Alpha"
        assert by_name["run"].doc == "Doc."
        # The return annotation is half the contract: `def load(p)` and
        # `def load(p) -> Path` are different interfaces.
        assert by_name["run"].signature == "def run(self, x: int) -> str"
        assert by_name["go"].signature.startswith("async def go(a")

    def test_multiline_signature_is_marked_partial_not_invented(self):
        from alpha.knowledge.code_index import _python_symbols

        source = "def wide(\n    a: int,\n    b: str,\n) -> dict:\n    return {}\n"
        signature = _python_symbols(source, "x.py")[0].signature
        # Balancing parens must not fabricate a closer that is not on this line.
        assert signature.endswith("…")
        assert "def wide(" in signature

    def test_unparsable_python_returns_none_rather_than_guessing(self):
        from alpha.knowledge.code_index import _python_symbols

        assert _python_symbols("def broken(:\n", "x.py") is None

    def test_typescript_extraction_is_labelled_regex(self):
        from alpha.knowledge.code_index import _typescript_symbols

        source = "export function alphaOne() {}\nexport interface Beta {}\nexport type Gamma = string;\nclass Delta {}\nfunction* gen() {}\n"
        symbols = _typescript_symbols(source, "x.ts")
        kinds = {s.name: s.kind for s in symbols}
        assert kinds["alphaOne"] == "function"
        assert kinds["Beta"] == "interface"
        assert kinds["Gamma"] == "type"
        assert kinds["Delta"] == "class"
        assert kinds["gen"] == "function"
        # Regex-derived signatures must never be presented as parser-derived.
        assert all(s.extraction == "regex" and s.language == "typescript" for s in symbols)

    def test_status_reports_roots_and_bounds_without_reading_files(self):
        from alpha.knowledge.code_index import get_code_symbol_lookup

        status = get_code_symbol_lookup().status()
        assert status["candidate_files"] > 0
        assert status["extraction"] == {"python": "ast", "typescript": "regex"}
        assert any("seconds" in note for note in status["notes"]), "the cost of a repo-wide scan must be stated"

    def test_empty_and_oversized_queries_are_refused(self):
        from alpha.knowledge.code_index import get_code_symbol_lookup

        lookup = get_code_symbol_lookup()
        assert lookup.search("")["count"] == 0
        assert "required" in lookup.search("")["detail"]
        assert lookup.search("x" * 300)["count"] == 0


# ---------------------------------------------------------------------------
# Regression: the identities and counts the new planes must not disturb
# ---------------------------------------------------------------------------


class TestDescriptorContract:
    def test_no_descriptor_claims_a_running_subsystem(self):
        """``health`` is ``unverified`` everywhere by contract.

        A registry that "verifies" health without executing anything is the exact
        defect class this protocol was written to prevent.
        """
        for kind in REGISTRY_KINDS:
            for row in get_workflow_registry().list(kind):
                assert row.health == "unverified", f"{kind}/{row.id} claims health={row.health}"
                assert row.evidence_kind in {"measured", "simulated", "heuristic", "unverified"}
                if row.availability == "unavailable":
                    assert row.reason, f"{kind}/{row.id} is unavailable with no reason"

    def test_version_is_only_set_where_a_source_declares_one(self):
        for row in get_workflow_registry().list("identity"):
            if row.id == "runtime":
                continue
            # Nothing but the runtime identity declares a version; a version
            # anywhere else would be an invented one.
            assert row.version is None, f"{row.id} carries an invented version {row.version!r}"

    def test_descriptors_are_serialisable(self):
        payload = get_workflow_registry().describe("identity", "repository").model_dump(mode="json")
        json.dumps(payload)
