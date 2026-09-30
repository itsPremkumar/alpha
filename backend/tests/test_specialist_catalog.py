"""`specialists:` is a leader-authored catalogue the rest of Alpha can schedule.

Every assertion here is about a guard that must BITE, so each test breaks the
thing it guards and shows the failure rather than asserting on a happy path
that a future edit could quietly stop exercising.

What is pinned, and why each guard exists:

* the catalogue loads through the **real** loader (``AppConfig.from_file`` on
  the shipped ``config.example.yaml``), not a hand-built model;
* a malformed entry fails **at load** and the message **names the field**;
* a specialist whose ``description`` is thinner than the forge's own
  thin-query floor is refused **at load**, because a description that thin
  scores 1.0 against anything sharing one word and therefore silently
  protects nothing;
* two specialists the forge's ``check_overlap`` would call the same job are
  refused **at load**, because the second Bot would be refused at build time
  anyway;
* the shipped default team actually **survives** ``forge.check_overlap`` in
  both directions for all 15 pairs — the proof the default team is not a
  token set of two overlapping entries;
* the routing vocabulary mirrors the routers, and a routing slot is never a
  model name;
* a specialist cannot smuggle an all-tools role past the permission rings;
* the projections land on real ``forge_bot`` / ``.alphabot.json`` surfaces
  rather than on a shape this module invented.

The honest limit, stated here because the tests cannot state it: a specialist
is **data**. It does not mint a Bot, does not schedule a run, and does not
widen anybody's authority. Tests
``test_a_specialist_grants_nothing_by_itself`` and
``test_the_catalogue_is_reachable_from_the_real_loader`` hold the line between
"declarable" and "does something".
"""

from __future__ import annotations

import ast
import copy
import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alpha.bots.forge import (
    DEFAULT_APPROVALS,
    SANDBOX_BACKENDS,
    build_guardrail_soul,
    check_overlap,
    forge_bot,
)
from alpha.bots.permissions import DEFAULT_ROLE_RINGS, ToolPermissionGate
from alpha.bots.portable import TEMPLATE_FIELDS
from alpha.bots.survey import MIN_OVERLAP_TERMS, score_overlap, significant_terms
from alpha.config.app_config import AppConfig
from alpha.config.specialist_config import (
    APPROVAL_POSTURES,
    ROUTING_CATEGORIES,
    ROUTING_TIERS,
    SELECTION_OVERLAP_THRESHOLD,
    SpecialistCatalogConfig,
    SpecialistConfig,
    get_specialist_catalog,
    known_role_rings,
    resolve_role_ring,
)

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

DEFAULT_TEAM = (
    "researcher",
    "code-reviewer",
    "security-reviewer",
    "test-engineer",
    "technical-writer",
    "operations-engineer",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_entry(**overrides: Any) -> dict[str, Any]:
    """A specialist entry that must always pass, so each test can break one field."""
    entry: dict[str, Any] = {
        "name": "example-specialist",
        "role": "tester",
        "description": "Carries out an explicitly assigned verification pass over a named artefact and reports the measured outcome.",
        "not_for": ["authoring new product code", "changing a production configuration"],
        "capabilities": ["verification_pass"],
    }
    entry.update(overrides)
    return entry


def _second_entry(**overrides: Any) -> dict[str, Any]:
    """A second, non-overlapping entry, for tests that need a pair.

    Deliberately shares no territory with :func:`_minimal_entry`: same role,
    same prohibitions, entirely different work. If this started to collide,
    the duplicate guard in the tests around it would be measuring the fixture
    rather than the guard.
    """
    entry: dict[str, Any] = {
        "name": "example-indexer",
        "role": "architect",
        "description": "Catalogues the schemas and interfaces a codebase already exposes, and reports the shape of each one without changing it.",
        "not_for": ["rewriting the module being catalogued", "touching a production credential"],
        "capabilities": ["schema_catalogue"],
    }
    entry.update(overrides)
    return entry


def _load(**overrides: Any) -> AppConfig:
    """Load ``config.example.yaml`` through the real loader, then override.

    Going through ``AppConfig.from_file`` (rather than ``model_validate`` on a
    dict) is the point: the shipped template, the YAML parser, the ``$VAR``
    resolution and the singleton wiring all run, so a test that passes here is
    a statement about what an operator's boot actually does.
    """
    config = AppConfig.from_file(str(CONFIG_EXAMPLE))
    return config.model_copy(update=overrides) if overrides else config


def _base_document() -> dict[str, Any]:
    """The shipped ``config.example.yaml`` as a dict, via the real loader's parser."""
    import yaml

    return yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}


def _write_config(tmp_path: Path, document: dict[str, Any]) -> Path:
    """Write a candidate config and return its path, for ``AppConfig.from_file``."""
    import yaml

    target = tmp_path / "config.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target


def _expect_load_error(tmp_path: Path, **overrides: Any) -> str:
    """Assert loading a mutated config FAILS, and return the message.

    The mutation is written to a real file and loaded through
    ``AppConfig.from_file``, so the failure being observed is the one an
    operator would hit at boot - not a hand-built model that skips the YAML
    path, the ``$VAR`` resolution or the cross-section validators.
    """
    document = _base_document()
    document.update(overrides)
    path = _write_config(tmp_path, document)
    with pytest.raises(ValidationError) as excinfo:
        AppConfig.from_file(str(path))
    return str(excinfo.value)


def _expect_load_ok(tmp_path: Path, **overrides: Any) -> AppConfig:
    """Assert a mutated config LOADS, so a guard is not a blanket refusal."""
    document = _base_document()
    document.update(overrides)
    return AppConfig.from_file(str(_write_config(tmp_path, document)))


# ---------------------------------------------------------------------------
# The shipped catalogue
# ---------------------------------------------------------------------------


