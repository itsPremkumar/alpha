"""Unified agent workspace facade over Alpha's capability modules.

- :class:`~alpha.workspace.workspace.AgentWorkspace` — one object composing
  modes, experts, automations, connectors, egress, routines, skills market and
  the capability scorecard.
"""

from __future__ import annotations

from alpha.workspace.workspace import AgentWorkspace

__all__ = ["AgentWorkspace"]
