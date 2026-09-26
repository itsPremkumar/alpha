"""Host configuration: the shared schema plus the resolvers for its files.

Exports are installed lazily (:pep:`562`) so that importing one leaf of this
package does not run the whole schema. ``AppConfig`` is the largest module here
and it imports roughly fifty sibling config models plus the extension loader,
so a caller that only wants a single typed sub-config -- or a memory subsystem
config that must be importable by the shared schema without a cycle -- used to
pay for all of it.

No public surface changes. ``__all__`` keeps the same names in the same order,
so ``from alpha.config import get_app_config`` and every other documented
symbol resolve exactly as before, now on first access instead of at import.
``from alpha.config import paths`` (and every other sibling) is unaffected:
CPython's own from-list handling imports a submodule when the attribute is
absent, so it never depended on this package's eager imports binding it.

Submodule imports (``from alpha.config import app_config``, ``import
alpha.config.memory_config``) are likewise unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alpha.memory._lazy_exports import install_lazy_exports

_EXPORTS = {
    "ExtensionsConfig": "extensions_config",
    "get_app_config": "app_config",
    "get_enabled_tracing_providers": "tracing_config",
    "get_explicitly_enabled_tracing_providers": "tracing_config",
    "get_extensions_config": "extensions_config",
    "get_memory_config": "memory_config",
    "get_paths": "paths",
    "get_tracing_config": "tracing_config",
    "is_monocle_tracing_enabled": "tracing_config",
    "is_tracing_enabled": "tracing_config",
    "LoopDetectionConfig": "loop_detection_config",
    "MemoryConfig": "memory_config",
    "Paths": "paths",
    "SkillEvolutionConfig": "skill_evolution_config",
    "SkillsConfig": "skills_config",
    "validate_enabled_tracing_providers": "tracing_config",
}

#: The package's documented export list, in its original order.
_PUBLIC = (
    "get_app_config",
    "SkillEvolutionConfig",
    "Paths",
    "get_paths",
    "SkillsConfig",
    "ExtensionsConfig",
    "get_extensions_config",
    "LoopDetectionConfig",
    "MemoryConfig",
    "get_memory_config",
    "get_tracing_config",
    "get_explicitly_enabled_tracing_providers",
    "get_enabled_tracing_providers",
    "is_monocle_tracing_enabled",
    "is_tracing_enabled",
    "validate_enabled_tracing_providers",
)

if TYPE_CHECKING:  # pragma: no cover - import-time-free type surface
    # Explicit ``X as X`` re-exports: the runtime surface is installed lazily
    # below, so this block exists for type checkers and IDEs only.
    from alpha.config.app_config import get_app_config as get_app_config
    from alpha.config.extensions_config import ExtensionsConfig as ExtensionsConfig
    from alpha.config.extensions_config import get_extensions_config as get_extensions_config
    from alpha.config.loop_detection_config import LoopDetectionConfig as LoopDetectionConfig
    from alpha.config.memory_config import MemoryConfig as MemoryConfig
    from alpha.config.memory_config import get_memory_config as get_memory_config
    from alpha.config.paths import Paths as Paths
    from alpha.config.paths import get_paths as get_paths
    from alpha.config.skill_evolution_config import SkillEvolutionConfig as SkillEvolutionConfig
    from alpha.config.skills_config import SkillsConfig as SkillsConfig
    from alpha.config.tracing_config import (
        get_enabled_tracing_providers as get_enabled_tracing_providers,
    )
    from alpha.config.tracing_config import (
        get_explicitly_enabled_tracing_providers as get_explicitly_enabled_tracing_providers,
    )
    from alpha.config.tracing_config import get_tracing_config as get_tracing_config
    from alpha.config.tracing_config import (
        is_monocle_tracing_enabled as is_monocle_tracing_enabled,
    )
    from alpha.config.tracing_config import is_tracing_enabled as is_tracing_enabled
    from alpha.config.tracing_config import (
        validate_enabled_tracing_providers as validate_enabled_tracing_providers,
    )

install_lazy_exports(__name__, _EXPORTS, public=_PUBLIC)
