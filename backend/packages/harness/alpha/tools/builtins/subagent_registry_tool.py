"""Model-facing subagent registry: create and manage deployment subagents.

`bot_roster` can mint a **bot**; nothing could mint a **subagent**. This closes
that gap, and the gap was measured, not assumed: asked to create a subagent, a
real agent called `bot_roster`, reported success, and changed nothing -- because
`bot_roster` creates bots. Of 132 registered tools, none registered a subagent;
managed subagent creation was reachable only through the admin HTTP API.

Why this lives in the harness and not in the Gateway router
----------------------------------------------------------
The router owns the admin surface; this must not import ``app.*`` (enforced by
``tests/test_harness_boundary.py``). So it calls the same
``alpha.persistence.managed_subagents`` store the router calls, and -- the part
that matters -- it validates through the same
``ManagedSubagentDefinition`` model. Every guard therefore applies to a
model-initiated create exactly as it does to an operator's, instead of being
reimplemented (and quietly weakened) here:

* the ``^[A-Za-z0-9-]+$`` name pattern;
* ``extra="forbid"``, so a mistyped knob is refused rather than dropped with a
  success code (see ``docs/audits/SUBAGENT_CREATION.md``);
* ``min_length=1`` on ``description`` and ``system_prompt``;
* ``ge=1`` on ``max_turns`` / ``timeout_seconds``;
* ``REQUIRED_DISALLOWED_TOOLS`` -- ``task``, ``ralph_loop``,
  ``ask_clarification`` and ``present_files`` are force-disallowed, so a
  subagent cannot nest delegation regardless of what a create request asks for.

Scope, and what this deliberately does not do
---------------------------------------------
It is **absent from ``SUBAGENT_TOOLS``**, which is an explicit allowlist, so a
subagent cannot create a subagent. That is the same boundary that stops ``bot_roster``
from being self-widening: *a Bot may configure itself, but may not widen itself.*
A managed subagent also does not grant itself tools the caller lacks -- it
declares a tool list, and the executor's existing per-subagent tool resolution
still applies at dispatch time.

Creating a subagent is a durable write, so it is reported honestly: the reply
states what was written and where it is readable back from. A create is **not**
reported as "running": nothing here proves a subagent can execute, and in a
deployment with no execution backend the honest answer is that the definition
exists and has never been dispatched.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain.tools import tool
from pydantic import ValidationError

from alpha.persistence.managed_subagents import (
    ManagedSubagentDefinition,
    ManagedSubagentExistsError,
    get_managed_subagent_store,
)
from alpha.persistence.managed_subagents.base import normalize_managed_subagent_name
from alpha.subagents.builtins import BUILTIN_SUBAGENTS


def _existing(store: Any, key: str) -> Any | None:
    """Return a managed definition, or ``None`` when there is none.

    ``ManagedSubagentStore.get`` **raises** ``FileNotFoundError`` for an absent
    name; it does not return ``None``. Assuming a ``None`` contract here made
    every ``inspect``/``update``/``delete`` of a missing subagent fall through to
    the broad handler and return "subagent registry operation failed: ...", which
    is both a worse message and not JSON -- so a caller parsing the reply got a
    decode error instead of an answer. An absent record is ordinary, not a fault.
    """
    try:
        return store.get(key)
    except FileNotFoundError:
        return None


def _as_dict(defn: Any) -> dict[str, Any]:
    """Coerce a definition to a plain dict.

    A *managed* definition is a ``ManagedSubagentDefinition`` with ``to_dict()``;
    a *builtin* is a ``SubagentConfig``, whose dump is a mapping on
    ``model_fields`` rather than something ``dict()`` accepts. Assuming one shape
    made ``inspect`` on a builtin raise ``TypeError: 'SubagentConfig' object is
    not iterable``.
    """
    to_dict = getattr(defn, "to_dict", None)
    if callable(to_dict):
        out = to_dict()
        if isinstance(out, dict):
            return dict(out)
    fields = getattr(defn, "model_fields", None)
    if isinstance(fields, dict):
        return {k: getattr(defn, k, None) for k in fields}
    dump = getattr(defn, "model_dump", None)
    if callable(dump):
        out = dump()
        if isinstance(out, dict):
            return dict(out)
    return {"repr": repr(defn)}


def _definition_payload(defn: Any) -> dict[str, Any]:
    """Render one definition without its full system prompt.

    The prompt is elided by default because a registry listing is a navigation
    surface, and inlining eight multi-paragraph prompts is how a list view turns
    into an unreadable wall. `inspect` reports its length instead.
    """
    d = _as_dict(defn)
    prompt = d.get("system_prompt") or ""
    if len(prompt) > 160:
        d["system_prompt"] = prompt[:160] + f"... [{len(prompt)} chars total]"
    d["system_prompt_chars"] = len(prompt)
    return d


def _managed_map(store: Any) -> dict[str, Any]:
    """Managed definitions keyed by name.

    ``FileManagedSubagentStore.list()`` returns a **list** of definitions, not a
    mapping. Assuming ``.items()`` raised ``AttributeError: 'list' object has no
    attribute 'items'``, so the single most useful action -- "what already
    exists?" -- was the one action that always failed.
    """
    rows = store.list()
    if isinstance(rows, dict):
        return dict(rows)
    out: dict[str, Any] = {}
    for row in rows or []:
        name = row.get("name") if isinstance(row, dict) else getattr(row, "name", None)
        if name:
            out[str(name).lower()] = row.get("definition", row) if isinstance(row, dict) else row
    return out


@tool("subagent_registry", parse_docstring=True)
def subagent_registry_tool(
    action: Literal["list", "inspect", "create", "update", "delete"],
    name: str = "",
    description: str = "",
    system_prompt: str = "",
    tools: str = "",
    disallowed_tools: str = "",
    skills: str = "",
    model: str = "",
    display_name: str = "",
    max_turns: int | None = None,
    timeout_seconds: int | None = None,
    enabled: bool | None = None,
) -> str:
    """List, inspect, create, update and delete deployment subagent definitions.

    A subagent is a delegated worker with its own instructions and tool list. Use
    `create` when a kind of work recurs and would otherwise consume the main
    agent's context; use `list` to see what already exists before creating a
    near-duplicate.

    Args:
        action: One of list, inspect, create, update, delete.
        name: Subagent handle. Letters, digits and hyphens only; lowercased on
            write. Required for every action except `list`.
        description: One line stating WHEN this subagent should fire. Required
            for `create`.
        system_prompt: The subagent's own instructions. Required for `create`.
        tools: Comma-separated tool names the subagent may use. Omit to inherit.
        disallowed_tools: Comma-separated extras to deny beyond the ones always
            denied. `task`, `ralph_loop`, `ask_clarification` and `present_files`
            are always denied and cannot be re-enabled here.
        skills: Comma-separated skill names to load with the subagent.
        model: Model name, or `inherit` to follow the parent's model.
        display_name: Human-readable label.
        max_turns: Turn budget (>= 1).
        timeout_seconds: Wall-clock budget (>= 1).
        enabled: Set false to keep a definition without dispatching it.
    """

    def _csv(raw: str) -> list[str] | None:
        if raw == "":
            return None
        return [p.strip() for p in raw.split(",") if p.strip()]

    try:
        store = get_managed_subagent_store()

        if action == "list":
            managed = _managed_map(store)
            rows = [
                {
                    "name": n,
                    "source": "managed",
                    "editable": True,
                    "enabled": getattr(d, "enabled", None),
                    "model": getattr(d, "model", None),
                    "description": (getattr(d, "description", "") or "").split("\n")[0][:110],
                    "tools": getattr(d, "tools", None),
                }
                for n, d in sorted(managed.items())
            ]
            builtin_rows = [
                {
                    "name": n,
                    "source": "builtin",
                    "editable": False,
                    "enabled": bool(getattr(d, "enabled", True)),
                    "description": (getattr(d, "description", "") or "").split("\n")[0][:110],
                }
                for n, d in sorted(BUILTIN_SUBAGENTS.items())
            ]
            return json.dumps(
                {
                    "managed_count": len(rows),
                    "builtin_count": len(builtin_rows),
                    "note": ("Managed entries are editable and appear first. `task` and `ralph_loop` are force-disallowed on every managed subagent, so none of these can nest delegation."),
                    "managed": rows,
                    "builtin": builtin_rows,
                },
                indent=2,
            )

        if not name.strip():
            return f"Error: 'name' is required for action={action}."

        # A managed name may not shadow a builtin: two definitions answering to
        # one handle is the ambiguity the router refuses with a 422, and doing it
        # here would create the conflict the router then has to police.
        try:
            key = normalize_managed_subagent_name(name)
        except ValueError as exc:
            return f"Error: {exc}"

        if action == "inspect":
            existing = _existing(store, key)
            if existing is not None:
                return json.dumps(
                    {
                        "name": key,
                        "source": "managed",
                        "editable": True,
                        "definition": _definition_payload(existing),
                    },
                    indent=2,
                )
            if key in BUILTIN_SUBAGENTS:
                d = BUILTIN_SUBAGENTS[key]
                return json.dumps(
                    {
                        "name": key,
                        "source": "builtin",
                        "editable": False,
                        "note": "Builtin subagents cannot be edited; create a managed one with a different name.",
                        "definition": _definition_payload(d),
                    },
                    indent=2,
                )
            return f"Error: no subagent named '{key}' exists."

        if action == "create":
            if key in BUILTIN_SUBAGENTS:
                return f"Error: '{key}' is a builtin subagent and cannot be redefined. Choose another name."
            # Refuse an existing managed name BEFORE building anything. The store's
            # `create` **overwrote** the stored definition instead of raising, so a
            # second create with the same name silently replaced the first one's
            # description and system prompt. That is the worst shape this tool can
            # take: the caller is told a worker was created, and the worker that
            # exists is not the one they described.
            if _existing(store, key) is not None:
                return f"Error: a managed subagent named '{key}' already exists. Use action='update' to change it, or choose another name."
            fields: dict[str, Any] = {
                "name": key,
                "description": description,
                "system_prompt": system_prompt,
            }
            for fname, value in (
                ("display_name", display_name),
                ("model", model),
                ("tools", _csv(tools)),
                ("disallowed_tools", _csv(disallowed_tools)),
                ("skills", _csv(skills)),
            ):
                if value:
                    fields[fname] = value
            if max_turns is not None:
                fields["max_turns"] = max_turns
            if timeout_seconds is not None:
                fields["timeout_seconds"] = timeout_seconds
            if enabled is not None:
                fields["enabled"] = enabled
            # Validating through the persistence model is deliberate: it is where
            # the name pattern, extra="forbid", min_length and ge=1 live, so a
            # model-initiated create cannot get a weaker contract than an
            # operator's. A hand-rolled dict would drift from it silently.
            defn = ManagedSubagentDefinition(**fields)
            try:
                store.create(defn)
            except ManagedSubagentExistsError:
                return f"Error: a managed subagent named '{key}' already exists. Use action='update' to change it."
            except ValueError as exc:
                return f"Error: {exc}"
            except ValidationError as exc:
                # Named explicitly so the caller sees WHICH field was refused.
                # Falling through to the broad handler produced
                # "operation failed: ValidationError: 1 validation error for ...",
                # which names it too -- but buried in a wrapper that implies the
                # registry itself broke, rather than the request being invalid.
                bad = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
                return f"Error: invalid subagent definition -- {bad}"
            return (
                f"Created managed subagent '{key}'.\n"
                f"It is readable back from action='inspect' and action='list'.\n"
                f"Always denied: {', '.join(defn.disallowed_tools)}.\n"
                f"NOTE: this proves the DEFINITION is stored. It does not prove the subagent can "
                f"execute -- only a real delegation does that. If the `task` tool is unavailable to "
                f"you, this subagent will not be dispatchable until it is."
            )

        if action == "update":
            if _existing(store, key) is None:
                return f"Error: no managed subagent named '{key}' exists, so there is nothing to update."
            patch: dict[str, Any] = {}
            if description:
                patch["description"] = description
            if system_prompt:
                patch["system_prompt"] = system_prompt
            if display_name:
                patch["display_name"] = display_name
            if model:
                patch["model"] = model
            for fname, raw in (("tools", tools), ("disallowed_tools", disallowed_tools), ("skills", skills)):
                parsed = _csv(raw)
                if parsed is not None:
                    patch[fname] = parsed
            if max_turns is not None:
                patch["max_turns"] = max_turns
            if timeout_seconds is not None:
                patch["timeout_seconds"] = timeout_seconds
            if enabled is not None:
                patch["enabled"] = enabled
            if not patch:
                return "Error: nothing to update; supply at least one field."
            try:
                store.update(key, **patch)
            except ValueError as exc:
                return f"Error: {exc}"
            return f"Updated managed subagent '{key}': {', '.join(sorted(patch))}."

        if action == "delete":
            if _existing(store, key) is None:
                return f"Error: no managed subagent named '{key}' exists."
            try:
                store.delete(key)
            except (FileNotFoundError, ValueError) as exc:
                return f"Error: could not delete '{key}': {exc}"
            return f"Deleted managed subagent '{key}'."

        return f"Unknown action '{action}'."
    except Exception as exc:  # noqa: BLE001 - a tool must not kill the run
        return f"Error: subagent registry operation failed: {type(exc).__name__}: {exc}"
