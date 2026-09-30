"""Curated connector marketplace (catalog + durable install/health state).

- :class:`~alpha.connectors.catalog.ConnectorCatalog` — searchable first-party catalog
- :class:`~alpha.connectors.state.ConnectorStore` — install / enable / health registry
"""

from __future__ import annotations

from alpha.connectors.catalog import (
    AuthKind,
    BUILTIN_CONNECTORS,
    ConnectorCatalog,
    ConnectorCategory,
    ConnectorSpec,
)
from alpha.connectors.state import (
    CONNECTOR_STORE_SCHEMA_VERSION,
    ConnectorError,
    ConnectorHealth,
    ConnectorInstallState,
    ConnectorStore,
    ConnectorStoreUnreadable,
)

__all__ = [
    "AuthKind",
    "BUILTIN_CONNECTORS",
    "CONNECTOR_STORE_SCHEMA_VERSION",
    "ConnectorCatalog",
    "ConnectorCategory",
    "ConnectorError",
    "ConnectorHealth",
    "ConnectorInstallState",
    "ConnectorSpec",
    "ConnectorStore",
    "ConnectorStoreUnreadable",
]
