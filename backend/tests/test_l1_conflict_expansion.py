"""Conflict-candidate expansion for implicit invalidations (adoption slice #2).

Design: the hard regime of knowledge updates is the IMPLICIT conflict —
"The export moved to Wednesdays" never lexically negates "The export runs on
Mondays" (the STALE/CUPMem finding). Conflict detection can only act on the
candidates recall gives it, and ``dedup_top_k=5`` silently misses a conflict
target that ranks below the cut. This slice adds a bounded, LLM-free widening:
when ``memory.l1.conflict_expansion`` is on, the dedup recall also surfaces up
to five lower-ranked candidates that pass a deterministic "same subject,
different value" shape check, giving the adjudicator a chance to see the
implicit conflict. The prefilter is over-inclusive BY DESIGN — the LLM
adjudicator, not the prefilter, decides what a conflict is.

Properties pinned here:

* default OFF — byte-identical candidate recall;
* the widening is deterministic and bounded (pool cap = ``dedup_top_k``
  + ``CONFLICT_EXPANSION_EXTRA``, documented constants);
* a candidate that shares no subject never leaks into the prompt;
* end-to-end, the gate turns a silent duplicate into an ``update``.

Every test is hermetic: an explicit ``MemoryConfig`` rooted in ``tmp_path``,
a scripted LLM fake, and a seeded store. No timer, no sleeping, no network.
"""

from __future__ import annotations

import json
import re
from typing import Any

from alpha.agents.memory.l1.dedup import L1Dedup, _conflict_shaped
from alpha.agents.memory.l1.models import MemoryRecord
from alpha.agents.memory.l1.pipeline import CaptureJob, L1Pipeline
from alpha.agents.memory.l1.store import L1RecordStore
from alpha.config.memory_config import L1MemoryConfig, MemoryConfig

_NEW_MEMORY_ID_RE = re.compile(r"### New memory #\d+ \(record_id: (mem_[0-9a-f_]+)\)")
_POOL_PAIR_RE = re.compile(r'"record_id": "(mem_[0-9a-f_]+)",\s*\n\s*"content": "([^"]+)"')


def make_config(tmp_path: Any, **l1_overrides: Any) -> MemoryConfig:
    l1 = {"enabled": True, "storage_path": str(tmp_path / "l1"), "debounce_seconds": 0.0, "persona_enabled": False}
    l1.update(l1_overrides)
    return MemoryConfig.model_validate({"enabled": True, "l1": l1})


def _decoys() -> list[MemoryRecord]:
    """Five records whose token MULTISET equals the incoming query's.

    They recall at maximum relevance (identical token sets) yet FAIL the
    conflict prefilter (no value token the incoming record lacks), so with the
    gate off they fill the whole ``dedup_top_k`` cut and with the gate on they
    exclude themselves, leaving room for the shaped target below.
    """
    shapes = [
        "The review cadence is hourly, for the audit board.",
        "THE REVIEW CADENCE IS HOURLY FOR THE AUDIT BOARD",
        "The review cadence is HOURLY, for THE audit board!",
        "the. Review. Cadence. Is. Hourly. For. The. Audit. Board.",
        "The  review   cadence is hourly for  the audit board.",
    ]
    return [MemoryRecord.create(text, memory_type="persona") for text in shapes]


def _value_records() -> list[MemoryRecord]:
    """Twelve same-subject records, each with a different value token."""
    values = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays", "midnights", "dawns", "noons", "eves", "uprights"]
    return [MemoryRecord.create(f"The review cadence is {value} for the audit board.", memory_type="persona") for value in values]


# ---------------------------------------------------------------------------
# prefilter unit contract
# ---------------------------------------------------------------------------


def test_conflict_shaped_accepts_implicit_invalidation_pair() -> None:
    assert _conflict_shaped(
        "The review cadence is hourly for the audit board.",
        "The review cadence is Mondays for the audit board.",
    )


def test_conflict_shaped_rejects_identical_token_sets() -> None:
    assert not _conflict_shaped(
        "The review cadence is hourly for the audit board.",
        "The review cadence is hourly for the audit board.",
    )


def test_conflict_shaped_rejects_a_disjoint_subject() -> None:
    assert not _conflict_shaped(
        "Ravi prefers a window seat on flights.",
        "Kiran ships the release notes on Fridays.",
    )


