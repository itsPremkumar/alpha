"""Post-turn learning fork middleware.

Runs a cheap aux-model replay after each turn with a bounded digest of the
latest conversation. The fork uses a whitelisted toolset (memory + proposal
tools only) so it cannot mutate sandbox state or invoke arbitrary tools.
All writes go through the existing MemoryManager and SkillProposalStore;
exceptions are isolated and never affect the primary run.
"""

import logging
from typing import TYPE_CHECKING, Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.config import get_config
from langgraph.runtime import Runtime

from alpha.config.learning_fork_config import LearningForkConfig, get_learning_fork_config
from alpha.models import create_chat_model
from alpha.trace_context import AGENT_WORKSPACE_TRACE_METADATA_KEY, resolve_trace_id

if TYPE_CHECKING:
    from alpha.agents.memory import MemoryManager
    from alpha.skills.proposals import SkillProposalStore

logger = logging.getLogger(__name__)


class LearningForkMiddlewareState(AgentState):
    """Compatible with the ThreadState schema."""

    pass


# Real registered @tool names (alpha.agents.memory.tools / builtins). The
# original {add_memory, recall_memory} named tools that do not exist anywhere
# in the repo — binding them made every fork execution raise ImportError.
_WHITELISTED_TOOL_NAMES = frozenset(
    {
        "memory_add",
        "memory_search",
        "propose_skill",
    }
)


def _build_digest(messages: list, max_chars: int) -> str:
    """Build a newest-first conversation digest capped at max_chars."""
    if not messages:
        return ""

    parts: list[str] = []
    total = 0
    for msg in reversed(messages):
        role = getattr(msg, "type", getattr(msg, "role", "unknown"))
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            # Handle multimodal content (e.g. image + text)
            text_parts = [c.get("text", "") for c in content if isinstance(c, dict)]
            content = " ".join(text_parts)
        chunk = f"[{role}] {content}"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(reversed(parts))


def get_memory_manager():
    """Module-level, patchable accessor for the memory manager.

    Tests replace this exact name; resolving it inside the handler with a
    local import bypassed the patch (that mismatch already broke three
    integration tests with AttributeError).
    """
    from alpha.agents.memory import get_memory_manager as _get_memory_manager

    return _get_memory_manager()


def get_skill_proposal_store():
    """Module-level, patchable accessor for the proposal store.

    ``alpha.skills.proposals`` exports no ``get_skill_proposal_store`` — the
    store is built as ``SkillProposalStore(proposals_root())``, the same
    construction the propose_skill tool uses.
    """
    from alpha.skills.proposals import SkillProposalStore, proposals_root

    return SkillProposalStore(proposals_root())


