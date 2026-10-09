"""Subprocess-side driver for browser-use — executed by the managed venv's python.

The Gateway process never imports this module: it is launched as a script by
``<venv>/bin/python`` (``Scripts/python.exe`` on Windows) so that browser-use
and its own pinned playwright/langchain live in a separate environment from the
one serving requests.

Protocol: the harness writes one JSON request to stdin and reads one JSON
envelope from the **last** non-empty stdout line. Anything else on stdout is
log noise (playwright and provider SDKs are chatty), and stderr is only kept
for a failure report.

This file must stay stdlib-only at import time — it imports ``browser_use`` and
the LLM class lazily, inside the run, so that an installation problem surfaces
as an honest envelope instead of a traceback that never reaches the harness.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import traceback
from pathlib import Path

#: How many history rows travel back. browser-use's own step log can be long;
#: the caller needs the shape of what happened, not a transcript.
_MAX_HISTORY = 20


def _force_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 and tolerant of unencodable characters.

    Found by running for real on Windows: browser-use's own progress output
    contains glyphs (U+25B6 and similar), and a cp1252 stdout raises
    ``UnicodeEncodeError`` the moment one is printed — killing a run that was
    otherwise fine, with a traceback about an arrow character rather than about
    the task. ``errors="backslashreplace"`` is also deliberate: a browser page can
    put any character on screen, so a single unprintable byte must never be
    allowed to end a run.

    Reconfiguring the streams (rather than only setting ``PYTHONIOENCODING``)
    matters because the harness pipes this process, and the child inherits the
    parent's console encoding rather than a UTF-8 one.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            # An already-detached or non-configurable stream is acceptable: the
            # envelope is written last and parse_errors="replace" below keeps it
            # readable either way.
            continue


def _emit(envelope: dict) -> None:
    # ensure_ascii keeps the envelope pure-ASCII, so it survives even a hostile
    # console encoding; parse_errors="replace" means a byte the stream cannot
    # represent degrades to an escape rather than raising mid-write.
    sys.stdout.write(json.dumps(envelope, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _browser_use_version() -> str | None:
    """Version of the installed distribution.

    ``browser_use.__version__`` does not exist in current releases, so this reads
    ``importlib.metadata`` — the same source the manager's install probe uses,
    which keeps the version reported on a run and the version reported as
    installed from ever disagreeing.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as dist_version

    try:
        return dist_version("browser-use")
    except PackageNotFoundError:
        return None


#: LiteLLM routes on a ``provider/model`` id. Alpha's ``models[].model`` is the
#: bare provider-side slug (``unbiased/pareto``), which LiteLLM rejects with
#: "LLM Provider NOT provided" — a failure that only appears once the agent is
#: already mid-task, repeated on every single step.
#:
#: So the prefix is derived from the endpoint host. Note this is a *different*
#: namespace from the OpenRouter API slug: ``unbiased/pareto`` is correct for the
#: HTTP API and ``openrouter/unbiased/pareto`` is correct for LiteLLM. Do not
#: "fix" the config to carry the prefix — that would break the ordinary
#: ChatOpenAI path this repo's baseline model uses.
_LITELLM_PROVIDER_BY_HOST: dict[str, str] = {
    "openrouter.ai": "openrouter",
    "api.anthropic.com": "anthropic",
    "api.groq.com": "groq",
    "generativelanguage.googleapis.com": "gemini",
    "api.mistral.ai": "mistral",
    "api.deepseek.com": "deepseek",
    "api.together.xyz": "together_ai",
    "api.fireworks.ai": "fireworks_ai",
    "api.cohere.com": "cohere",
    "api.cerebras.ai": "cerebras",
}

#: Prefixes LiteLLM already understands. A model whose first segment is one of
#: these passes through untouched, so an operator who declared a
#: provider-qualified id is never double-prefixed.
_KNOWN_LITELLM_PROVIDERS: frozenset[str] = frozenset(
    {
        "openrouter",
        "openai",
        "anthropic",
        "gemini",
        "google",
        "groq",
        "mistral",
        "deepseek",
        "together_ai",
        "fireworks_ai",
        "cohere",
        "cerebras",
        "azure",
        "bedrock",
        "vertex_ai",
        "ollama",
        "litellm_proxy",
    }
)


def _litellm_model_id(model: str, base_url: str | None) -> str:
    """Return ``model`` in the ``provider/model`` form LiteLLM requires."""
    if model.split("/", 1)[0].lower() in _KNOWN_LITELLM_PROVIDERS:
        return model
    host = ""
    if base_url:
        from urllib.parse import urlparse

        host = (urlparse(base_url).hostname or "").lower()
    return f"{_LITELLM_PROVIDER_BY_HOST.get(host, 'openai')}/{model}"


