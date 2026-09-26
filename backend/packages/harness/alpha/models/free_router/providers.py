"""Keyless free/anonymous LLM provider specs and HTTP primitives.

This is the network layer of Alpha's free-LLM router: a curated set of public
endpoints that document a keyless / no-signup path. It is an advanced rework
of the standalone ``alpha_free_llm_router`` script, adapted to Alpha's
conventions:

* **Single HTTP seam.** Every request goes through the module-level
  :func:`request`. Tests stub that one function; nothing else touches the
  network. No personal API keys exist anywhere in this module — the only
  constants are documented public anonymous identifiers (e.g. AI Horde's
  documented anonymous apikey ``0000000000``).

* **Honest statuses, no fabricated availability.** The upstream script
  claimed ``available=True`` for providers it never contacted and injected
  "documented" models as if they were discovered. Here:
  - discovery over HTTP records ``ok=False`` + the real error on failure;
  - providers with no public catalog return their *documented* candidates
    with ``source="documented"`` and no availability claim at all;
  - a response with no message content raises :class:`ProviderError`
    instead of dumping the raw JSON as if it were model output.

* **Error classification for failover.** :class:`ProviderError` carries
  ``status_code`` so Alpha's fallback chain can distinguish retryable
  failures (429/5xx) from deterministic ones (400/401/404). Connection and
  timeout failures propagate as ``httpx``/builtin exceptions whose class
  names are already treated as retryable by ``alpha.models.fallback``.

Provider reachability and *free* pricing on public anonymous gateways can
change at any time; nothing here guarantees an SLA. Do not send secrets or
sensitive data to anonymous endpoints.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

USER_AGENT = "Alpha-Free-LLM-Router/1.1 (+anonymous-public-llm-integration)"
DEFAULT_TIMEOUT = 60.0
DISCOVERY_TIMEOUT = 12.0

# Documented public anonymous identifier for AI Horde (not a secret; the
# upstream project explicitly documents this value for anonymous usage).
AI_HORDE_ANONYMOUS_KEY = "0000000000"

# Obvious non-text model keywords for catalogs that mix media/embedding
# models into the same list. Vision-capable *language* models stay allowed.
NON_TEXT_HINTS = re.compile(
    r"(?:^|[-_:./])(?:image|video|audio|tts|stt|speech|whisper|embedding|embed|"
    r"vision-only|upscale|flux|stable-diffusion)(?:$|[-_:./])",
    re.I,
)
CODE_HINTS = re.compile(
    r"(?:code|coder|codestral|dev|developer|qwen.*coder|deepseek.*coder|starcoder|nemo)",
    re.I,
)
REASONING_HINTS = re.compile(
    r"(?:reason|thinking|r1|o[1-9]|o3|o4|glm|qwen3|deepseek|nemotron|minimax|gpt-oss)",
    re.I,
)


class ProviderError(RuntimeError):
    """A provider answered, but with an error or an unusable payload.

    Carries ``status_code`` so ``alpha.models.fallback.is_retryable_llm_error``
    can classify it: 429/5xx are retryable (failover may continue), other
    statuses are deterministic and must surface honestly.
    """

    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status_code = status_code


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one keyless provider endpoint."""

    name: str
    base_url: str
    # Relative OpenAI-compatible chat path; None => provider-specific schema.
    chat_path: str | None
    # Relative catalog path; None => no public catalog (documented candidates).
    models_path: str | None
    # Static headers (anonymous constants only — never personal credentials).
    auth_header: tuple[tuple[str, str], ...] = ()
    # Model ids documented as anonymous/free when the catalog omits them.
    documented_models: tuple[str, ...] = ()
    openai_compat: bool = True


