"""Agent-facing tools for the managed `browser-use`_ runtime.

Two tools, deliberately split:

``browser_use_setup``
    Installs (or upgrades) the latest browser-use into the managed virtualenv
    and reports the version it can prove by importing it.

``browser_use_run``
    Hands **one** bounded task to browser-use and returns its result. Alpha
    stays the planner: the inner agent loop runs in a subprocess with a hard
    step budget, and only a small JSON envelope (result text, step count, URL
    trail) crosses back into the conversation.

This is an *opt-in* addition to, not a replacement for, the ``browser_*`` tools
in :mod:`alpha.community.browser_automation`. Those are the cheap, host-driven
loop where Alpha sees and approves every step; these hand a whole task to
browser-use and take its word for the outcome. Prefer the ``browser_*`` tools
when the steps need auditing, and this one when the task is well-scoped and the
detailed trajectory is not the deliverable.

.. _browser-use: https://github.com/browser-use/browser-use
"""

# NOTE: no ``from __future__ import annotations`` — under PEP 563 the
# ``runtime: Runtime`` annotation below becomes the *string* "Runtime", and
# LangChain's injected-argument detection inspects the annotation object, so a
# string never matches and ``runtime`` stops being registered as injected (see
# ``alpha.tools.types.Runtime``).

import asyncio
import logging
from typing import Annotated, Any

from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from alpha.community.url_safety import resolve_host_addresses as _resolve_host_addresses
from alpha.community.url_safety import validate_public_http_url
from alpha.config import get_app_config
from alpha.tools.types import Runtime

from .manager import (
    DEFAULT_INSTALL_TIMEOUT_SECONDS,
    DEFAULT_MAX_STEPS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    MAX_MAX_STEPS,
    MAX_RESULT_CHARS,
    MAX_RUN_TIMEOUT_SECONDS,
    MAX_TASK_CHARS,
    BrowserUseError,
    BrowserUseStatus,
    get_browser_use_manager,
    resolve_llm_spec,
)

logger = logging.getLogger(__name__)


def _get_tool_config(tool_name: str) -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    if config is None:
        return {}
    return config.model_extra or {}


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def _extra_packages(cfg: dict[str, Any]) -> list[str]:
    raw = cfg.get("extra_packages") or []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    return [str(pkg).strip() for pkg in raw if str(pkg).strip()]


def _tool_message(content: str, tool_call_id: str) -> Command:
    return Command(update={"messages": [ToolMessage(content, tool_call_id=tool_call_id)]})


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[truncated: {len(text) - limit} more characters not shown]"


def _render_status(status: BrowserUseStatus, *, log: tuple[str, ...] = ()) -> str:
    lines = [status.summary()]
    if log:
        lines.append("")
        lines.append("Install log (tail):")
        lines.extend(f"  - {entry}" for entry in log[-6:])
    return "\n".join(lines)


@tool("browser_use_setup", parse_docstring=True)
async def browser_use_setup_tool(runtime: Runtime, tool_call_id: Annotated[str, InjectedToolCallId], upgrade: bool = False) -> Command:
    """Install or upgrade the open-source browser-use automation library.

    browser-use is a full autonomous browser agent: it plans its own steps,
    clicks, types and reads pages on its own. Use it for a **whole, well-scoped
    web task** ("find the current price of X and summarise the options"), not for
    single page interactions — for those, the cheaper browser_navigate /
    browser_click / browser_type tools show you every step and are much faster.

    The first call is slow: it downloads browser-use and its Chromium browser
    into a private virtualenv (several minutes, once per machine). After that
    calls are fast. Everything runs in a subprocess, so a browser-use failure
    can never take down the session.

    Call this when browser_use_run reports that browser-use is not installed, or
    when you want to pick up a new upstream release.

    Args:
        upgrade: Set true to install the newest available version even when browser-use is already present.
    """
    cfg = _get_tool_config("browser_use_setup")
    manager = get_browser_use_manager(_as_str(cfg.get("venv_path")))
    want_upgrade = upgrade or _as_bool(cfg.get("upgrade"), False)
    timeout_seconds = _as_int(cfg.get("install_timeout_seconds"), DEFAULT_INSTALL_TIMEOUT_SECONDS)
    extra_packages = _extra_packages(cfg)

    try:
        # Every install step is a blocking subprocess; keep it off the event loop.
        status = await asyncio.to_thread(
            manager.ensure_installed,
            upgrade=want_upgrade,
            timeout_seconds=timeout_seconds,
            extra_packages=extra_packages,
        )
    except BrowserUseError as e:
        logger.error("browser_use_setup failed: %s", e)
        return _tool_message(f"Error: browser-use installation failed: {e}", tool_call_id)
    except Exception as e:  # noqa: BLE001 - a tool must answer, not raise
        logger.error(f"browser_use_setup unexpected failure: {e}")
        return _tool_message(f"Error: browser-use installation failed: {e}", tool_call_id)

    return _tool_message(_render_status(status, log=status.log), tool_call_id)


