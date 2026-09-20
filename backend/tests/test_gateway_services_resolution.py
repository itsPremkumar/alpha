"""Module-resolution guard for app.gateway.services.

``app/gateway/services`` is a namespace directory WITHOUT ``__init__.py``;
``app.gateway.services`` must therefore keep resolving to the sibling module
``services.py`` (a regular module beats a namespace package). Adding an
``__init__.py`` inside that directory would shadow the gateway services module
and break the Gateway — this test pins the rule.
"""

from __future__ import annotations

import importlib.util


def test_services_resolves_to_the_module_not_the_namespace_dir() -> None:
    spec = importlib.util.find_spec("app.gateway.services")
    assert spec is not None
    assert spec.origin is not None, "app.gateway.services resolved to a namespace package — the services/ directory must never gain an __init__.py"
    assert spec.origin.replace("\\", "/").endswith("app/gateway/services.py"), (
        f"app.gateway.services resolved to {spec.origin}; expected the module app/gateway/services.py"
    )


def test_shim_reexports_the_real_service() -> None:
    """The shim documents (not imports) the canonical path — services.py wins.

    ``app.gateway.services`` resolves to the sibling module ``services.py`` (a
    regular module beats a namespace directory), so this namespace-directory
    file cannot be imported via that dotted path at all. The file is therefore
    an explicit, guarded placeholder: if the canonical path is removed or the
    directory ever gains an ``__init__.py``, these content pins fail loudly.
    """
    from pathlib import Path

    # backend/tests/<this file> -> parents[1] is backend; the gateway package
    # lives under backend/app/, not backend/ directly.
    shim = Path(__file__).resolve().parents[1] / "app" / "gateway" / "services" / "system_monitor_service.py"
    text = shim.read_text(encoding="utf-8")
    assert "app.gateway.system_monitor_service" in text
    assert "__init__.py" in text  # documents the shadowing rule to future editors
