"""Probe: can a real executor be built for a lifecycle / deep contract?

Answers three questions the 3.T1 + 3.T2 design depends on, so neither is written
against an assumption:

1. Does `get_subagent_config` resolve a DEEP agent, or only general-purpose/bash?
2. What does `SubagentExecutor.__init__` actually require (which kwargs have no
   default that would fail)?
3. Does `execute_async` return an id that `get_background_task_result` accepts?

Read-only. Constructs nothing that spends tokens or reaches the network.
"""

from __future__ import annotations

import inspect

# Import order matters and is not cosmetic. `alpha.subagents.executor` reaches
# `alpha.authz.principal`, whose package `__init__` loads `alpha.guardrails`,
# which loads `alpha.tools.governance`, whose package `__init__` loads
# `alpha.tools.builtins`, and `self_improvement_tool` imports `alpha.subagents`
# again - which is still initialising. Loading `alpha.tools` FIRST is what
# breaks the cycle, and it is the order the Gateway uses at startup. Importing
# the executor first raises:
#   ImportError: cannot import name 'SubagentExecutor' from partially
#   initialized module 'alpha.subagents.executor'
import alpha.tools  # noqa: F401  (imported for its side effect: ordering)
from alpha.subagents import SubagentExecutor, get_subagent_config
from alpha.subagents.builtins import BUILTIN_SUBAGENTS

# Safe now that `alpha.tools` is loaded: the concrete module can be imported.
from alpha.subagents.executor import get_background_task_result  # noqa: E402

print("=== 1. deep agents resolvable through get_subagent_config? ===")
for name in ("general-purpose", "bash", "deep-architect", "deep-security", "nope-does-not-exist"):
    cfg = get_subagent_config(name)
    if cfg is None:
        print(f"  {name:22} -> None")
    else:
        print(f"  {name:22} -> tools={len(cfg.tools or [])} disallowed={len(cfg.disallowed_tools or [])} turns={cfg.max_turns} timeout={cfg.timeout_seconds} model={cfg.model}")

print()
print("=== 2. SubagentExecutor required (defaultless) kwargs ===")
sig = inspect.signature(SubagentExecutor.__init__)
required = [n for n, p in sig.parameters.items() if n != "self" and p.default is inspect.Parameter.empty]
optional = [n for n, p in sig.parameters.items() if n != "self" and p.default is not inspect.Parameter.empty]
print(f"  required: {required}")
print(f"  optional: {len(optional)} -> {optional}")

print()
print("=== 3. registry round-trip signature ===")
print(f"  execute_async{inspect.signature(SubagentExecutor.execute_async)}")
print(f"  get_background_task_result{inspect.signature(get_background_task_result)}")

print()
print("=== 4. deep agents present in BUILTIN_SUBAGENTS ===")
deep = sorted(n for n in BUILTIN_SUBAGENTS if n.startswith("deep"))
print(f"  {deep}")