@tool("browser_use_run", parse_docstring=True)
async def browser_use_run_tool(
    runtime: Runtime,
    task: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    model: str | None = None,
    start_url: str | None = None,
    max_steps: int | None = None,
) -> Command:
    """Delegate one complete web task to the autonomous browser-use agent.

    Use this when a task needs many dependent web steps that you do not want to
    drive one at a time: logging into a portal and exporting a report, filling a
    multi-step checkout, comparing options across several pages, reading a table
    that needs pagination and filtering. Describe the finished result you want;
    browser-use plans and executes the steps.

    Prefer browser_navigate / browser_click / browser_type for single actions or
    anything where each step must be visible and auditable — those are cheaper
    and you stay in control. Do not call both for the same task.

    The first run installs browser-use and downloads its Chromium browser, which
    takes several minutes. The agent runs in a subprocess under a step and time
    budget, so a stuck or failing page cannot hang the session. Its final answer
    is what you get back, plus the URLs it visited and how many steps it took.

    Args:
        task: What you want accomplished on the web, in plain language, including any target site and the exact output you need.
        model: Optional model name from config.yaml to drive browser-use. Defaults to the configured default model.
        start_url: Optional URL to open first, instead of leaving the starting point to browser-use.
        max_steps: Optional step budget (default 10, max 50). Use it when you know the task is short; leave it unset otherwise.
    """
    cfg = _get_tool_config("browser_use_run")

    cleaned_task = (task or "").strip()
    if not cleaned_task:
        return _tool_message("Error: task must not be empty — describe what you want browser-use to accomplish.", tool_call_id)
    if len(cleaned_task) > MAX_TASK_CHARS:
        return _tool_message(f"Error: task is {len(cleaned_task)} characters, over the {MAX_TASK_CHARS}-character limit. Describe the goal more tightly.", tool_call_id)

    resolved_steps = _as_int(max_steps if max_steps is not None else cfg.get("max_steps"), DEFAULT_MAX_STEPS)
    if resolved_steps < 1:
        return _tool_message(f"Error: max_steps must be at least 1, got {resolved_steps}.", tool_call_id)
    if resolved_steps > MAX_MAX_STEPS:
        return _tool_message(f"Error: max_steps={resolved_steps} is over the {MAX_MAX_STEPS}-step ceiling. Split the task into smaller pieces, or raise the cap in config.yaml if you truly need it.", tool_call_id)

    timeout_seconds = _as_int(cfg.get("timeout_seconds"), DEFAULT_RUN_TIMEOUT_SECONDS)
    if timeout_seconds < 10:
        return _tool_message(f"Error: timeout_seconds must be at least 10, got {timeout_seconds}.", tool_call_id)
    if timeout_seconds > MAX_RUN_TIMEOUT_SECONDS:
        return _tool_message(f"Error: timeout_seconds={timeout_seconds} is over the {MAX_RUN_TIMEOUT_SECONDS}s ceiling.", tool_call_id)

    url_error: str | None = None
    if start_url:
        allow_private = _as_bool(cfg.get("allow_private_addresses"), False)
        url_error = validate_public_http_url(
            start_url,
            allow_private_addresses=allow_private,
            action="browse",
            resolver=_resolve_host_addresses,
        )
        if url_error:
            return _tool_message(f"Error: start_url rejected by the URL policy. {url_error}", tool_call_id)

    manager = get_browser_use_manager(_as_str(cfg.get("venv_path")))
    auto_install = _as_bool(cfg.get("auto_install"), True)

    try:
        # Config resolution stats the config file on every call, so it — like the
        # install and the run — happens off the event loop.
        status = await asyncio.to_thread(manager.status)
        if not status.installed:
            if not auto_install:
                return _tool_message(f"Error: browser-use is not installed in {manager.venv_dir}. Call browser_use_setup first, or set auto_install: true on the browser_use_run tool config.", tool_call_id)
            logger.info("browser_use_run installing browser-use on demand (first use)")
            await asyncio.to_thread(
                manager.ensure_installed,
                upgrade=_as_bool(cfg.get("upgrade_on_install"), False),
                timeout_seconds=_as_int(cfg.get("install_timeout_seconds"), DEFAULT_INSTALL_TIMEOUT_SECONDS),
                extra_packages=_extra_packages(cfg),
            )

        llm_spec = await asyncio.to_thread(resolve_llm_spec, model)
        envelope = await asyncio.to_thread(
            manager.run,
            task=cleaned_task,
            llm_spec=llm_spec,
            start_url=start_url,
            max_steps=resolved_steps,
            timeout_seconds=timeout_seconds,
            headless=_as_bool(cfg.get("headless"), True),
            use_vision=_as_bool(cfg.get("use_vision"), False),
        )
    except BrowserUseError as e:
        logger.error(f"browser_use_run could not run: {e}")
        return _tool_message(f"Error: browser-use could not run: {e}", tool_call_id)
    except Exception as e:  # noqa: BLE001 - a tool must answer, not raise
        logger.error(f"browser_use_run unexpected failure: {e}")
        return _tool_message(f"Error: browser-use run failed: {e}", tool_call_id)

    return _tool_message(_render_envelope(envelope, version=None, model=model), tool_call_id)


