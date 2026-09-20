"""Intentional re-export shim for the system monitor service.

The canonical implementation lives in ``app/gateway/system_monitor_service.py``.
This file was previously an empty rename leftover; it is kept (not deleted) as a
stable import path and documents the module-resolution rule:

``app/gateway/services`` is a namespace directory WITHOUT ``__init__.py``, so
``app.gateway.services`` resolves to the sibling module ``services.py`` — a
regular module beats a namespace package. Adding an ``__init__.py`` here would
shadow the gateway services module and break the Gateway; a guard test pins this.
"""

from app.gateway.system_monitor_service import SystemMonitorService as SystemMonitorService

__all__ = ["SystemMonitorService"]