class TestShippedCatalogueLoads:
    def test_the_shipped_template_declares_a_team(self) -> None:
        config = _load()
        assert [entry.name for entry in config.specialists.entries] == list(DEFAULT_TEAM)

    def test_every_shipped_specialist_is_selectable(self) -> None:
        catalog = _load().specialists
        for name in DEFAULT_TEAM:
            entry = catalog.get(name)
            assert entry is not None, f"{name} missing from the shipped catalogue"
            assert entry.enabled, f"{name} is declared disabled in the shipped template"

    def test_the_shipped_team_has_no_duplicate_territories(self) -> None:
        """The actual bite proof for the default team, in both directions.

        Runs the forge's own ``check_overlap`` over every ordered pair of the
        six shipped specialists. If this ever returns a non-``None`` offender
        the loader would already be refusing the file, so the failure mode
        this guards against is subtler: a future description edit that pushes
        one pair over the threshold. It is asserted here so the *reason* is on
        record next to the scores.
        """
        entries = _load().specialists.entries
        measured: list[tuple[str, str, float]] = []
        for entry in entries:
            others = {other.name: other.territory() for other in entries if other.name != entry.name}
            assert others, "the default team must have at least two specialists for the pair check to mean anything"
            role, _, claim = entry.territory().partition("\n")
            offender, score = check_overlap(
                claim,
                role,
                existing=others,
                threshold=SELECTION_OVERLAP_THRESHOLD,
                name=entry.name,
            )
            measured.append((entry.name, offender or "-", score or 0.0))
            assert offender is None, (
                f"shipped specialist '{entry.name}' is refused by forge.check_overlap against '{offender}' (score {score:.2f} >= {SELECTION_OVERLAP_THRESHOLD}); the shipped default team must survive the forge's own rule"
            )
        # Reverse direction: the pair must be symmetric, or the refusal rule
        # would depend on declaration order.
        for entry in entries:
            others = {other.name: other.territory() for other in entries if other.name != entry.name}
            role, _, claim = entry.territory().partition("\n")
            for other in others:
                only = {other: others[other]}
                offender, _ = check_overlap(
                    claim,
                    role,
                    existing=only,
                    threshold=SELECTION_OVERLAP_THRESHOLD,
                    name=entry.name,
                )
                assert offender is None, f"'{entry.name}' is refused by forge.check_overlap against '{other}' considered alone"
        assert len(measured) == len(entries)

    def test_every_shipped_description_clears_the_thin_query_floor(self) -> None:
        """The other half of the forge's load-bearing rule.

        ``_overlap_score`` normalises against the query's own ceiling, so a
        one-word role scores 1.0 against any SOUL sharing the word. The forge
        answers that with a floor of 3 distinct significant terms. Every
        shipped description must clear it with room to spare, because a
        specialist's whole value is that its territory is *distinguishable*.
        """
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            terms = significant_terms(entry.declared_prose())
            assert len(terms) >= MIN_OVERLAP_TERMS, (
                f"'{name}' has {len(terms)} significant terms in role+description, below the forge's MIN_OVERLAP_TERMS of {MIN_OVERLAP_TERMS}; its territory would be indistinguishable from any neighbour sharing a word"
            )
            # Comfortably clear, not barely: a description sitting on the floor
            # is one edit away from being refused.
            assert len(terms) >= MIN_OVERLAP_TERMS * 3, f"'{name}' has only {len(terms)} significant terms and is one edit away from the floor"

    def test_every_not_for_entry_states_a_real_exclusion(self) -> None:
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            assert entry.not_for, f"'{name}' declares no negative scope"
            for exclusion in entry.not_for:
                assert len(significant_terms(exclusion)) >= 2, f"'{name}' exclusion '{exclusion}' is too thin to check"

    def test_the_shipped_roles_resolve_to_declared_permission_rings(self) -> None:
        rings = known_role_rings()
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            ring, reason = resolve_role_ring(entry.role)
            assert ring is not None, reason
            assert not rings[ring], f"'{name}' resolved to the all-tools ring '{ring}'; a worker specialist must not hold one"

    def test_the_shipped_team_holds_no_allow_all_ring(self) -> None:
        """Bite proof for the ring guard, on the shipped file.

        ``lead``/``supervisor``/``admin`` are ``allow_all`` rings, and
        ``_resolve_ring`` matches them EXACTLY, so a specialist holding one
        would receive every tool — including ``git_push`` and
        ``drop_database``. Every shipped entry must resolve to a worker ring
        whose allow-list the permission gate actually enforces.
        """
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            ring, reason = resolve_role_ring(entry.role)
            assert ring is not None, f"'{name}' resolves to no ring: {reason}"
            assert not known_role_rings()[ring], f"'{name}' holds the allow_all ring '{ring}'"
            # The gate agrees, and the ring is not a blanket allow.
            allowed, denial, _ = ToolPermissionGate().check_permission(entry.role, "drop_database")
            assert not allowed, f"'{name}' may drop the database (denial={denial!r})"

    def test_the_shipped_team_declares_a_routing_slot_not_a_model(self) -> None:
        """The mistake this repository already made once, pinned against.

        A specialist that named a vendor model id would reintroduce the
        ``DEFAULT_CATEGORY_SPECS`` failure: a routing decision naming a model
        no operator has. Every routing declaration must be a slot key.
        """
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None, f"{name} missing"
            if entry.routing is not None:
                for label in entry.routing.slot_labels():
                    kind, key = label.split(":", 1)
                    assert kind in {"category", "tier"}
                    assert key in (ROUTING_CATEGORIES if kind == "category" else ROUTING_TIERS)
        # And the shape itself has no field a model name could go in.
        assert "model" not in SpecialistConfig.model_fields
        assert "model_name" not in SpecialistConfig.model_fields

    def test_the_shipped_team_covers_six_distinct_capability_sets(self) -> None:
        """Distinct capabilities, not one template six times."""
        seen: list[frozenset[str]] = []
        for name in DEFAULT_TEAM:
            caps = frozenset(_load().specialists.get(name).capabilities)
            assert caps, f"'{name}' declares no capabilities"
            assert caps not in seen, f"'{name}' repeats another specialist's capability set: {sorted(caps)}"
            seen.append(caps)

    def test_config_example_parses_as_one_document_with_the_section(self) -> None:
        import yaml

        doc = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}
        assert isinstance(doc, dict)
        assert "specialists" in doc, "config.example.yaml must ship the specialists block it documents"
        assert isinstance(doc["specialists"], dict)
        assert isinstance(doc["specialists"].get("entries"), list)
        assert len(doc["specialists"]["entries"]) == len(DEFAULT_TEAM)


# ---------------------------------------------------------------------------
# Malformed entries fail at load, naming the field
# ---------------------------------------------------------------------------