def test_conflict_shaped_rejects_thin_input() -> None:
    assert not _conflict_shaped("ok", "The review cadence is Mondays.")
    assert not _conflict_shaped("The review cadence Mondays", "Mondays")


def test_conflict_expansion_gate_defaults_off() -> None:
    assert L1MemoryConfig().conflict_expansion is False
    assert MemoryConfig().l1.conflict_expansion is False


# ---------------------------------------------------------------------------
# recall-time widening
# ---------------------------------------------------------------------------


def _seeded_wide_store(tmp_path: Any) -> tuple[L1RecordStore, MemoryRecord]:
    """Decoys fill the top-5 cut; the target sits below it by construction."""
    store = L1RecordStore(str(tmp_path / "store"))
    target = MemoryRecord.create("The review cadence is Mondays for the audit board.", memory_type="persona")
    store.put_records([*_decoys(), target], user_id="u1", agent_name="agent")
    return store, target


def _recalled_ids(store: L1RecordStore, content: str, **dedup_kwargs: Any) -> list[str]:
    dedup = L1Dedup(store, None, top_k=5, user_id="u1", agent_name="agent", **dedup_kwargs)
    new_record = MemoryRecord.create(content, memory_type="persona")
    matches = dedup._build_matches([new_record])
    return [candidate["id"] for candidate in matches[0]["candidates"]]


def test_without_expansion_the_below_cut_target_is_not_recalled(tmp_path) -> None:
    store, target = _seeded_wide_store(tmp_path)
    recalled = _recalled_ids(store, "The review cadence is hourly for the audit board.")
    assert len(recalled) == 5, "top_k cut without the gate"
    assert target.id not in recalled


def test_with_expansion_the_below_cut_target_is_recalled(tmp_path) -> None:
    store, target = _seeded_wide_store(tmp_path)
    recalled = _recalled_ids(
        store,
        "The review cadence is hourly for the audit board.",
        conflict_expansion=True,
    )
    assert len(recalled) == 6, "the five decoys plus the shaped target"
    assert target.id in recalled


def test_expansion_excludes_a_subject_disjoint_record(tmp_path) -> None:
    store, _target = _seeded_wide_store(tmp_path)
    outsider = MemoryRecord.create("Workshop agenda planning materials ship review.", memory_type="episodic")
    store.put_records([outsider], user_id="u1", agent_name="agent")
    recalled = _recalled_ids(
        store,
        "The review cadence is hourly for the audit board.",
        conflict_expansion=True,
    )
    assert outsider.id not in recalled


def test_expansion_is_bounded(tmp_path) -> None:
    store = L1RecordStore(str(tmp_path / "store"))
    store.put_records(_value_records(), user_id="u1", agent_name="agent")
    recalled = _recalled_ids(
        store,
        "The review cadence is hourly for the audit board.",
        conflict_expansion=True,
    )
    assert 5 < len(recalled) <= 10, "pool cap is dedup_top_k + CONFLICT_EXPANSION_EXTRA"


# ---------------------------------------------------------------------------
# end-to-end: the gate turns a silent duplicate into an update
# ---------------------------------------------------------------------------


class ScriptedExpansionModel:
    """Updates the recalled candidate that conflicts on the SAME subject.

    The decision binds to the candidate whose content carries the replaced
    value ("Mondays"); when that candidate was not recalled (gate off), the
    model returns no decision and the memory is stored as a duplicate —
    exactly the silent-miss the gate exists to fix.
    """

    EXTRACTION = json.dumps(
        [
            {
                "scene_name": "review cadence",
                "message_ids": ["m1"],
                "memories": [
                    {
                        "content": "The review cadence is hourly for the audit board.",
                        "type": "persona",
                        "priority": 85,
                        "source_message_ids": ["m1"],
                        "metadata": {},
                    }
                ],
            }
        ]
    )

    def __init__(self, replaced_value: str = "Mondays") -> None:
        self._replaced_value = replaced_value
        self.conflict_prompts: list[str] = []

    def invoke(self, prompt: str, config: dict[str, Any] | None = None) -> str:
        if "memory extraction expert" in prompt:
            return self.EXTRACTION
        if "conflict detector" in prompt:
            self.conflict_prompts.append(prompt)
            pairs = _POOL_PAIR_RE.findall(prompt)
            target = next((record_id for record_id, content in pairs if self._replaced_value in content), None)
            if target is None:
                return ""
            new_id = _NEW_MEMORY_ID_RE.search(prompt).group(1)
            return json.dumps(
                [
                    {
                        "record_id": new_id,
                        "action": "update",
                        "target_ids": [target],
                        "merged_content": "The review cadence is hourly for the audit board.",
                        "merged_type": "persona",
                        "reason": "implicit same-subject invalidation",
                    }
                ]
            )
        return "# Profile\nstub profile"


