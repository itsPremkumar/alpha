"""Lazy package exports for memory subsystems (import-cycle hygiene).

A memory subsystem package (``alpha/memory/<type>/``) may be imported by the
shared config schema (``alpha.config.memory_config``) so its typed settings can
be promoted into ``MemoryConfig``. If the package's ``__init__`` eagerly imports
its facade, store, and models, that chain reaches back into the config layer and
recreates the circular import this layout exists to prevent.

Installing lazy exports keeps the package import cheap and side-effect free: the
module's public names resolve to their submodules on first access
(:pep:`562`), so ``from alpha.memory.affective.config import AffectiveConfig``
loads only the config model while ``from alpha.memory.affective import
AffectiveMemory`` still works exactly as before.

Usage inside a package ``__init__``::

    from alpha.memory._lazy_exports import install_lazy_exports

    install_lazy_exports(
        __name__,
        {"AffectiveConfig": "config", "AffectiveMemory": "memory"},
        public=("AffectiveConfig", "AffectiveMemory"),
    )
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from importlib import import_module
from types import ModuleType
from typing import Any

__all__ = ["install_lazy_exports"]


def install_lazy_exports(
    module_name: str,
    exports: Mapping[str, str],
    *,
    public: Sequence[str] | None = None,
) -> ModuleType:
    """Attach :pep:`562` lazy attribute resolution to ``module_name``.

    Args:
        module_name: ``__name__`` of the calling package.
        exports: public attribute name -> submodule name that defines it.
        public: the package's ``__all__``; defaults to ``exports`` keys sorted.

    Returns:
        The package module, for convenience in the caller's ``__init__``.

    Raises:
        RuntimeError: if called twice for the same module (a double install
            would silently shadow a real attribute).
    """
    module = sys.modules.get(module_name)
    if module is None:  # pragma: no cover - only if called outside the package
        raise RuntimeError(f"module {module_name!r} is not initialized")
    if "__getattr__" in module.__dict__ and getattr(module.__dict__["__getattr__"], "_lazy_exports", False):
        raise RuntimeError(f"lazy exports already installed for {module_name!r}")
    table = dict(exports)
    names = tuple(public) if public is not None else tuple(sorted(table))

    def __getattr__(name: str) -> Any:
        submodule = table.get(name)
        if submodule is None:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        value = getattr(import_module(f".{submodule}", module_name), name)
        # Cache on the module so subsequent lookups skip __getattr__ entirely.
        module.__dict__[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted(set(module.__dict__) | set(table))

    __getattr__._lazy_exports = True  # type: ignore[attr-defined]
    module.__dict__["__getattr__"] = __getattr__
    module.__dict__["__dir__"] = __dir__
    module.__dict__["__all__"] = list(names)
    return module


def __getattr__(name: str) -> Any:
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