class TestMalformedEntriesAreRefusedAtLoad:
    def test_an_unknown_field_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(typo_field="oops")]})
        assert "typo_field" in message
        assert "specialists" in message

    def test_a_missing_required_field_is_refused_and_named(self, tmp_path: Path) -> None:
        entry = _minimal_entry()
        del entry["description"]
        message = _expect_load_error(tmp_path, specialists={"entries": [entry]})
        assert "description" in message

    def test_a_bad_handle_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(name="Not A Handle")]})
        assert "name" in message
        assert "handle" in message.lower()

    @pytest.mark.parametrize("posture", ["sometimes", "DRAFT_FIRST", "", "autonomous-ish"])
    def test_an_unknown_approval_posture_is_refused_and_named(self, tmp_path: Path, posture: str) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(approvals=posture)]})
        assert "approvals" in message

    def test_an_unknown_department_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(department="wizardry")]})
        assert "department" in message

    def test_an_unknown_sandbox_backend_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(sandbox="chroot-please")]})
        assert "sandbox" in message

    def test_an_unknown_routing_category_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(routing={"category": "vibes"})]})
        assert "vibes" in message
        assert "routing" in message

    def test_an_unknown_routing_tier_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(routing={"tier": "platinum"})]})
        assert "platinum" in message

    def test_an_empty_routing_block_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(routing={})]})
        assert "routing" in message

    def test_an_empty_capability_list_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(capabilities=[])]})
        assert "capabilities" in message

    def test_a_non_slug_capability_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(capabilities=["Not A Slug"])]})
        assert "capabilities" in message

    def test_an_empty_list_entry_is_refused_rather_than_declared_as_nothing(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(tool_groups=["bash", "  "])]})
        assert "tool_groups" in message
        assert "empty" in message.lower()

    def test_a_non_string_list_entry_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(skills=["pytest", 7])]})
        assert "skills" in message

    def test_a_duplicate_name_is_refused(self, tmp_path: Path) -> None:
        # The second entry shares the name but NOT the territory, so the
        # failure observed is the name collision and not the overlap guard.
        message = _expect_load_error(
            tmp_path,
            specialists={"entries": [_minimal_entry(), _second_entry(name="example-specialist")]},
        )
        assert "already declared" in message

    def test_a_dangling_reports_to_is_refused_and_named(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(reports_to="nobody")]})
        assert "reports_to" in message
        assert "nobody" in message

    def test_a_self_referential_reports_to_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(reports_to="example-specialist")]})
        assert "itself" in message

    def test_a_reporting_cycle_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(name="a-spec", reports_to="b-spec"),
                    _second_entry(name="b-spec", reports_to="a-spec"),
                ]
            },
        )
        assert "cycle" in message
        # The whole path, not a repeated node name.
        assert "a-spec -> b-spec -> a-spec" in message, f"the cycle path is not reported: {message}"

    def test_an_undeclared_tool_group_is_refused_against_tool_groups(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(tool_groups=["not_a_group"])]})
        assert "not_a_group" in message
        assert "tool_groups" in message

    def test_an_undeclared_agent_preset_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(agent_preset="no-such-preset")]})
        assert "no-such-preset" in message
        assert "agent_preset" in message

    def test_a_built_in_agent_preset_is_accepted(self, tmp_path: Path) -> None:
        """The negative guard has a live positive side, or it is a blanket refusal."""
        config = _expect_load_ok(tmp_path, specialists={"entries": [_minimal_entry(agent_preset="standard")]})
        assert config.specialists.get("example-specialist").agent_preset == "standard"

    def test_a_duplicate_specialist_is_refused_naming_both_and_the_measured_score(self, tmp_path: Path) -> None:
        """The load-time duplicate guard, on the forge's own numbers.

        Two entries describing the same territory is exactly what
        ``forge.check_overlap`` refuses to build, so declaring both is refused
        at load with the same measurement rather than deferred to a build
        that would fail later.
        """
        message = _expect_load_error(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(name="dup-a"),
                    _minimal_entry(
                        name="dup-b",
                        description="Carries out an explicitly assigned verification pass over a named artefact and reports the measured outcome.",
                    ),
                ]
            },
        )
        assert "dup-a" in message and "dup-b" in message
        assert "forge overlap" in message
        assert "allow_overlap" in message

    def test_the_duplicate_guard_agrees_with_the_forge_it_delegates_to(self, tmp_path: Path) -> None:
        """The guard is not a second scoring rule: same pair, same verdict.

        Reads the *refused* pair straight back out of ``forge.check_overlap``
        and asserts the load error names exactly that offender. A guard that
        reimplemented the score could disagree with the forge, and then a
        catalogue could pass load for a pair the forge would refuse.
        """
        entries = [_minimal_entry(name="dup-a"), _minimal_entry(name="dup-b", description="Carries out an explicitly assigned verification pass over a named artefact and reports the measured outcome.")]
        catalog = SpecialistCatalogConfig(entries=[SpecialistConfig(**e) for e in entries], allow_overlap=True)
        role, _, claim = catalog.get("dup-a").territory().partition("\n")
        forge_offender, forge_score = check_overlap(
            claim,
            role,
            existing={"dup-b": catalog.get("dup-b").territory()},
            threshold=SELECTION_OVERLAP_THRESHOLD,
            name="dup-a",
        )
        assert forge_offender == "dup-b", "test premise broken: the forge no longer refuses this pair"
        assert forge_score >= SELECTION_OVERLAP_THRESHOLD

        message = _expect_load_error(tmp_path, specialists={"entries": entries})
        assert forge_offender in message
        assert f"{forge_score:.2f}" in message, "the load error must report the score the forge measured, not a recomputed one"

    def test_allow_overlap_is_the_operator_opt_out(self, tmp_path: Path) -> None:
        """Forge parity: ``allow_overlap`` is an override, not a removal."""
        config = _expect_load_ok(
            tmp_path,
            specialists={
                "allow_overlap": True,
                "entries": [
                    _minimal_entry(name="dup-a"),
                    _minimal_entry(
                        name="dup-b",
                        description="Carries out an explicitly assigned verification pass over a named artefact and reports the measured outcome.",
                    ),
                ],
            },
        )
        assert {entry.name for entry in config.specialists.entries} == {"dup-a", "dup-b"}

    def test_a_thin_description_is_refused_at_load(self, tmp_path: Path) -> None:
        """The load-bearing one: a description the forge could never judge.

        ``role: qa`` plus a one-word description is the shape the forge
        explicitly declines to refuse on, because ``_overlap_score``
        normalises against the query's own ceiling. Accepting it here would
        ship a declaration whose duplicate-detection is inert.
        """
        message = _expect_load_error(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(
                        name="thin-spec",
                        role="qa",
                        description="Testing",
                        not_for=["shipping unreviewed code to production"],
                    )
                ]
            },
        )
        assert "MIN_OVERLAP_TERMS" in message
        assert "thin-spec" in message

    def test_a_thin_description_is_refused_even_with_nothing_to_compare_against(self, tmp_path: Path) -> None:
        """The floor is per-specialist, not a pair check.

        With a single entry there is nothing to be a duplicate *of*, so a
        pair-only guard would pass a one-word description. The forge's rule is
        about the query's own thinness, not about the existence of a rival,
        so it has to bite on the first entry too.
        """
        message = _expect_load_error(
            tmp_path,
            specialists={"entries": [_minimal_entry(name="solo-thin", role="qa", description="Verifies", not_for=["shipping unreviewed code"])]},
        )
        assert "MIN_OVERLAP_TERMS" in message

    def test_a_thin_not_for_entry_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(
                        name="thin-exclusion",
                        not_for=["code"],
                    )
                ]
            },
        )
        assert "not_for" in message

    def test_an_empty_not_for_list_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(not_for=[])]})
        assert "not_for" in message

    def test_an_all_tools_role_is_refused_at_load(self, tmp_path: Path) -> None:
        """The ring guard's bite, for the ``allow_all`` rings.

        ``_resolve_ring`` matches ``lead``/``supervisor``/``admin`` EXACTLY and
        returns ``allow_all``, so a catalogue entry naming one would receive
        every tool including ``git_push`` and ``drop_database``. A worker
        specialist is not the leader, so the load refuses it.
        """
        for privileged in ("lead", "supervisor", "admin"):
            message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(role=privileged)]})
            assert "role" in message
            assert "permission ring" in message

    def test_the_leaders_own_narrow_ring_is_refused_too(self) -> None:
        """The leader ring is not an escalation, and is still not a specialist.

        ``DEFAULT_ROLE_RINGS`` gives the Alpha leader an explicit read/search/
        message allowlist, denied every write, shell, push and deploy verb — so
        it is narrow, and a specialist adopting it would not gain a tool. It is
        excluded anyway, because the leader dispatches work and a specialist
        performs it, and a catalogue that let an entry carry the leader's
        identity would make that distinction unreadable where it matters.
        """
        from alpha.config.specialist_config import LEADER_ROLE_RING

        # ``LEADER_ROLE_RING`` is the lower-cased key, matching the
        # lower-casing ``known_role_rings()`` applies; the permission table
        # itself keeps the title's original casing.
        source = next(ring for name, ring in DEFAULT_ROLE_RINGS.items() if name.lower() == LEADER_ROLE_RING)
        assert not source.allow_all, "test premise: this ring is narrow, not all-tools"

        ring, reason = resolve_role_ring(LEADER_ROLE_RING)
        assert ring is None
        assert "leader's own ring" in reason

        # The gate itself still resolves that role, so the exclusion is the
        # catalogue's and does not change what a leader Bot may do.
        allowed, _reason, _approval = ToolPermissionGate().check_permission(LEADER_ROLE_RING, "git_push")
        assert not allowed, "the leader ring is supposed to be denied git_push by the gate"

    def test_the_leader_ring_exclusion_does_not_collapse_the_vocabulary(self) -> None:
        """Removing one ring must not remove the others."""
        from alpha.config.specialist_config import LEADER_ROLE_RING

        rings = known_role_rings()
        workers = {name for name, allow_all in rings.items() if not allow_all and name != LEADER_ROLE_RING}
        assert {"researcher", "architect", "coder", "developer", "tester", "qa"} <= workers, f"worker rings went missing: {sorted(workers)}"
        # And each surviving worker ring still resolves on its own name.
        for ring in sorted(workers):
            assert resolve_role_ring(ring)[0] == ring, f"'{ring}' no longer resolves to itself"

    def test_the_section_itself_rejects_an_unknown_switch(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"enable": True, "entries": []})
        assert "enable" in message

    def test_an_unknown_role_is_refused(self, tmp_path: Path) -> None:
        message = _expect_load_error(tmp_path, specialists={"entries": [_minimal_entry(role="archivist")]})
        assert "role" in message
        assert "permission ring" in message

    def test_roster_ring_names_are_derived_from_the_permission_table(self) -> None:
        """The guard reads the executable table rather than a copied list."""
        rings = known_role_rings()
        assert set(rings) == {name.lower() for name in DEFAULT_ROLE_RINGS}
        for name, allow_all in rings.items():
            source = next(ring for ring_name, ring in DEFAULT_ROLE_RINGS.items() if ring_name.lower() == name)
            assert allow_all == source.allow_all