def _build_llm(spec: dict) -> object:
    """Construct the chat model browser-use will be driven by.

    Two shapes are supported, in this order, because current browser-use
    releases no longer accept a bare LangChain client: they require their own
    ``BaseChatModel`` protocol (a ``model`` attribute plus a ``provider``
    property), which ``langchain_openai.ChatOpenAI`` does not satisfy. Passing
    one anyway fails deep inside the *first model call* with
    ``'ChatOpenAI' object has no attribute 'provider'`` — a much worse place to
    learn it than here.

    1. ``browser_use.llm.litellm.chat.ChatLiteLLM`` — browser-use's own
       provider adapter. Any OpenAI-compatible endpoint works through it
       (``base_url`` maps onto its ``api_base``), which covers the common case
       and every OpenAI-compatible gateway.
    2. The operator's configured ``module:Class`` path, so an explicitly declared
       custom client still wins.

    The model id and endpoint always come from Alpha's ``models[]`` entry, so no
    second model declaration is introduced.
    """
    model = spec.get("model_name")
    if not model:
        raise ValueError("llm spec has no model_name; check the models[] entry browser-use was pointed at.")

    native = _try_native_llm(spec, model)
    if native is not None:
        return native
    return _build_configured_llm(spec, model)


def _try_native_llm(spec: dict, model: str) -> object | None:
    """browser-use's own adapter, or ``None`` to fall back to the declared class.

    Every failure path is logged with its reason. A silent ``None`` here is what
    made a real failure unreadable: the adapter was unavailable, the run fell
    back to the operator's declared class, and the only thing surfaced was that
    fallback's ImportError — pointing at a package that was never the problem.
    """
    try:
        from browser_use.llm.litellm.chat import ChatLiteLLM
    except Exception as exc:  # noqa: BLE001 - any import failure means "unavailable"
        print(f"[browser-use runner] native ChatLiteLLM unavailable ({type(exc).__name__}: {exc}); using the configured model class instead", file=sys.stderr)
        return None

    kwargs: dict = {"model": _litellm_model_id(model, spec.get("base_url"))}
    if spec.get("api_key"):
        kwargs["api_key"] = spec["api_key"]
    else:
        # LiteLLM's OpenAI-compatible provider refuses to build a client without
        # an api_key ("The api_key client option must be set...") even when the
        # endpoint is keyless and ignores the value. Several gateways Alpha lists
        # as free are exactly that, so a placeholder is passed rather than failing
        # a call the endpoint would have accepted. It is not a credential.
        kwargs["api_key"] = "not-required"
    if spec.get("base_url"):
        # Same endpoint, different parameter name on this adapter.
        kwargs["api_base"] = spec["base_url"]
    extra = spec.get("extra") or {}
    for key in ("temperature", "max_tokens", "max_retries"):
        if key in extra:
            kwargs[key] = extra[key]
    try:
        return ChatLiteLLM(**kwargs)
    except Exception as exc:  # noqa: BLE001 - degrade to the declared class
        print(f"[browser-use runner] native ChatLiteLLM unavailable ({exc}); using the configured model class instead", file=sys.stderr)
        return None


def _build_configured_llm(spec: dict, model: str) -> object:
    """Build the chat model from Alpha's ``module:Class`` convention.

    An operator who configured ``langchain_openai:ChatOpenAI`` against OpenRouter
    keeps the same endpoint here.
    """
    use = spec.get("use") or ""
    module_path, _, class_name = use.partition(":")
    if not module_path or not class_name:
        raise ValueError(f"llm spec has no `module:Class` path (got {use!r})")

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        distribution = module_path.split(".")[0].replace("_", "-")
        raise RuntimeError(
            f"the browser-use venv cannot import {module_path!r} ({exc}). Install its client package into the managed venv ({sys.prefix}) with `pip install {distribution}`, or declare it in browser_use_setup's extra_packages."
        ) from exc

    factory = getattr(module, class_name, None)
    if factory is None:
        raise RuntimeError(f"{module_path!r} has no attribute {class_name!r}")

    kwargs: dict = {"model": model}
    if spec.get("api_key"):
        kwargs["api_key"] = spec["api_key"]
    if spec.get("base_url"):
        kwargs["base_url"] = spec["base_url"]
    kwargs.update(spec.get("extra") or {})
    return factory(**kwargs)