class LearningForkMiddleware(AgentMiddleware[LearningForkMiddlewareState]):
    """Middleware that runs a post-turn learning fork with whitelisted tools."""

    state_schema = LearningForkMiddlewareState

    def __init__(
        self,
        *,
        learning_fork_config: LearningForkConfig | None = None,
        memory_manager: "MemoryManager | None" = None,
        proposal_store: "SkillProposalStore | None" = None,
        review_queue: Any | None = None,
    ) -> None:
        super().__init__()
        self._config = learning_fork_config or get_learning_fork_config()
        self._memory_manager = memory_manager
        self._proposal_store = proposal_store
        self._review_queue = review_queue

    def _resolve_thread_context(self, state: LearningForkMiddlewareState, runtime: Runtime) -> tuple[str, str, list] | None:
        """Extract thread_id, user_id, and messages from state/runtime."""
        thread_id = None
        if runtime.context:
            thread_id = runtime.context.get("thread_id")
        if not thread_id:
            config_data = get_config()
            thread_id = config_data.get("configurable", {}).get("thread_id")
        if not thread_id:
            logger.debug("LearningFork: no thread_id, skipping")
            return None

        messages = state.get("messages", [])
        if not messages:
            logger.debug("LearningFork: no messages, skipping")
            return None

        from alpha.runtime.user_context import resolve_runtime_user_id

        user_id = resolve_runtime_user_id(runtime)

        runtime_context = runtime.context if isinstance(runtime.context, dict) else {}
        trace_id = resolve_trace_id(runtime_context.get(AGENT_WORKSPACE_TRACE_METADATA_KEY))

        return thread_id, user_id, messages, trace_id

    def _create_fork_model(self, lead_model_name: str | None) -> any:
        """Create the aux model for the fork."""
        model_name = self._config.model_name or lead_model_name
        if not model_name:
            logger.warning("LearningFork: no model available, skipping")
            return None
        try:
            return create_chat_model(model_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LearningFork: failed to create model %s: %s", model_name, exc)
            return None

    def _build_fork_prompt(self, digest: str) -> str:
        """Build the system prompt for the learning fork."""
        return (
            "You are a post-turn learning assistant. Your job is to review the latest "
            "conversation digest and optionally propose memory facts or new skills.\n\n"
            "CONVERSATION DIGEST (newest last):\n"
            f"{digest}\n\n"
            "AVAILABLE TOOLS:\n"
            "- memory_add: Store a factual memory for the user\n"
            "- memory_search: Search existing memories (read-only)\n"
            "- propose_skill: Propose a new skill for admin review\n\n"
            "RULES:\n"
            "1. Only propose memories that are factual, user-specific, and durable.\n"
            "2. Only propose skills when the conversation reveals a reusable pattern.\n"
            f"3. Max {self._config.max_proposals_per_run} proposals per run.\n"
            "4. Be concise. Do not spam.\n"
        )

    @override
    async def after_agent(self, state: LearningForkMiddlewareState, runtime: Runtime) -> dict | None:
        """Run the learning fork after the primary agent completes."""
        if not self._config.enabled:
            return None

        ctx = self._resolve_thread_context(state, runtime)
        if ctx is None:
            return None

        thread_id, user_id, messages, trace_id = ctx

        # Resolve lead model name from runtime config
        lead_model_name = None
        if runtime.context:
            lead_model_name = runtime.context.get("model_name")
        if not lead_model_name:
            config_data = get_config()
            lead_model_name = config_data.get("configurable", {}).get("model_name")

        digest = _build_digest(messages, self._config.digest_chars)
        if not digest.strip():
            return None

        if self._config.defer_when_busy:
            # Defer: coalesce into the review queue, run when idle.
            from alpha.learning.review_queue import get_review_queue

            queue = self._review_queue or get_review_queue(max_age_seconds=self._config.defer_max_age_seconds)
            queue.defer(thread_id, {"digest": digest, "user_id": user_id, "trace_id": trace_id, "model_name": lead_model_name})
            logger.debug("LearningFork: review deferred for thread %s", thread_id)
            return None

        await self._execute_fork(thread_id, user_id, trace_id, digest, lead_model_name)
        return None

    async def drain_deferred(self, is_idle=None) -> list[str]:
        """Run due queued reviews (idle or aged-out). Returns drained thread ids."""
        from alpha.learning.review_queue import get_review_queue

        queue = self._review_queue or get_review_queue(max_age_seconds=self._config.defer_max_age_seconds)

        async def _run(entry) -> None:
            snapshot = entry.snapshot or {}
            await self._execute_fork(
                entry.session_id,
                snapshot.get("user_id", ""),
                snapshot.get("trace_id", ""),
                snapshot.get("digest", ""),
                snapshot.get("model_name"),
            )

        drained: list[str] = []
        for session_id in queue.pending_ids():
            entry = queue.pop_if_due(session_id, is_idle=is_idle() if callable(is_idle) else True)
            if entry is None:
                continue
            try:
                await _run(entry)
                drained.append(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LearningFork: deferred review failed for %s: %s", session_id, exc)
        return drained

    async def _execute_fork(self, thread_id: str, user_id: str, trace_id: str, digest: str, lead_model_name: str | None) -> None:
        """Run one fork invocation over a digest (immediate or dequeued)."""
        fork_model = self._create_fork_model(lead_model_name)
        if fork_model is None:
            return

        # Build a minimal toolset with only whitelisted tools — the REAL
        # registered @tool objects (the old add_memory/recall_memory names
        # never existed; this import crashed every fork with ImportError).
        from alpha.agents.memory.tools import memory_add_tool, memory_search_tool
        from alpha.tools.builtins import propose_skill_tool

        whitelisted_tools = [memory_add_tool, memory_search_tool, propose_skill_tool]

        # Bind tools to the fork model
        fork_model_with_tools = fork_model.bind_tools(whitelisted_tools)

        # Inject the fork prompt as a system message
        from langchain_core.messages import SystemMessage

        fork_messages = [SystemMessage(content=self._build_fork_prompt(digest))]

        try:
            # Run the fork - single invocation, no streaming
            response = await fork_model_with_tools.ainvoke(fork_messages)

            # Execute any tool calls the fork made
            tool_calls = getattr(response, "tool_calls", [])
            proposals_made = 0

            for tool_call in tool_calls:
                if proposals_made >= self._config.max_proposals_per_run:
                    break

                tool_name = tool_call.get("name", "")
                if tool_name not in _WHITELISTED_TOOL_NAMES:
                    logger.warning("LearningFork: attempted non-whitelisted tool %s", tool_name)
                    continue

                try:
                    if tool_name == "memory_add":
                        manager = self._memory_manager or get_memory_manager()
                        # aadd: async entry, keyword-only user_id/trace_id.
                        # The old call awaited the sync, positional-incompatible
                        # add(...) — TypeError on every memory write.
                        await manager.aadd(thread_id, [response], user_id=user_id, trace_id=trace_id)
                        proposals_made += 1

                    elif tool_name == "propose_skill":
                        store = self._proposal_store or get_skill_proposal_store()
                        args = tool_call.get("args", {})
                        await store.propose(
                            name=args.get("name", ""),
                            description=args.get("description", ""),
                            content=args.get("content", ""),
                            proposed_by=user_id,
                        )
                        proposals_made += 1

                    elif tool_name == "memory_search":
                        # Read-only search against the store (the one-shot fork
                        # cannot consume the results; the old code called
                        # `manager.recall`, which does not exist at all).
                        manager = self._memory_manager or get_memory_manager()
                        await manager.asearch(args.get("query", ""), top_k=5, user_id=user_id)

                except Exception as exc:  # noqa: BLE001
                    logger.warning("LearningFork: tool %s failed: %s", tool_name, exc)
                    # Isolate fork failures - never affect primary run

        except Exception as exc:  # noqa: BLE001
            logger.warning("LearningFork: model invocation failed: %s", exc)

        return None

    def release_policy_parameters(self) -> dict[str, object]:
        """Expose config for assembly identity."""
        return {
            "learning_fork_enabled": self._config.enabled,
            "learning_fork_model": self._config.model_name,
            "learning_fork_max_proposals": self._config.max_proposals_per_run,
            "learning_fork_digest_chars": self._config.digest_chars,
            "learning_fork_defer_when_busy": self._config.defer_when_busy,
        }


def build_learning_fork_middleware(
    *,
    learning_fork_config: LearningForkConfig | None = None,
    memory_manager: "MemoryManager | None" = None,
    proposal_store: "SkillProposalStore | None" = None,
    review_queue: Any | None = None,
) -> LearningForkMiddleware:
    """Factory for the learning fork middleware."""
    return LearningForkMiddleware(
        learning_fork_config=learning_fork_config,
        memory_manager=memory_manager,
        proposal_store=proposal_store,
        review_queue=review_queue,
    )