# ---------------------------------------------------------------------------
# The self-service boundary
# ---------------------------------------------------------------------------


class TestLeaderAuthoredNotSelfEdited:
    def test_no_specialist_field_is_bot_self_editable(self) -> None:
        """A specialist must not be a way around the bot_roster self-service gate.

        ``_SELF_EDITABLE`` is ``display_name``/``avatar``; everything that
        decides what a Bot may *do* is leader-only. A specialist field named
        after a leader-only field would be a re-grant of exactly the authority
        that gate exists to withhold.
        """
        tool_source = (BACKEND / "packages" / "harness" / "alpha" / "tools" / "builtins" / "bot_roster_tool.py").read_text(encoding="utf-8")
        self_editable = set(re.search(r"_SELF_EDITABLE: frozenset\[str\] = frozenset\(\{([^}]*)\}\)", tool_source).group(1).replace('"', "").replace("'", "").split(","))
        self_editable = {item.strip() for item in self_editable if item.strip()}
        assert self_editable == {"display_name", "avatar"}, f"the self-service set changed; re-check this test's premise: {self_editable}"

        overlap = self_editable & set(SpecialistConfig.model_fields)
        assert not overlap, f"specialist fields {sorted(overlap)} are Bot-self-editable, so a catalogue entry would re-grant self-service authority"

    def test_every_specialist_field_is_leader_only_at_the_profile_level(self) -> None:
        """The same boundary, expressed on ``BotProfile``.

        A specialist's data lands on a profile. Anything the bot_roster gate
        refuses a Bot from editing about itself must not be settable by a Bot
        through a specialist either.
        """
        from alpha.bots.profile import BotProfile

        tool_source = (BACKEND / "packages" / "harness" / "alpha" / "tools" / "builtins" / "bot_roster_tool.py").read_text(encoding="utf-8")
        leader_only = set(re.search(r"_LEADER_ONLY: frozenset\[str\] = frozenset\(\{([^}]*)\}\)", tool_source).group(1).replace('"', "").replace("'", "").split(","))
        leader_only = {item.strip() for item in leader_only if item.strip()}
        assert {"role", "model", "skills", "capabilities", "department", "reports_to"} <= leader_only, f"the leader-only set changed; re-check this test's premise: {leader_only}"

        profile_fields = set(BotProfile.__dataclass_fields__)
        # Each specialist field must either be leader-only, or be one this
        # catalogue deliberately does not expose (model), or be presentation.
        mapped_to_profile = {
            "role": "role",
            "capabilities": "capabilities",
            "skills": "skills",
            "tool_groups": "toolsets",
            "department": "department",
            "reports_to": "reports_to",
        }
        for specialist_field, profile_field in mapped_to_profile.items():
            assert specialist_field in SpecialistConfig.model_fields
            assert profile_field in profile_fields
        assert "model" not in SpecialistConfig.model_fields, "a specialist must not carry a model name; routing is the only executable model source"

    def test_the_catalogue_grants_no_authority_a_bare_role_could_not(self) -> None:
        """The honest-limit test: a specialist is data, not a permission.

        Everything a specialist declares is already expressible through the
        forge's own arguments and the permission rings. Nothing here widens
        what a role may do, and the projection into ``forge_bot`` carries no
        argument the forge does not already accept.
        """
        forge_signature = inspect.signature(forge_bot)
        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            kwargs = entry.to_forge_kwargs()
            unexpected = set(kwargs) - set(forge_signature.parameters)
            assert not unexpected, f"'{name}' projects arguments the forge does not accept: {sorted(unexpected)}"
            # Binding against the real signature is the check: an unexpected
            # keyword raises here rather than being silently accepted.
            forge_signature.bind(**kwargs, registry=None)
        # A disabled specialist still projects the same arguments - it is out
        # of rotation, not out of existence, so a caller can see what it was.
        catalogue = _load().specialists
        entry = copy.deepcopy(catalogue.get("researcher"))
        entry.enabled = False
        assert entry.to_forge_kwargs()["name"] == "researcher"


