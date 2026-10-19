"""Component helpers: id hygiene, bundle construction, and bundle diffing.

Three properties are load-bearing here:

* **A component id is a logical name, never a path.** ``skills/report`` is a
  name; ``../../etc/passwd``, ``C:/Windows``, ``~/.ssh`` and ``/etc`` are not,
  and :func:`validate_component_id` is the single place that decides. A
  candidate cannot "address" anything outside the harness surface because there
  is no filesystem in this vocabulary to address.
* **A bundle is built from content, not edited in place.** :func:`build_bundle`
  takes ``{component_id: content}`` and returns a new immutable manifest; the
  baseline is untouched by construction.
* **A diff is between manifests.** :func:`diff_bundles` compares two bundles by
  component id and content hash, so it cannot silently miss a rename or report
  a change the caller merely re-ordered.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from alpha.harness_improvement.models import (
    ComponentKind,
    ComponentRef,
    HarnessBundle,
    compute_bundle_id,
    content_hash,
)

#: Component ids are lowercase, slug-shaped, optionally namespaced with one
#: slash: ``skills/report``, ``tool_guidance/bash``, ``system_prompt/base``.
_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}(?:/[a-z0-9][a-z0-9_.-]{0,127})?$")


class ComponentIdError(ValueError):
    """Raised when a component id is not a safe logical name."""


def validate_component_id(component_id: str) -> str:
    """Return the id when valid, raise :class:`ComponentIdError` otherwise.

    Rejection reasons are specific (path separator beyond one namespace slash,
    traversal segment, drive letter, backslash, whitespace, unicode controls) so
    a caller can explain the refusal instead of re-deriving it.
    """
    if not component_id or component_id != component_id.strip():
        raise ComponentIdError(f"component id {component_id!r} must be non-empty and have no surrounding whitespace")
    if "\\" in component_id:
        raise ComponentIdError(f"component id {component_id!r} must not contain backslashes")
    if component_id.count("/") > 1:
        raise ComponentIdError(f"component id {component_id!r} may contain at most one namespace slash")
    for part in component_id.split("/"):
        if part in {".", ".."}:
            raise ComponentIdError(f"component id {component_id!r} must not contain traversal segments")
    if re.match(r"^[A-Za-z]:", component_id) or component_id.startswith("/") or component_id.startswith("~"):
        raise ComponentIdError(f"component id {component_id!r} must not look like an absolute path")
    if not _COMPONENT_ID.match(component_id):
        raise ComponentIdError(f"component id {component_id!r} is not a lowercase slug of the form 'namespace/name'")
    return component_id


def kind_for_component_id(component_id: str, *, default: ComponentKind | None = None) -> ComponentKind:
    """Infer the component kind from its namespace prefix.

    ``skills/...`` is a :class:`ComponentKind.SKILL`, ``tool_guidance/...`` a
    ``TOOL_GUIDANCE`` and so on. Unknown namespaces fall back to *default*; when
    no default is supplied the id itself must be declared with an explicit kind
    by its caller.
    """
    prefix = component_id.split("/", 1)[0]
    mapping = {
        "system_prompt": ComponentKind.SYSTEM_PROMPT,
        "guardrail_guidance": ComponentKind.GUARDRAIL_GUIDANCE,
        "memory": ComponentKind.MEMORY_REFERENCE,
        "tool_guidance": ComponentKind.TOOL_GUIDANCE,
        "skills": ComponentKind.SKILL,
        "subagents": ComponentKind.SUBAGENT,
        "scripts": ComponentKind.SCRIPT,
        "middleware": ComponentKind.MIDDLEWARE,
    }
    kind = mapping.get(prefix)
    if kind is not None:
        return kind
    if default is None:
        raise ComponentIdError(f"component id {component_id!r} has an unknown namespace; declare its kind explicitly")
    return default


class UnknownComponentsError(ValueError):
    """Raised when a diff references a component the base bundle does not have."""


def build_component_ref(kind: ComponentKind, component_id: str, content: str) -> ComponentRef:
    """Create the immutable ref for one piece of content."""
    validate_component_id(component_id)
    return ComponentRef(
        kind=kind,
        component_id=component_id,
        sha256=content_hash(content),
        size_bytes=len(content.encode("utf-8")),
    )


def build_bundle(
    *,
    components: Mapping[str, tuple[ComponentKind, str]],
    parent_bundle_id: str | None,
    created_by: str,
    agent_profile: str = "general",
    alpha_runtime_version: str = "unknown",
    tool_schema_version: str = "unknown",
    policy_version: str = "unknown",
    source_run_id: str | None = None,
    source_task_type: str | None = None,
    promotion_state: Any = None,
    created_at: str | None = None,
) -> tuple[HarnessBundle, dict[str, str]]:
    """Build a bundle manifest plus its ``{component_id: content}`` payload.

    The payload is returned separately because the store needs the raw content
    to write the content-addressed blobs; the manifest only carries hashes.
    """
    refs: list[ComponentRef] = []
    contents: dict[str, str] = {}
    for component_id, (kind, content) in sorted(components.items()):
        ref = build_component_ref(kind, component_id, content)
        refs.append(ref)
        contents[component_id] = content
    component_tuple = tuple(refs)
    bundle_id = compute_bundle_id(
        parent_bundle_id=parent_bundle_id,
        components=component_tuple,
        alpha_runtime_version=alpha_runtime_version,
        tool_schema_version=tool_schema_version,
        policy_version=policy_version,
    )
    from alpha.harness_improvement.models import PromotionState

    bundle = HarnessBundle(
        bundle_id=bundle_id,
        parent_bundle_id=parent_bundle_id,
        created_at=created_at or _utc_now(),
        created_by=created_by,
        agent_profile=agent_profile,
        components=component_tuple,
        alpha_runtime_version=alpha_runtime_version,
        tool_schema_version=tool_schema_version,
        policy_version=policy_version,
        source_run_id=source_run_id,
        source_task_type=source_task_type,
        promotion_state=promotion_state or PromotionState.DRAFT,
    )
    return bundle, contents


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def diff_bundles(base: HarnessBundle, candidate: HarnessBundle) -> dict[str, Any]:
    """Component-level diff between two manifests.

    Returns ``{added, removed, modified, unchanged}`` lists of component ids
    with both hashes where applicable. It compares *content hashes*, so a
    modified-then-reverted component reads as unchanged rather than as two
    edits.
    """
    base_map = {ref.component_id: ref for ref in base.components}
    cand_map = {ref.component_id: ref for ref in candidate.components}
    added = sorted(set(cand_map) - set(base_map))
    removed = sorted(set(base_map) - set(cand_map))
    modified: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for component_id in sorted(set(base_map) & set(cand_map)):
        before, after = base_map[component_id], cand_map[component_id]
        if before.sha256 == after.sha256 and before.kind == after.kind:
            unchanged.append(component_id)
        else:
            modified.append(
                {
                    "component_id": component_id,
                    "kind": str(after.kind),
                    "before_sha256": before.sha256,
                    "after_sha256": after.sha256,
                    "size_delta": after.size_bytes - before.size_bytes,
                }
            )
    return {
        "base_bundle_id": base.bundle_id,
        "candidate_bundle_id": candidate.bundle_id,
        "added": added,
        "removed": removed,
        "modified": modified,
        "unchanged": unchanged,
    }


def resolve_components(base: HarnessBundle, diffs: Any) -> dict[str, tuple[ComponentKind, str | None]]:
    """Apply *diffs* to a base bundle's component map, without content.

    Returns ``{component_id: (kind, content_or_None)}`` where ``None`` means the
    component is removed. Raises :class:`UnknownComponentsError` for a MODIFY or
    REMOVE that names a component the base does not carry — an editor that
    "edits" a non-existent component is confused, and silently creating it would
    make the confusion a feature.
    """
    resolution: dict[str, tuple[ComponentKind, str | None]] = {ref.component_id: (ref.kind, None) for ref in base.components}
    existing = {ref.component_id for ref in base.components}
    for diff in diffs:
        if diff.op in {"modify", "remove"} and diff.component_id not in existing:
            raise UnknownComponentsError(f"component {diff.component_id!r} does not exist in bundle {base.bundle_id}; op={diff.op}")
        if diff.op == "remove":
            resolution[diff.component_id] = (diff.kind, None)
        else:
            resolution[diff.component_id] = (diff.kind, diff.content or "")
    return resolution