PROVIDERS: dict[str, ProviderSpec] = {
    "ovhcloud": ProviderSpec(
        name="ovhcloud",
        base_url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        chat_path="/chat/completions",
        models_path="/models",
        documented_models=(
            "Meta-Llama-3_3-70B-Instruct",
            "Qwen3-Coder-30B-A3B-Instruct",
            "Mistral-7B-Instruct-v0.3",
            "gpt-oss-20b",
        ),
    ),
    "vireonix": ProviderSpec(
        name="vireonix",
        base_url="https://vireonix.ai",
        chat_path="/v1/chat/completions",
        models_path="/v1/models",
        documented_models=("auto",),
    ),
    "blockrun": ProviderSpec(
        name="blockrun",
        base_url="https://blockrun.ai/api/v1",
        chat_path="/chat/completions",
        models_path="/models",
    ),
    "llm7": ProviderSpec(
        name="llm7",
        base_url="https://api.llm7.io/v1",
        chat_path="/chat/completions",
        models_path="/models",
        auth_header=(("Authorization", "Bearer unused"),),
        documented_models=(
            "codestral-latest",
            "mistral-Nemo-Instruct-2407",
            "gemma4:31b",
            "minimax-m2.7",
            "gpt-oss",
        ),
    ),
    "persorai": ProviderSpec(
        name="persorai",
        base_url="https://persorai.com/v1",
        chat_path="/chat/completions",
        models_path="/models",
        auth_header=(("Authorization", "Bearer alpha-public-anonymous"),),
    ),
    "pollinations": ProviderSpec(
        name="pollinations",
        base_url="https://text.pollinations.ai",
        chat_path="/openai",
        models_path="/models",
        documented_models=("openai-fast", "openai"),
    ),
    "cehpoint": ProviderSpec(
        name="cehpoint",
        base_url="https://ai-api.cehpoint.co.in/v1",
        chat_path="/chat/completions",
        models_path=None,  # no public catalog endpoint is documented
        documented_models=("cehpoint-ai", "cehpoint-ai-multilingual"),
    ),
    "aihorde": ProviderSpec(
        name="aihorde",
        base_url="https://aihorde.net/api",
        chat_path=None,  # Kobold-style async schema, handled specially
        models_path="/v2/status/models",
        openai_compat=False,
    ),
}

PROVIDER_ORDER: tuple[str, ...] = (
    "vireonix",
    "blockrun",
    "ovhcloud",
    "pollinations",
    "llm7",
    "persorai",
    "cehpoint",
    "aihorde",
)


# ---------------------------------------------------------------------------
# HTTP seam
# ---------------------------------------------------------------------------

_CLIENT: httpx.Client | None = None


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
        )
    return _CLIENT


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.Response:
    """The single module-level HTTP seam (tests stub this function)."""
    return _client().request(
        method, url, headers=headers, params=params, json=json_body, timeout=timeout
    )


# ---------------------------------------------------------------------------
# Catalog discovery
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryResult:
    """Outcome of one provider's catalog discovery.

    ``ok`` means "we obtained a candidate list" — for catalog endpoints that
    is an HTTP success; for documented-only providers it means the
    documented candidates exist (no availability claim is made either way).
    ``error`` carries the real failure text when ``ok`` is False.
    """

    ok: bool
    models: list[dict[str, Any]]
    error: str | None = None
    latency_ms: float | None = None


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _body_snippet(response: httpx.Response) -> str:
    return (response.text or "").strip().replace("\n", " ")[:300]