# ---------------------------------------------------------------------------
# Projections land on real surfaces
# ---------------------------------------------------------------------------


class TestProjectionsOntoExistingMachinery:
    def test_draft_first_resolves_to_the_forge_default_approvals(self) -> None:
        entry = SpecialistConfig(**_minimal_entry())
        assert entry.approvals == "draft_first"
        assert entry.resolve_approval_list() == list(DEFAULT_APPROVALS)
        assert set(APPROVAL_POSTURES) == {"draft_first", "autonomous"}

    def test_autonomous_is_the_forge_empty_list_opt_out(self) -> None:
        entry = SpecialistConfig(**_minimal_entry(approvals="autonomous"))
        assert entry.resolve_approval_list() == []

    def test_extra_approvals_extend_the_posture(self) -> None:
        entry = SpecialistConfig(**_minimal_entry(extra_approvals=["apply a schema migration", "apply a schema migration"]))
        assert entry.resolve_approval_list() == [*DEFAULT_APPROVALS, "apply a schema migration"]

    def test_extra_approvals_on_an_autonomous_specialist_are_not_dropped(self) -> None:
        entry = SpecialistConfig(**_minimal_entry(approvals="autonomous", extra_approvals=["sign a release"]))
        assert entry.resolve_approval_list() == ["sign a release"]

    def test_the_approvals_reach_the_guardrail_soul_the_forge_builds(self) -> None:
        """Not a copy of the forge's block: the real function is called."""
        entry = SpecialistConfig(**_minimal_entry(approvals="autonomous", extra_approvals=["sign a release"]))
        soul = build_guardrail_soul("example-specialist", entry.role, approvals=entry.resolve_approval_list(), reports_to=None, sandbox=None, base_soul=entry.to_soul())
        assert "sign a release" in soul
        assert "Never do these" in soul

    def test_the_bot_template_only_carries_portable_fields(self) -> None:
        entry = SpecialistConfig(**_minimal_entry(tool_groups=["bash"], skills=["pytest"], sandbox="docker"))
        payload = entry.to_bot_template()
        assert set(payload) <= set(TEMPLATE_FIELDS), f"template carries non-portable keys: {sorted(set(payload) - set(TEMPLATE_FIELDS))}"
        assert "model" not in payload, "a template must not pin a model name"
        assert payload["toolsets"] == ["bash"]
        assert payload["skills"] == ["pytest"]
        assert payload["sandbox"] == "docker"
        assert payload["display_name"] == "example-specialist"

    def test_the_soul_states_the_territory_and_the_prohibitions(self) -> None:
        entry = SpecialistConfig(**_minimal_entry(title="Example Specialist"))
        soul = entry.to_soul()
        assert soul.startswith("# SOUL.md - Example Specialist (tester)")
        assert "Never handle this" in soul
        assert "Declared capabilities" in soul
        # The forge appends the guardrails; this must not restate them.
        assert "Never do these" not in soul
        assert "Where you run" not in soul

    def test_territory_is_the_positive_claim_and_excludes_are_not_scored(self) -> None:
        """Why ``not_for`` is deliberately absent from the scored text.

        ``_overlap_score`` normalises against the query's own ceiling, so
        folding prohibitions into the query inflates the ceiling and depresses
        every genuine match. Measured on the shipped team: a documentation task
        scored 0.43 against ``security-reviewer`` purely because both entries
        mention a configuration change — one as competence, the other as a
        prohibition. An exclusion is not a claim of competence.
        """
        entry = SpecialistConfig(**_minimal_entry())
        assert entry.territory() == "\n".join([entry.role, entry.description, *entry.capabilities])
        for exclusion in entry.not_for:
            assert exclusion not in entry.territory(), f"'{exclusion}' is a prohibition and must not be scored as competence"

    def test_a_shared_prohibition_does_not_make_two_specialists_collide(self) -> None:
        """The bite for the previous test, on the shipped team.

        Every shipped entry prohibits shipping a release, and three of them
        prohibit touching code. If those prohibitions were scored, five of the
        six would be near-duplicates of each other.
        """
        entries = _load().specialists.entries
        prohibitions = [item for entry in entries for item in entry.not_for if "release" in item]
        assert len(prohibitions) >= 5, f"test premise: expected many entries prohibiting a release, got {len(prohibitions)}"
        for entry in entries:
            role, _, claim = entry.territory().partition("\n")
            offender, _ = check_overlap(claim, role, existing={other.name: other.territory() for other in entries if other.name != entry.name}, threshold=SELECTION_OVERLAP_THRESHOLD, name=entry.name)
            assert offender is None, f"'{entry.name}' collides with '{offender}' once shared prohibitions are excluded, so something else is overlapping"

    def test_declared_prose_is_the_operator_text_not_the_generated_soul(self) -> None:
        """The floor is measured over what the operator wrote, nothing else.

        ``to_soul`` adds a heading, a "Never handle this" label and the
        capability slugs. Those are this module's formatting; counting them
        would let a one-word description clear the forge's floor on the
        strength of the generator's own output.
        """
        entry = SpecialistConfig(**_minimal_entry())
        assert entry.declared_prose() == f"{entry.role}\n{entry.description}"
        generated_only = significant_terms(entry.to_soul()) - significant_terms(entry.declared_prose())
        assert generated_only, "the SOUL must actually add text, or this test proves nothing"

    def test_a_thin_description_is_refused_however_long_the_capability_list_is(self) -> None:
        """The bite: the generator's own output cannot launder a thin description.

        A specialist with a one-word description and six capability slugs has
        plenty of text; if the floor were measured over the derived text it
        would pass, and the forge's duplicate check would then have nothing
        meaningful to compare.
        """
        with pytest.raises(ValidationError) as excinfo:
            SpecialistConfig(
                **_minimal_entry(
                    name="laundered",
                    role="qa",
                    description="Checks",
                    capabilities=["one", "two", "three", "four", "five", "six"],
                )
            )
        assert "MIN_OVERLAP_TERMS" in str(excinfo.value)

    def test_every_shipped_sandbox_and_department_is_a_real_one(self) -> None:
        from alpha.bots.templates import DEPARTMENTS

        for name in DEFAULT_TEAM:
            entry = _load().specialists.get(name)
            assert entry is not None
            if entry.sandbox is not None:
                assert entry.sandbox in SANDBOX_BACKENDS
            if entry.department is not None:
                assert entry.department in DEPARTMENTS

    def test_every_shipped_tool_group_is_declared_in_config(self) -> None:
        config = _load()
        declared = {group.name for group in config.tool_groups}
        for name in DEFAULT_TEAM:
            entry = config.specialists.get(name)
            assert entry is not None
            for group in entry.tool_groups:
                assert group in declared, f"'{name}' declares tool group '{group}' which tool_groups[] does not declare"

    def test_routing_slots_are_resolvable_when_model_routing_declares_them(self) -> None:
        """The negative case for the routing guard, with routing actually on.

        The shipped template declares no ``model_routing``, so the guard is
        otherwise untested against a populated table. Here the routing table
        is filled with a real model name and the load must succeed.
        """
        config = _load(
            model_routing={
                "enabled": True,
                "default_model": None,
                "categories": {"deep": ["alpha-free"]},
                "tiers": {"fast": ["alpha-free"]},
            }
        )
        catalog = config.specialists
        assert catalog.get("researcher").routing.tier == "fast"
        assert catalog.get("code-reviewer").routing.category == "deep"

    def test_a_routing_slot_the_table_does_not_declare_is_refused(self, tmp_path: Path) -> None:
        """The bite: routing IS declared, and the specialist names a slot that is not in it.

        Without this the operator would get a specialist that reads as routed
        and is not — the exact silent degradation ``model_routing`` exists to
        remove, one layer up.
        """
        message = _expect_load_error(
            tmp_path,
            model_routing={
                "enabled": True,
                "default_model": None,
                "categories": {"deep": ["alpha-free"]},
                "tiers": {"fast": ["alpha-free"]},
            },
            specialists={"entries": [_minimal_entry(routing={"tier": "frontier"})]},
        )
        assert "frontier" in message
        assert "model_routing" in message

    def test_a_specialist_slot_resolvable_from_the_table_is_accepted(self, tmp_path: Path) -> None:
        """The positive side of the guard above, in the same shape."""
        config = _expect_load_ok(
            tmp_path,
            model_routing={
                "enabled": True,
                "default_model": None,
                "categories": {"deep": ["alpha-free"]},
                "tiers": {"fast": ["alpha-free"]},
            },
            specialists={"entries": [_minimal_entry(routing={"tier": "fast"})]},
        )
        assert config.specialists.get("example-specialist").routing.tier == "fast"

    def test_disabling_model_routing_leaves_the_slot_unresolved_not_fatal(self, tmp_path: Path) -> None:
        """``model_routing.enabled: false`` degrades, it does not refuse.

        A deliberate choice to turn routing off must not make the file
        unloadable — the declaration is still valid, it just resolves to no
        model. The same is true of a file with no ``model_routing`` block at
        all, which is what ``config.example.yaml`` ships. Both are warned
        about, neither is fatal; only a *populated* table that omits the slot
        is an error, because that one is a typo rather than a choice.
        """
        config = _expect_load_ok(
            tmp_path,
            model_routing={
                "enabled": False,
                "default_model": None,
                "categories": {"deep": ["alpha-free"]},
                "tiers": {"fast": ["alpha-free"]},
            },
            specialists={"entries": [_minimal_entry(routing={"tier": "fast"})]},
        )
        assert config.specialists.get("example-specialist").routing.tier == "fast"

    def test_the_two_routing_cases_differ_only_in_the_operator_s_table(self, tmp_path: Path) -> None:
        """The pair that decides the two tests above, stated once.

        Identical specialist declaration in both cases; the only variable is
        whether the operator's ``model_routing`` table contains the slot.
        Declared -> loads. Absent from a populated table -> refuses.
        """
        table = {"enabled": True, "default_model": None, "categories": {"deep": ["alpha-free"]}, "tiers": {"fast": ["alpha-free"]}}

        config = _expect_load_ok(tmp_path, model_routing=table, specialists={"entries": [_minimal_entry(routing={"tier": "fast"})]})
        assert config.specialists.get("example-specialist").routing.tier == "fast"

        message = _expect_load_error(tmp_path, model_routing=table, specialists={"entries": [_minimal_entry(routing={"tier": "coding"})]})
        assert "coding" in message and "model_routing" in message

    def test_disabling_the_section_takes_every_specialist_out_of_rotation(self, tmp_path: Path) -> None:
        config = _expect_load_ok(tmp_path, specialists={"enabled": False, "entries": [_minimal_entry(), _second_entry()]})
        catalog = config.specialists
        assert catalog.enabled_specialists() == []
        assert catalog.select_for_task("triage this failure to the assertion that broke") == []
        # The declarations are still parsed, still validated and still
        # inspectable: off is a rotation switch, not a parse-skip.
        assert len(catalog.entries) == 2
        assert catalog.get("example-specialist") is not None

    def test_a_disabled_entry_keeps_its_declaration_but_leaves_rotation(self, tmp_path: Path) -> None:
        config = _expect_load_ok(tmp_path, specialists={"entries": [_minimal_entry(name="solo", enabled=False), _second_entry(name="other")]})
        catalog = config.specialists
        assert catalog.get("solo") is not None, "a disabled entry must stay inspectable"
        assert [entry.name for entry in catalog.enabled_specialists()] == ["other"]


