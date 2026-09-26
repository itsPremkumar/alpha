"""Registry accessors used by ``__init__`` and by the drift gate."""

from __future__ import annotations

from typing import Callable, Iterable

from .registry import REGISTRY, Contract, UnregisteredContract, contract_fields
from .registry import register as _register
from .registry import registered as _registered
from .registry import render_manifest as _render_manifest
from .registry import render_typescript as _render_typescript
from .registry import require as _require

_BOOTSTRAPPED = False


def registered() -> tuple[Contract, ...]:
    """Every registered contract, bootstrapping on first use."""
    if not _BOOTSTRAPPED:
        registry_bootstrap(lambda: ())
    return _registered()


def render_typescript(contracts: Iterable[Contract] | None = None) -> str:
    return _render_typescript(list(contracts) if contracts is not None else registered())


def render_manifest(contracts: Iterable[Contract] | None = None) -> dict:
    return _render_manifest(list(contracts) if contracts is not None else registered())


def registry_bootstrap(hook: Callable[[], object]) -> None:
    """Run *hook* once, on first registry access.

    The hook is a callable rather than an import so this module has no import
    cycle with the models it registers.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True
    hook()