def _iter_entries(payload: Any) -> list[Any]:
    """Normalize common catalog shapes to a list of entries."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "models", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        # Dict keyed by model id => treat keys as ids.
        if payload and all(isinstance(k, str) for k in payload):
            entries: list[Any] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("id", key)
                    entries.append(item)
                elif isinstance(value, str):
                    entries.append({"id": key, "name": value})
            return entries
    return []


def _entry_id(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry.strip() or None
    if isinstance(entry, dict):
        for key in ("id", "name", "model", "model_id"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    try:
        return int(number)
    except (ValueError, TypeError):
        return None


def _extract_price(entry: dict[str, Any], kind: str) -> float | None:
    """Read input/output pricing from the common shapes providers use."""
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        pricing = {}
    if kind == "input":
        candidates = [
            pricing.get("input"),
            pricing.get("prompt"),
            entry.get("input_price"),
            entry.get("prompt_price"),
            entry.get("input"),
        ]
    else:
        candidates = [
            pricing.get("output"),
            pricing.get("completion"),
            entry.get("output_price"),
            entry.get("completion_price"),
            entry.get("output"),
        ]
    for candidate in candidates:
        value = _to_float(candidate)
        if value is not None:
            return value
    return None


def _is_text_model(model_id: str, entry: dict[str, Any]) -> bool:
    modality = str(
        entry.get("modality") or entry.get("type") or entry.get("task") or entry.get("category") or ""
    ).lower()
    if any(word in modality for word in ("image", "video", "audio", "speech")):
        return False
    return not bool(NON_TEXT_HINTS.search(model_id.lower()))


def _free_candidate(spec: ProviderSpec, model_id: str, entry: dict[str, Any]) -> bool:
    """Per-provider policy: is this catalog entry a *free anonymous* candidate?

    Each branch states its evidence: explicit zero pricing, an explicit free
    flag, membership in the provider's documented anonymous set, or the
    endpoint's documented access model. Entries with *positive* known prices
    are never kept.
    """
    in_price = _extract_price(entry, "input")
    out_price = _extract_price(entry, "output")
    if in_price is not None and in_price != 0:
        return False
    if out_price is not None and out_price != 0:
        return False

    if spec.name == "ovhcloud":
        # OVHcloud Kepler AI public endpoints are keyless for text chat models.
        return True
    if spec.name == "pollinations":
        # Legacy public text endpoint: anonymous access model is the evidence.
        return True
    if spec.name == "aihorde":
        # Community-run, kudos/priority based — no per-token billing.
        return True
    if spec.name == "vireonix":
        # Documented keyless gateway; keep everything the catalog returns.
        return True
    if spec.name == "llm7":
        # Free = catalog says both prices are zero, or docs list it as anonymous.
        return (in_price == 0 and out_price == 0) or model_id in spec.documented_models
    if spec.name == "blockrun":
        explicit = entry.get("free") if isinstance(entry.get("free"), bool) else None
        if explicit is None and isinstance(entry.get("is_free"), bool):
            explicit = entry.get("is_free")
        return explicit is True or (in_price == 0 and out_price == 0)
    if spec.name == "persorai":
        # Documented free gateway: keep when no positive price metadata exists
        # (checked above); zero-price and price-less entries both qualify.
        return True
    if spec.name == "cehpoint":
        return model_id in spec.documented_models
    # Unknown provider shape: only keep entries with explicit zero pricing.
    return in_price == 0 and out_price == 0


def discover(spec: ProviderSpec) -> DiscoveryResult:
    """Fetch (or document) the provider's free anonymous model candidates."""
    if spec.models_path is None:
        return DiscoveryResult(
            ok=True,
            models=[{"id": mid, "source": "documented"} for mid in spec.documented_models],
        )

    start = time.perf_counter()
    try:
        response = request(
            "GET",
            spec.base_url + spec.models_path,
            headers=dict(spec.auth_header),
            timeout=DISCOVERY_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        return DiscoveryResult(
            ok=False,
            models=[],
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=(time.perf_counter() - start) * 1000.0,
        )
    latency = (time.perf_counter() - start) * 1000.0

    if response.status_code >= 400:
        return DiscoveryResult(
            ok=False,
            models=[],
            error=f"HTTP {response.status_code}: {_body_snippet(response)}",
            latency_ms=latency,
        )

    models: list[dict[str, Any]] = []
    for raw in _iter_entries(_safe_json(response)):
        entry = raw if isinstance(raw, dict) else {"id": raw}
        mid = _entry_id(raw)
        if not mid or not _is_text_model(mid, entry):
            continue
        if not _free_candidate(spec, mid, entry):
            continue
        models.append(
            {
                "id": mid,
                "display_name": str(entry.get("name") or mid),
                "context_length": _to_int(
                    entry.get("context_length") or entry.get("context_window") or entry.get("context")
                ),
                "max_output_tokens": _to_int(
                    entry.get("max_output_tokens") or entry.get("max_completion_tokens")
                ),
                "created": _to_int(entry.get("created")),
                "source": "catalog",
            }
        )

    # Documented anonymous ids absent from the live catalog stay available as
    # explicitly-labeled candidates (never presented as discovered entries).
    known = {m["id"] for m in models}
    for mid in spec.documented_models:
        if mid not in known:
            models.append({"id": mid, "source": "documented"})

    return DiscoveryResult(ok=True, models=models, latency_ms=latency)


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


@dataclass
class ProviderChatResult:
    """Raw result of one provider chat call (honest passthrough)."""

    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_openai_chat(payload: dict[str, Any]) -> ProviderChatResult:
    choices = payload.get("choices")
    message: dict[str, Any] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first = choices[0]
        if isinstance(first.get("message"), dict):
            message = first["message"]
        elif isinstance(first.get("text"), str):
            return ProviderChatResult(text=first["text"])

    text = _content_to_text(message.get("content"))
    tool_calls_raw = message.get("tool_calls")
    tool_calls = (
        [tc for tc in tool_calls_raw if isinstance(tc, dict)] if isinstance(tool_calls_raw, list) else []
    )

    if not text and not tool_calls:
        # Never present a raw payload dump as if the model wrote it.
        raise ProviderError("response", "response contained no message content")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    return ProviderChatResult(text=text, tool_calls=tool_calls, usage=usage)


def _aihorde_prompt(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user")).upper()
        parts.append(f"{role}: {_content_to_text(message.get('content'))}")
    parts.append("ASSISTANT:")
    return "\n\n".join(parts)


def _aihorde_chat(
    spec: ProviderSpec,
    model_id: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
    timeout: float,
) -> ProviderChatResult:
    headers = {"apikey": AI_HORDE_ANONYMOUS_KEY}
    payload = {
        "prompt": _aihorde_prompt(messages),
        "params": {
            "temperature": temperature,
            "max_length": max_tokens,
            "max_context_length": 8192,
        },
        "models": [model_id],
    }
    response = request(
        "POST",
        spec.base_url + "/v2/generate/text/async",
        headers=headers,
        json_body=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise ProviderError(spec.name, _body_snippet(response), status_code=response.status_code)
    submit = _safe_json(response)
    request_id = submit.get("id") if isinstance(submit, dict) else None
    if not request_id:
        raise ProviderError(spec.name, f"no request id in response: {_body_snippet(response)}")

    deadline = time.time() + max(60.0, timeout)
    while time.time() < deadline:
        time.sleep(2.0)
        status_response = request(
            "GET",
            spec.base_url + f"/v2/generate/text/status/{request_id}",
            headers=headers,
            timeout=timeout,
        )
        if status_response.status_code >= 400:
            raise ProviderError(
                spec.name,
                _body_snippet(status_response),
                status_code=status_response.status_code,
            )
        status_payload = _safe_json(status_response)
        if not isinstance(status_payload, dict):
            continue
        if status_payload.get("faulted"):
            raise ProviderError(spec.name, "generation faulted")
        if status_payload.get("cancelled"):
            raise ProviderError(spec.name, "generation cancelled")
        generations = status_payload.get("generations")
        if status_payload.get("done") and isinstance(generations, list) and generations:
            first = generations[0]
            text = first.get("text") if isinstance(first, dict) else str(first)
            if text:
                return ProviderChatResult(text=str(text).strip())
    # Builtin TimeoutError: classified retryable by alpha.models.fallback.
    raise TimeoutError(f"AI Horde request {request_id} did not complete before timeout")


def chat_completion(
    spec: ProviderSpec,
    model_id: str,
    messages: list[dict[str, Any]],
    *,
    temperature: float | None = None,
    max_tokens: float | int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    extra_body: dict[str, Any] | None = None,
) -> ProviderChatResult:
    """Send one chat request; raises on HTTP error or empty content.

    ``temperature``/``max_tokens`` are optional: ``None`` omits the key from
    the payload so the provider's documented default applies (never a value
    this module invented).
    """
    if not spec.openai_compat or spec.chat_path is None:
        return _aihorde_chat(
            spec,
            model_id,
            messages,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=int(max_tokens) if max_tokens is not None else 1024,
            timeout=timeout,
        )

    body: dict[str, Any] = {"model": model_id, "messages": messages}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if extra_body:
        body.update(extra_body)

    response = request(
        "POST",
        spec.base_url + spec.chat_path,
        headers=dict(spec.auth_header),
        json_body=body,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise ProviderError(spec.name, _body_snippet(response), status_code=response.status_code)
    payload = _safe_json(response)
    if not isinstance(payload, dict):
        raise ProviderError(spec.name, "provider returned a non-JSON response")
    try:
        result = _extract_openai_chat(payload)
    except ProviderError as exc:
        raise ProviderError(spec.name, str(exc)) from None
    return result


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    """Outcome of a small liveness probe.

    A failed probe is *inconclusive*, never proof that the chat path is
    dead: the catalog records the error and leaves health ``None``.
    """

    ok: bool
    latency_ms: float
    error: str | None = None


def health_probe(spec: ProviderSpec, model_id: str) -> ProbeResult:
    start = time.perf_counter()
    try:
        if not spec.openai_compat:
            response = request(
                "GET",
                spec.base_url + "/v2/status/heartbeat",
                headers={"apikey": AI_HORDE_ANONYMOUS_KEY},
                timeout=DISCOVERY_TIMEOUT,
            )
            latency = (time.perf_counter() - start) * 1000.0
            if response.status_code >= 400:
                return ProbeResult(
                    ok=False,
                    latency_ms=latency,
                    error=f"HTTP {response.status_code}: heartbeat failed",
                )
            return ProbeResult(ok=True, latency_ms=latency)

        chat_completion(
            spec,
            model_id,
            [{"role": "user", "content": "Reply with OK."}],
            temperature=0.0,
            max_tokens=16,
            timeout=DISCOVERY_TIMEOUT,
        )
        return ProbeResult(ok=True, latency_ms=(time.perf_counter() - start) * 1000.0)
    except Exception as exc:  # noqa: BLE001 - probes must never raise
        return ProbeResult(
            ok=False,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            error=f"{type(exc).__name__}: {str(exc)[:150]}",
        )