def _capture_job() -> CaptureJob:
    message = type(
        "Msg",
        (),
        {
            "type": "human",
            "content": "Heads up - the review cadence is hourly now for the audit board.",
            "id": "m1",
            "tool_calls": None,
            "additional_kwargs": {},
        },
    )()
    return CaptureJob(thread_id="t1", user_id="u1", agent_name="agent", mode="chat", trace_id=None, messages=[message])


def _expansion_pipeline(tmp_path: Any, model: ScriptedExpansionModel, *, expansion: bool) -> tuple[L1Pipeline, MemoryRecord, L1RecordStore]:
    """Seeded pipeline: five decoys plus the below-cut 'Mondays' target."""
    cfg = make_config(tmp_path, conflict_expansion=expansion)
    store = L1RecordStore(cfg.l1.storage_path)
    target = MemoryRecord.create("The review cadence is Mondays for the audit board.", memory_type="persona")
    store.put_records([*_decoys(), target], user_id="u1", agent_name="agent")
    return L1Pipeline(config=cfg, store=store, model=model), target, store


def test_pipeline_without_expansion_stores_the_duplicate(tmp_path) -> None:
    """Gate off: the below-cut target is never seen; the memory is stored."""
    model = ScriptedExpansionModel()
    pipeline, target, store = _expansion_pipeline(tmp_path, model, expansion=False)

    report = pipeline.run_job(_capture_job())

    assert report.ok, report.error
    assert model.conflict_prompts, "dedup ran"
    prompt = model.conflict_prompts[0]
    new_id = _NEW_MEMORY_ID_RE.search(prompt).group(1)
    pairs_remaining = _POOL_PAIR_RE.findall(prompt)
    # The regex also binds the NEW memory's own header pair; only the
    # remaining pairs are the recalled candidate pool.
    candidate_ids = [record_id for record_id, _content in pairs_remaining if record_id != new_id]
    assert len(candidate_ids) == 5, "top_k cut without the gate"
    assert target.id not in candidate_ids
    assert report.stored == 1, "silently stored as a duplicate"
    assert store.get(target.id, user_id="u1", agent_name="agent") is not None


def test_pipeline_expansion_finds_the_implicit_conflict(tmp_path) -> None:
    """Gate on: the below-cut target reaches the adjudicator and is replaced."""
    model = ScriptedExpansionModel()
    pipeline, target, store = _expansion_pipeline(tmp_path, model, expansion=True)

    report = pipeline.run_job(_capture_job())

    assert report.ok, report.error
    assert report.updated == 1, "the implicit conflict became an update"
    assert "mondays" in model.conflict_prompts[0].lower(), "the target reached the prompt"
    assert store.get(target.id, user_id="u1", agent_name="agent") is None, "the replaced target is gone"


def test_pipeline_expansion_prompt_includes_the_pinned_pool_format(tmp_path) -> None:
    """The expansion rides the SAME batch prompt — no new prompt surface."""
    model = ScriptedExpansionModel()
    pipeline, _target, _store = _expansion_pipeline(tmp_path, model, expansion=True)
    pipeline.run_job(_capture_job())

    prompt = model.conflict_prompts[0]
    assert "## Unified candidate pool (6 existing memories)" in prompt
    assert "### New memory #1 (record_id: " in prompt


def test_pipeline_disabled_dedup_ignores_expansion(tmp_path) -> None:
    """With dedup disabled the whole seam is inert regardless of the gate."""
    model = ScriptedExpansionModel()
    pipeline, _target, _store = _expansion_pipeline(tmp_path, model, expansion=True)
    pipeline._config.l1.dedup_enabled = False

    report = pipeline.run_job(_capture_job())

    assert report.ok, report.error
    assert not model.conflict_prompts, "no dedup call happened"
    assert report.stored == 1
