"""Keep documented middleware examples aligned with the locked LangChain API."""

import inspect
import re
from pathlib import Path

import pytest
from langchain.agents.middleware import AgentMiddleware

from alpha.agents import create_alpha_agent
from alpha.client import AlphaClient
from alpha.config.extensions_config import ExtensionsConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
MIDDLEWARE_GUIDES = (
    Path("backend/CONTRIBUTING.md"),
    Path("frontend/src/content/en/harness/customization.mdx"),
    Path("frontend/src/content/en/harness/middlewares.mdx"),
)
MIDDLEWARE_GUIDE_DOCS = (Path("frontend/src/content/en/harness/middlewares.mdx"),)


def _middleware_examples(path: Path) -> list[str]:
    content = (REPO_ROOT / path).read_text(encoding="utf-8")
    examples = [block for block in re.findall(r"```python\n(.*?)\n```", content, flags=re.DOTALL) if "AgentMiddleware" in block and ("class MyMiddleware" in block or "class AuditMiddleware" in block)]
    assert examples, f"no custom middleware example in {path}"
    return examples


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDES, ids=str)
def test_custom_middleware_example_uses_current_lifecycle_hooks(path: Path) -> None:
    for example in _middleware_examples(path):
        namespace: dict[str, object] = {}
        exec(compile(example, str(path), "exec"), namespace)  # noqa: S102 - executes a controlled in-repo documentation example

        middleware_types = [value for value in namespace.values() if isinstance(value, type) and value is not AgentMiddleware and issubclass(value, AgentMiddleware)]
        assert len(middleware_types) == 1

        middleware_type = middleware_types[0]
        assert middleware_type.before_model is not AgentMiddleware.before_model
        assert middleware_type.after_model is not AgentMiddleware.after_model

        middleware = middleware_type()
        assert middleware.before_model({"messages": []}, None) is None
        assert middleware.after_model({"messages": []}, None) is None


def test_documented_registration_apis_exist() -> None:
    ExtensionsConfig.model_validate({"middlewares": ["pkg.mod:MyMiddleware"]})
    assert "middlewares" in inspect.signature(AlphaClient.__init__).parameters
    assert "extra_middleware" in inspect.signature(create_alpha_agent).parameters


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDES, ids=str)
def test_embedded_middleware_scope_is_explicit(path: Path) -> None:
    content = (REPO_ROOT / path).read_text(encoding="utf-8")
    markers = (
        "AlphaClient(middlewares=[",
        "builds the full lead-agent chain",
        "create_alpha_agent(extra_middleware=[",
        "builds a smaller feature-based lead-agent chain",
        "Neither API forwards middleware to subagents.",
    )
    normalized = " ".join(content.split())
    positions = [normalized.index(marker) for marker in markers]
    assert positions == sorted(positions)


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDES, ids=str)
def test_lifecycle_return_contract_is_explicit(path: Path) -> None:
    content = (REPO_ROOT / path).read_text(encoding="utf-8")
    marker = "Lifecycle hooks can return a dictionary of state updates"
    assert marker in " ".join(content.split())


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDES, ids=str)
def test_middleware_placement_scope_is_explicit(path: Path) -> None:
    content = (REPO_ROOT / path).read_text(encoding="utf-8")
    marker = "On the lead-agent pipeline, it runs before the terminal-response, model-length, safety, and clarification tail; subagents have no terminal-response, model-length, or clarification stage"
    assert marker in " ".join(content.split())


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDE_DOCS, ids=str)
def test_middleware_order_includes_configured_extension_tail(path: Path) -> None:
    content = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    markers = (
        "`SkillToolPolicyMiddleware`",
        "Configured extension middlewares (if any)",
        "`TerminalResponseMiddleware`",
        "`ModelLengthFinishReasonMiddleware`",
    )
    positions = [content.index(marker) for marker in markers]
    assert positions == sorted(positions)


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDES, ids=str)
def test_subagent_summarization_optionality_is_explicit(path: Path) -> None:
    content = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    marker = "so configured middleware is followed by the optional safety guard, `DurableContextMiddleware`, optional `SummarizationMiddleware`, then `SubagentDateContextMiddleware` and `SystemMessageCoalescingMiddleware`."
    assert marker in content


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDE_DOCS, ids=str)
def test_runtime_middleware_summary_includes_current_guards(path: Path) -> None:
    content = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    marker = (
        "Runtime middlewares (`InputSanitizationMiddleware` for input sanitization → `ToolOutputBudgetMiddleware` "
        "for output-budget truncation → `ToolResultSanitizationMiddleware` for tool-result sanitization, then thread data, "
        "uploads, sandbox, dangling tool-call patching, and LLM error handling; tool receipts (if enabled), "
        "authorization/guardrail (if enabled), sandbox audit, read-before-write (if enabled), tool progress (if enabled), "
        "and tool error handling follow)"
    )
    assert marker in content


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDE_DOCS, ids=str)
def test_runtime_sanitization_and_budget_order_is_explicit(path: Path) -> None:
    content = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    markers = (
        "`InputSanitizationMiddleware`",
        "`ToolOutputBudgetMiddleware`",
        "`ToolResultSanitizationMiddleware`",
    )
    positions = [content.index(marker) for marker in markers]
    assert positions == sorted(positions)


@pytest.mark.parametrize("path", MIDDLEWARE_GUIDE_DOCS, ids=str)
def test_subagent_callout_does_not_overstate_lead_only_scope(path: Path) -> None:
    content = " ".join((REPO_ROOT / path).read_text(encoding="utf-8").split())
    marker = "other Lead-Agent-specific middlewares such as memory, title generation, and clarification do not run there."
    assert marker in content
