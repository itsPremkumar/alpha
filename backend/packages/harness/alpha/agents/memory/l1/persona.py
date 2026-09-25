"""L1 persona synthesis: Markdown profile from persona-type records.

Design provenance: incremental persona evolution (existing profile +
new memories + change note -> rewritten profile, <= 2000 chars, stable
sections) follows ``tencentdb-agent-memory``
``MemoryCore/src/core/persona/persona-generation.ts`` (MIT). The system/user
prompt split comes from ``alpha.agents.memory.l1.prompts.build_persona_prompt``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .extractor import message_text
from .paths import atomic_write_text, persona_path
from .prompts import build_persona_prompt
from .store import L1RecordStore

logger = logging.getLogger(__name__)

#: Persona profile cap enforced post-hoc (the prompt asks for <= 2000 chars).
MAX_PROFILE_CHARS = 2000


def _credits_for(response: object) -> float:
    """Credits for one synthesis response (0.0 when usage is unreported)."""
    try:
        from .quota import usage_from_response

        return usage_from_response(response)
    except Exception:  # noqa: BLE001 - usage bookkeeping must not fail a run
        return 0.0


@dataclass(slots=True)
class PersonaReport:
    """Outcome of one persona synthesis attempt."""

    status: str  # "updated" | "unchanged" | "below_threshold" | "llm_error" | "disabled"
    profile: str = ""
    memory_count: int = 0
    error: str = ""
    #: Credits consumed by the synthesis call (0.0 when no usage reported).
    credits: float = 0.0

    @property
    def updated(self) -> bool:
        return self.status == "updated"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "status": self.status,
            "memory_count": self.memory_count,
            "error": self.error,
            "profile_chars": len(self.profile),
        }


def load_profile(
    store: L1RecordStore,
    *,
    user_id: str | None,
    agent_name: str | None,
) -> str:
    """Read the stored persona profile for a scope ('' when absent)."""
    path = persona_path(store.root, user_id, agent_name)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError as exc:
        logger.warning("L1 persona: could not read %s (%s)", path, exc)
        return ""


def save_profile(
    store: L1RecordStore,
    text: str,
    *,
    user_id: str | None,
    agent_name: str | None,
) -> Path:
    """Persist the persona profile for a scope (atomic write)."""
    path = persona_path(store.root, user_id, agent_name)
    atomic_write_text(path, text.strip() + "\n")
    return path


def synthesize(
    store: L1RecordStore,
    model: object,
    *,
    user_id: str | None,
    agent_name: str | None,
    min_memories: int = 3,
    changed_note: str = "new persona memories captured",
    mode: str = "chat",
) -> PersonaReport:
    """Synthesize (or refresh) the persona profile for one scope.

    Honest reporting: below the memory threshold -> ``below_threshold`` (no
    LLM call, nothing written); LLM failure -> ``llm_error`` (old profile
    untouched); success -> ``updated`` only after the file was written.
    """
    if model is None:
        return PersonaReport(status="llm_error", error="no_model_configured")

    records = [
        record
        for record in store.list_records(user_id, agent_name)
        if record.type == "persona" and record.priority >= 0
    ]
    if len(records) < max(1, min_memories):
        return PersonaReport(status="below_threshold", memory_count=len(records))

    existing = load_profile(store, user_id=user_id, agent_name=agent_name)
    memory_dicts = [
        {
            "content": record.content,
            "priority": record.priority,
            "scene_name": record.metadata.get("scene_name", ""),
        }
        for record in sorted(records, key=lambda r: (-r.priority, -r.updated_at))
    ][:50]
    system_prompt, user_prompt = build_persona_prompt(
        memories=memory_dicts,
        existing_profile=existing or None,
        changed_note=changed_note,
    )
    invoke_config = {
        "run_name": "l1_persona_synthesis",
        "metadata": {"user_id": user_id or "", "agent_name": agent_name or "", "mode": mode},
    }
    try:
        response = model.invoke(f"{system_prompt}\n\n{user_prompt}", config=invoke_config)
    except BaseException as exc:  # noqa: BLE001 - report, never crash the turn
        return PersonaReport(status="llm_error", memory_count=len(records), error=str(exc)[:500])

    text = message_text(response).strip()
    if not text:
        return PersonaReport(status="llm_error", memory_count=len(records), error="empty_response")
    credits = _credits_for(response)
    # Strip an accidental fence/preamble; keep semantics.
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    text = text.strip()
    if len(text) > MAX_PROFILE_CHARS:
        text = text[:MAX_PROFILE_CHARS].rstrip()
    if text == existing.strip():
        return PersonaReport(
            status="unchanged", profile=text, memory_count=len(records), credits=credits
        )
    try:
        save_profile(store, text, user_id=user_id, agent_name=agent_name)
    except OSError as exc:
        return PersonaReport(
            status="llm_error",
            memory_count=len(records),
            error=f"write_failed: {exc}"[:500],
            credits=credits,
        )
    return PersonaReport(
        status="updated", profile=text, memory_count=len(records), credits=credits
    )


__all__ = ["MAX_PROFILE_CHARS", "PersonaReport", "load_profile", "save_profile", "synthesize"]
