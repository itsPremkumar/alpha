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


def repo_root() -> Path:
    """The repository root, found structurally rather than from the cwd.

    Delegates to :func:`alpha.config.runtime_paths.repository_root`, which owns
    the single structural marker walk. Deliberately not
    ``runtime_paths.project_root()``: that resolves ``ALPHA_PROJECT_ROOT`` or
    the **process working directory**, so a test run from ``backend/`` yields
    ``backend/`` and every source root below silently resolves to
    ``backend/backend/...``. This audit must be anchored to the tree it is
    auditing.
    """
    from alpha.config.runtime_paths import repository_root

    return repository_root()


def _source_roots(extra: tuple[str, ...] = ()) -> list[Path]:
    """Every tree a production call site could live in.

    Deliberately excludes ``tests/``: a test proves the unit works, which is the
    question that let every stub above pass review.
    """
    root = repo_root()
    candidates = [
        root / "backend" / "packages" / "harness" / "alpha",
        root / "backend" / "app",
        root / "frontend" / "src",
        root / "scripts",
        root / "electron",
    ]
    candidates.extend(root / item for item in extra)
    found = [p for p in candidates if p.is_dir()]
    if not found:
        # Never silently audit nothing: that is how this gate passed vacuously
        # once already, when the cwd-anchored roots all missed.
        raise FileNotFoundError("no source roots resolved; the advertised-vs-wired audit must not report an empty tree as healthy")
    return found


def _scan_roots(roots: list[Path]) -> tuple[list[Path], dict[str, Path]]:
    """One tree walk producing both things the audit needs.

    Returns ``(source_files, relative_path -> file_or_package)``.

    This used to be two separate walks executed *per claim*: module resolution
    ran ``rglob("*")`` over every root and source enumeration ran
    ``rglob("*.py")`` over every root. On this checkout each walk measures
    ~11 s, so four claims paid ~88 s of pure directory traversal before a single
    line of source was read.
    """
    sources: list[Path] = []
    module_map: dict[str, Path] = {}
    for base in roots:
        for path in base.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            try:
                relative = path.relative_to(base)
            except ValueError:
                continue
            if path.suffix.lower() == ".py":
                sources.append(path)
                module_map.setdefault(relative.with_suffix("").as_posix(), path)
            elif path.suffix == "":
                # A package directory (or an extensionless file) can be the
                # target of a whole-module claim such as ``runtime/side_effects``.
                module_map.setdefault(relative.as_posix(), path)
    return sources, module_map


def _import_name(path: Path, roots: list[Path]) -> str | None:
    """The dotted import name Alpha uses for *path*.

    The search roots are the *contents* of a package (``harness/alpha``,
    ``app``), but import names include the package itself: a module at
    ``harness/alpha/runtime/side_effects`` is imported as
    ``alpha.runtime.side_effects``. Dropping the prefix made every module-level
    claim report UNWIRED, which is how this gate first failed.
    """
    for base in roots:
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        parts = [p for p in rel.with_suffix("").parts if p != "__init__"]
        if not parts:
            continue
        # Re-attach the package name the root sits inside.
        package = base.name
        if package not in {"alpha", "app"}:
            continue
        return ".".join([package, *parts])
    return None