# ---------------------------------------------------------------------------
# Selection — the scheduling surface
# ---------------------------------------------------------------------------


class TestSelection:
    def test_a_task_spanning_two_territories_surfaces_both_owners(self) -> None:
        """Not a single forced answer.

        A task that genuinely spans two territories must produce both, in
        score order. A selection surface that always returns exactly one name
        is a lookup table, not a router.
        """
        catalog = _load().specialists
        task = "review this diff, then write the regression case for the finding"
        matches = catalog.select_for_task(task, limit=4)
        assert len(matches) >= 2, f"expected both owners, got {[(m.name, round(m.score, 2)) for m in matches]}"
        names = [m.name for m in matches]
        assert "code-reviewer" in names, f"{task!r} did not surface the reviewer: {names}"
        assert "test-engineer" in names, f"{task!r} did not surface the tester: {names}"
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True), f"matches are not in score order: {[(m.name, m.score) for m in matches]}"

    @pytest.mark.parametrize(
        ("task", "expected"),
        [
            ("gather external evidence and cite the sources", "researcher"),
            ("cross-check these two claims against a second source", "researcher"),
            ("review this diff and report the finding with its file and line", "code-reviewer"),
            ("find the unhandled failure path in this proposed change", "code-reviewer"),
            ("audit the exposed route for authentication and authorization gaps", "security-reviewer"),
            ("scan for committed secret material and untrusted input reaching a shell", "security-reviewer"),
            ("write the missing regression case and run the test command", "test-engineer"),
            ("triage this failure to the assertion that broke", "test-engineer"),
            ("update the setup guide and the api reference pages", "technical-writer"),
            ("flag a documented option that no longer exists", "technical-writer"),
            ("read the logs and narrow this alert to the failing component", "operations-engineer"),
            ("follow the runbook for a rollback", "operations-engineer"),
        ],
    )
    def test_a_task_picks_the_specialist_that_owns_the_territory(self, task: str, expected: str) -> None:
        catalog = _load().specialists
        matches = catalog.select_for_task(task, limit=1)
        assert matches, f"no specialist proposed for {task!r}"
        assert matches[0].name == expected, f"{task!r} proposed {matches[0].name} (score {matches[0].score:.2f}, terms {matches[0].terms}), expected {expected}"

    def test_every_shipped_specialist_is_reachable_from_some_task(self) -> None:
        """All six are selectable, not just the four with obvious vocabulary.

        A specialist nothing can ever be selected for is a declaration that
        looks load-bearing and is not. Each entry is proven reachable by a
        task phrased in its own declared vocabulary.
        """
        catalog = _load().specialists
        reachable: set[str] = set()
        for name in DEFAULT_TEAM:
            entry = catalog.get(name)
            assert entry is not None
            # Take the specialist's own territory statement and score it back
            # against itself: a specialist that cannot match its own prose
            # cannot be selected by anything a human would write.
            score, _terms = score_overlap(entry.declared_prose(), entry.territory())
            assert score >= SELECTION_OVERLAP_THRESHOLD, f"'{name}' scores {score:.2f} against its own declaration, below the selection threshold"
            reachable.add(name)
        assert reachable == set(DEFAULT_TEAM)

    def test_a_thin_task_is_reported_as_unjudgeable_not_confidently_matched(self) -> None:
        """The forge's floor, applied to the *task* side too.

        A one-word task scored against a long description would otherwise
        produce a confident-looking routing decision from a single shared
        word, which is the failure the whole thin-query rule exists to stop.
        """
        matches = _load().specialists.select_for_task("review", limit=3)
        assert len(significant_terms("review")) < MIN_OVERLAP_TERMS
        for match in matches:
            assert match.judgeable is False, f"a one-word task produced a confident match on '{match.name}'"

    def test_a_rich_task_is_reported_as_judgeable(self) -> None:
        """The positive side: the flag tracks the floor, it does not always read false."""
        matches = _load().specialists.select_for_task("triage this failure down to the assertion that broke", limit=1)
        assert matches
        assert matches[0].judgeable is True

    def test_the_match_carries_the_terms_that_produced_the_score(self) -> None:
        """Evidence, not just a number.

        The survey's own contract is that a fit is shown with the words that
        made it. A score with no terms is a number an operator cannot argue
        with, which is not the same as a decision they can audit.
        """
        match = _load().specialists.select_for_task("triage this failure down to the assertion that broke", limit=1)[0]
        assert match.terms
        assert "assertion" in match.terms or "triage" in match.terms
        assert set(match.to_dict()) == {"name", "role", "score", "terms", "judgeable"}

    def test_selection_is_deterministic_and_calls_no_model(self) -> None:
        catalog = _load().specialists
        task = "triage the failing integration suite and report the measured result"
        first = [m.to_dict() for m in catalog.select_for_task(task)]
        second = [m.to_dict() for m in catalog.select_for_task(task)]
        assert first == second

    @pytest.mark.parametrize(
        "task",
        [
            "bake a lemon cake with a sponge base",
            "rename the colour palette in the stylesheet",
            "order more coffee beans for the office",
        ],
    )
    def test_a_weak_match_is_not_proposed(self, task: str) -> None:
        """The negative case: an unrelated task proposes nobody.

        Asserted as "no specialist clears the threshold" rather than "the raw
        score is zero", because a shared word will always produce some score;
        the threshold is what stops a single incidental word from reading as
        a routing decision.
        """
        catalog = _load().specialists
        raw_scores = {entry.name: score_overlap(task, entry.territory())[0] for entry in catalog.enabled_specialists()}
        assert max(raw_scores.values()) < SELECTION_OVERLAP_THRESHOLD, f"test premise broken: {raw_scores} already clears the threshold"
        assert catalog.select_for_task(task) == []

    def test_the_limit_is_honoured(self) -> None:
        catalog = _load().specialists
        task = "review this code diff, check its security, and write the regression test for it"
        assert len(catalog.select_for_task(task, limit=2)) <= 2

    def test_the_selection_threshold_is_the_forge_default(self) -> None:
        """One number, one place: selection and the forge's refusal agree."""
        default = inspect.signature(check_overlap).parameters["threshold"].default
        assert SELECTION_OVERLAP_THRESHOLD == default, (
            f"the catalogue's selection threshold ({SELECTION_OVERLAP_THRESHOLD}) has drifted from forge.check_overlap's default ({default}); a match and a refusal would then disagree on what 'the same job' means"
        )

    def test_reporting_structure_is_queryable(self) -> None:
        catalog = _load().specialists
        assert {entry.name for entry in catalog.roots()} == set(DEFAULT_TEAM), "the shipped team is flat by design; the leader is a Bot, not a specialist"
        assert catalog.reports("researcher") == []
        assert [entry.name for entry in catalog.chain("researcher")] == ["researcher"]

    def test_a_hierarchical_catalogue_answers_reports_and_chain(self, tmp_path: Path) -> None:
        """The reporting structure the flat default team does not exercise."""
        config = _expect_load_ok(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(name="verification-lead"),
                    _second_entry(name="schema-indexer", reports_to="verification-lead"),
                ]
            },
        )
        catalog = config.specialists
        assert [entry.name for entry in catalog.roots()] == ["verification-lead"]
        assert [entry.name for entry in catalog.reports("verification-lead")] == ["schema-indexer"]
        assert [entry.name for entry in catalog.chain("schema-indexer")] == ["schema-indexer", "verification-lead"]
        # And it reaches the profile field the registry groups by.
        assert catalog.get("schema-indexer").to_forge_kwargs()["reports_to"] == "verification-lead"

    def test_a_disabled_manager_still_leaves_its_report_in_the_declaration(self, tmp_path: Path) -> None:
        """Rotation is a filter, not a deletion.

        A disabled manager does not orphan its report in the declaration — the
        report is still inspectable and still names its escalation target,
        which matters because ``reports_to`` becomes the SOUL's escalation
        line and a dangling one would be a Bot escalating into nothing.
        """
        config = _expect_load_ok(
            tmp_path,
            specialists={
                "entries": [
                    _minimal_entry(name="verification-lead", enabled=False),
                    _second_entry(name="schema-indexer", reports_to="verification-lead"),
                ]
            },
        )
        catalog = config.specialists
        assert [entry.name for entry in catalog.enabled_specialists()] == ["schema-indexer"]
        assert catalog.get("schema-indexer").reports_to == "verification-lead"
        # ``chain`` walks declarations, so the manager is still reachable.
        assert [entry.name for entry in catalog.chain("schema-indexer")] == ["schema-indexer", "verification-lead"]


