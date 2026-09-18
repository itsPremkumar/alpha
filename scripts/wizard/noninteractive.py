"""Non-interactive (env-driven) setup resolution for the Agent Workspace wizard.

Lets operators, Docker/Electron automation, and CI configure Agent Workspace without
a TTY. Every value comes from the environment; nothing prompts:

    LLM provider upright:
      AGENT_WORKSPACE_SETUP_PROVIDER   Provider ``name`` (e.g. ``openai``, ``deepseek``,
                                 ``ollama_qwen``). When unset, the first provider
                                 whose API-key env var is already set wins; when
                                 none is set, a local Ollama daemon is probed.
      AGENT_WORKSPACE_SETUP_MODEL      Model id (must belong to the provider).
                                 Defaults to the provider's default model.
      AGENT_WORKSPACE_SETUP_API_KEY    Explicit key (overrides the provider env var).
      AGENT_WORKSPACE_SETUP_BASE_URL   Gateway URL for OpenAI-compatible providers.
      AGENT_WORKSPACE_SETUP_THINKING   ``1``/``0`` for generic gateways that ask about
                                 thinking support. Defaults to ``0``.
      AGENT_WORKSPACE_SETUP_OLLAMA     ``1`` to force the local Ollama provider.

    Web tools (names, ``skip``/empty to disable; defaults: ddg + jina_ai):
      AGENT_WORKSPACE_SETUP_SEARCH     e.g. ``tavily`` (key read from its env var).
      AGENT_WORKSPACE_SETUP_FETCH      e.g. ``jina_ai``.

    Execution (automation-first defaults: the agent is meant to act):
      AGENT_WORKSPACE_SETUP_SANDBOX    ``local`` (default) or ``container``.
      AGENT_WORKSPACE_SETUP_BASH       ``1`` (default) or ``0``.
      AGENT_WORKSPACE_SETUP_WRITE_TOOLS ``1`` (default) or ``0``.

    IM channels (default: none):
      AGENT_WORKSPACE_SETUP_CHANNELS   Comma-separated names, e.g. ``telegram,slack``.

    Reconfigure guard:
      AGENT_WORKSPACE_SETUP_RECONFIGURE ``1`` to overwrite an existing config.yaml.

Resolution reuses the interactive step result dataclasses so the writer phase
stays identical between modes.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

from wizard.providers import (
    LLM_PROVIDERS,
    SEARCH_PROVIDERS,
    WEB_FETCH_PROVIDERS,
    LLMProvider,
    with_thinking_support,
)
from wizard.steps.channels import (
    CHANNEL_CONNECTION_OPTIONS,
    ChannelConnectionsStepResult,
)
from wizard.steps.execution import (
    CONTAINER_SANDBOX,
    LOCAL_SANDBOX,
    ExecutionStepResult,
)
from wizard.steps.llm import LLMStepResult
from wizard.steps.search import SearchStepResult


class SetupError(RuntimeError):
    """Raised when the environment does not describe a valid setup."""


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _find_llm_provider(name: str) -> LLMProvider:
    wanted = name.strip().lower()
    for provider in LLM_PROVIDERS:
        if provider.name == wanted or provider.display_name.strip().lower() == wanted:
            return provider
    known = ", ".join(p.name for p in LLM_PROVIDERS)
    raise SetupError(f"Unknown provider '{name}'. Known providers: {known}")


def _ollama_reachable(
    host: str = "127.0.0.1", port: int = 11434, timeout: float = 2.0
) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_llm(env: Mapping[str, str]) -> LLMStepResult:
    requested = (env.get("AGENT_WORKSPACE_SETUP_PROVIDER") or "").strip()
    provider: LLMProvider | None = None
    if requested:
        provider = _find_llm_provider(requested)
    elif _flag(env, "AGENT_WORKSPACE_SETUP_OLLAMA", False):
        provider = _find_llm_provider("ollama_qwen")
    else:
        # Auto-detect: first provider whose key is already exported.
        for candidate in LLM_PROVIDERS:
            if candidate.env_var and (env.get(candidate.env_var) or "").strip():
                provider = candidate
                break
        if provider is None:
            if _ollama_reachable():
                provider = _find_llm_provider("ollama_qwen")
            else:
                raise SetupError(
                    "No LLM provider configured. Set AGENT_WORKSPACE_SETUP_PROVIDER "
                    "(e.g. openai, deepseek, ollama_qwen) or export the provider's "
                    "API-key env var (e.g. OPENAI_API_KEY), or start a local "
                    "Ollama daemon."
                )

    model_name = (
        env.get("AGENT_WORKSPACE_SETUP_MODEL") or ""
    ).strip() or provider.default_model
    if model_name not in provider.models:
        # Generic gateways accept arbitrary model ids via their model prompt.
        if provider.model_prompt or provider.name in {"openrouter", "vllm"}:
            pass
        else:
            raise SetupError(
                f"Model '{model_name}' is not offered by provider '{provider.name}'. "
                f"Available: {', '.join(provider.models)}"
            )

    base_url: str | None = None
    if provider.name in {"openrouter", "vllm"}:
        base_url = provider.extra_config.get("base_url")
    override_base_url = (env.get("AGENT_WORKSPACE_SETUP_BASE_URL") or "").strip()
    if override_base_url:
        base_url = override_base_url
    elif provider.base_url_prompt:
        raise SetupError(
            f"Provider '{provider.name}' needs AGENT_WORKSPACE_SETUP_BASE_URL "
            f"({provider.base_url_prompt})."
        )

    if provider.ask_thinking_support:
        provider = with_thinking_support(
            provider, _flag(env, "AGENT_WORKSPACE_SETUP_THINKING", False)
        )

    api_key: str | None = None
    if provider.auth_hint:
        api_key = None
    else:
        api_key = (env.get("AGENT_WORKSPACE_SETUP_API_KEY") or "").strip() or None
        if api_key is None and provider.env_var:
            api_key = (env.get(provider.env_var) or "").strip() or None
        if not api_key:
            raise SetupError(
                f"Provider '{provider.name}' needs an API key: set "
                f"AGENT_WORKSPACE_SETUP_API_KEY or {provider.env_var}."
            )

    return LLMStepResult(
        provider=provider, model_name=model_name, api_key=api_key, base_url=base_url
    )


def _resolve_search(env: Mapping[str, str]) -> SearchStepResult:
    def pick(names: list, raw: str | None, default: str, kind: str):
        wanted = (raw or "").strip().lower() or default
        if wanted in {"skip", "none", "off"}:
            return None
        for item in names:
            if item.name == wanted:
                return item
        known = ", ".join(i.name for i in names)
        raise SetupError(f"Unknown {kind} provider '{wanted}'. Known: {known}, skip")

    search_provider = pick(
        SEARCH_PROVIDERS, env.get("AGENT_WORKSPACE_SETUP_SEARCH"), "ddg", "search"
    )
    fetch_provider = pick(
        WEB_FETCH_PROVIDERS, env.get("AGENT_WORKSPACE_SETUP_FETCH"), "jina_ai", "fetch"
    )

    search_api_key: str | None = None
    if search_provider is not None and search_provider.env_var:
        search_api_key = (env.get(search_provider.env_var) or "").strip() or None
        if not search_api_key:
            raise SetupError(
                f"Search provider '{search_provider.name}' needs {search_provider.env_var} set."
            )
    fetch_api_key: str | None = None
    if fetch_provider is not None and fetch_provider.env_var:
        if (
            search_provider is not None
            and fetch_provider.env_var == search_provider.env_var
        ):
            fetch_api_key = search_api_key
        else:
            fetch_api_key = (env.get(fetch_provider.env_var) or "").strip() or None
            if not fetch_api_key:
                raise SetupError(
                    f"Fetch provider '{fetch_provider.name}' needs {fetch_provider.env_var} set."
                )

    return SearchStepResult(
        search_provider=search_provider,
        search_api_key=search_api_key,
        fetch_provider=fetch_provider,
        fetch_api_key=fetch_api_key,
    )


def _resolve_execution(env: Mapping[str, str]) -> ExecutionStepResult:
    sandbox_raw = (env.get("AGENT_WORKSPACE_SETUP_SANDBOX") or "local").strip().lower()
    if sandbox_raw in {"local"}:
        sandbox_use = LOCAL_SANDBOX
    elif sandbox_raw in {"container"}:
        sandbox_use = CONTAINER_SANDBOX
    else:
        raise SetupError("AGENT_WORKSPACE_SETUP_SANDBOX must be 'local' or 'container'.")
    include_bash_tool = _flag(env, "AGENT_WORKSPACE_SETUP_BASH", True)
    include_write_tools = _flag(env, "AGENT_WORKSPACE_SETUP_WRITE_TOOLS", True)
    return ExecutionStepResult(
        sandbox_use=sandbox_use,
        allow_host_bash=sandbox_use == LOCAL_SANDBOX and include_bash_tool,
        include_bash_tool=include_bash_tool,
        include_write_tools=include_write_tools,
    )


def _resolve_channels(env: Mapping[str, str]) -> ChannelConnectionsStepResult:
    raw = (env.get("AGENT_WORKSPACE_SETUP_CHANNELS") or "").strip()
    if not raw:
        return ChannelConnectionsStepResult(enabled_providers=[])
    known = {key for key, _, _ in CHANNEL_CONNECTION_OPTIONS}
    enabled: list[str] = []
    for part in raw.split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key not in known:
            raise SetupError(
                f"Unknown channel '{key}'. Known: {', '.join(sorted(known))}"
            )
        if key not in enabled:
            enabled.append(key)
    return ChannelConnectionsStepResult(enabled_providers=enabled)


def resolve_noninteractive_setup(
    env: Mapping[str, str] | None = None,
) -> tuple[
    LLMStepResult, SearchStepResult, ExecutionStepResult, ChannelConnectionsStepResult
]:
    """Resolve all wizard steps from the environment (no prompting)."""
    source = env if env is not None else os.environ
    llm = _resolve_llm(source)
    search = _resolve_search(source)
    execution = _resolve_execution(source)
    channels = _resolve_channels(source)
    return llm, search, execution, channels