def _render_envelope(envelope: dict[str, Any], *, version: str | None, model: str | None) -> str:
    """Turn the child's JSON envelope into the text the model reads.

    A failure is never dressed as a result, and **an unfinished run is not
    reported as a completed one**. ``ok`` only means the subprocess returned a
    coherent envelope; whether browser-use actually finished the task is
    ``completed`` (upstream's own ``is_done``). A real run that failed on every
    model call still returned ``ok: true`` with no answer, and reporting that as
    "completed" is the most expensive kind of wrong — the caller moves on
    believing it has a result.
    """
    version = envelope.get("version") or version
    steps = envelope.get("steps")
    header_parts = []
    if version:
        header_parts.append(f"browser-use {version}")
    if steps is not None:
        header_parts.append(f"{steps} step{'s' if steps != 1 else ''}")
    if model:
        header_parts.append(f"model {model}")
    header = " — ".join(header_parts) if header_parts else "browser-use run"

    if not envelope.get("ok"):
        reason = envelope.get("error") or "browser-use reported a failure without a reason."
        return f"{header} did NOT complete.\n\n{reason}"

    errors = [str(e) for e in envelope.get("errors") or [] if e]
    completed = envelope.get("completed")

    # `completed` is None only for an older runner; treat unknown as "don't claim
    # success" only when there is also no answer, so an old envelope cannot make a
    # silent failure look like a success.
    answer = envelope.get("result") or ""
    if completed is False or (completed is None and errors and not answer.strip()):
        reasons = ""
        if errors:
            unique = list(dict.fromkeys(errors))
            reasons = "\n\nReasons reported by browser-use:\n" + "\n".join(f"  - {err}" for err in unique[:5])
        return f"{header} did NOT finish the task (it stopped without producing a final answer).{reasons}"

    body = _truncate(answer) if answer.strip() else "(browser-use finished without producing any text — check the pages it visited.)"

    urls = [row.get("url") for row in envelope.get("history") or [] if isinstance(row, dict) and row.get("url")]
    visited = ""
    if urls:
        unique = list(dict.fromkeys(urls))[-10:]
        visited = "\n\nPages visited: " + ", ".join(unique)

    return f"{header} completed.\n\n{body}{visited}"


# Governance declarations (see ``alpha.tools.governance``). These are not
# cosmetic labels: they feed ``resolve_risk_level`` in the discovery catalog and
# the enforcement half, ``GovernanceGuardrailProvider``.
#
# Installing a package is the *higher* risk of the two and is deliberately gated
# ``ask``: it writes to the filesystem, reaches the network, and runs third-party
# setup code — so an unattended background run must not silently mutate the host.
# Running a task is ``external``/``ask`` because the agent acts as an
# authenticated browser session on arbitrary sites, and side effects there
# (submitting a form, posting to an account) are not reversible from here.
browser_use_setup_tool.metadata = {
    "governance_risk_class": "execute",
    "governance_permissions": ["network", "filesystem", "subprocess"],
    "governance_side_effects": ["package_install", "process_spawn"],
    "governance_reversibility": "reversible",
    "governance_verification_method": "status",
    "governance_confirmation": "ask",
    "governance_timeout_seconds": DEFAULT_INSTALL_TIMEOUT_SECONDS,
}

browser_use_run_tool.metadata = {
    "governance_risk_class": "external",
    "governance_permissions": ["network"],
    "governance_side_effects": ["http_request", "browser_automation"],
    # Deliberately not "reversible": a task may submit forms or post to an
    # authenticated account, and no rollback exists from this surface.
    "governance_reversibility": "unknown",
    "governance_verification_method": "none",
    "governance_confirmation": "ask",
    "governance_timeout_seconds": MAX_RUN_TIMEOUT_SECONDS,
}
