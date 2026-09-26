"""Honesty pins for cognitive-engine bootstrap beliefs (wave-2 finding F18).

Startup-seeded beliefs are UNVERIFIED assumptions, mirroring the
established ``alpha/epistemics/engine.py`` pattern (seeds as HYPOTHESIS at
the neutral 0.5 prior, empty evidence ledgers, FACT only via real
evidence). They must be seeded at modest base rates (0.5-0.7 — never the
old near-certain 0.98/0.95/0.96/0.92), carry the explicit
``source="bootstrap-assumption"`` provenance, keep empty evidence
ledgers, and not be tagged ``pinned`` (a tag asserting permanence these
seeds never earned). Seeded procedural skills must start with zero
success/failure counts — usage stats may only come from real executions.

Disclosed test inversion: no existing test pinned the old bootstrap
confidences (verified by repo-wide grep on the seeded subjects and
values), so there was zero honest coverage; these pins replace that
absence rather than flipping a dishonest expectation.
"""

from pathlib import Path

from alpha.memory.cognitive.engine import CognitiveMemorySystem
from alpha.memory.cognitive.models import BeliefStatus

_BOOTSTRAP_SUBJECTS = {
    "AgentArchitecture",
    "MemoryRetrieval",
    "CognitiveConsolidation",
    "EpistemicBeliefs",
}

_BOOTSTRAP_SKILLS = {
    "verify_code_with_targeted_pytest",
    "reconcile_contradictory_user_preferences",
}


def test_bootstrap_beliefs_are_modest_sourced_assumptions(tmp_path: Path):
    system = CognitiveMemorySystem(storage_dir=tmp_path)
    nodes = [n for n in system.semantic_graph.list_nodes(limit=100) if n.subject in _BOOTSTRAP_SUBJECTS]
    assert {n.subject for n in nodes} == _BOOTSTRAP_SUBJECTS

    for node in nodes:
        # Modest base rate only — no near-certain startup knowledge.
        assert 0.5 <= node.confidence <= 0.7, f"{node.subject} seeded at {node.confidence}, above a base-rate prior"
        # Explicit provenance: readers can tell this is an assumption.
        assert node.source == "bootstrap-assumption"
        # No evidence was ever gathered for these at startup.
        assert node.evidence == []
        # "pinned" asserted permanence/certainty the seeds never earned.
        assert "pinned" not in node.tags
        assert "bootstrap" in node.tags
        assert node.status is BeliefStatus.ACTIVE


def test_bootstrap_belief_provenance_survives_persistence(tmp_path: Path):
    system = CognitiveMemorySystem(storage_dir=tmp_path)
    system.save_to_disk()

    reloaded = CognitiveMemorySystem(storage_dir=tmp_path)
    reloaded_nodes = {n.subject: n for n in reloaded.semantic_graph.list_nodes(limit=100) if n.subject in _BOOTSTRAP_SUBJECTS}
    assert set(reloaded_nodes) == _BOOTSTRAP_SUBJECTS
    for subject, node in reloaded_nodes.items():
        assert node.source == "bootstrap-assumption", f"{subject} lost its provenance across save/load"
        assert 0.5 <= node.confidence <= 0.7
        assert node.evidence == []


def test_bootstrap_skills_have_no_fabricated_success_history(tmp_path: Path):
    system = CognitiveMemorySystem(storage_dir=tmp_path)
    skills = [s for s in system.procedural_mem.list_skills(limit=100) if s.name in _BOOTSTRAP_SKILLS]
    assert {s.name for s in skills} == _BOOTSTRAP_SKILLS
    for skill in skills:
        # Previously seeded success_count=5/3 — executions that never
        # happened. Usage stats must start at zero.
        assert skill.success_count == 0, f"{skill.name} claims {skill.success_count} successes it never had"
        assert skill.failure_count == 0
        assert skill.last_executed_at == 0.0
