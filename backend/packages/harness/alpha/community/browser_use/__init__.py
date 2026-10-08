"""Optional `browser-use`_ integration: install the latest version, drive it on demand.

Exposed as two opt-in tools wired through ``config.yaml`` (see the commented
``browser_use_*`` entries in ``config.example.yaml``), in the same style as
:mod:`alpha.community.browser_automation`.

Three parts:

* :mod:`~alpha.community.browser_use.manager` — owns the managed virtualenv,
  the on-demand install, and every subprocess against it.
* :mod:`~alpha.community.browser_use.runner` — the child-side driver, executed
  by that virtualenv's interpreter and never imported by the Gateway.
* :mod:`~alpha.community.browser_use.tools` — the agent-facing surface.

.. _browser-use: https://github.com/browser-use/browser-use
"""

from .manager import (
    DEFAULT_INSTALL_TIMEOUT_SECONDS,
    DEFAULT_MAX_STEPS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    MAX_MAX_STEPS,
    MAX_RESULT_CHARS,
    MAX_RUN_TIMEOUT_SECONDS,
    MAX_TASK_CHARS,
    BrowserUseError,
    BrowserUseManager,
    BrowserUseStatus,
    get_browser_use_manager,
    redact_secrets,
    reset_browser_use_manager,
    resolve_llm_spec,
)
from .tools import (
    browser_use_run_tool,
    browser_use_setup_tool,
)

__all__ = [
    "DEFAULT_INSTALL_TIMEOUT_SECONDS",
    "DEFAULT_MAX_STEPS",
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "MAX_MAX_STEPS",
    "MAX_RESULT_CHARS",
    "MAX_RUN_TIMEOUT_SECONDS",
    "MAX_TASK_CHARS",
    "BrowserUseError",
    "BrowserUseManager",
    "BrowserUseStatus",
    "browser_use_run_tool",
    "browser_use_setup_tool",
    "get_browser_use_manager",
    "redact_secrets",
    "reset_browser_use_manager",
    "resolve_llm_spec",
]
