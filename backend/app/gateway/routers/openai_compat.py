"""OpenAI-compatible chat surface over Gateway threads and runs.

Lets any OpenAI client drive the agent: ``POST /compat/openai/chat/completions``
(non-streaming) creates (or reuses) a thread, runs it to completion via the
existing run lifecycle — all admission, ownership, and idempotency invariants
intact — and returns an OpenAI-shaped response. No parallel runtime: this is
pure translation over ``threads.create`` + ``runs.wait``.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_thread_store
from app.gateway.routers.thread_runs import wait_run
from app.gateway.routers.threads import ThreadCreateRequest, create_thread
from app.gateway.run_models import RunCreateRequest

router = APIRouter(prefix="/api/compat/openai", tags=["openai-compat"])


class ChatMessageIn(BaseModel):
    role: str = Field(default="user", max_length=16)
    content: Any = None


class ChatCompletionsRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    messages: list[ChatMessageIn] = Field(default_factory=list)
    thread_id: str | None = Field(default=None, max_length=64)
    assistant_id: str | None = Field(default=None, max_length=64)


def openai_messages_to_run_input(messages: list[dict[str, Any] | ChatMessageIn]) -> dict[str, Any]:
    """Map OpenAI chat messages to graph input. System messages become context prefix."""
    runs: list[dict[str, str]] = []
    system_parts: list[str] = []
    for raw in messages or []:
        role = raw.get("role", "user") if isinstance(raw, dict) else raw.role
        content = raw.get("content") if isinstance(raw, dict) else raw.content
        if isinstance(content, list):
            content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
        text = "" if content is None else str(content)
        if role == "system":
            if text.strip():
                system_parts.append(text.strip())
            continue
        if role not in ("user", "assistant", "tool"):
            role = "user"
        if text.strip():
            runs.append({"role": role, "content": text})
    if system_parts and runs and runs[0]["role"] == "user":
        runs[0] = {"role": "user", "content": "\n\n".join(system_parts) + "\n\n" + runs[0]["content"]}
    return {"messages": runs}


def _last_assistant_text(values: Any) -> str:
    messages = values.get("messages", []) if isinstance(values, dict) else []
    for entry in reversed(messages):
        if isinstance(entry, dict):
            msg_type = str(entry.get("type", "")).lower()
            role = str(entry.get("role", "")).lower()
            if msg_type in ("ai", "aimessagechunk") or role in ("assistant", "ai"):
                content = entry.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
                text = str(content or "").strip()
                if text:
                    return text
        else:
            text = str(getattr(entry, "content", "") or "").strip()
            if text and type(entry).__name__.lower().startswith("ai"):
                return text
    return ""


def openai_chat_response(model: str, text: str, *, thread_id: str) -> dict[str, Any]:
    created = int(time.time())
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": created,
        "model": model or "deerflow",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "system_fingerprint": f"deerflow:{thread_id}",
    }


@router.post("/chat/completions")
@require_permission("threads", "write")
@require_permission("runs", "create")
async def chat_completions(body: ChatCompletionsRequest, request: Request) -> dict:
    if not body.messages:
        raise HTTPException(status_code=422, detail="messages must not be empty.")
    run_input = openai_messages_to_run_input(body.messages)
    if not run_input["messages"]:
        raise HTTPException(status_code=422, detail="messages must contain at least one user or assistant turn.")
    thread_id = body.thread_id
    if thread_id:
        row = await get_thread_store(request).get(thread_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Thread not found")
    else:
        created = await create_thread(ThreadCreateRequest(), request)
        thread_id = created.thread_id
    result = await wait_run(
        thread_id,
        RunCreateRequest(assistant_id=body.assistant_id, input=run_input),
        request,
    )
    if isinstance(result, dict) and "messages" in result:
        text = _last_assistant_text(result)
    elif isinstance(result, dict) and result.get("status") not in (None, "success"):
        raise HTTPException(status_code=502, detail=f"Run finished with status {result.get('status')}: {result.get('error') or 'unknown error'}")
    else:
        text = ""
    return openai_chat_response(body.model or "", text, thread_id=thread_id)
