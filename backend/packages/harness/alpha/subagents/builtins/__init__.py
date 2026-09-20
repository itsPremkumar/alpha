"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .deep_architect_agent import DEEP_ARCHITECT_AGENT_CONFIG
from .deep_code_reviewer_agent import DEEP_CODE_REVIEWER_AGENT_CONFIG
from .deep_debugger_agent import DEEP_DEBUGGER_AGENT_CONFIG
from .deep_performance_agent import DEEP_PERFORMANCE_AGENT_CONFIG
from .deep_security_agent import DEEP_SECURITY_AGENT_CONFIG
from .deep_test_synthesizer_agent import DEEP_TEST_SYNTHESIZER_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
    "DEEP_ARCHITECT_AGENT_CONFIG",
    "DEEP_DEBUGGER_AGENT_CONFIG",
    "DEEP_SECURITY_AGENT_CONFIG",
    "DEEP_TEST_SYNTHESIZER_AGENT_CONFIG",
    "DEEP_PERFORMANCE_AGENT_CONFIG",
    "DEEP_CODE_REVIEWER_AGENT_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "deep-architect": DEEP_ARCHITECT_AGENT_CONFIG,
    "deep-debugger": DEEP_DEBUGGER_AGENT_CONFIG,
    "deep-security": DEEP_SECURITY_AGENT_CONFIG,
    "deep-test-synthesizer": DEEP_TEST_SYNTHESIZER_AGENT_CONFIG,
    "deep-performance": DEEP_PERFORMANCE_AGENT_CONFIG,
    "deep-code-reviewer": DEEP_CODE_REVIEWER_AGENT_CONFIG,
}