# ---------------------------------------------------------------------------
# Reachability — not a module nothing imports
# ---------------------------------------------------------------------------


class TestTheCatalogueIsReachable:
    def test_app_config_declares_the_section(self) -> None:
        assert AppConfig.model_fields["specialists"].default_factory is SpecialistCatalogConfig
        # Hot-reloadable, not restart-required: the reload-boundary guard fails
        # a field whose description carries the startup-only prefix without a
        # registry entry, so this asserts the opposite - that it is NOT marked.
        assert "startup-only:" not in (AppConfig.model_fields["specialists"].description or "")

    def test_app_config_offers_an_oxygen_lookup_like_the_other_sections(self) -> None:
        """Same O(1) shape as get_model_config / get_tool_config."""
        config = _load()
        assert config.get_specialist_config("researcher") is not None
        assert config.get_specialist_config("RESEARCHER") is not None
        assert config.get_specialist_config("nobody") is None

    def test_get_specialist_catalog_reads_the_live_config(self) -> None:
        import alpha.config.app_config as app_config_module

        config = _load()
        original = app_config_module.get_app_config
        app_config_module.get_app_config = lambda *a, **k: config  # type: ignore[assignment]
        try:
            assert [entry.name for entry in get_specialist_catalog().enabled_specialists()] == list(DEFAULT_TEAM)
        finally:
            app_config_module.get_app_config = original

    def test_a_production_module_imports_the_catalogue(self) -> None:
        """The orphan-module guard in spirit: something in the source tree names it.

        ``tests/test_no_orphan_modules.py`` proves no module is unreferenced;
        this pins the specific reference that must not disappear, so a later
        refactor cannot quietly leave the catalogue parsed and unreachable.
        """
        app_config_source = (BACKEND / "packages" / "harness" / "alpha" / "config" / "app_config.py").read_text(encoding="utf-8")
        assert "from alpha.config.specialist_config import" in app_config_source
        assert "SpecialistCatalogConfig" in app_config_source
        tree = ast.parse(app_config_source)
        imported = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "alpha.config.specialist_config":
                imported = True
        assert imported, "app_config.py no longer imports the specialist catalogue"

    def test_the_catalogue_is_readable_from_the_yaml_document_alone(self) -> None:
        """No second source of truth: the shipped file is the declaration."""
        import yaml

        doc = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}
        declared = {entry["name"] for entry in doc["specialists"]["entries"]}
        assert declared == set(DEFAULT_TEAM)
        config = _load()
        assert {entry.name for entry in config.specialists.entries} == declared, "the parsed catalogue and the shipped YAML disagree"

    def test_config_version_was_bumped_for_the_schema_change(self) -> None:
        """The repository rule: changing the config schema bumps config_version."""
        import yaml

        doc = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}
        assert int(doc.get("config_version", 0)) >= 56, "bump config_version in config.example.yaml when the schema gains a section"

    def test_no_second_specialist_file_exists(self) -> None:
        """The models.yaml lesson, applied to this section."""
        for name in ("specialists.yaml", "specialists.example.json", "specialists.json"):
            assert not (REPO_ROOT / name).exists(), f"{name} would be a second source of truth for specialists"
        assert not (BACKEND / name).exists()

    def test_the_summary_projection_is_json_safe_and_carries_the_approvals(self) -> None:
        import json

        summary = _load().specialists.summary()
        assert len(summary) == len(DEFAULT_TEAM)
        json.dumps(summary)  # must not raise
        by_name = {row["name"]: row for row in summary}
        assert by_name["researcher"]["approvals"] == "draft_first"
        assert by_name["researcher"]["approvals_ask_first"] == list(DEFAULT_APPROVALS)
        assert by_name["researcher"]["routing"] == ["tier:fast"]


