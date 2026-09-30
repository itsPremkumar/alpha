"""Inert-declaration guard: a config key nobody reads, and a flag that starts nothing.

WHY THIS EXISTS
---------------
The most expensive bug in this repository's history was a configuration key that was
declared, shipped, documented as authoritative, and read by nobody: eight
``model_pricing`` entries sat in ``config.example.yaml`` while every cost in the
product was ``null``, and no test failed, because the test that existed asserted that
the config *parses*. Parsing is not reading.

That is not an accident, it is a class, and the same shape has been found repeatedly:

* ``SideEffectLedger``: implemented, migrated, tested, zero production callers.
* Group A -- declared, shipped, read by nothing outside their own declaration
  module: ``evolution_evidence.*``, ``verification.judge_enabled`` and
  ``verification.judge_model_name``.
* Group B -- read only by a subsystem production never invokes: the
  ``self_tuning.*`` protocol and ``memory.fabric.*``. Flipping ``enabled: true``
  starts nothing at all.

TWO GATES
---------

**Gate A -- a declared key has a reader outside its own declaring module.**
Every field reachable from ``AppConfig`` is walked from the model (not grepped from
the template, so a renamed key is caught even though its old name still appears in
``config.example.yaml``). A field passes when some production module mentions its
key identifier; it fails when the only mentions are in the module that declares it.

**Gate B -- an ``enabled`` flag maps to a real entry point.**
Gate A cannot see Group B, because a Group B key *is* read: by its own subsystem. So
Gate B asks the harder question, in two independent channels, because either alone
gives a wrong answer:

* a **live section reader** -- a module that references the flag's section *and* sits
  in the import closure of a production entry point; or
* a **live non-declaring importer** -- a live module that imports the module
  declaring the flag for something other than the field declaration (an accessor, a
  constant, a validator).

Importing a config model in order to hang it on a parent section is the declaration
itself and proves nothing, which is exactly how a self-tuning protocol or a memory
fabric can look wired while nothing ever constructs it.

WHY THIS IS NOT A MIRROR OF THE CODE
------------------------------------
Both gates key on things that belong to the operator-facing contract, never on an
internal symbol:

* Gate A keys on the **config key identifier**. Renaming a local variable, a helper,
  a class or a middleware cannot make this gate fail. Renaming a *config key* can,
  and that is correct: a renamed key is a breaking configuration change
  (``config/AGENTS.md``) and it must arrive with its reader.
* Gate B keys on the **section identifier** and on **import statements between
  modules**. It names no function, class or private attribute, so an implementation
  rename cannot fail it.

KNOWN LIMITS, STATED RATHER THAN HIDDEN
---------------------------------------
A static scan cannot type-check ``cfg.enabled``. For a key whose identifier is also
an ordinary word in the codebase (``enabled``, ``model``, ``description``,
``timeout_seconds``), Gate A's proof is *any* production mention of that identifier,
which is weak evidence. Strictness was measured on this tree rather than guessed: a
path-qualified-or-distinctive rule reports 274-472 findings here, which is how a
gate gets disabled inside a week and stops catching anything. The token channel is
the loose-but-honest floor; ``report()`` prints the finding count so a reviewer sees
how much of the field set rests on it. Every finding this guard has ever produced
had a distinctive key name, which is the actual reason it is detectable at all.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import types
import typing
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CONTRACTS = ROOT / "contracts"
ALLOWLIST_PATH = CONTRACTS / "inert_declarations_allowlist.v1.json"
ALLOWLIST_SCHEMA_PATH = CONTRACTS / "inert_declarations_allowlist.v1.schema.json"

HARNESS = BACKEND / "packages" / "harness"
APP = BACKEND / "app"

#: Directories that are not production source. ``tests`` matters most: a test that
#: asserts a default value is not a reader, and counting tests would make this gate
#: pass for every key that has a test -- the exact failure mode being guarded.
SKIP_PARTS = frozenset({"tests", "__pycache__", "node_modules", "alembic", ".venv"})

#: The trees that can read a config key, with the dotted prefix each base
#: contributes. The harness root already *is* the ``alpha`` package directory; the
#: app root is not (``backend/app/gateway`` is ``app.gateway``).
SCAN_BASES: tuple[tuple[Path, str], ...] = ((HARNESS, ""), (APP, "app"))

#: Production entry points. A module is "live" when it is in the transitive import
#: closure of one of these: the ASGI app that uvicorn and ``langgraph.json`` both
#: name, the autonomy supervisor the Gateway lifespan starts, and the three operator
#: CLIs the orphan-module gate already pins.
ROOT_MODULES: tuple[str, ...] = (
    "app.gateway.app",
    "app.gateway.langgraph_auth",
    "app.gateway.langgraph_studio",
    "app.gateway.autonomy.supervisor",
    "alpha.tui.__main__",
    "alpha.runtime.sentinel.__main__",
    "alpha.safety.authority.__main__",
)


def is_flag_name(name: str) -> bool:
    """A flag is ``enabled``-shaped when its own name reads as a switch."""
    return name == "enabled" or name.endswith("_enabled")


# --------------------------------------------------------------------------- #
# Module inventory
# --------------------------------------------------------------------------- #
def _module_name(path: Path, base: Path) -> str | None:
    parts = list(path.relative_to(base).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


@lru_cache(maxsize=1)
def production_modules() -> dict[str, Path]:
    """``{dotted module: path}`` for every production module in the two trees."""
    found: dict[str, Path] = {}
    for base, prefix in SCAN_BASES:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if SKIP_PARTS & set(path.relative_to(BACKEND).parts):
                continue
            name = _module_name(path, base)
            if name:
                found[f"{prefix}.{name}" if prefix else name] = path
    return found


@lru_cache(maxsize=1)
def _is_package() -> dict[str, bool]:
    return {module: path.name == "__init__.py" for module, path in production_modules().items()}


def _package_of(module: str) -> str:
    if _is_package().get(module):
        return module
    return module.rsplit(".", 1)[0] if "." in module else module


def _relative_target(module: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = [p for p in _package_of(module).split(".") if p]
    up = node.level - 1
    if up:
        parts = parts[:-up] if up <= len(parts) else []
    if node.module:
        parts.append(node.module)
    return ".".join(parts)


@lru_cache(maxsize=1)
def _sources() -> dict[str, str]:
    return {module: path.read_text(encoding="utf-8", errors="replace") for module, path in production_modules().items()}


@lru_cache(maxsize=1)
def _trees() -> dict[str, ast.AST | None]:
    parsed: dict[str, ast.AST | None] = {}
    for module, text in _sources().items():
        try:
            parsed[module] = ast.parse(text)
        except SyntaxError:
            parsed[module] = None
    return parsed


@lru_cache(maxsize=1)
def import_graph() -> dict[str, dict[str, frozenset[str]]]:
    """``{module: {imported module: names taken from it}}`` over production modules.

    Names are recorded so Gate B can tell *declaring* a config model into a parent
    section (which proves nothing) apart from *using* it (which does). An import
    inside a function body is an edge: this is a reachability question, not a
    module-load-order question, and a lazy import is still reachable.
    """
    graph: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for module, tree in _trees().items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    graph[module][alias.name].update(alias.name.split(".")[:1])
            elif isinstance(node, ast.ImportFrom):
                target = _relative_target(module, node)
                if not target:
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        graph[module][target].add(alias.name)
                        graph[module][f"{target}.{alias.name}"].add(alias.name)
    return {module: {target: frozenset(names) for target, names in targets.items()} for module, targets in graph.items()}


@lru_cache(maxsize=1)
def live_modules() -> frozenset[str]:
    """Every production module in the import closure of :data:`ROOT_MODULES`."""
    graph = import_graph()
    seen: set[str] = set()
    stack = list(ROOT_MODULES)
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        for target in graph.get(module, ()):
            if target not in seen:
                stack.append(target)
    return frozenset(seen)


def live_non_declaring_importers(module: str) -> list[str]:
    """Live modules importing *module* for something other than a config model class.

    A config model imported to be attached to a parent section is the declaration.
    Anything else -- an accessor, a validator, a constant -- is a real use, and it is
    the only thing that can make a flag reachable without any module spelling out the
    flag's own name.
    """
    graph = import_graph()
    models = declaring_models()
    return sorted(importer for importer in live_modules() if (targets := graph.get(importer, {})).get(module) and targets[module] - models.get(module, frozenset()))


# --------------------------------------------------------------------------- #
# Reference index
# --------------------------------------------------------------------------- #
#: An identifier component. This is Gate A's channel, and it is deliberately the loose
#: one: the commonest way production reads a key is ``cfg.key``, which always puts a
#: dot in front of the name, so a rule that excluded dotted context would fail ~128
#: live keys (``auth.local.allow_registration`` among them) and would be the "strict
#: gate that gets disabled inside a week" this file is written to avoid.
_COMPONENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: A whole identifier, or a dotted path. Gate B uses this channel and the loose one
#: deliberately, because for Gate B the loose one is actively wrong:
#: ``alpha.config.self_tuning.models`` names a *module*, not the ``self_tuning``
#: config section, and treating it as a reference would make an unwired protocol look
#: wired.
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_DOTTED_PATH = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    """``cfg.memory.fabric`` -> ``("memory", "fabric")``; ``cfg["a"]["b"]`` likewise.

    The base is a local name and is deliberately not part of the chain: a chain names
    the *keys* walked through, which is what identifies a config section.
    """
    parts: list[str] = []
    while True:
        if isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            parts.append(node.slice.value)
            node = node.value
        else:
            break
    parts.reverse()
    return tuple(parts)


def _code_references(tree: ast.AST) -> tuple[set[str], set[str]]:
    """``(identifier components, whole identifiers)`` appearing in *code*.

    Built from the AST rather than from the raw text so a **comment or a docstring is
    not a reader**. That distinction is load-bearing: the prose in
    ``app/gateway/app.py`` names ``autonomy.loops`` and ``autonomy.enabled`` while
    describing the supervisor, and a text scan would conclude the flag is read in the
    one file that matters. String *literals* still count, because
    ``current.get("self_tuning")`` is a real dynamic read.
    """
    components: set[str] = set()
    standalone: set[str] = set()

    def add_string(value: str) -> None:
        if _IDENTIFIER.match(value):
            components.add(value)
            standalone.add(value)
            return
        if _DOTTED_PATH.match(value):
            components.update(_COMPONENT.findall(value))
            standalone.add(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            components.add(node.id)
            standalone.add(node.id)
        elif isinstance(node, ast.Attribute):
            components.add(node.attr)
        elif isinstance(node, ast.arg):
            components.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            components.add(node.arg)
        elif isinstance(node, ast.alias):
            components.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            components.add(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            add_string(node.value)
    return components, standalone


@lru_cache(maxsize=1)
def reference_index() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]], dict[tuple[str, ...], frozenset[str]]]:
    """``(components, standalone, chains)`` -> ``{key: modules}`` over production.

    Three channels, each with the strictness its caller needs:

    * **components** (Gate A) -- a key is *named* somewhere outside its declaration.
    * **standalone** (Gate B) -- a whole identifier or dotted path, so a module
      reference cannot be mistaken for a section reference.
    * **chains** (Gate B) -- a key is *reached* by attribute or subscript access, which
      is how Gate B tells ``cfg.verification.receipts_enabled`` apart from an
      unrelated ``verification``.
    """
    components: dict[str, set[str]] = defaultdict(set)
    standalone: dict[str, set[str]] = defaultdict(set)
    chains: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for module, tree in _trees().items():
        if tree is None:
            continue
        module_components, module_standalone = _code_references(tree)
        for token in module_components:
            components[token].add(module)
        for token in module_standalone:
            standalone[token].add(module)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Subscript)):
                chain = _attribute_chain(node)
                for start in range(len(chain)):
                    chains[chain[start:]].add(module)
    return (
        {key: frozenset(value) for key, value in components.items()},
        {key: frozenset(value) for key, value in standalone.items()},
        {key: frozenset(value) for key, value in chains.items()},
    )


def key_mentions(key: str) -> frozenset[str]:
    """Modules whose *code* names *key*, including as ``something.key`` (Gate A)."""
    return reference_index()[0].get(key, frozenset())


@lru_cache(maxsize=1)
def _exported_callables() -> dict[str, frozenset[str]]:
    """``{module: names it defines and exports}`` for the modules that declare config."""
    out: dict[str, set[str]] = {}
    for module in sorted({declaration.module for declaration in declared_fields().values()}):
        loaded = sys.modules.get(module)
        if loaded is None:  # pragma: no cover - the walk imported every declaring module
            continue
        out[module] = {name for name, value in vars(loaded).items() if callable(value) and not name.startswith("_") and getattr(value, "__module__", None) == module}
    return {module: frozenset(names) for module, names in out.items()}


@lru_cache(maxsize=1)
def _named_call_string_arguments() -> dict[tuple[str, str], frozenset[str]]:
    """``{(module, callee): string-literal arguments}`` for calls to a bare name."""
    calls: dict[tuple[str, str], set[str]] = defaultdict(set)
    for module, tree in _trees().items():
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    calls[(module, node.func.id)].add(arg.value)
    return {key: frozenset(value) for key, value in calls.items()}


def dynamic_field_readers(section: tuple[str, ...], declaring_module: str) -> frozenset[str]:
    """Live modules that ask the declaring module to resolve a field of *section* by name.

    A real and repeated idiom in this tree: ``database.checkpoint_graph_cache`` is
    never spelled out in a read expression, because
    ``alpha/config/database_config.py::resolve_checkpoint_graph_cache_max`` takes the
    section field name as a *string* and reaches it with ``getattr`` --
    ``app/gateway/services.py`` calls it with ``"accessor_graph_max"``. Without this
    channel the section reads as unread, which is a false accusation rather than a
    finding, and a gate that accuses wrongly gets switched off.

    The pattern is deliberately narrow: the callee must be a function *defined and
    exported* by the module that declares the section, and an argument must be a
    literal naming a field inside it. A comment, a docstring or an unrelated module
    cannot satisfy it.
    """
    prefix = ".".join(section)
    names = {path.split(".")[-1] for path in declared_fields() if path.startswith(f"{prefix}.")}
    if not names:
        return frozenset()
    calls = _named_call_string_arguments()
    exported = _exported_callables().get(declaring_module, frozenset())
    return frozenset(module for (module, callee) in calls if callee in exported and calls[(module, callee)] & names)


def section_readers(section: tuple[str, ...]) -> frozenset[str]:
    """Production modules whose *code* references *section*, matched as a chain suffix.

    A chain counts when it contains **any suffix** of the section path as a contiguous
    run, so a consumer that binds an intermediate still counts:

    * ``app_config.auth.oidc`` then ``oidc_config.providers.nonce_enabled`` proves
      ``auth.oidc.providers.nonce_enabled`` is read even though no chain spells the
      whole section;
    * ``config.affective`` proves ``memory.affective.enabled`` is read, because
      ``recall_composition`` gates every memory type on its own section.

    Prefixes deliberately do *not* count: ``cfg.memory`` is reached by half the
    codebase, so a prefix match would make every ``memory.*`` flag look wired and
    would hide the finding this gate exists for. A whole dotted section
    (``current.get("self_tuning")``, ``"autonomy.bus.queue_maxsize"``) counts too,
    via the standalone index.
    """
    _, standalone, chains = reference_index()
    suffixes = [section[start:] for start in range(len(section))]
    readers = {module for chain, modules in chains.items() for suffix in suffixes if any(chain[start : start + len(suffix)] == suffix for start in range(len(chain))) for module in modules}
    readers |= set(standalone.get(".".join(section), frozenset()))
    return frozenset(readers)


# --------------------------------------------------------------------------- #
# The AppConfig field walk
# --------------------------------------------------------------------------- #
#: The root model. Its ``extra="allow"`` is about *undeclared* top-level keys, so it
#: exempts no declared field -- see :func:`kwarg_bag_paths`.
ROOT_CONFIG_MODEL = "alpha.config.app_config.AppConfig"


class Declaration(NamedTuple):
    """One config field: its dotted path, the model that declares it, and its ancestry."""

    path: str
    module: str
    model_ref: str
    """``module.QualName`` of the declaring model."""
    owner_modules: tuple[str, ...]
    """Every module that declares a model on the path from ``AppConfig`` to this field.

    These modules are the *declaration* of the key and can never count as its reader:
    ``app_config.py`` attaching ``SelfTuningConfig`` to ``self_tuning`` is the field
    existing, not anyone reading it. Excluding the whole ancestry rather than only the
    innermost module is what stops ``evolution_evidence`` from looking wired because
    the file that declares the field also names it.
    """
    owner_models: tuple[str, ...]
    """``module.QualName`` for each entry of :attr:`owner_modules`, outermost first."""


def _nested_models(annotation: object) -> list[type]:
    """Every pydantic model reachable through *annotation*, containers and unions included.

    Following only a direct ``BaseModel`` annotation silently skips every list, dict
    and Optional member: on this tree that is 211 of 791 fields, including all of
    ``models[]``, ``tools[]``, ``agent_presets{}``, ``auth.oidc.providers{}`` and
    ``extensions.mcp_servers.*``. A gate that cannot see a key is not a gate.
    """
    from pydantic import BaseModel

    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is getattr(types, "UnionType", None):
        return [model for arg in typing.get_args(annotation) for model in _nested_models(arg)]
    if origin is not None:
        found: list[type] = []
        for arg in typing.get_args(annotation):
            if arg is Ellipsis or arg is type(None):
                continue
            found.extend(_nested_models(arg))
        return found
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return []


@lru_cache(maxsize=1)
def declared_fields() -> dict[str, Declaration]:
    """``{dotted field path: Declaration}`` for every ``AppConfig``-reachable field."""
    from alpha.config.app_config import AppConfig

    fields: dict[str, Declaration] = {}
    visited: set[str] = set()

    def walk(model: type, prefix: str, modules: tuple[str, ...], models: tuple[str, ...]) -> None:
        # Keyed by module-qualified class path rather than by id(): the same class
        # under two prefixes must be walked under both, and one class reached twice
        # in the same walk must not be.
        marker = f"{model.__module__}.{model.__qualname__}"
        if marker in visited:
            return
        visited.add(marker)
        module_chain = (*modules, model.__module__)
        model_chain = (*models, marker)
        for name, info in model.model_fields.items():
            path = f"{prefix}{name}"
            fields.setdefault(path, Declaration(path=path, module=model.__module__, model_ref=marker, owner_modules=module_chain, owner_models=model_chain))
            for nested in _nested_models(info.annotation):
                walk(nested, f"{path}.", module_chain, model_chain)

    walk(AppConfig, "", (), ())
    return fields


@lru_cache(maxsize=1)
def declaring_models() -> dict[str, frozenset[str]]:
    """``{module: pydantic model class names it declares}`` over the walked field set."""
    from pydantic import BaseModel

    found: dict[str, set[str]] = defaultdict(set)
    for module in sorted({decl.module for decl in declared_fields().values()}):
        loaded = sys.modules.get(module)
        if loaded is None:
            continue
        for name, value in vars(loaded).items():
            if isinstance(value, type) and issubclass(value, BaseModel):
                found[module].add(name)
    return {module: frozenset(names) for module, names in found.items()}


@lru_cache(maxsize=1)
def _open_models() -> frozenset[str]:
    """Every ``extra="allow"`` model the walk reaches, named where it is declared."""
    from pydantic import BaseModel

    found: set[str] = set()
    for declaration in declared_fields().values():
        loaded = sys.modules.get(declaration.module)
        if loaded is None:  # pragma: no cover - the walk imported every declaring module
            continue
        for name, value in vars(loaded).items():
            # ``value.__module__`` keeps a re-export (``app_config`` importing
            # ``ToolConfig``) out of the set, so an entry names the model that
            # actually declares the fields rather than an alias for it.
            if isinstance(value, type) and issubclass(value, BaseModel) and value.__module__ == declaration.module and getattr(value, "model_config", {}).get("extra") == "allow":
                found.add(f"{declaration.module}.{name}")
    return frozenset(found)


@lru_cache(maxsize=1)
def operator_kwarg_models() -> frozenset[str]:
    """The outermost ``extra="allow"`` models on the AppConfig field tree.

    Outermost, because an open model nested inside another open model is consumed by
    exactly the same mechanism: ``McpOAuthConfig`` is read by the MCP client factory
    for the same reason ``McpServerConfig`` is, so listing both would be two entries
    for one consumption path. Listing only the outermost keeps the allowlist a
    statement about consumption mechanisms instead of a transcription of the class
    tree.

    Derived from the models themselves, never hand-listed, so closing a model loses
    its exemption instead of keeping a permanent waiver, and opening one demands a
    written reason. The root is excluded -- see :func:`kwarg_bag_paths`.
    """
    open_models = _open_models()
    # Nesting is judged against the non-root open models. The root's openness is
    # about undeclared top-level keys (see kwarg_bag_paths), so it must not make every
    # section model look like a nested member of a kwarg bag.
    containers = open_models - {ROOT_CONFIG_MODEL}
    nested = {declaration.owner_models[-1] for declaration in declared_fields().values() if any(model in containers for model in declaration.owner_models[:-1])}
    return frozenset(open_models - nested)


@lru_cache(maxsize=1)
def kwarg_bag_paths() -> frozenset[str]:
    """Config fields that live inside an ``extra="allow"`` operator-kwarg bag.

    **The root model is excluded, and that exclusion is the judgement this gate
    turns on.** ``AppConfig`` is ``extra="allow"`` as well, but its openness is
    about *undeclared top-level keys* -- an operator may put a key in ``config.yaml``
    that no model declares. It says nothing about the sections ``AppConfig`` *does*
    declare, and those are exactly where an inert declaration lives:
    ``evolution_evidence`` is a declared, named, shipped section that nothing reads,
    and exempting it would exempt the single most valuable thing this gate can catch.
    ``test_the_root_config_openness_exempts_no_declared_field`` pins that.

    For every *other* open model the declared fields really are the operator's
    keyword bag: ``ToolConfig``'s settings are forwarded verbatim to whichever
    provider its ``use:`` names, ``SandboxConfig``'s to whichever sandbox provider is
    selected, ``ModelConfig``'s to whichever chat client its ``use:`` names. Who
    honours a given key is a property of the operator's choice, not of the model
    carrying it, so no static reference can prove a reader and demanding one would be
    demanding a fiction.
    """
    kwarg = operator_kwarg_models() - {ROOT_CONFIG_MODEL}
    return frozenset(path for path, declaration in declared_fields().items() if any(model in kwarg for model in declaration.owner_models[1:]))


# --------------------------------------------------------------------------- #
# Gate A
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def declaration_only_keys() -> dict[str, str]:
    """``{key: declaring module}`` for every key read only where it is declared."""
    bag = kwarg_bag_paths()
    found: dict[str, str] = {}
    for path, declaration in declared_fields().items():
        if path in bag:
            continue
        parts = path.split(".")
        readers = set(key_mentions(parts[-1])) | set(dynamic_field_readers(tuple(parts), declaration.module))
        readers.discard(declaration.module)
        if not readers:
            found[path] = declaration.module
    return found


# --------------------------------------------------------------------------- #
# Gate B
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def unreachable_flags() -> dict[str, str]:
    """``{flag key: why no entry point is visible}`` for every ``enabled`` flag.

    A flag is reachable when a module that references its section sits inside the
    production import closure, or when the module declaring it is imported by a live
    module for something other than the field declaration. A flag whose reader exists
    but whose subsystem is never constructed is inert, and that is the case this gate
    exists for: ``memory.fabric`` is fully implemented and nothing builds it.

    The second category -- a flag read through a *dynamic* accessor, e.g.
    ``getattr(connection_config, provider, None)`` in
    ``app/channels/runtime_config_store.py`` -- is genuinely read and is not visible to
    a name-based scan. Those keys are listed in the allowlist with the accessor named,
    so the record says which kind of entry is missing rather than asserting one that
    is not true.
    """
    bag = kwarg_bag_paths()
    live = live_modules()
    inert: dict[str, str] = {}
    for path, declaration in declared_fields().items():
        if path in bag:
            continue
        parts = path.split(".")
        if not is_flag_name(parts[-1]):
            continue
        readers = set(section_readers(tuple(parts[:-1]))) | set(dynamic_field_readers(tuple(parts[:-1]), declaration.module))
        readers -= set(declaration.owner_modules)
        if readers & live or live_non_declaring_importers(declaration.module):
            continue
        where = ", ".join(sorted(readers)[:3]) or "no module outside the field's declaration references the section"
        reach = "none of them is in the production import closure" if readers else "there are none"
        inert[path] = f"declared by {declaration.module}; section readers: {where}; live: {reach}"
    return inert


# --------------------------------------------------------------------------- #
# The allowlist contract
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def allowlist() -> dict:
    return json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))


SECTIONS = ("operator_kwarg_models", "declaration_only_keys", "unreachable_flags", "known_blind_spots", "reported_defects")


def _assert_reasons(section: str) -> None:
    for entry in allowlist()[section]:
        target = entry.get("target", "")
        reason = entry.get("reason", "")
        assert isinstance(target, str) and target.strip(), f"{ALLOWLIST_PATH.name} [{section}] entry has no target: {entry!r}"
        assert isinstance(reason, str) and reason.strip(), f"{ALLOWLIST_PATH.name} [{section}] entry {target!r} has no reason. An allowlist entry nobody can verify is a list of things nobody checked."


def _assert_no_undocumented_findings(section: str, found: dict[str, str]) -> None:
    listed = {entry["target"] for entry in allowlist()[section]}
    undocumented = sorted(set(found) - listed)
    assert not undocumented, (
        f"these config keys are inert and are not recorded in {ALLOWLIST_PATH.name} [{section}]: {[(key, found[key]) for key in undocumented]}. "
        f"Wire a reader, or record the key with the reason it is inert. Deleting a config key is a breaking change for every "
        f"existing install and an operator decision (config/AGENTS.md), so this gate asks for a record, never for a deletion."
    )
    stale = sorted(listed - set(found))
    assert not stale, f"{ALLOWLIST_PATH.name} [{section}] lists {stale}, which this scan no longer finds inert. Delete the entries: a stale allowlist entry is a permanent waiver nobody re-reads."


# --------------------------------------------------------------------------- #
# Contract tests
# --------------------------------------------------------------------------- #
def test_allowlist_matches_its_schema() -> None:
    """The allowlist is a contract, so it is validated as one."""
    import jsonschema

    jsonschema.validate(instance=allowlist(), schema=json.loads(ALLOWLIST_SCHEMA_PATH.read_text(encoding="utf-8")))


def test_every_allowlist_entry_states_why() -> None:
    for section in SECTIONS:
        _assert_reasons(section)


def test_allowlist_entries_still_exist() -> None:
    """A recorded defect that no longer exists is a record of a problem this tree has not got.

    The mirror of the gates: they fail on an undocumented finding, this fails on a
    documented one that is gone. Together they stop the file from growing in either
    direction without a reviewer looking at it.
    """
    modules = production_modules()
    for entry in allowlist()["reported_defects"]:
        target = entry["target"]
        assert target in modules, f"{ALLOWLIST_PATH.name} reports {target} as an inert declaration, but that module no longer exists. Delete the entry, or record the fix in its reason, so the record stays true."
    for entry in allowlist()["operator_kwarg_models"]:
        target = entry["target"]
        assert target in operator_kwarg_models(), f'{ALLOWLIST_PATH.name} exempts {target} as an operator-kwarg bag, but the model is no longer extra="allow". Delete the exemption so the field-level gate applies to it again.'
    fields = declared_fields()
    for section in ("declaration_only_keys", "unreachable_flags", "known_blind_spots"):
        for entry in allowlist()[section]:
            assert entry["target"] in fields, f"{ALLOWLIST_PATH.name} records {entry['target']!r} as inert, but no such config key is declared any more. Delete the entry."


def test_every_operator_kwarg_bag_is_justified() -> None:
    """Every ``extra="allow"`` model reachable from AppConfig needs a written reason.

    Derived on one side, recorded on the other, so neither can drift alone: opening a
    model to operator kwargs fails here until somebody says how those kwargs are
    consumed, and closing one fails ``test_allowlist_entries_still_exist``.
    """
    derived = operator_kwarg_models()
    listed = {entry["target"] for entry in allowlist()["operator_kwarg_models"]}
    undocumented = sorted(derived - listed)
    assert not undocumented, (
        f'these AppConfig-reachable models are extra="allow", so their fields are an operator-kwarg bag that no static reader scan can judge, '
        f"but they have no entry in {ALLOWLIST_PATH.name} [operator_kwarg_models]: {undocumented}. Record how the kwargs are consumed, or close the model."
    )
    stale = sorted(listed - derived)
    assert not stale, f'{ALLOWLIST_PATH.name} [operator_kwarg_models] lists {stale}, which are not extra="allow" models reachable from AppConfig: {stale}'


# --------------------------------------------------------------------------- #
# The gates
# --------------------------------------------------------------------------- #
def test_gate_a_every_declared_config_key_has_a_reader() -> None:
    """Gate A: a key an operator can set must be read outside its own declaring module."""
    _assert_reasons("declaration_only_keys")
    _assert_no_undocumented_findings("declaration_only_keys", declaration_only_keys())


def test_gate_b_every_enabled_flag_maps_to_a_reachable_entry_point() -> None:
    """Gate B: a flag that reads as a switch must reach a construction site."""
    _assert_reasons("unreachable_flags")
    _assert_no_undocumented_findings("unreachable_flags", unreachable_flags())


# --------------------------------------------------------------------------- #
# Falsifiability: the scanners themselves must keep working
# --------------------------------------------------------------------------- #
def test_the_root_config_openness_exempts_no_declared_field() -> None:
    """``AppConfig`` is ``extra="allow"`` and that must not blind the gate.

    The root's openness is about keys no model declares. Every section it *does*
    declare is a real, shipped, operator-settable section, and that is exactly where
    an inert declaration lives: ``evolution_evidence`` is a declared section that
    nothing reads. If the root's ``extra="allow"`` exempted its fields, Gate A would
    lose the ability to catch a new inert top-level section, which is the single most
    valuable thing it can catch.
    """
    top_level = {path for path in declared_fields() if "." not in path}
    exempt_top_level = top_level & kwarg_bag_paths()
    assert not exempt_top_level, f'the root config\'s extra="allow" is exempting declared sections {sorted(exempt_top_level)}. Its openness concerns undeclared keys only; remove the root from the kwarg-bag exemption.'
    assert "evolution_evidence" in top_level, "the walk lost a top-level section, which is the case the root exemption must never cover"
    assert "evolution_evidence" in declaration_only_keys() or "evolution_evidence" in {e["target"] for e in allowlist()["declaration_only_keys"]}, (
        "evolution_evidence stopped being detectable as an unread top-level section; the root exemption is leaking"
    )


def test_the_appsconfig_walk_sees_through_containers_and_unions() -> None:
    """The walk must follow list, dict and Optional members.

    A walk that only follows a direct ``BaseModel`` annotation sees 582 of 791 fields
    on this tree and reports nothing at all, because everything it misses is a
    container member. These are the specific fields that prove each channel.
    """
    fields = declared_fields()
    for expected, channel in (
        ("models.context_window", "list[ModelConfig]"),
        ("tools.use", "list[ToolConfig]"),
        ("agent_presets.subagent_enabled", "dict[str, AgentPresetConfig]"),
        ("auth.oidc.providers.client_id", "list[OIDCProviderConfig]"),
        ("checkpointer.postgres_schema", "CheckpointerConfig | None"),
        ("extensions.mcp_servers.tool_call_timeout", "dict[str, MCPServerConfig]"),
        ("memory.fabric.enabled", "a model declared outside alpha/config/"),
        ("self_tuning.verification_policy.relative_tolerance", "a nested model in a subpackage"),
        ("evolution_evidence.noise_floors", "a model declared outside alpha/config/"),
    ):
        assert expected in fields, f"the AppConfig walk missed {expected!r} ({channel}), so it cannot gate it"
    assert len(fields) > 700, f"the AppConfig walk found only {len(fields)} fields, which means a channel stopped working"


def test_the_reference_channels_still_resolve() -> None:
    """Every reference channel must keep finding what the rules are built on.

    A regex or a chain extractor that silently stops matching makes every gate below
    pass for the wrong reason, which is the failure this repository keeps paying for.
    """
    components, standalone, chains = reference_index()
    # Gate A's loose channel: a key read by attribute access. The dot in front of
    # the name is the common case, so a channel that ignored it would fail ~128
    # live keys.
    assert "alpha.config.auth_config" in components.get("allow_registration", frozenset()) | components.get("auth_config", frozenset()), "the component index lost its attribute-access readers"
    assert len(components.get("allow_registration", frozenset())) > 1, "the component index should see allow_registration outside its declaring module"
    # Gate A's detection power: a distinctive key is named in exactly one production
    # file, which is the only reason it is detectable as declaration-only at all.
    assert components["judge_enabled"] == frozenset({"alpha.config.verification_config"}), f"the component index no longer isolates judge_enabled: {sorted(components.get('judge_enabled', ()))}"
    # Gate B's strict channel: a module reference to
    # alpha.config.self_tuning.models is a reference to a *module*, not to the
    # self_tuning config section, so it must not count as a section reader.
    assert "alpha.config.self_tuning.models" in standalone and "self_tuning" in standalone, "the standalone index no longer separates dotted paths from bare identifiers"
    assert "alpha.observability.trace.codes" in standalone.get("alpha.config.self_tuning.models", frozenset()), "the standalone index lost the dotted-path reference this guard depends on"
    # Gate B's chain channel, both directions. A live read: app/gateway/routers/
    # autonomy.py writes ``autonomy.bus.enabled`` and the base is a local name, so
    # the indexed chain is the keys walked through. And the negative that makes the
    # Gate B finding real: no code anywhere walks into the memory fabric section.
    assert "app.gateway.routers.autonomy" in chains.get(("bus", "enabled"), frozenset()), f"the attribute-chain index lost its autonomy.bus consumer; chains[('bus', 'enabled')] = {sorted(chains.get(('bus', 'enabled'), ()))}"
    assert ("memory", "fabric") not in chains, (
        f"something now reaches into the memory fabric config section, so memory.fabric is no longer inert. Remove that allowlist entry in this change; chains[('memory', 'fabric')] = {sorted(chains.get(('memory', 'fabric'), ()))}"
    )
    # A comment is not a reader, and this is the exact case that proves it: the
    # caption in app/gateway/services.py names the config path in prose, and it used
    # to be the only mention of ``checkpoint_graph_cache`` outside its declaration
    # module. The real reader is a call with the field name as a string literal,
    # which the dynamic-field channel finds instead.
    assert "database.checkpoint_graph_cache.accessor_graph_max" not in standalone, "the standalone index is reading comments again: a config path that appears only in a caption must not count as a reference"
    assert "app.gateway.services" in dynamic_field_readers(("database", "checkpoint_graph_cache"), "alpha.config.database_config"), "the dynamic-field channel lost the accessor_graph_max reader in app/gateway/services.py"


def test_the_reachability_channel_still_discriminates() -> None:
    """Gate B's reachability must separate wired subsystems from unwired ones.

    Every assertion is a pair -- one module that is reachable and one that is not --
    so a closure that collapses to "everything" or to "nothing" fails here instead of
    silently reclassifying the entire allowlist.
    """
    live = live_modules()
    modules = production_modules()
    assert "app.gateway.app" in live, "the production entry point is not in its own import closure, so nothing is reachable and Gate B would call every flag inert"
    assert "alpha.agents.middlewares.title_middleware" in live, "the title middleware is not reachable from the Gateway, so Gate B would start calling live subsystems inert"
    assert "alpha.evolution.evidence.service" in modules, "the evolution evidence service moved, so the known-inert subsystem behind the evolution_evidence entries is stale"
    assert "alpha.evolution.evidence.service" not in live, "the evolution evidence subsystem is now imported by production, so its config section is no longer inert. Remove the evolution_evidence entries from the allowlist in this change."
    # The accessor channel's positive and negative cases. The title config is reached
    # through its module-level singleton accessor from a live middleware; the
    # self-tuning config is imported by exactly one live module, and that module
    # imports it only to declare the field.
    title_importers = live_non_declaring_importers("alpha.config.title_config")
    assert "alpha.agents.middlewares.title_middleware" in title_importers, f"title_config's live importers changed to {title_importers}, so the accessor channel is no longer discriminating a wired subsystem"
    assert not live_non_declaring_importers("alpha.config.self_tuning.config"), (
        "something now imports the self-tuning protocol config for something other than declaring it, so the self_tuning flags are no longer inert. Remove those allowlist entries in this change."
    )
    assert not live_non_declaring_importers("alpha.memory.fabric.config"), "something now uses the memory fabric config for something other than declaring it, so memory.fabric is no longer inert. Remove that allowlist entry in this change."


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(stream=None) -> None:
    """Print the current state of both gates, for the non-blocking CI report step."""
    out = stream if stream is not None else sys.stdout
    fields = declared_fields()
    inert_a = declaration_only_keys()
    inert_b = unreachable_flags()
    print("=== inert-declaration gates ===", file=out)
    print(f"AppConfig-reachable fields: {len(fields)}", file=out)
    print(f'  exempt as an extra="allow" operator-kwarg bag: {len(kwarg_bag_paths())}', file=out)
    print(f"  gated by Gate A: {len(fields) - len(kwarg_bag_paths())}", file=out)
    print(f"Gate A - declared, read nowhere outside their own module: {len(inert_a)}", file=out)
    for key in sorted(inert_a):
        mark = "recorded" if key in {e["target"] for e in allowlist()["declaration_only_keys"]} else "NEW"
        print(f"  [{mark}] {key}  (declared by {inert_a[key]})", file=out)
    print(f"Gate B - enabled-shaped flags with no reachable entry point: {len(inert_b)}", file=out)
    for key in sorted(inert_b):
        mark = "recorded" if key in {e["target"] for e in allowlist()["unreachable_flags"]} else "NEW"
        print(f"  [{mark}] {key}  ({inert_b[key]})", file=out)
    print(f"Known blind spots (inert per audit, not provable by the gate): {len(allowlist()['known_blind_spots'])}", file=out)
    for entry in allowlist()["known_blind_spots"]:
        print(f"  [blind] {entry['target']}", file=out)
    print(
        f"Recorded in {ALLOWLIST_PATH.name}: {len(allowlist()['declaration_only_keys'])} declaration-only, "
        f"{len(allowlist()['unreachable_flags'])} unreachable, {len(allowlist()['known_blind_spots'])} blind spots, "
        f"{len(allowlist()['reported_defects'])} reported-not-fixed",
        file=out,
    )


if __name__ == "__main__":  # pragma: no cover
    report()
