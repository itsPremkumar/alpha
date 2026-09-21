"""Indexed action space for browser automation.

Ported from the design in `browser-use/jev-ultrafast`. The key ideas:

1. **Every observation produces an indexed element table.** The model never
   invents selectors or coordinates — it names an index, and the executor
   resolves that index back to an observed node.

2. **Per-operation target heads.** Elements are partitioned by which operation
   they support, so `click_target` only offers clickable elements and
   `type_text_target` only offers editable ones. This is also what keeps each
   choice under the model's 255-option limit: partitioning is the fix, not
   truncation.

3. **Controls are separate from targets.** `SCROLL_UP`, `WAIT`, `DONE` and
   `BLOCKED` are operations with no element.

This module is pure — no IO, no model calls — so it is fully unit-testable and
works whether the element list came from Playwright, a CDP snapshot, or the
built-in DOM summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: System One supports at most 255 options per choice question.
MAX_CHOICE_OPTIONS = 255

CLICK = "CLICK"
TYPE_TEXT = "TYPE_TEXT"
SELECT = "SELECT"
SCROLL_UP = "SCROLL_UP"
SCROLL_DOWN = "SCROLL_DOWN"
WAIT = "WAIT"
DONE = "DONE"
BLOCKED = "BLOCKED"

#: Operations that act on an observed element.
TARGETED_OPERATIONS = (CLICK, TYPE_TEXT, SELECT)

#: Operations with no element target.
CONTROL_OPERATIONS = (SCROLL_UP, SCROLL_DOWN, WAIT, DONE, BLOCKED)

OPERATION_LABELS = {
    CLICK: "Click an element, button, link, menu option, autocomplete suggestion, or calendar day.",
    TYPE_TEXT: "Enter or replace text in an editable field. A text model will supply the value from the goal.",
    SELECT: "Select an observed dropdown value.",
    SCROLL_UP: "Scroll up to reveal more of the page.",
    SCROLL_DOWN: "Scroll down to reveal more of the page.",
    WAIT: "Wait briefly for the page to load or update.",
    DONE: "Every requirement is visibly satisfied.",
    BLOCKED: "No supported operation can make progress.",
}

#: Which operations each observed role supports.
_ROLE_OPERATIONS: dict[str, tuple[str, ...]] = {
    "button": (CLICK,),
    "link": (CLICK,),
    "a": (CLICK,),
    "checkbox": (CLICK,),
    "radio": (CLICK,),
    "tab": (CLICK,),
    "menuitem": (CLICK,),
    "option": (CLICK,),
    "textarea": (TYPE_TEXT,),
    "select": (SELECT,),
    "combobox": (TYPE_TEXT, SELECT),
    "textbox": (TYPE_TEXT,),
    "input": (TYPE_TEXT,),
    "searchbox": (TYPE_TEXT,),
}


@dataclass
class Element:
    """One observed interactive control."""

    index: str
    role: str
    label: str
    value: str = ""
    operations: list[str] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    selector: str = ""
    coords: tuple[int, int] | None = None
    #: Destination of a link, when the source observed one. Carried through
    #: deliberately: it is the only thing that lets an executor follow a link
    #: without a live DOM to resolve the selector against, and it is part of a
    #: link's identity — the same label pointing somewhere else is a different
    #: target.
    href: str = ""
    #: Form field name, and which form it belongs to. Both are needed to submit
    #: anything: `name` is the key in the request body, and the form carries the
    #: action, the method, and the hidden inputs.
    name: str = ""
    form: int | None = None
    #: Tri-state, like ``changed``: ``None`` means the source did not report it,
    #: which is deliberately distinct from ``False``. A checkbox that is off and a
    #: checkbox nobody described must not look the same — the rubric tells the
    #: model not to toggle a box that is already in the requested state, and it
    #: cannot obey that unless it can see the state.
    checked: bool | None = None
    disabled: bool | None = None
    expanded: bool | None = None
    #: The live DOM node this element is, as an integer id the *page* issued.
    #:
    #: Only a CDP-backed observation can supply this, and it is the difference
    #: between executing by identity and executing by description: the executor
    #: resolves this id back to the real element, so the model's chosen index
    #: never becomes a selector, a coordinate, or JavaScript. ``selector`` is the
    #: fallback for backends that have no node registry.
    node: int | None = None
    #: A password field. Its value is deliberately never observed, so the blank
    #: value is not evidence the field is empty — it is evidence we refused to
    #: look. Kept as a target so a login can still be filled.
    secret: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "role": self.role,
            "label": self.label,
            "value": self.value,
            "operations": list(self.operations),
            "options": list(self.options),
            "selector": self.selector,
            "coords": list(self.coords) if self.coords else None,
            "href": self.href,
            "name": self.name,
            "form": self.form,
            "checked": self.checked,
            "disabled": self.disabled,
            "expanded": self.expanded,
            "node": self.node,
            "secret": self.secret,
        }


@dataclass
class ActionSpace:
    """The indexed table plus per-operation target maps."""

    elements: list[Element]
    targets: dict[str, dict[str, Element]]
    controls: dict[str, str]
    truncated: bool = False

    def operations(self) -> dict[str, str]:
        """Every operation offered this step, mapped to its description."""
        offered = {op: OPERATION_LABELS[op] for op in self.targets}
        offered.update(self.controls)
        offered[DONE] = OPERATION_LABELS[DONE]
        offered[BLOCKED] = OPERATION_LABELS[BLOCKED]
        return offered

    def resolve(self, operation: str, target: str | None) -> Element | None:
        """Resolve an (operation, target) pair back to an observed element.

        Returns None for control operations and for unknown indices — callers
        must treat None as "do nothing", never as "guess".
        """
        if operation not in self.targets or target is None:
            return None
        return self.targets[operation].get(str(target))

    def is_empty(self) -> bool:
        return not self.elements


def _first(raw: dict[str, Any], keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        value = raw.get(key)
        if value:
            return str(value)
    return default


def _optional_bool(value: Any) -> bool | None:
    """Tri-state read of a flag: absent is not the same as false.

    An unchecked box and a box whose state nobody reported must not look alike —
    the freshness signature treats them differently, and so should the model.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "checked", "on", "selected"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_role(raw: dict[str, Any]) -> str:
    role = _first(raw, ("role", "tag"), "generic").lower().strip()
    return role or "generic"


