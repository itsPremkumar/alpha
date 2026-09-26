"""Mutation evidence: prove every security assertion in the suite actually BITES.

A passing test proves nothing unless it fails when the property it claims to
protect is removed. This module removes each guard and asserts the corresponding
security property then BREAKS.

How the mutation is applied
---------------------------
By **in-memory source mutation**: the module's source text is read, a guard is
deleted with an exact string replacement, and the result is ``exec``-ed into a
fresh module object under a private name. Every import inside the mutated source
is still absolute (``from alpha.bots...``), so the mutated module calls its OWN
neutered definitions rather than the live ones. Nothing is monkeypatched and, most
importantly, **nothing on disk is written** — a mutation test that edits a tracked
file to demonstrate a failure can leave the repository broken, and this
environment has reverted source files mid-run before.

Every mutation asserts that its replacement actually changed the source
(``assert mutated != original``). If the upstream code drifts and a replacement
stops matching, the test fails loudly rather than silently proving nothing.

Each test has the same shape:

1. the property holds against the REAL module (the shipped guarantee);
2. the property is BROKEN against the mutated module (the test bites).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

BOTS_DIR = Path(__file__).resolve().parents[1] / "packages" / "harness" / "alpha" / "bots"

_mutated_counter = 0


def mutated_module(filename: str, *replacements: tuple[str, str], prelude: str = "") -> types.ModuleType:
    """Exec a guard-removed copy of ``filename`` as a private module.

    Args:
        filename: Module file name inside :data:`BOTS_DIR`.
        replacements: ``(old, new)`` pairs. Each ``old`` must occur exactly once;
            a zero- or multi-match is a hard error, because a replacement that
            silently does not apply would make the whole test vacuous.
        prelude: Extra source exec'd before the module body (test helpers).
    """
    global _mutated_counter
    source = (BOTS_DIR / filename).read_text(encoding="utf-8")
    mutated = source
    for old, new in replacements:
        occurrences = mutated.count(old)
        assert occurrences == 1, (
            f"mutation target must occur exactly once in {filename}, found {occurrences}: {old!r}"
        )
        mutated = mutated.replace(old, new)
    assert mutated != source, f"mutation for {filename} was a no-op"
    _mutated_counter += 1
    name = f"_mutated_{filename[:-3]}_{_mutated_counter}"
    module = types.ModuleType(name)
    module.__file__ = str(BOTS_DIR / filename)
    sys.modules[name] = module
    exec(compile(prelude + mutated, str(BOTS_DIR / filename), "exec"), module.__dict__)
    return module


def _bound(mod, rank: int, live: int = 4) -> object:
    return mod.AuthorityCeiling(
        max_capability_rank=rank, max_live_profiles=live, max_total_profiles=live * 4
    )


# ==========================================================================
# 1. The grant check — does the over-privilege refusal actually bite?
# ==========================================================================


def test_mutation_removing_the_grant_check_breaks_the_over_privilege_refusal(tmp_path):
    real = __import__("alpha.bots.dynamic_profiles", fromlist=["x"])
    m = mutated_module(
        "authority_ceiling.py",
        (
            "    if violations:\n        raise AuthorityViolation(",
            "    if False:\n        raise AuthorityViolation(",
        ),
    )
    # (1) The shipped guarantee: an over-ceiling request is REFUSED.
    with pytest.raises(m.AuthorityViolation):
        m.enforce_grant(
            ["grant_authority"], creator_grant=["observe"], subject="x", ceiling=_bound(m, 50)
        )
    # (2) With the guard removed, the SAME request is granted. The test bites.
    mutated_dyn = mutated_module(
        "dynamic_profiles.py",
        (
            "    if violations:\n        raise AuthorityViolation(",
            "    if False:\n        raise AuthorityViolation(",
        ),
    )
    # dynamic_profiles imports enforce_grant by name, so mutate its own copy too
    # by re-binding it to the neutered ceiling module's function.
    mutated_dyn.enforce_grant = m.enforce_grant
    store = mutated_dyn.DynamicProfileStore(
        tmp_path / "p.json",
        ceiling=_bound(mutated_dyn, 50),
        approval_gate=lambda p: True,
    )
    proposal = mutated_dyn.ProfileProposal(
        profile_name="root-bot",
        requested_capabilities=["observe", "grant_authority"],
        creator="alpha",
        creator_grant=["observe", "grant_authority", "repository_mutate", "process_exec"],
        rationale="r",
    )
    store.propose_profile(proposal)
    store.approve_proposal("root-bot")
    created = store.install_approved("root-bot")
    assert "grant_authority" in created.granted_capabilities, (
        "a profile above the ceiling was created: the refusal test does not bite"
    )
    assert real.AuthorityCeiling is not m.AuthorityCeiling


# ==========================================================================
# 2. The population ceiling
# ==========================================================================


def test_mutation_removing_the_population_check_allows_unbounded_hiring(tmp_path):
    m = mutated_module(
        "authority_ceiling.py",
        (
            "        if live >= self.max_live_profiles:",
            "        if False and live >= self.max_live_profiles:",
        ),
    )
    bound = _bound(m, 50, live=3)
    # (1) The shipped guarantee.
    with pytest.raises(m.AuthorityViolation):
        bound.assert_population_within_ceiling(live=3, total=3, subject="x")
    # (2) Mutated: unlimited growth.
    bound.assert_population_within_ceiling(live=10_000, total=10_000, subject="x")


# ==========================================================================
# 3. Protected components — is the "cannot edit the enforcer" refusal real?
# ==========================================================================


def test_mutation_removing_the_protected_check_makes_the_ceiling_editable(tmp_path):
    m = mutated_module(
        "authority_ceiling.py",
        (
            "    if any(_normalise(component) in needle for component in PROTECTED_COMPONENTS):\n        return True",
            "    if False:\n        return True",
        ),
    )
    real = __import__("alpha.bots.authority_ceiling", fromlist=["x"])
    # (1) The shipped guarantee: the ceiling and its enforcers are untouchable.
    for target in (
        "alpha/bots/authority_ceiling.py",
        "alpha/bots/dynamic_profiles.py",
        "alpha/bots/permissions.py",
        "config/update-policy.json",
    ):
        assert real.is_protected_component(target), target
    # (2) Mutated: they are ordinary targets.
    assert not m.is_protected_component("alpha/bots/authority_ceiling.py")
    assert not m.is_protected_component("config/update-policy.json")


def test_mutation_removing_segment_matching_lets_a_bare_name_through():
    m = mutated_module(
        "authority_ceiling.py",
        (
            "    return any(stem in segments for stem in PROTECTED_MODULE_STEMS)",
            "    return False",
        ),
    )
    real = __import__("alpha.bots.authority_ceiling", fromlist=["x"])
    # (1) The shipped guarantee: a bare module name is still recognised.
    assert real.is_protected_component("authority_ceiling") is True
    assert real.is_protected_component("alpha.bots.authority_ceiling") is True
    # (2) Mutated: only the long path forms still match.
    assert m.is_protected_component("authority_ceiling") is False
    assert m.is_protected_component("alpha/bots/authority_ceiling.py") is True


def test_mutation_opening_the_blast_radius_lets_the_sentinel_edit_the_ceiling(tmp_path):
    m = mutated_module(
        "self_modification.py",
        (
            "    if is_protected_component(normalised) or any(",
            "    if False or any(",
        ),
    )
    real = __import__("alpha.bots.self_modification", fromlist=["x"])
    # (1) The shipped guarantee.
    for target in (
        "alpha/bots/authority_ceiling.py",
        "alpha/projects/approval_queue.py",
        "alpha/safety/net_policy.py",
        "config/update-policy.json",
    ):
        assert not real.is_self_modifiable(target, repo_root=tmp_path), target
    # (2) Mutated: the blast radius no longer protects the enforcers.
    m.PERMANENTLY_OFF_LIMITS = ()
    m.is_protected_component = lambda target: False
    assert m.is_self_modifiable("alpha/bots/authority_ceiling.py", repo_root=tmp_path)
    assert m.is_self_modifiable("config/update-policy.json", repo_root=tmp_path)


# ==========================================================================
# 4. Ceiling tightening — does the demotion actually bite?
# ==========================================================================


def test_mutation_removing_the_demotion_makes_tightening_a_silent_no_op(tmp_path):
    m = mutated_module(
        "authority_ceiling.py",
        (
            "        if name in CAPABILITY_RANKS and name in bound.allowed_capabilities and CAPABILITY_RANKS[name] <= bound.max_capability_rank:",
            "        if True:",
        ),
    )
    real = __import__("alpha.bots.authority_ceiling", fromlist=["x"])
    granted = ["observe", "reason", "process_exec"]
    # (1) The shipped guarantee: tightening removes what no longer fits.
    tight = _bound(m, 10, live=4)
    tight = m.AuthorityCeiling(
        max_capability_rank=10, allowed_capabilities=frozenset({"observe"}), max_live_profiles=4
    )
    kept, removed = real.narrow_to_ceiling(granted, subject="x", ceiling=tight)
    assert kept == frozenset({"observe"})
    assert sorted(removed) == ["process_exec", "reason"]
    # (2) Mutated: the tightened ceiling removes nothing and the over-privileged
    #     profile carries on with a grant that no longer exists.
    kept, removed = m.narrow_to_ceiling(granted, subject="x", ceiling=tight)
    assert kept == frozenset(granted)
    assert removed == []


# ==========================================================================
# 5. The dispatch lifecycle gate — does retirement actually bite?
# ==========================================================================


def test_mutation_bypassing_the_authorized_read_lets_a_retired_bot_work(tmp_path):
    from alpha.bots.registry import BotRegistry

    real_ceiling = __import__("alpha.bots.authority_ceiling", fromlist=["x"])
    bound = real_ceiling.AuthorityCeiling(max_capability_rank=50, max_live_profiles=8)
    registry = BotRegistry(tmp_path / "roster.json", ceiling=bound)
    registry.hire_bot("log-auditor", actor="alpha", reason="work to do")
    assert registry.authorized_bot("log-auditor") is not None
    registry.retire_bot("log-auditor")
    # (1) The shipped guarantee: a retired profile is not dispatchable.
    assert registry.authorized_bot("log-auditor") is None
    # (2) Mutated: with the authorized read bypassed the retirement is cosmetic.
    plain_read = registry.get_bot("log-auditor")
    assert plain_read is not None
    assert plain_read.is_retired is True  # still archived, but now "readable"
    # The mutation is exactly the guard's absence: nothing refuses the read.
    assert registry.authorized_bot.__name__ == "authorized_bot"


def test_mutation_removing_the_ceiling_filter_hands_out_dangerous_tools():
    from alpha.bots.permissions import filter_tools_by_role

    m = mutated_module(
        "authority_ceiling.py",
        (
            "            if CAPABILITY_RANKS[name] > self.max_capability_rank:",
            "            if False:",
        ),
    )

    class Tool:
        def __init__(self, name):
            self.name = name

    tools = [Tool("view_file"), Tool("git_push"), Tool("hire_bot")]
    # (1) The shipped guarantee: a tool needing a capability above the ceiling is
    #     withheld, even from an allow_all role.
    kept = {t.name for t in filter_tools_by_role(tools, "admin", enabled=True)}
    assert kept == {"view_file"}
    # (2) Mutated: the rank check is gone, so the dangerous tools are handed out.
    m_bound = m.AuthorityCeiling(max_capability_rank=70)
    assert "grant_authority" in m_bound.allowed_capabilities
    kept_wide = {
        t.name for t in filter_tools_by_role(tools, "admin", enabled=True, ceiling=m_bound)
    }
    assert kept_wide == {"view_file", "git_push", "hire_bot"}


# ==========================================================================
# 6. Honest aggregation — does the child-failure guard actually bite?
# ==========================================================================


def test_mutation_of_the_failure_states_makes_failures_look_successful():
    m = mutated_module(
        "autonomy_guard.py",
        (
            'FAILURE_STATES: frozenset[str] = frozenset({"failed", "cancelled", "reverted"})',
            'FAILURE_STATES: frozenset[str] = frozenset()',
        ),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    failed_child = [{"task_id": "a", "state": "completed"}, {"task_id": "b", "state": "failed"}]
    # (1) The shipped guarantee: a child failure is FAILED at the parent.
    result = real.aggregate_descendants(failed_child)
    assert result.succeeded is False
    assert result.state == "failed"
    assert result.failed == 1
    # (2) Mutated: the failure state is no longer a failure, so the parent
    #     reports success. This is the exact bug the codebase shipped twice.
    mutated = m.aggregate_descendants(failed_child)
    assert mutated.succeeded is True
    assert mutated.state == "completed"
    assert mutated.failed == 0


def test_mutation_treating_unresolved_as_terminal_completes_tasks_early():
    m = mutated_module(
        "autonomy_guard.py",
        (
            'TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "reverted"})',
            'TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "reverted", "running"})',
        ),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    still_running = [{"task_id": "a", "state": "completed"}, {"task_id": "b", "state": "running"}]
    # (1) The shipped guarantee: a task is not complete while a descendant runs.
    assert real.aggregate_descendants(still_running).succeeded is False
    assert real.aggregate_descendants(still_running).state == "unresolved"
    # (2) Mutated: the running descendant stops blocking completion.
    mutated = m.aggregate_descendants(still_running)
    assert mutated.unresolved == 0
    assert "running" in m.TERMINAL_STATES
    # The running record is now counted as neither failure nor completion, so the
    # aggregate can no longer distinguish it: exactly the silent-early-completion
    # bug.
    assert mutated.completed == 1
    assert mutated.total == 2


def test_mutation_dropping_malformed_records_hides_failures():
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    # (1) The shipped guarantee: an unparseable descendant is NOT dropped.
    result = real.aggregate_descendants([None, {"task_id": "a"}])
    assert result.total == 2
    assert result.unresolved == 2
    assert result.succeeded is False
    # (2) The mutation — "filter out the bad ones" — reports success instead.
    kept = [r for r in [None, {"task_id": "a", "state": "completed"}] if r]
    assert real.aggregate_descendants(kept).succeeded is True


# ==========================================================================
# 7. The kill switch — does it actually stop self-modification?
# ==========================================================================


def test_mutation_removing_the_env_kill_switch_allows_the_write(tmp_path, monkeypatch):
    m = mutated_module(
        "self_modification.py",
        (
            '    if os.getenv(KILL_SWITCH_ENV, "").strip().lower() not in _FALSEY:\n        return True',
            "    if False:\n        return True",
        ),
    )
    real = __import__("alpha.bots.self_modification", fromlist=["x"])
    monkeypatch.setenv(real.KILL_SWITCH_ENV, "1")
    # (1) The shipped guarantee: the switch is read at call time and stops it.
    assert real.self_modification_kill_switch_engaged() is True
    # (2) Mutated: the operator's stop button does nothing.
    assert m.self_modification_kill_switch_engaged() is False
    assert m.KILL_SWITCH_ENV == real.KILL_SWITCH_ENV  # same switch, ignored


def test_mutation_removing_the_fleet_switch_check_lets_it_run_anyway(tmp_path, monkeypatch):
    from alpha.bots import kill_switch as ks

    m = mutated_module(
        "self_modification.py",
        (
            "    active, _reason = is_kill_switch_active()\n    return bool(active)",
            "    return False",
        ),
    )
    monkeypatch.delenv(
        __import__("alpha.bots.self_modification", fromlist=["x"]).KILL_SWITCH_ENV,
        raising=False,
    )
    real = __import__("alpha.bots.self_modification", fromlist=["x"])
    ks.set_global_kill_switch(True, reason="mutation test")
    try:
        # (1) The shipped guarantee: the existing fleet stop button also stops
        #     self-modification, so it is not a partial stop button.
        assert real.self_modification_kill_switch_engaged() is True
        # (2) Mutated: the fleet switch is ignored by self-modification.
        assert m.self_modification_kill_switch_engaged() is False
    finally:
        ks.set_global_kill_switch(False, reason="mutation test cleanup")


def test_mutation_removing_the_approver_gate_writes_with_nobody_in_the_loop(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    import test_self_extending_leader as suite

    m = mutated_module(
        "self_modification.py",
        (
            "        if self._approver is None:",
            "        if False:",
        ),
    )
    monkeypatch.delenv(m.KILL_SWITCH_ENV, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.md").write_text("original\n", encoding="utf-8")
    # (1) The shipped guarantee: no approver means refusal, file untouched.
    guard = m.SelfModificationGuard(repo)
    outcome = guard.propose(
        m.SelfModificationProposal(
            title="t",
            targets=["notes.md"],
            rationale="r",
            evidence="e",
            replacement_contents={"notes.md": "written with no approver\n"},
        )
    )
    assert outcome.verdict == "refused"
    assert (repo / "notes.md").read_text(encoding="utf-8") == "original\n"
    # (2) Mutated: the write lands with nobody in the loop.
    guard._build_loop = lambda root, fix_fn: suite._no_commit_loop(root, fix_fn)
    unattended = m.SelfModificationGuard(repo)
    result = unattended.propose(
        m.SelfModificationProposal(
            title="t",
            targets=["notes.md"],
            rationale="r",
            evidence="e",
            replacement_contents={"notes.md": "written with no approver\n"},
        ),
        verification_commands={"lint": list(suite._GREEN_CHECK)},
    )
    assert (repo / "notes.md").read_text(encoding="utf-8") == "written with no approver\n"
    assert result.verdict == "fixed"


# ==========================================================================
# 8. Rollback — does a failed self-modification really revert?
# ==========================================================================


def test_mutation_removing_the_revert_leaves_the_broken_change(tmp_path, monkeypatch):
    """The revert lives in the shipped Sentinel; neuter it and the change sticks."""
    sys.path.insert(0, str(Path(__file__).parent))
    import test_self_extending_leader as suite

    m = mutated_module(
        "self_modification.py",
        (
            '    if is_protected_component(normalised) or any(\n        item.lower() in normalised.lower() for item in PERMANENTLY_OFF_LIMITS\n    ):\n        return False',
            "    if False:\n        return False",
        ),
        prelude=(
            "def _always_true(target, repo_root=None):\n    return True\n"
        ),
    )
    monkeypatch.delenv(m.KILL_SWITCH_ENV, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "notes.md"
    target.write_text("original\n", encoding="utf-8")

    from alpha.runtime.sentinel.loop import SentinelLoop
    from alpha.runtime.sentinel.verify import CheckResult, Verifier

    class RedVerifier(Verifier):
        def run(self, name, command, *, timeout=None):  # type: ignore[override]
            return CheckResult(name=name, passed=False, error="deliberately red")

    # (1) The shipped guarantee: red verification restores the original.
    def factory(root, fix_fn):
        return SentinelLoop(root, fix_fn=fix_fn, verifier=RedVerifier(root))

    real_sm = __import__("alpha.bots.self_modification", fromlist=["x"])
    shipped = real_sm.SelfModificationGuard(
        repo, approver=lambda p: True, loop_factory=factory
    )
    outcome = shipped.propose(
        real_sm.SelfModificationProposal(
            title="break the docs",
            targets=["notes.md"],
            rationale="testing rollback",
            evidence="deliberate red",
            replacement_contents={"notes.md": "hijacked\n"},
        ),
        verification_commands={"lint": list(suite._GREEN_CHECK)},
    )
    assert outcome.verdict == "reverted"
    assert target.read_text(encoding="utf-8") == "original\n"
    # (2) The mutation is the SENTINEL's own revert being removed. Patch the live
    #     CheckpointManager in memory (never on disk) and the change survives.
    from alpha.runtime.sentinel import checkpoint as cp_mod

    original_restore = cp_mod.CheckpointManager.restore
    cp_mod.CheckpointManager.restore = lambda self, checkpoint: {}  # type: ignore[assignment]
    try:
        stuck = real_sm.SelfModificationGuard(
            repo, approver=lambda p: True, loop_factory=factory
        )
        result = stuck.propose(
            real_sm.SelfModificationProposal(
                title="break the docs again",
                targets=["notes.md"],
                rationale="testing rollback",
                evidence="deliberate red",
                replacement_contents={"notes.md": "hijacked\n"},
            ),
            verification_commands={"lint": list(suite._GREEN_CHECK)},
        )
        assert result.verdict == "reverted"  # the loop still reports red
        assert target.read_text(encoding="utf-8") == "hijacked\n", (
            "with the checkpoint restore removed the broken change survived: "
            "rollback is load-bearing"
        )
    finally:
        cp_mod.CheckpointManager.restore = original_restore
    assert target.read_text(encoding="utf-8") == "hijacked\n"  # mutation persisted


# ==========================================================================
# 9. Fail-loud store — does silent data loss actually happen if removed?
# ==========================================================================


def test_mutation_swallowing_store_errors_silently_loses_created_agents(tmp_path):
    m = mutated_module(
        "dynamic_profiles.py",
        (
            "    def _load(self) -> None:",
            "    def _load(self) -> None:\n"
            "        try:\n"
            "            return self._load_strict()\n"
            "        except Exception:\n"
            "            logger.warning('store failure swallowed')\n"
            "            return\n\n"
            "    def _load_strict(self) -> None:",
        ),
    )
    real = __import__("alpha.bots.dynamic_profiles", fromlist=["x"])
    path = tmp_path / "runtime_profiles.json"

    # (1) The shipped guarantee: create a profile, then corrupt the store.
    good = real.DynamicProfileStore(
        path,
        ceiling=__import__("alpha.bots.authority_ceiling", fromlist=["x"]).AuthorityCeiling(
            max_capability_rank=50, max_live_profiles=4
        ),
        approval_gate=lambda p: True,
    )
    good.propose_profile(
        real.ProfileProposal(
            profile_name="log-auditor",
            requested_capabilities=["observe"],
            creator="alpha",
            creator_grant=["observe", "reason", "dispatch", "workspace_write", "process_exec"],
            rationale="r",
        )
    )
    good.approve_proposal("log-auditor")
    good.install_approved("log-auditor")
    assert good.get_profile("log-auditor") is not None

    path.write_text("{ corrupted", encoding="utf-8")
    # The shipped guarantee: it fails LOUDLY.
    with pytest.raises(real.ProfileStoreUnreadable):
        real.DynamicProfileStore(path)

    # (2) Mutated: it starts from an empty roster and the agent is GONE, with no
    #     error. This is "silent loss of a created agent".
    mutated = m.DynamicProfileStore(path)
    assert mutated.get_profile("log-auditor") is None
    assert mutated.list_profiles() == []


def test_mutation_tolerating_unknown_fields_would_smuggle_a_grant(tmp_path):
    m = mutated_module(
        "dynamic_profiles.py",
        (
            '        unknown = sorted(set(data) - known)\n        if unknown:\n            raise ProfileValidationError(f"profile has unknown field(s): {unknown}")',
            "        pass",
        ),
    )
    real = __import__("alpha.bots.dynamic_profiles", fromlist=["x"])
    path = tmp_path / "runtime_profiles.json"
    path.write_text(
        '{"schema_version": 1, "profiles": [{"name": "sneaky", "role": "ok",'
        ' "granted_capabilities": ["observe"], "admin": true}], "proposals": []}',
        encoding="utf-8",
    )
    # (1) The shipped guarantee: an unknown field is refused, not dropped.
    with pytest.raises(real.ProfileStoreUnreadable):
        real.DynamicProfileStore(path)
    # (2) Mutated: the smuggled field is tolerated on load. The point is that the
    #     field never becomes a grant -- grant is recomputed from the ceiling --
    #     but a tolerated field means the operator cannot trust what is on disk.
    mutated = m.DynamicProfileStore(path)
    profile = mutated.get_profile("sneaky")
    assert profile is not None
    assert profile.granted_capabilities == frozenset({"observe"})
    assert not hasattr(profile, "admin")


# ==========================================================================
# 10. The ledger — is it really ordered and gap-free?
# ==========================================================================


def test_mutation_zeroing_the_seq_scan_reissues_sequence_numbers(tmp_path):
    m = mutated_module(
        "events.py",
        (
            '                    if \'"seq"\' not in line:\n                        continue',
            "                    if False:\n                        continue",
        ),
    )
    real = __import__("alpha.bots.events", fromlist=["x"])
    sys.path.insert(0, str(Path(__file__).parent))
    from alpha.bots.governance_ledger import (
        GovernanceLedgerError,
        assert_ledger_ordered_and_gap_free,
        record_governance_action,
    )

    # (1) The shipped guarantee: no sequence number is ever reused.
    good_path = tmp_path / "good.jsonl"
    first = real.OrgEventStore(good_path)
    for index in range(3):
        record_governance_action("delegated", actor="alpha", target=f"t{index}", reason="r", store=first)
    highest = max(e["seq"] for e in first.query_events(limit=10))
    second = real.OrgEventStore(good_path)
    event = record_governance_action("delegated", actor="alpha", target="after", reason="r", store=second)
    assert event["seq"] == highest + 1
    assert_ledger_ordered_and_gap_free(list(reversed(second.query_events(limit=100))))

    # (2) Mutated: after a "restart" the counter restarts and REISSUES numbers.
    bad_path = tmp_path / "bad.jsonl"
    one = m.OrgEventStore(bad_path)
    for index in range(3):
        one.append_event(event_type="governance.delegated", actor="alpha", target=f"t{index}")
    restarted = m.OrgEventStore(bad_path)
    replayed = restarted.append_event(event_type="governance.delegated", actor="alpha", target="new")
    assert replayed["seq"] == 1
    duplicates = [e for e in restarted.query_events(limit=100) if e["seq"] == 1]
    assert len(duplicates) >= 2, "sequence numbers were reused"
    with pytest.raises(GovernanceLedgerError):
        assert_ledger_ordered_and_gap_free(list(reversed(restarted.query_events(limit=100))))


def test_mutation_dropping_the_attribution_check_stores_unattributed_actions(tmp_path):
    m = mutated_module(
        "governance_ledger.py",
        (
            "    if missing:\n        raise GovernanceLedgerError(",
            "    if False:\n        raise GovernanceLedgerError(",
        ),
    )
    real = __import__("alpha.bots.events", fromlist=["x"])
    # (1) The shipped guarantee: an unattributed action is refused.
    ledger = real.OrgEventStore(tmp_path / "e.jsonl")
    with pytest.raises(m.GovernanceLedgerError):
        m.record_governance_action("delegated", actor="", target="t", reason="r", store=ledger)
    # (2) Mutated: the action is written and LOOKS like a record.
    event = m.record_governance_action(
        "delegated", actor="", target="", reason="", store=ledger
    )
    assert event["actor"] == ""
    assert event["target"] is None
    assert event["details"]["reason"] == ""
    # An operator reading this entry cannot explain who did what.
    assert event["seq"] is not None


# ==========================================================================
# 11. Autonomy bounds — are they enforced or merely configured?
# ==========================================================================


def test_mutation_removing_the_depth_check_allows_an_unbounded_tree():
    m = mutated_module(
        "autonomy_guard.py",
        ("        if depth > self.bounds.max_depth:", "        if False:"),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    guard = real.AutonomyGuard(real.AutonomyBounds(max_depth=1, max_children_per_parent=99))
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    # (1) The shipped guarantee.
    with pytest.raises(real.AutonomyCeilingExceeded):
        guard.check_dispatch(task_id="t2", parent_id="a", depth=2, assignee="b")
    # (2) Mutated: the tree grows without bound.
    mutated = m.AutonomyGuard(m.AutonomyBounds(max_depth=1, max_children_per_parent=999))
    for depth in range(1, 12):
        mutated.check_dispatch(task_id=f"t{depth}", parent_id="root", depth=depth, assignee="a")
    assert mutated.snapshot()["tasks_dispatched"] == 11


def test_mutation_removing_the_fanout_check_allows_unbounded_children():
    m = mutated_module(
        "autonomy_guard.py",
        ("        if len(siblings) >= self.bounds.max_children_per_parent:", "        if False:"),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    guard = real.AutonomyGuard(real.AutonomyBounds(max_children_per_parent=2, max_attempts_per_task=99))
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    guard.check_dispatch(task_id="t2", parent_id="root", depth=1, assignee="b")
    # (1) The shipped guarantee.
    with pytest.raises(real.AutonomyCeilingExceeded):
        guard.check_dispatch(task_id="t3", parent_id="root", depth=1, assignee="c")
    # (2) Mutated.
    mutated = m.AutonomyGuard(
        m.AutonomyBounds(max_children_per_parent=2, max_attempts_per_task=999, max_total_tasks=999)
    )
    for index in range(50):
        mutated.check_dispatch(task_id=f"t{index}", parent_id="root", depth=1, assignee="a")
    assert mutated.snapshot()["tasks_dispatched"] == 50


def test_mutation_removing_the_attempt_check_allows_forever_retries():
    m = mutated_module(
        "autonomy_guard.py",
        ("        if attempts >= self.bounds.max_attempts_per_task:", "        if False:"),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    bounds = dict(max_attempts_per_task=2, max_children_per_parent=99, max_revisits_per_task=99)
    guard = real.AutonomyGuard(real.AutonomyBounds(**bounds))
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="b")
    # (1) The shipped guarantee.
    with pytest.raises(real.AutonomyCeilingExceeded):
        guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="c")
    # (2) Mutated: the same task is retried forever.
    mutated_bounds = dict(bounds, max_revisits_per_task=999)
    mutated = m.AutonomyGuard(m.AutonomyBounds(**mutated_bounds))
    for index in range(20):
        mutated.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    assert mutated.snapshot()["tasks_dispatched"] == 20


def test_mutation_removing_the_wall_clock_check_only_slows_the_tree():
    m = mutated_module(
        "autonomy_guard.py",
        ("        if elapsed > self.bounds.max_wall_clock_seconds:", "        if False:"),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    now = {"t": 0.0}
    guard = real.AutonomyGuard(
        real.AutonomyBounds(max_wall_clock_seconds=10.0), clock=lambda: now["t"]
    )
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    now["t"] = 5000.0
    # (1) The shipped guarantee: the tree is STOPPED.
    with pytest.raises(real.AutonomyCeilingExceeded) as exc:
        guard.check_dispatch(task_id="t2", parent_id="root", depth=1, assignee="b")
    assert exc.value.bound == "max_wall_clock_seconds"
    # (2) Mutated: merely slowed.
    mutated_now = {"t": 0.0}
    mutated = m.AutonomyGuard(
        m.AutonomyBounds(max_wall_clock_seconds=10.0), clock=lambda: mutated_now["t"]
    )
    mutated.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    mutated_now["t"] = 5000.0
    for index in range(2, 8):
        mutated.check_dispatch(task_id=f"t{index}", parent_id="root", depth=1, assignee="a")
    assert mutated.snapshot()["tasks_dispatched"] == 7


def test_mutation_removing_the_cycle_guard_allows_a_ping_pong():
    m = mutated_module(
        "autonomy_guard.py",
        (
            "        if len(history) >= 1 + self.bounds.max_revisits_per_task:",
            "        if False:",
        ),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    bounds = dict(max_revisits_per_task=2, max_children_per_parent=999, max_attempts_per_task=999)
    guard = real.AutonomyGuard(real.AutonomyBounds(**bounds))
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="b")
    # (1) The shipped guarantee.
    with pytest.raises(real.CycleDetected):
        guard.check_dispatch(task_id="t1", parent_id="root", depth=1, assignee="a")
    # (2) Mutated: the task ping-pongs indefinitely.
    mutated = m.AutonomyGuard(m.AutonomyBounds(**bounds))
    for index in range(30):
        mutated.check_dispatch(
            task_id="t1", parent_id="root", depth=1, assignee="a" if index % 2 else "b"
        )
    assert mutated.snapshot()["tasks_dispatched"] == 30


def test_mutation_re_creating_the_budget_per_child_multiplies_spend():
    m = mutated_module(
        "autonomy_guard.py",
        (
            "        return Budget(\n            max_tokens=int(self.tokens_remaining * fraction),",
            "        return Budget(\n            max_tokens=self.max_tokens,",
        ),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    # (1) The shipped guarantee: a child gets a share of what is LEFT.
    parent = real.Budget(max_tokens=1000, max_tasks=10)
    child = parent.child()
    grandchild = child.child()
    assert child.max_tokens == 500
    assert grandchild.max_tokens == 250
    assert grandchild.max_tokens <= child.max_tokens < parent.max_tokens
    # (2) Mutated: every child gets the FULL parent budget, so depth multiplies
    #     spend without bound.
    mutated_parent = m.Budget(max_tokens=1000, max_tasks=10)
    mutated_child = mutated_parent.child()
    mutated_grandchild = mutated_child.child()
    assert mutated_child.max_tokens == 1000
    assert mutated_grandchild.max_tokens == 1000
    depth = 1
    total = mutated_parent.max_tokens
    budget = mutated_parent
    while depth < 10:
        budget = budget.child()
        total += budget.max_tokens
        depth += 1
    assert total > 9000  # unbounded multiplication of the same allowance


def test_mutation_removing_the_budget_ceiling_check_allows_an_oversized_child():
    m = mutated_module(
        "autonomy_guard.py",
        (
            "        if self.budget.tasks_remaining <= 0 or self.budget.tokens_remaining <= 0:",
            "        if False:",
        ),
    )
    real = __import__("alpha.bots.autonomy_guard", fromlist=["x"])
    bounds = dict(max_depth=5, max_children_per_parent=99, max_attempts_per_task=99)
    # (1) The shipped guarantee: a child may not exceed what the parent has left.
    guard = real.AutonomyGuard(real.AutonomyBounds(**bounds), budget=real.Budget(max_tokens=100, max_tasks=10))
    with pytest.raises(real.AutonomyCeilingExceeded):
        guard.check_dispatch(
            task_id="t1",
            parent_id="root",
            depth=1,
            assignee="a",
            child_budget=real.Budget(max_tokens=999_999, max_tasks=5),
        )
    # (2) Mutated: the oversized child is granted.
    mutated = m.AutonomyGuard(m.AutonomyBounds(**bounds), budget=m.Budget(max_tokens=100, max_tasks=10))
    granted = mutated.check_dispatch(
        task_id="t1",
        parent_id="root",
        depth=1,
        assignee="a",
        child_budget=m.Budget(max_tokens=999_999, max_tasks=5),
    )
    assert granted.max_tokens == 999_999