# ---------------------------------------------------------------------------
# Routing vocabulary drift
# ---------------------------------------------------------------------------


def test_routing_vocabulary_matches_the_routers() -> None:
    """Drift guard. The catalogue must not carry its own copy of a vocabulary.

    ``ROUTING_CATEGORIES`` / ``ROUTING_TIERS`` mirror the router tables rather
    than importing them, because ``AppConfig`` imports this module at module
    scope and the routers import config. A mirror without this test is a
    second source of truth; a mirror with it is a pinned cache.
    """
    from alpha.models.category_router import DEFAULT_CATEGORY_SPECS
    from alpha.models.workforce_router import ModelTier

    assert set(ROUTING_CATEGORIES) == set(DEFAULT_CATEGORY_SPECS), (
        f"ROUTING_CATEGORIES has drifted from alpha.models.category_router.DEFAULT_CATEGORY_SPECS; "
        f"catalogue-only={sorted(set(ROUTING_CATEGORIES) - set(DEFAULT_CATEGORY_SPECS))}, "
        f"router-only={sorted(set(DEFAULT_CATEGORY_SPECS) - set(ROUTING_CATEGORIES))}"
    )
    assert set(ROUTING_TIERS) == {tier.value for tier in ModelTier}, (
        f"ROUTING_TIERS has drifted from alpha.models.workforce_router.ModelTier; catalogue-only={sorted(set(ROUTING_TIERS) - {t.value for t in ModelTier})}, router-only={sorted({t.value for t in ModelTier} - set(ROUTING_TIERS))}"
    )


def test_every_shipped_routing_slot_is_a_key_the_routers_understand() -> None:
    """Proves the shipped team against the live router tables, not the mirror."""
    from alpha.models.category_router import DEFAULT_CATEGORY_SPECS
    from alpha.models.workforce_router import ModelTier

    tiers = {tier.value for tier in ModelTier}
    used: set[str] = set()
    for name in DEFAULT_TEAM:
        entry = _load().specialists.get(name)
        assert entry is not None
        if entry.routing is None:
            continue
        if entry.routing.category is not None:
            assert entry.routing.category in DEFAULT_CATEGORY_SPECS
        if entry.routing.tier is not None:
            assert entry.routing.tier in tiers
        used.update(entry.routing.slot_labels())
    assert used, "the shipped team should route somewhere, or the field is decorative"