class SourceIndex:
    """One tree walk and one read pass, shared by every claim.

    ## Why this exists

    ``check_claims`` used to call :func:`find_consumers` once per claim, and
    every call re-walked the whole tree with ``rglob`` *and* re-read and
    re-parsed every source file. Measured on this checkout (2069 files):

    ================================  =========
    per claim (old)                   111 s
    four registered claims (old)      358 s
    ================================  =========

    That number is not a performance footnote - it was an outage. ``GET
    /api/features`` ran this inline inside an ``async def`` route, so the
    Gateway's asyncio event loop stopped for the whole audit: every other
    request, including ``/health/ready``, timed out, the launcher gave up at its
    240 s readiness budget and restarted the stack, and the audit - needing
    358 s - never survived long enough to fill its per-process cache. Every boot
    therefore repeated it, and the stack restarted forever.

    Sharing one index makes it a single walk and a single read, and the token
    pre-filter turns a 42 s full re-parse into a 0.08 s substring scan plus
    parsing only the few files that can mention the symbol.

    The pre-filter is sound rather than heuristic: a name that does not occur
    literally in a file's text cannot appear as an ``ast.Name``/``ast.Attribute``
    with that spelling, nor as a dotted import target, so a file rejected by
    ``token in text`` could never have produced a match.
    """

    __slots__ = ("roots", "sources", "_module_map", "_texts", "_trees")

    def __init__(self, roots: list[Path]):
        self.roots = list(roots)
        self.sources, self._module_map = _scan_roots(self.roots)
        self._texts: dict[Path, str] | None = None
        self._trees: dict[Path, ast.AST | None] = {}

    @property
    def texts(self) -> dict[Path, str]:
        """Every source file's text, read once and memoized.

        Read lazily so constructing an index to resolve a module does not pay
        for files the audit will never look at.
        """
        if self._texts is None:
            texts: dict[Path, str] = {}
            for path in self.sources:
                try:
                    texts[path] = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
            self._texts = texts
        return self._texts

    def resolve_module(self, module_path: str) -> Path | None:
        """Locate the module or package that defines ``module_path``."""
        wanted = module_path.replace("\\", "/").strip("/")
        if wanted.endswith(".py"):
            wanted = wanted[: -len(".py")]
        return self._module_map.get(wanted)

    def _tree(self, path: Path) -> ast.AST | None:
        """Parse *path* once, and only if its text can match the token asked for."""
        if path in self._trees:
            return self._trees[path]
        text = self.texts.get(path)
        tree: ast.AST | None = None
        if text is not None:
            try:
                tree = ast.parse(text, filename=str(path))
            except (SyntaxError, ValueError):
                # ValueError covers embedded NUL bytes, which ``ast.parse``
                # rejects but ``read_text(errors="ignore")`` keeps. The old
                # catch list omitted it, so one binary file left in a source
                # root raised out of the audit.
                tree = None
        self._trees[path] = tree
        return tree

    def _module_references(self, definition: Path) -> list[str]:
        """Find imports of *definition* from any other module.

        Module-level, so ``from alpha.x import y`` and ``import alpha.x`` both
        count while an unrelated same-named local variable does not.
        """
        base_module = _import_name(definition, self.roots)
        if base_module is None:
            return []

        hits: list[str] = []
        for path, text in self.texts.items():
            if path == definition or base_module not in text:
                continue
            tree = self._tree(path)
            if tree is None:
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
                    hits.append(f"{path.as_posix()}:{node.lineno}")
                    break
        return hits

    def find_consumers(self, claim: Claim) -> WiringReport:
        """Find every production call site for ``claim.symbol``.

        The defining module is excluded: a symbol referencing itself is not a
        consumer, which is precisely what a stub looks like.
        """
        definition = self.resolve_module(claim.module_path)
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
            consumers = self._module_references(definition)
            noun = "module"
        else:
            consumers = []
            for path, text in self.texts.items():
                if path == definition or member not in text:
                    continue
                tree = self._tree(path)
                if tree is None:
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
    resolved = list(roots) if roots is not None else _source_roots(claim.extra_roots)
    if not resolved:
        return WiringReport(
            WiringState.UNPROVEN,
            claim.capability_id,
            claim.symbol,
            reason=claim.reason,
            detail="no source roots found to search",
        )
    return SourceIndex(resolved).find_consumers(claim)


def check_claims(claims: list[Claim], *, roots: list[Path] | None = None) -> list[WiringReport]:
    """Check every claim, in declaration order.

    Every claim in one batch shares a single :class:`SourceIndex`, so the tree
    is walked and read once no matter how many claims are registered. Claims
    that declare *different* ``extra_roots`` fall back to one index each,
    because they genuinely search different trees.
    """
    claims = list(claims)
    if not claims:
        return []
    if roots is not None:
        index = SourceIndex(list(roots))
        return [index.find_consumers(c) for c in claims]
    extras = {c.extra_roots for c in claims}
    if len(extras) == 1:
        index = SourceIndex(_source_roots(next(iter(extras))))
        return [index.find_consumers(c) for c in claims]
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
        symbol="runtime/estop.py::get_estop_manager",
        reason=(
            "The emergency stop was documented as pausing 'all background tasks and "
            "subagents' while its only consumer was rsi/switchboard.py, which gates the "
            "recursive-self-improvement cycle and nothing else. Claimed on the accessor "
            "rather than the class, because the class is instantiated once inside its own "
            "module by design and a caller never names it."
        ),
    ),
    Claim(
        capability_id="agent_side_effect_ledger",
        symbol="runtime/side_effects",
        reason=("The side-effect ledger is the mechanism that makes an announced effect recoverable, so it needs a production writer in deps.py rather than only a dataclass and a unit test."),
    ),
    Claim(
        capability_id="durable_run_event_feed",
        symbol="runtime/events/store",
        reason=("The durable run-event feed is what an orphan-recovered run is reconciled against, so its store layer needs a live production consumer."),
    ),
    Claim(
        capability_id="local_laya_decision_engine",
        symbol="models/system_one.py::SystemOneClient",
        reason=("Laya is documented as Alpha's free local System-1 decision engine. A model name in config plus a setup script is not a capability; the client must be constructed by production code."),
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
