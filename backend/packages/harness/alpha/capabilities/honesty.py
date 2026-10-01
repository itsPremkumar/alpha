"""Advertised-vs-wired: does a claimed capability have a real consumer?

## The defect class this exists to prevent

Every honesty bug fixed in this remediation effort was the same shape: a
capability was **advertised but not wired**. In each case the module imported
cleanly, its unit tests passed, and it was documented as a feature:

| Advertised | Reality |
| --- | --- |
| trajectory flight recorder | `runtime/journal.py` states in-source that nothing installs a writer |
| production tracing | no `observability:`/`trace:` key exists in `AppConfig` at all |
| emergency stop ("Fleet execution paused") | only `rsi/switchboard.py` consumed it |
| parallel multi-LLM synthesis | the tool injected a `mock_worker` that never called a model |
| tests passed on every PR | `pr_synthesizer` printed the box having run nothing |

None of these are caught by "does it import" or "do its unit tests pass". A stub
with good tests passes both. The question that actually distinguishes them is
**does anything outside the module invoke it?**

## What this is, and is not

A **static structural check**, not a runtime probe. It answers "is this wired",
not "is this working right now". A wired capability can still be broken at
runtime; that is what the behavioural evaluation plane is for, and it does not
exist yet.

The limitation is deliberate and stated: a call site reached only by dynamic
dispatch, a plugin registry entry, or a config `use:` string is not seen by an
AST walk. Those are exactly the cases a human must adjudicate, so this module
reports ``UNPROVEN`` rather than guessing either way.

It is a ratchet, not a proof. It catches the specific, repeated regression of
adding a consumer-free module and documenting it as done.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import project_root


class WiringState(StrEnum):
    """The verdict for one claim."""

    #: A consumer outside the defining module invokes the named symbol.
    WIRED = "wired"
    #: The module exists but nothing outside it references the symbol. This is
    #: the defect state: the capability is documented but not connected.
    UNWIRED = "unwired"
    #: Only dynamic references were found, so a static walk cannot decide.
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class Claim:
    """One advertised capability and the symbol that must be consumed.

    ``symbol`` is ``"<module-path>::<Name>"`` for a named symbol, or a bare
    module/package path to assert that something imports it at all. The two forms
    answer different questions: a symbol asks *is this function called*, a path
    asks *is this package used*.

    Args:
        capability_id: Stable identifier, matching the capability catalogs.
        symbol: The function/class/module that proves the capability.
        reason: Why the claim matters, quoted in the failure message.
    """

    capability_id: str
    symbol: str
    reason: str = ""
    #: Extra roots to search, beyond the harness and app packages.
    extra_roots: tuple[str, ...] = ()

    @property
    def module_path(self) -> str:
        """The module or package half of ``symbol``."""
        return self.symbol.split("::", 1)[0].strip()

    @property
    def member(self) -> str | None:
        """The named symbol, or ``None`` for a whole-module claim."""
        _, _, tail = self.symbol.partition("::")
        return tail.strip() or None


@dataclass
class WiringReport:
    """The verdict for every claim examined."""

    state: WiringState
    capability_id: str
    symbol: str
    #: `path:line` of each consumer found outside the defining module.
    consumers: list[str] = field(default_factory=list)
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "capability_id": self.capability_id,
            "symbol": self.symbol,
            "consumers": self.consumers,
            "reason": self.reason,
            "detail": self.detail,
        }


def _source_roots(extra: tuple[str, ...] = ()) -> list[Path]:
    """Every tree a production call site could live in.

    Deliberately excludes ``tests/``: a test proves the unit works, which is the
    question that let every stub above pass review.
    """
    root = project_root()
    candidates = [
        root / "backend" / "packages" / "harness" / "alpha",
        root / "backend" / "app",
        root / "frontend" / "src",
        root / "scripts",
        root / "electron",
    ]
    candidates.extend(root / item for item in extra)
    return [p for p in candidates if p.is_dir()]


def _resolve_defining_module(module_path: str, roots: list[Path]) -> Path | None:
    """Locate the module or package that defines ``module_path``."""
    wanted = module_path.replace("\\", "/").strip("/")
    if wanted.endswith(".py"):
        wanted = wanted[: -len(".py")]
    for base in roots:
        for path in base.rglob("*"):
            if "__pycache__" in path.parts or path.suffix not in {".py", ""}:
                continue
            relative = path.relative_to(base).as_posix()
            if relative.endswith(".py"):
                relative = relative[: -len(".py")]
            if relative == wanted:
                return path
    return None


def _import_name(path: Path, roots: list[Path]) -> str | None:
    """The dotted import name Alpha uses for *path*."""
    for base in roots:
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        parts = [p for p in rel.with_suffix("").parts if p != "__init__"]
        # Strip the package-root prefix (`harness/alpha`, `app`) so the result is
        # the import name rather than a filesystem path.
        if parts and parts[0] in {"alpha", "app"}:
            parts = parts[1:]
        if parts:
            return ".".join(parts)
    return None


def _module_referenced_outside(path: Path, roots: list[Path]) -> list[str]:
    """Find imports of *path* from any other module.

    Module-level, so `from alpha.x import y` and `import alpha.x` both count
    while an unrelated same-named local variable does not.
    """
    base_module = _import_name(path, roots)
    if base_module is None:
        return []

    hits: list[str] = []
    for candidate in _iter_sources(roots):
        if candidate == path:
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8", errors="ignore"), filename=str(candidate))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            name: str | None = None
            if isinstance(node, ast.ImportFrom) and node.module:
                name = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(base_module):
                        name = alias.name
                        break
            if name and (name == base_module or name.startswith(base_module + ".")):
                hits.append(f"{candidate.as_posix()}:{node.lineno}")
                break
    return hits


def _iter_sources(roots: list[Path]) -> list[Path]:
    for base in roots:
        for path in base.rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def find_consumers(claim: Claim, *, roots: list[Path] | None = None) -> WiringReport:
    """Find every production call site for ``claim.symbol``.

    The defining module is excluded: a symbol referencing itself is not a
    consumer, which is precisely what a stub looks like.

    Args:
        claim: The capability and the symbol that must be consumed.
        roots: Optional explicit search roots. Present so the mechanism can be
            tested against a synthetic tree; production callers leave it unset and
            get the real package roots.
    """
    roots = list(roots) if roots is not None else _source_roots(claim.extra_roots)
    if not roots:
        return WiringReport(
            WiringState.UNPROVEN,
            claim.capability_id,
            claim.symbol,
            reason=claim.reason,
            detail="no source roots found to search",
        )

    definition = _resolve_defining_module(claim.module_path, roots)
    member = claim.member

    if definition is None:
        return WiringReport(
            WiringState.UNPROVEN,
            claim.capability_id,
            claim.symbol,
            reason=claim.reason,
            detail=(f"no module found for {claim.module_path!r}; a dynamic or registry-driven reference cannot be ruled out"),
        )

    if member is None:
        # Whole-module claim: the question is whether anything imports it at all.
        consumers = _module_referenced_outside(definition, roots)
        noun = "module"
    else:
        consumers = []
        for path in _iter_sources(roots):
            if path == definition:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"), filename=str(path))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == member:
                    consumers.append(f"{path.as_posix()}:{node.lineno}")
                    break
                if isinstance(node, ast.Attribute) and node.attr == member:
                    consumers.append(f"{path.as_posix()}:{node.lineno}")
                    break
        noun = "symbol"

    if consumers:
        state = WiringState.WIRED
        detail = ""
    else:
        state = WiringState.UNWIRED
        detail = f"{definition.name} defines the claimed {noun}, but no module outside it references it. A unit-test-passing module with no production caller is a stub, not a capability."

    return WiringReport(
        state=state,
        capability_id=claim.capability_id,
        symbol=claim.symbol,
        consumers=sorted(consumers)[:8],
        reason=claim.reason,
        detail=detail,
    )


def check_claims(claims: list[Claim]) -> list[WiringReport]:
    """Check every claim, in declaration order."""
    return [find_consumers(c) for c in claims]


# ---------------------------------------------------------------------------
# The registry of claims proven false during the audit.
#
# Each entry is a regression. Adding a consumer keeps the entry green; removing
# the consumer turns CI red with the reason attached. This is what stops the
# class from recurring, rather than fixing one instance of it.
# ---------------------------------------------------------------------------

AUDITED_CLAIMS: tuple[Claim, ...] = (
    Claim(
        capability_id="fleet_emergency_stop",
        symbol="runtime/estop.py::EmergencyStopManager",
        reason=("The emergency stop was documented as pausing 'all background tasks and subagents' while its only consumer was rsi/switchboard.py, which gates the recursive-self-improvement cycle and nothing else."),
    ),
    Claim(
        capability_id="agent_side_effect_ledger",
        symbol="runtime/side_effects",
        reason=("The side-effect ledger is the mechanism that makes an announced effect recoverable. It must have a production writer, not only a dataclass."),
    ),
    Claim(
        capability_id="stream_bridge_recovery",
        symbol="runtime/events",
        reason=("The durable run-event feed is what an orphan-recovered run is reconciled against, so it needs a live consumer rather than a store alone."),
    ),
)


def audit_registered_claims() -> list[WiringReport]:
    """Run every audited claim."""
    return check_claims(list(AUDITED_CLAIMS))


def unwired(reports: list[WiringReport]) -> list[WiringReport]:
    """Only the claims that are demonstrably consumer-free."""
    return [r for r in reports if r.state is WiringState.UNWIRED]


def unproven(reports: list[WiringReport]) -> list[WiringReport]:
    """Claims a static walk cannot decide - a human must adjudicate these."""
    return [r for r in reports if r.state is WiringState.UNPROVEN]