def _build_agent(**kwargs):
    """Construct ``browser_use.Agent``, dropping optional kwargs it rejects.

    browser-use renames optional constructor arguments between releases — 0.13.x
    has no ``headless`` parameter at all, because it drives the host's Chrome over
    CDP — so a hard failure on an optional kwarg would make an otherwise-working
    install unusable for a version skew. Required arguments (``task``/``llm``)
    are never dropped: if those change, the caller deserves the real error.
    """
    from browser_use import Agent

    optional = [key for key in ("use_vision", "headless", "use_judge", "judge_llm", "ground_truth") if key in kwargs]
    attempt = dict(kwargs)
    while True:
        try:
            return Agent(**attempt)
        except TypeError as exc:
            rejected = next((key for key in optional if key in attempt and key in str(exc)), None)
            if rejected is None:
                raise
            print(f"[browser-use runner] dropping unsupported argument {rejected!r}: {exc}", file=sys.stderr)
            attempt.pop(rejected)


def _as_text(result: object) -> str:
    """Normalize whatever ``agent.run()`` returned into the agent's answer.

    Current releases return an ``AgentHistoryList`` whose ``final_result()`` is
    the answer; older ones returned the string directly. Both are read, in that
    order.

    When neither yields text — which is what a run that exhausts its step budget
    without finishing produces, since ``final_result()`` is then ``None`` — this
    falls back to the last extracted page content and, failing that, says so
    explicitly. It must **not** fall back to ``str(result)``: that is a pydantic
    repr of the entire internal history, which would ship hundreds of
    characters of upstream object dump into the model context as if it were the
    agent's conclusion.
    """
    if result is None:
        return ""
    if isinstance(result, str):
        return result

    for attr in ("final_result", "final_result_text", "extract_content"):
        candidate = getattr(result, attr, None)
        if callable(candidate):
            try:
                value = candidate()
            except Exception:  # noqa: BLE001 - a cosmetic path must not fail the run
                continue
            if isinstance(value, str) and value.strip():
                return value

    # No final answer (an unfinished or budget-exhausted run). The most useful
    # honest substitute is whatever was last read off the page.
    extracted = getattr(result, "extracted_content", None)
    if callable(extracted):
        try:
            value = extracted()
        except Exception:  # noqa: BLE001
            value = None
        if isinstance(value, str) and value.strip():
            return f"(No final answer — the step budget ran out. Last page content observed:)\n{value.strip()}"
    return "(No final answer — the agent stopped without producing one. Check the pages it visited.)"


def _history_rows(agent: object) -> list[dict]:
    """Summarize browser-use's step log defensively.

    Prefers the public aggregate properties (``urls``, ``action_names``) over
    walking the internal step objects, which are pydantic models whose nested
    shape shifts between releases. Every access is probed rather than asserted,
    so a rename degrades the summary to empty instead of turning a completed
    task into a reported failure.
    """
    history = getattr(agent, "history", None)
    if history is None:
        return []

    urls = _safe_call(history, "urls") or []
    names = _safe_call(history, "action_names") or []
    if isinstance(urls, list) and isinstance(names, list) and (urls or names):
        return [{"url": urls[index] if index < len(urls) else None, "actions": [names[index]] if index < len(names) else []} for index in range(min(len(names), len(urls) or len(names), _MAX_HISTORY))]

    rows = getattr(history, "history", None) or []
    summary: list[dict] = []
    for row in list(rows)[-_MAX_HISTORY:]:
        actions: list[str] = []
        model_output = getattr(row, "model_output", None)
        for item in getattr(model_output, "action", None) or []:
            label = type(item).__name__
            data = getattr(item, "data", None)
            if isinstance(data, dict):
                index = next((value for key, value in data.items() if key.startswith("index")), None)
                if index is not None:
                    label = f"{label}[{index}]"
            actions.append(label)
        url = None
        for source in (getattr(row, "result", None), row):
            candidate = getattr(source, "url", None)
            if isinstance(candidate, str) and candidate:
                url = candidate
                break
        summary.append({"actions": actions, "url": url})
    return summary


def _safe_call(obj: object, attr: str) -> object | None:
    """Call ``obj.attr()`` if it exists, never raising."""
    candidate = getattr(obj, attr, None)
    if not callable(candidate):
        return None
    try:
        return candidate()
    except Exception:  # noqa: BLE001 - a cosmetic path must not fail the run
        return None


def _screenshot_paths(result: object, scratch_dir: str) -> list[str]:
    """Write browser-use's captured screenshots to disk and return their paths.

    Returns absolute paths under the run's scratch directory. ``use_vision`` is
    what makes browser-use capture a frame per step, so with vision off this is
    legitimately empty — a caller that wants visual evidence asks for a
    vision-enabled run rather than getting nothing silently.
    """
    shots = _safe_call(result, "screenshots") or []
    if not isinstance(shots, list) or not shots:
        return []

    out_dir = Path(scratch_dir) / "screenshots"
    written: list[str] = []
    for index, encoded in enumerate(shots):
        if not isinstance(encoded, str) or not encoded.strip():
            continue
        target = out_dir / f"step-{index:03d}.png"
        if _write_screenshot(target, encoded):
            written.append(str(target))
    return written


