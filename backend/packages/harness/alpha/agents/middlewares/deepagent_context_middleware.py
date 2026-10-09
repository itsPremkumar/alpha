"""Project the working plane into every model request.

This is Alpha's analogue of ``deepagents``' ``FilesystemMiddleware``: the piece
that turns a state channel into something the model can actually use, by
injecting a bounded **index** of the plane before each model call.

Two properties decide the whole implementation:

**Addresses only, never source.** The payload names each file, its measured
size and the model's own one-line purpose — it never carries the body. A whole
plane dumped into every request would be a prompt that grows with the run
instead of staying flat, and this repository has already measured the
alternative: an interface map more than doubles reuse of an agent's own earlier
work while dumping full source achieves nothing *and raises* duplication
(``GroundingMiddleware``, whose test fails if implementation text reaches the
prompt).

**The payload rides the untrusted channel.** The plane is written by the model,
so its paths and summaries are model-supplied data. Framework-owned authority
text rides the system channel; anything model-supplied rides the ``HumanMessage``
that ``InputSanitizationMiddleware`` boundary-frames. A summary the model wrote
must never be able to promote itself into a system instruction, even
neutralised — natural-language injection survives tag escaping.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from alpha.config import get_app_config
from alpha.config.deepagent_config import get_deepagent_config
from alpha.deepagent.index import render_working_index
from alpha.deepagent.state import WorkingFile

logger = logging.getLogger(__name__)

#: Reserved id prefix for this middleware's own injected message, so it can be
#: swept out of a request before rebuilding it — the same discipline
#: ViewImageMiddleware uses for its base64 payload. A message carrying only the
#: prefix is not swapped out: a client-chosen id must never be droppable.
DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX = "__deepagent_workspace__"

#: Server-owned marker required beside the prefix before a message is treated as
#: ours. Mirrors ViewImageMiddleware's sweep guard.
DEEPAGENT_CONTEXT_MARKER = "deepagent_context_injected"

_STATE_KEY = "working_files"


def _read_files(state: object) -> list[WorkingFile]:
    raw = state.get(_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(raw, list):
        return []
    return [f for f in raw if isinstance(f, dict) and isinstance(f.get("path"), str) and isinstance(f.get("content"), str)]


def _is_own_message(message: object) -> bool:
    """True only for a message this middleware injected itself.

    Both identifiers are required. An unmarked leftover predating the marker is
    left in place and merely not duplicated, exactly as
    ``ViewImageMiddleware`` documents for its own sweep.
    """
    message_id = getattr(message, "id", None)
    if not isinstance(message_id, str) or not message_id.startswith(DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX):
        return False
    kwargs = getattr(message, "additional_kwargs", None)
    return isinstance(kwargs, dict) and kwargs.get(DEEPAGENT_CONTEXT_MARKER) is True


class DeepAgentContextMiddleware(AgentMiddleware):
    """Inject the working-plane index before each model call.

    Registered in the shared runtime base so the lead agent **and** delegated
    subagents see the same plane: that is the one property that makes delegation
    useful on a long task, because a child can read what its parent already
    established instead of re-deriving it in an isolated context window.
    """

    def __init__(self, *, app_config=None) -> None:
        self._app_config = app_config

    # -- configuration -----------------------------------------------------

    def _config(self):
        if self._app_config is not None:
            return self._app_config
        try:
            return get_app_config()
        except Exception:  # pragma: no cover - config unreadable at assembly
            logger.debug("deepagent: app config unavailable; working-plane injection disabled", exc_info=True)
            return None

    @property
    def _enabled(self) -> bool:
        config = self._config()
        section = getattr(config, "deepagent", None)
        return bool(getattr(section, "enabled", False)) and bool(getattr(section, "inject_index", False))

    # -- request projection ------------------------------------------------

    def _build_message(self, files: list[WorkingFile]) -> HumanMessage | None:
        rendered = render_working_index(files)
        if not rendered:
            return None
        try:
            from alpha_extension_api.provenance import provenance_kwargs

            stamp = provenance_kwargs(
                content_kind="durable_context",
                producer_kind="deepagent_context",
            )
        except Exception:  # pragma: no cover - contract package always present
            stamp = {}
        stamp[DEEPAGENT_CONTEXT_MARKER] = True
        return HumanMessage(
            content=rendered,
            id=DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX,
            additional_kwargs=stamp,
        )

    def _prepare(self, request) -> None:
        """Sweep any stranded own message, then append one fresh index.

        The sweep comes first because a checkpoint written by an interrupted run
        can carry an index that reached state but was never removed; leaving it
        would resend a stale manifest for the life of the thread.
        """
        if not self._enabled:
            return
        messages = getattr(request, "messages", None)
        if not isinstance(messages, list):
            return
        request.messages = [m for m in messages if not _is_own_message(m)]
        files = _read_files(getattr(request, "state", None))
        message = self._build_message(files)
        if message is not None:
            request.messages = [*request.messages, message]

    def wrap_model_call(self, request, handler):
        self._prepare(request)
        return handler(request)

    async def awrap_model_call(self, request, handler):
        self._prepare(request)
        return await handler(request)

    # -- self-description --------------------------------------------------

    def release_policy_parameters(self) -> dict[str, object]:
        """Declare the behaviour that affects assembly identity.

        The rendered index is bounded, so only the bounds and the two switches
        need declaring — never a prompt copy. ``canonical_hash`` is for long
        text, and there is none here.

        The bounds come from the module constants, not the operator config:
        those are the *hard* channel ceiling, so an operator narrowing them does
        not change the assembly identity of a state channel that is bounded to
        the same maximum whatever they set.
        """
        section = getattr(self._config(), "deepagent", None)
        if section is None:
            return {"enabled": False, "max_files": None, "max_file_bytes": None, "max_total_bytes": None, "max_summary_chars": None}
        return {
            "enabled": bool(getattr(section, "enabled", False)),
            "inject_index": bool(getattr(section, "inject_index", False)),
            "max_files": get_deepagent_config().max_files,
            "max_file_bytes": get_deepagent_config().max_file_bytes,
            "max_total_bytes": get_deepagent_config().max_total_bytes,
            "max_summary_chars": get_deepagent_config().max_summary_chars,
            "state_key": _STATE_KEY,
        }


__all__ = [
    "DEEPAGENT_CONTEXT_MARKER",
    "DEEPAGENT_CONTEXT_MESSAGE_ID_PREFIX",
    "DeepAgentContextMiddleware",
]