def _operations_for(role: str, raw: dict[str, Any]) -> tuple[str, ...]:
    if role in _ROLE_OPERATIONS:
        return _ROLE_OPERATIONS[role]
    # Unknown roles stay clickable rather than becoming unreachable.
    if raw.get("options"):
        return (CLICK, SELECT)
    return (CLICK,)


def build_action_space(
    raw_elements: list[dict[str, Any]],
    *,
    max_options: int = MAX_CHOICE_OPTIONS,
) -> ActionSpace:
    """Build the indexed table from a list of observed elements.

    Args:
        raw_elements: Dicts describing observed controls. Recognised keys are
            ``role``/``tag``, ``label``/``text``/``placeholder``/``name``,
            ``value``/``current_value``, ``options``, ``selector``, ``coords``,
            ``href``/``url``, ``checked``/``disabled``/``expanded``, and
            ``form``. Unknown shapes degrade to a clickable element rather than
            being dropped.
        max_options: Per-head cap. Heads are truncated only when a *single*
            operation genuinely has more candidates than the model allows;
            ``truncated`` is set so the caller can scroll instead of silently
            losing options.

    Returns:
        An ActionSpace. Elements are indexed from "1" in document order.
    """
    elements: list[Element] = []
    targets: dict[str, dict[str, Element]] = {}
    truncated = False

    for raw in raw_elements or []:
        if not isinstance(raw, dict):
            continue
        role = _normalize_role(raw)
        label = _first(raw, ("label", "text", "placeholder", "name", "aria_label"), role)
        value = _first(raw, ("current_value", "value"))
        coords_raw = raw.get("coords")
        coords = tuple(coords_raw) if isinstance(coords_raw, (list, tuple)) and len(coords_raw) == 2 else None

        element = Element(
            index=str(len(elements) + 1),
            role=role,
            label=label,
            value=value,
            selector=_first(raw, ("selector",)),
            coords=coords,
            # `href` is emitted by every adapter in dom_snapshot; without this it
            # was dropped here, so a link's destination never reached the
            # executor and no executor could follow one.
            href=_first(raw, ("href", "url")),
            name=_first(raw, ("name",)),
            form=_optional_int(raw.get("form")),
            checked=_optional_bool(raw.get("checked")),
            disabled=_optional_bool(raw.get("disabled")),
            expanded=_optional_bool(raw.get("expanded")),
            node=_optional_int(raw.get("node")),
            secret=bool(raw.get("secret")),
        )

        for operation in _operations_for(role, raw):
            if operation not in element.operations:
                element.operations.append(operation)
            head = targets.setdefault(operation, {})
            if operation == SELECT and raw.get("options"):
                # Native dropdowns: offer each observed option as "element:option".
                for offset, option in enumerate(raw["options"], start=1):
                    option_label = option.get("label") if isinstance(option, dict) else str(option)
                    option_value = option.get("value") if isinstance(option, dict) else str(option)
                    element.options.append(
                        {
                            "index": f"{element.index}:{offset}",
                            "label": option_label,
                            "value": option_value,
                            # Which option is currently chosen. Not part of the
                            # option's *identity* — choosing a different option
                            # does not make this a different option — so it stays
                            # out of the signature and is carried as state.
                            "selected": bool(option.get("selected")) if isinstance(option, dict) else False,
                        }
                    )
                    head[f"{element.index}:{offset}"] = element
            else:
                head[element.index] = element

        elements.append(element)

    for operation, head in list(targets.items()):
        if len(head) > max_options:
            # Keep document order; flag it so the caller can scroll for more.
            kept = dict(list(head.items())[:max_options])
            dropped = set(head) - set(kept)
            for index in dropped:
                element = elements[int(str(index).split(":")[0]) - 1]
                if operation in element.operations:
                    element.operations.remove(operation)
            targets[operation] = kept
            truncated = True

    # Drop elements that lost every operation so the table stays honest.
    elements = [e for e in elements if e.operations]

    controls = {op: OPERATION_LABELS[op] for op in (SCROLL_UP, SCROLL_DOWN, WAIT)}
    return ActionSpace(elements=elements, targets=targets, controls=controls, truncated=truncated)
