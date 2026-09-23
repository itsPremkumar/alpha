"""Real model invocation for the deliberation engines.

Historically every deliberation strategy returned a canned answer with
fabricated confidence/consensus numbers and never called a model at all.
This module is the single seam through which the strategies make REAL calls:

- :func:`configured_model_roster` returns the genuinely configured model names
  (the old code hardcoded ``candidate-1``/``advocate-alpha``/``lead-model`` —
  names that exist nowhere in the config).
- :func:`invoke_model` performs one real chat invocation and returns the exact
  model output. With no chat models configured it raises ``RuntimeError`` so
  the failure surfaces honestly instead of as a fake success.

Tests replace ``invoke_model`` (single patch target
``alpha.deliberation.invocation.invoke_model``) to stay offline.
"""

from __future__ import annotations


def configured_model_roster() -> list[str]:
    """Names of the chat models actually configured in ``config.yaml``."""
    from alpha.config import get_app_config

    app_config = get_app_config()
    return [m.name for m in (getattr(app_config, "models", None) or [])]


def resolve_model(model_name: str):
    """Build the chat model named ``model_name`` from the active config.

    Raises ``RuntimeError`` when no chat models are configured at all so a
    misconfigured deployment fails loudly instead of deliberating with
    fabrications.
    """
    from alpha.config import get_app_config

    app_config = get_app_config()
    if not getattr(app_config, "models", None):
        raise RuntimeError("No chat models configured; deliberation cannot run.")
    from alpha.models import create_chat_model

    return create_chat_model(model_name, app_config=app_config)


def invoke_model(model_name: str, *, system: str, user: str) -> str:
    """Run one real model invocation and return its exact (non-empty) output."""
    from langchain_core.messages import HumanMessage, SystemMessage

    model = resolve_model(model_name)
    response = model.invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
    )
    content = getattr(response, "content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    text = str(content)
    if not text.strip():
        raise RuntimeError(f"Model {model_name!r} returned an empty deliberation response.")
    return text


def response_similarity(a: str, b: str) -> float:
    """Deterministic token overlap (Jaccard) between two responses.

    Used as the real convergence/consensus signal that the old code faked
    with constants like ``0.86``/``0.88``. Order-insensitive, in [0.0, 1.0].
    """
    import re

    tokens_a = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    tokens_b = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    return round(len(tokens_a & tokens_b) / len(tokens_a | tokens_b), 4)
