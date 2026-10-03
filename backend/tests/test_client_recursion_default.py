"""The embedded client's default ``recursion_limit`` must match the Gateway's
interactive budget (1000), not the historical 100.

Why the old default was wrong
-----------------------------
``AlphaClient`` defaulted ``recursion_limit=100`` while the Gateway (Web UI),
IM channels, and the scheduler all run at 1000. At ~12.5 LangGraph super-steps
per model turn, 100 bought roughly 8 tool cycles: the Gateway comment at
``app/gateway/services.py`` records a plain tool task dying
``GRAPH_RECURSION_LIMIT`` at step=101 in the embedded path while the Gateway
finished the same task in the browser. The user-facing symptom was long
CLI/headless runs failing mid-task with a recursion error.

The explicit per-run override stays authoritative: ``max_recursion_limit`` is
only the server-side clamp for *client-supplied* values and is deliberately not
the default for trusted embedded runs (see ``backend/docs/TUI.md``).
"""

from __future__ import annotations

from alpha.client import AlphaClient


def test_default_recursion_limit_matches_gateway_budget():
    client = AlphaClient()
    config = client._get_runnable_config("t")
    assert config["recursion_limit"] == 1000


def test_explicit_recursion_limit_override_wins():
    client = AlphaClient()
    config = client._get_runnable_config("t", recursion_limit=250)
    assert config["recursion_limit"] == 250
