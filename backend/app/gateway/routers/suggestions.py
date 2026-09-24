import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

import alpha.utils.llm_text as llm_text
from alpha.config.app_config import AppConfig
from alpha.config.suggestions_config import DEFAULT_MAX_SUGGESTIONS, MAX_SUGGESTIONS_LIMIT
from alpha.utils.oneshot_llm import run_oneshot_llm
from alpha.utils.thread_id import ThreadId
from app.gateway.authz import require_permission
from app.gateway.deps import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["suggestions"])


class SuggestionMessage(BaseModel):
    role: str = Field(..., description="Message role: user|assistant")
    content: str = Field(..., description="Message content as plain text")


class SuggestionsRequest(BaseModel):
    messages: list[SuggestionMessage] = Field(..., description="Recent conversation messages")
    n: int = Field(default=DEFAULT_MAX_SUGGESTIONS, ge=1, le=MAX_SUGGESTIONS_LIMIT, description="Number of suggestions to generate")
    model_name: str | None = Field(default=None, description="Optional model override")


class SuggestionsResponse(BaseModel):
    suggestions: list[str] = Field(default_factory=list, description="Suggested follow-up questions")


class SuggestionsConfigResponse(BaseModel):
    enabled: bool = Field(..., description="Whether follow-up suggestions are enabled globally")
    max_suggestions: int = Field(..., ge=1, le=MAX_SUGGESTIONS_LIMIT, description="Maximum number of follow-up suggestions to generate")


_strip_markdown_code_fence = llm_text.strip_markdown_code_fence
_strip_think_blocks = llm_text.strip_think_blocks


def _parse_json_string_list(text: str) -> list[str] | None:
    candidate = _strip_think_blocks(text)
    candidate = _strip_markdown_code_fence(candidate)
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = candidate[start : end + 1]
    try:
        data = json.loads(candidate)
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    out: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s:
            continue
        out.append(s)
    return out


def _format_conversation(messages: list[SuggestionMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        role = m.role.strip().lower()
        if role in ("user", "human"):
            parts.append(f"User: {m.content.strip()}")
        elif role in ("assistant", "ai"):
            parts.append(f"Assistant: {m.content.strip()}")
        else:
            parts.append(f"{m.role}: {m.content.strip()}")
    return "\n".join(parts).strip()


def _configured_max_suggestions(config: AppConfig) -> int:
    return getattr(config.suggestions, "max_suggestions", DEFAULT_MAX_SUGGESTIONS)


@router.get(
    "/suggestions/config",
    response_model=SuggestionsConfigResponse,
    summary="Get Suggestions Configuration",
    description="Returns the global configuration for follow-up suggestions.",
)
async def get_suggestions_config(
    config: AppConfig = Depends(get_config),
) -> SuggestionsConfigResponse:
    return SuggestionsConfigResponse(enabled=config.suggestions.enabled, max_suggestions=_configured_max_suggestions(config))


@router.post(
    "/threads/{thread_id}/suggestions",
    response_model=SuggestionsResponse,
    summary="Generate Follow-up Questions",
    description="Generate short follow-up questions a user might ask next, based on recent conversation context.",
)
@require_permission("threads", "read", owner_check=True)
async def generate_suggestions(
    thread_id: ThreadId,
    body: SuggestionsRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> SuggestionsResponse:
    if not config.suggestions.enabled:
        return SuggestionsResponse(suggestions=[])
    if not body.messages:
        return SuggestionsResponse(suggestions=[])

    n = min(body.n, _configured_max_suggestions(config))
    conversation = _format_conversation(body.messages)
    if not conversation:
        return SuggestionsResponse(suggestions=[])

    system_instruction = (
        "You are generating follow-up questions to help the user continue the conversation.\n"
        f"Based on the conversation below, produce EXACTLY {n} short questions the user might ask next.\n"
        "Requirements:\n"
        "- Questions must be relevant to the preceding conversation.\n"
        "- Questions must be written in the same language as the user.\n"
        "- Keep each question concise (ideally <= 20 words / <= 40 Chinese characters).\n"
        "- Do NOT include numbering, markdown, or any extra text.\n"
        "- Output MUST be a JSON array of strings only.\n"
    )
    user_content = f"Conversation Context:\n{conversation}\n\nGenerate {n} follow-up questions"

    try:
        raw = await run_oneshot_llm(
            system_instruction=system_instruction,
            user_content=user_content,
            run_name="suggest_agent",
            app_config=config,
            model_name=body.model_name,
            thread_id=thread_id,
        )
    except Exception as exc:
        # A generation failure must be detectable by clients: a 200 with an
        # empty list would be indistinguishable from a genuine empty result.
        logger.exception("Failed to generate suggestions: thread_id=%s err=%s", thread_id, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Follow-up suggestion generation failed (upstream model error: {type(exc).__name__}).",
        ) from exc

    try:
        parsed = _parse_json_string_list(raw)
    except Exception:
        parsed = None
    if parsed is None:
        # The model answered, but not with the required JSON array: that is a
        # generation failure too, not a genuine "no suggestions".
        logger.error("Unparseable suggestion response: thread_id=%s raw_type=%s", thread_id, type(raw).__name__)
        raise HTTPException(
            status_code=502,
            detail="Follow-up suggestion generation failed (model response was not a parseable list of questions).",
        )

    cleaned = [s.replace("\n", " ").strip() for s in parsed if s.strip()]
    cleaned = cleaned[:n]
    # parsed == [] means the model genuinely returned zero suggestions.
    return SuggestionsResponse(suggestions=cleaned)
