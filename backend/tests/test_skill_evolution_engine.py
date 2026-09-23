"""Tests for the skill self-evolution engine (evidence -> versioned candidate
-> offline validation -> policy gate -> promote/rollback).

Honesty rules pinned here:
- the active/canonical SKILL.md is NEVER mutated before promotion;
- a candidate without a declared test suite records validation_kind
  "structural-only" with an explicit "NOT verified" note;
- evaluation errors reject under security_fail_closed=true and stay
  unpromotable (never "validated") under security_fail_closed=false;
- promotion requires recorded evaluation evidence, and auto_promote
  defaults off (explicit approve required);
- absence of a moderation model records "not_configured" - never a pass.

Isolation: engine tests pass store_dir/skills_root constructor params (no
global env fixtures). The endpoint test monkeypatches AGENT_WORKSPACE_HOME
(function-scoped) for runtime_home(). The ONLY stubbed function anywhere is
the module-level invoke seam ``evolution_engine.moderation_invoke``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alpha.config.skill_evolution_config import SkillEvolutionConfig
from alpha.skills import evolution_engine
from alpha.skills.evolution_engine import (
    PROMOTED,
    PROPOSED,
    REJECTED,
    ROLLED_BACK,
    VALIDATED,
    VALIDATION_STRUCTURAL_AND_SUITE,
    VALIDATION_STRUCTURAL_ONLY,
    InvalidTransitionError,
    SkillEvolutionDisabledError,
    SkillEvolutionEngine,
    UnknownProposalError,
    _frontmatter_version,
)
from app.gateway.auth.models import User
from app.gateway.deps import get_config
from app.gateway.routers import skills_workshop

ACTIVE_MD = (
    "---\n"
    "name: demo-skill\n"
    "description: Does demo work.\n"
    "version: 0.1.0\n"
    "---\n\n"
    "# demo-skill\n\n"
    "Original procedure step.\n"
)

CANDIDATE_MD = (
    "---\n"
    "name: demo-skill\n"
    "description: Does demo work better.\n"
    "---\n\n"
    "# demo-skill\n\n"
    "Improved procedure step.\n"
)

EVIDENCE = [
    "usage: demo-skill failed 3 of 5 recent tasks (usage tracker)",
    "curator: idle report 2026-09-20",
]


def _quoted_python() -> str:
    exe = sys.executable
    return f'"{exe}"' if " " in exe else exe


def _suite_candidate(command: str) -> str:
    return (
        "---\n"
        "name: demo-skill\n"
        "description: Does demo work better.\n"
        f"test-command: '{command}'\n"
        "---\n\n"
        "# demo-skill\n\n"
        "Improved procedure step.\n"
    )


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    skill_dir = root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(ACTIVE_MD, encoding="utf-8")
    (skill_dir / "check_pass.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (skill_dir / "check_fail.py").write_text("import sys\nprint('failure mode', file=sys.stderr)\nsys.exit(1)\n", encoding="utf-8")
    return root


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "skill_evolution"


def _engine(skills_root: Path, store_dir: Path, **config_overrides: object) -> SkillEvolutionEngine:
    config = SkillEvolutionConfig(**{"enabled": True, **config_overrides})  # type: ignore[arg-type]
    return SkillEvolutionEngine(store_dir=store_dir, skills_root=skills_root, config=config)


def _allow_seam(content: str, *, executable: bool, model_name: str | None) -> dict:
    return {"status": "allow", "model": model_name, "reason": "stubbed moderation decision"}


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def test_propose_from_evidence_stores_versioned_candidate_without_touching_active(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    active_before = (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")

    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE, created_by="tester")

    assert record.status == PROPOSED
    assert [item["ref"] for item in record.evidence] == EVIDENCE
    assert record.base_version == "0.1.0"
    assert record.candidate_version == "0.1.1"
    assert "Improved procedure step." in record.candidate_md
    assert _frontmatter_version(record.candidate_md) == "0.1.1"
    assert record.active_sha256
    assert record.history and record.history[-1]["event"] == "proposed"

    # Active/canonical body untouched before promotion.
    assert (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == active_before

    # Persisted under the store root (atomic tmp+replace leaves no .tmp behind).
    stored = store_dir / f"{record.id}.json"
    assert stored.is_file()
    assert list(store_dir.glob("*.tmp")) == []
    persisted = json.loads(stored.read_text(encoding="utf-8"))
    assert persisted["status"] == PROPOSED
    assert persisted["evidence"] == record.to_dict()["evidence"]


def test_propose_guards_evidence_and_disabled_config(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    with pytest.raises(ValueError, match="evidence reference"):
        engine.propose("demo-skill", CANDIDATE_MD, [])
    with pytest.raises(ValueError, match="evidence reference"):
        engine.propose("demo-skill", CANDIDATE_MD, ["   "])
    with pytest.raises(ValueError, match="not found"):
        engine.propose("ghost-skill", CANDIDATE_MD, EVIDENCE)

    disabled = SkillEvolutionEngine(
        store_dir=store_dir,
        skills_root=skills_root,
        config=SkillEvolutionConfig(enabled=False),
    )
    with pytest.raises(SkillEvolutionDisabledError, match="disabled"):
        disabled.propose("demo-skill", CANDIDATE_MD, EVIDENCE)

    with pytest.raises(UnknownProposalError, match="Unknown skill-evolution proposal"):
        engine.get("ab" * 16)


# ---------------------------------------------------------------------------
# evaluate - validation kind honesty
# ---------------------------------------------------------------------------


def test_evaluate_without_declared_suite_records_structural_only(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)  # no moderation model configured
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)

    evaluated = engine.evaluate(record.id)

    assert evaluated.status == VALIDATED, evaluated.reject_reason
    evaluation = evaluated.evaluation
    assert evaluation is not None
    assert evaluation["error"] is None
    assert evaluation["validation_kind"] == VALIDATION_STRUCTURAL_ONLY
    assert evaluation["suite"] is None
    assert evaluation["checks_total"] >= 9
    assert evaluation["checks_passed"] == evaluation["checks_total"]
    assert evaluation["findings"] == []
    # Score is computed from the checks that actually ran - never invented.
    assert evaluation["score"] == round(evaluation["checks_passed"] / evaluation["checks_total"], 4)

    # Moderation absence is recorded as absence, never as a pass.
    assert evaluated.moderation is not None
    assert evaluated.moderation["status"] == "not_configured"
    assert any("validation was structural-only" in note and "NOT verified" in note for note in evaluated.notes)
    assert any("moderation was NOT performed" in note and "not a moderation pass" in note for note in evaluated.notes)
    assert any(f"checks_passed {evaluation['checks_passed']} / checks_total {evaluation['checks_total']}" in note for note in evaluated.notes)
    # Still promotable-status only after this honest evaluation.
    assert evaluated.status == VALIDATED


def test_evaluate_rejects_structural_failure_with_real_reason(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    mismatched = CANDIDATE_MD.replace("name: demo-skill", "name: other-skill")
    record = engine.propose("demo-skill", mismatched, EVIDENCE)

    evaluated = engine.evaluate(record.id)

    assert evaluated.status == REJECTED
    assert evaluated.reject_reason is not None
    assert "validation failed" in evaluated.reject_reason
    assert "does not match target skill 'demo-skill'" in evaluated.reject_reason
    # Re-evaluating a terminal record is refused honestly.
    with pytest.raises(InvalidTransitionError, match="only a 'proposed' proposal"):
        engine.evaluate(record.id)


def test_evaluate_declared_suite_runs_and_records_real_exit_code(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    pass_cmd = f"{_quoted_python()} check_pass.py"
    passing = engine.propose("demo-skill", _suite_candidate(pass_cmd), EVIDENCE)

    evaluated = engine.evaluate(passing.id)

    assert evaluated.status == VALIDATED, evaluated.reject_reason
    assert evaluated.evaluation["validation_kind"] == VALIDATION_STRUCTURAL_AND_SUITE
    suite = evaluated.evaluation["suite"]
    assert suite["ran_against"] == "staged-candidate"
    assert suite["timed_out"] is False
    assert suite["exit_code"] == 0
    assert any("only the declared command was exercised" in note and "NOT verified" in note for note in evaluated.notes)


def test_evaluate_declared_suite_failure_rejects_with_real_exit_code(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    fail_cmd = f"{_quoted_python()} check_fail.py"
    record = engine.propose("demo-skill", _suite_candidate(fail_cmd), EVIDENCE)

    evaluated = engine.evaluate(record.id)

    assert evaluated.status == REJECTED
    assert evaluated.evaluation["suite"]["exit_code"] == 1
    assert "exit code 1" in (evaluated.reject_reason or "")


# ---------------------------------------------------------------------------
# evaluate - fail_closed evaluation-error semantics
# ---------------------------------------------------------------------------


def _raising_seam(content: str, *, executable: bool, model_name: str | None) -> dict:
    raise RuntimeError("moderation backend unavailable")


def test_evaluation_error_with_fail_closed_rejects(skills_root, store_dir, monkeypatch):
    monkeypatch.setattr(evolution_engine, "moderation_invoke", _raising_seam)
    engine = _engine(skills_root, store_dir, moderation_model_name="unit-mod")
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)

    evaluated = engine.evaluate(record.id)

    assert evaluated.status == REJECTED
    assert "evaluation error (security_fail_closed=true)" in (evaluated.reject_reason or "")
    assert "moderation backend unavailable" in (evaluated.reject_reason or "")
    assert evaluated.evaluation["error"] is not None
    assert "moderation backend unavailable" in evaluated.evaluation["error"]
    assert evaluated.moderation["status"] == "not_run"
    assert evaluated.evaluation["validation_kind"] is None


def test_evaluation_error_with_fail_open_stays_proposed_and_unpromotable(skills_root, store_dir, monkeypatch):
    monkeypatch.setattr(evolution_engine, "moderation_invoke", _raising_seam)
    engine = _engine(skills_root, store_dir, moderation_model_name="unit-mod", security_fail_closed=False)
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)

    evaluated = engine.evaluate(record.id)

    # Honest fail-open: error recorded, status NOT promoted to validated.
    assert evaluated.status == PROPOSED
    assert evaluated.evaluation["error"] is not None
    assert any("NOT verified" in note and "security_fail_closed=false" in note for note in evaluated.notes)
    # And an unverified proposal can never be promoted.
    with pytest.raises(InvalidTransitionError, match="not 'validated'"):
        engine.promote(record.id, approve=True)


# ---------------------------------------------------------------------------
# promote / rollback
# ---------------------------------------------------------------------------


def test_promote_requires_approval_then_roundtrips_via_store(skills_root, store_dir, monkeypatch):
    monkeypatch.setattr(evolution_engine, "moderation_invoke", _allow_seam)
    engine = _engine(skills_root, store_dir, moderation_model_name="unit-mod")
    active_path = skills_root / "demo-skill" / "SKILL.md"
    active_before = active_path.read_text(encoding="utf-8")
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE, created_by="tester")
    assert engine.evaluate(record.id).status == VALIDATED

    # auto_promote defaults OFF: approve=false holds without touching the skill.
    held = engine.promote(record.id, approve=False)
    assert held.status == VALIDATED
    assert held.reject_reason is None
    assert any("auto_promote is disabled" in note for note in held.notes)
    assert held.history[-1]["event"] == "promotion_held"
    assert active_path.read_text(encoding="utf-8") == active_before

    promoted = engine.promote(record.id, approve=True, actor="admin", reason="reviewed")
    assert promoted.status == PROMOTED
    promoted_md = active_path.read_text(encoding="utf-8")
    assert "Improved procedure step." in promoted_md
    assert _frontmatter_version(promoted_md) == "0.1.1"
    assert promoted.previous_md == active_before
    assert promoted.promoted_sha256

    # Curator version mechanism: freshly promoted skill is pinned.
    curator_state = json.loads((skills_root / ".curator.json").read_text(encoding="utf-8"))
    assert "demo-skill" in curator_state["pinned"]

    # Store roundtrip: a fresh engine (new instance, same store) sees the promotion.
    fresh = _engine(skills_root, store_dir, moderation_model_name="unit-mod")
    assert fresh.get(record.id).status == PROMOTED

    rolled = fresh.rollback(record.id, reason="regression reported", actor="admin")
    assert rolled.status == ROLLED_BACK
    assert active_path.read_text(encoding="utf-8") == active_before
    events = [item["event"] for item in rolled.history]
    assert "promoted" in events and events[-1] == "rolled_back"
    assert _engine(skills_root, store_dir).get(record.id).status == ROLLED_BACK


def test_promote_without_evaluation_evidence_refuses(skills_root, store_dir):
    engine = _engine(skills_root, store_dir)
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)
    # Not evaluated -> refuse; nothing written.
    with pytest.raises(InvalidTransitionError, match="only an evaluated proposal"):
        engine.promote(record.id, approve=True)
    assert (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == ACTIVE_MD


def test_auto_promote_opt_in_still_requires_evaluation_evidence(skills_root, store_dir, monkeypatch):
    monkeypatch.setattr(evolution_engine, "moderation_invoke", _allow_seam)
    engine = _engine(skills_root, store_dir, moderation_model_name="unit-mod", auto_promote=True)
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)
    with pytest.raises(InvalidTransitionError, match="not 'validated'"):
        engine.promote(record.id)  # approve defaults false, but evidence is still missing
    assert (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == ACTIVE_MD
    engine.evaluate(record.id)
    promoted = engine.promote(record.id)  # operator opted in: no approve flag needed
    assert promoted.status == PROMOTED


def test_promote_without_moderation_honors_fail_closed_config(skills_root, store_dir):
    # Default config: security_fail_closed=true and no moderation model.
    engine = _engine(skills_root, store_dir)
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)
    assert engine.evaluate(record.id).status == VALIDATED  # moderation gate is a write gate

    promoted = engine.promote(record.id, approve=True)
    assert promoted.status == REJECTED
    assert "security_fail_closed=true blocks promotion without moderation" in (promoted.reject_reason or "")
    assert (skills_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == ACTIVE_MD


def test_fail_open_non_executable_promotion_records_not_a_pass(skills_root, store_dir):
    engine = _engine(skills_root, store_dir, security_fail_closed=False)  # no moderation model
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)
    assert engine.evaluate(record.id).status == VALIDATED

    promoted = engine.promote(record.id, approve=True)
    assert promoted.status == PROMOTED
    assert any("this is NOT a moderation pass" in note for note in promoted.notes)


def test_rollback_refuses_when_active_modified_after_promotion(skills_root, store_dir, monkeypatch):
    monkeypatch.setattr(evolution_engine, "moderation_invoke", _allow_seam)
    engine = _engine(skills_root, store_dir, moderation_model_name="unit-mod")
    active_path = skills_root / "demo-skill" / "SKILL.md"
    record = engine.propose("demo-skill", CANDIDATE_MD, EVIDENCE)
    assert engine.evaluate(record.id).status == VALIDATED
    assert engine.promote(record.id, approve=True).status == PROMOTED

    active_path.write_text(ACTIVE_MD + "\nHand-edited after promotion.\n", encoding="utf-8")
    with pytest.raises(InvalidTransitionError, match="modified after promotion"):
        engine.rollback(record.id)
    # The later edit was not clobbered.
    assert "Hand-edited after promotion." in active_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# endpoint (single stubbed seam: evolution_engine.moderation_invoke)
# ---------------------------------------------------------------------------


def test_workshop_evolution_endpoints_full_flow(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(home))
    skill_dir = home / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(ACTIVE_MD, encoding="utf-8")

    captured: list[dict] = []

    def fake_moderation(content: str, *, executable: bool, model_name: str | None) -> dict:
        captured.append({"content": content, "executable": executable, "model_name": model_name})
        return {"status": "allow", "model": model_name, "reason": "stubbed moderation decision"}

    monkeypatch.setattr(evolution_engine, "moderation_invoke", fake_moderation)

    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        role = request.headers.get("x-role")
        if role is not None:
            request.state.user = User(
                id="00000000-0000-0000-0000-000000000000",
                email="test@example.com",
                password_hash="x",
                system_role=role,
            )
        request.state.auth_source = request.headers.get("x-auth-source")
        return await call_next(request)

    app.dependency_overrides[get_config] = lambda: SimpleNamespace(
        skill_evolution=SkillEvolutionConfig(enabled=True, moderation_model_name="unit-moderation-model", security_fail_closed=True)
    )
    app.include_router(skills_workshop.router)

    admin = {"x-role": "admin"}
    base = "/api/skills/workshop/evolution"
    active_path = skill_dir / "SKILL.md"
    active_before = active_path.read_text(encoding="utf-8")

    with TestClient(app) as client:
        proposed = client.post(
            base,
            json={"skill_name": "demo-skill", "candidate_markdown": CANDIDATE_MD, "evidence": EVIDENCE},
            headers=admin,
        )
        assert proposed.status_code == 201, proposed.text
        body = proposed.json()
        assert body["status"] == PROPOSED
        assert [item["ref"] for item in body["evidence"]] == EVIDENCE
        proposal_id = body["id"]
        assert (home / "skill_evolution" / f"{proposal_id}.json").is_file()
        assert active_path.read_text(encoding="utf-8") == active_before

        evaluated = client.post(f"{base}/{proposal_id}/evaluate", headers=admin)
        assert evaluated.status_code == 200, evaluated.text
        eval_body = evaluated.json()
        assert eval_body["status"] == VALIDATED, eval_body.get("reject_reason")
        assert eval_body["evaluation"]["validation_kind"] == VALIDATION_STRUCTURAL_ONLY
        assert captured and captured[0]["model_name"] == "unit-moderation-model"

        held = client.post(f"{base}/{proposal_id}/promote", json={"approve": False}, headers=admin)
        assert held.status_code == 200, held.text
        held_body = held.json()
        assert held_body["promoted"] is False
        assert held_body["status"] == VALIDATED
        assert any("auto_promote is disabled" in note for note in held_body["notes"])
        assert active_path.read_text(encoding="utf-8") == active_before

        promoted = client.post(f"{base}/{proposal_id}/promote", json={"approve": True, "reason": "reviewed"}, headers=admin)
        assert promoted.status_code == 200, promoted.text
        prom_body = promoted.json()
        assert prom_body["promoted"] is True
        assert prom_body["status"] == PROMOTED
        assert "Improved procedure step." in active_path.read_text(encoding="utf-8")

        fetched = client.get(f"{base}/{proposal_id}", headers=admin)
        assert fetched.status_code == 200
        assert fetched.json()["status"] == PROMOTED
        listed = client.get(base, headers=admin)
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["proposals"]] == [proposal_id]

        rolled = client.post(f"{base}/{proposal_id}/rollback", json={"reason": "regression reported"}, headers=admin)
        assert rolled.status_code == 200, rolled.text
        assert rolled.json()["rolled_back"] is True
        assert active_path.read_text(encoding="utf-8") == active_before

        # Honest error statuses: unknown id -> 404, non-admin -> 403, disabled engine -> 403.
        missing = client.post(f"{base}/{'ab' * 16}/evaluate", headers=admin)
        assert missing.status_code == 404
        non_admin = client.get(f"{base}/{proposal_id}", headers={"x-role": "user"})
        assert non_admin.status_code == 403
        anonymous = client.get(f"{base}/{proposal_id}")
        assert anonymous.status_code in (401, 403)

    # Store cleanliness: no stray temp files from the atomic writes.
    assert list((home / "skill_evolution").glob("*.tmp")) == []
    assert len(captured) == 1  # moderation seam stubbed ONCE for the whole flow