def _write_screenshot(path: Path, b64: str) -> bool:
    """Decode one base64 PNG to disk. A single bad frame must not lose the rest."""
    import base64

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(b64.split(",", 1)[-1], validate=False)
    except Exception:  # noqa: BLE001 - a cosmetic artifact must not fail the run
        return False
    if not data.startswith(b"\x89PNG"):
        return False
    try:
        path.write_bytes(data)
    except OSError:
        return False
    return True


async def _run_agent(payload: dict) -> dict:
    llm = _build_llm(payload.get("llm") or {})

    task = str(payload.get("task") or "").strip()
    start_url = payload.get("start_url")
    if start_url:
        # browser-use has no cross-version start-url parameter, so the entry
        # point is stated in the task itself — which every version honours.
        task = f"Start by opening {start_url}. {task}"

    agent = _build_agent(
        task=task,
        llm=llm,
        use_vision=bool(payload.get("use_vision", False)),
        headless=bool(payload.get("headless", True)),
        # Optional verification, driven by the operator. browser-use's own judge
        # re-checks the run against a declared ground truth and reports the
        # reason when it disagrees — which is how a real run was caught claiming
        # example.com had no <h1> when it plainly did. Without it the agent's own
        # "success" is the only verdict available.
        use_judge=bool(payload.get("verify")),
        judge_llm=_maybe_build_judge(payload),
        ground_truth=payload.get("ground_truth") or None,
    )

    max_steps = int(payload.get("max_steps") or 10)
    result = await agent.run(max_steps=max_steps)
    history = _history_rows(agent)

    # `is_done`/`errors` are public on AgentHistoryList, and both are load-bearing.
    # A run that ends without `is_done` did NOT accomplish the task, and `errors`
    # is where upstream puts the reason: a real run failed on every single model
    # call with "No module named 'litellm'" while the envelope still said ok, and
    # the only thing left to report was "no final answer". Reading these two
    # turns that into "did not complete: <the actual reason>".
    completed = bool(_safe_call(result, "is_done"))
    errors = [str(err) for err in (_safe_call(result, "errors") or []) if err]
    # The judge's verdict, when one was requested. `success` is the agent's own
    # claim and is deliberately kept separate from `judgement` — the judge exists
    # precisely because they can disagree.
    judgement = _safe_call(result, "judgement")
    if not isinstance(judgement, dict):
        judgement = None

    # ``number_of_steps`` is public on AgentHistoryList and counts real steps;
    # the summarized rows are capped, so they are not a reliable step count.
    steps = _safe_call(result, "number_of_steps")
    if not isinstance(steps, int):
        steps = len(history)
    shots = _screenshot_paths(result, os.getcwd())
    return {
        "ok": True,
        "completed": completed,
        "errors": errors,
        "result": _as_text(result),
        "steps": steps,
        "history": history,
        "screenshots": shots,
        "judgement": judgement,
        "version": _browser_use_version(),
        "error": None,
        "timed_out": False,
    }


def _maybe_build_judge(payload: dict) -> object | None:
    """Build the judge model only when verification was asked for.

    The judge defaults to the driving model: the point of a separate judge is a
    second opinion on the same evidence, not a different model.
    """
    if not payload.get("verify"):
        return None
    judge_spec = payload.get("judge_llm") or payload.get("llm")
    if not judge_spec:
        return None
    try:
        return _build_llm(judge_spec)
    except Exception as exc:  # noqa: BLE001 - verification must not sink the task
        print(f"[browser-use runner] judge unavailable, running unverified: {exc}", file=sys.stderr)
        return None


def main() -> int:
    _force_utf8_streams()
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        _emit({"ok": False, "error": f"runner received invalid JSON on stdin: {exc}", "steps": None, "result": None, "history": []})
        return 2

    if not isinstance(payload, dict):
        _emit({"ok": False, "error": "runner expected a JSON object request", "steps": None, "result": None, "history": []})
        return 2

    try:
        envelope = asyncio.run(_run_agent(payload))
    except Exception as exc:  # noqa: BLE001 - every failure is reported as data
        envelope = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-2000:],
            "steps": None,
            "result": None,
            "history": [],
            "timed_out": False,
        }
    _emit(envelope)
    return 0 if envelope.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
