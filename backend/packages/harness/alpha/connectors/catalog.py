"""Curated connector catalog (the "marketplace" surface).

Alpha already has the *plumbing* to talk to external systems (MCP servers,
``alpha.extensions``, managed integration packs such as Lark). What it lacked
was a curated, discoverable catalog of first-party connectors — a marketplace
entry point that lists which apps Alpha can operate, what auth each needs, and
what actions each exposes.

This module owns the read-only catalog. Install/enable/health state lives in
:mod:`alpha.connectors.state`. Neither module performs network I/O or stores
credentials; a connector is a *declaration*, not a live session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class AuthKind(str, Enum):
    """How a connector authenticates."""

    NONE = "none"
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    BROWSER_PROFILE = "browser_profile"


class ConnectorCategory(str, Enum):
    """Functional grouping used to browse the marketplace."""

    COMMUNICATION = "communication"
    CALENDAR = "calendar"
    STORAGE = "storage"
    SOCIAL = "social"
    DEVTOOLS = "devtools"
    CRM = "crm"
    FINANCE = "finance"


@dataclass(frozen=True)
class ConnectorSpec:
    """A declaration of one connectable app."""

    id: str
    name: str
    vendor: str
    category: ConnectorCategory
    auth: AuthKind
    actions: tuple[str, ...] = ()
    description: str = ""
    #: True for connectors shipped first-party by Alpha.
    first_party: bool = True

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError("connector id must be a non-empty string")
        if not self.name:
            raise ValueError("connector name must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        data["auth"] = self.auth.value
        data["actions"] = list(self.actions)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ConnectorSpec:
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"connector spec has unknown field(s): {unknown}")
        payload = dict(data)
        payload["category"] = ConnectorCategory(payload["category"])
        payload["auth"] = AuthKind(payload["auth"])
        payload["actions"] = tuple(payload.get("actions", ()))
        return cls(**payload)


#: First-party connectors shipped with Alpha. Mirrors the apps Grok Bot
#: advertises, plus Alpha's existing strengths (devtools, crm).
BUILTIN_CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec("gmail", "Gmail", "Google", ConnectorCategory.COMMUNICATION, AuthKind.OAUTH2,
                  ("search", "read", "draft", "send", "label", "archive"),
                  "Read, search, draft and send email."),
    ConnectorSpec("google_calendar", "Google Calendar", "Google", ConnectorCategory.CALENDAR, AuthKind.OAUTH2,
                  ("list_events", "find_free_slot", "create_event", "update_event", "respond_invite"),
                  "Query availability and manage events."),
    ConnectorSpec("outlook", "Outlook Mail & Calendar", "Microsoft", ConnectorCategory.COMMUNICATION, AuthKind.OAUTH2,
                  ("search", "read", "draft", "send", "list_events", "create_event"),
                  "Microsoft 365 mail and calendar."),
    ConnectorSpec("onedrive", "OneDrive", "Microsoft", ConnectorCategory.STORAGE, AuthKind.OAUTH2,
                  ("list", "read", "write", "share"),
                  "Read and write files in OneDrive."),
    ConnectorSpec("x", "X", "xAI", ConnectorCategory.SOCIAL, AuthKind.OAUTH2,
                  ("post", "read_timeline", "search"),
                  "Post and read on X."),
    ConnectorSpec("slack", "Slack", "Salesforce", ConnectorCategory.COMMUNICATION, AuthKind.OAUTH2,
                  ("post_message", "read_channel", "search"),
                  "Team messaging."),
    ConnectorSpec("github", "GitHub", "Microsoft", ConnectorCategory.DEVTOOLS, AuthKind.OAUTH2,
                  ("list_repos", "read_file", "open_pr", "comment", "create_issue"),
                  "Repositories, issues and pull requests."),
    ConnectorSpec("notion", "Notion", "Notion", ConnectorCategory.STORAGE, AuthKind.OAUTH2,
                  ("search", "read_page", "append_block", "create_page"),
                  "Docs and databases."),
    ConnectorSpec("hubspot", "HubSpot", "HubSpot", ConnectorCategory.CRM, AuthKind.OAUTH2,
                  ("list_contacts", "read_deal", "create_note", "update_stage"),
                  "CRM contacts, deals and notes."),
    ConnectorSpec("composio_bridge", "Composio Bridge", "Composio", ConnectorCategory.DEVTOOLS, AuthKind.API_KEY,
                  ("discover_tools", "invoke_tool"),
                  "Umbrella connector exposing 1000+ third-party apps.", first_party=False),
)


class ConnectorCatalog:
    """Read-only, searchable view over the connector catalog."""

    def __init__(self, connectors: tuple[ConnectorSpec, ...] | None = None) -> None:
        self._by_id: dict[str, ConnectorSpec] = {c.id: c for c in (connectors if connectors is not None else BUILTIN_CONNECTORS)}

    def get(self, connector_id: str) -> ConnectorSpec | None:
        return self._by_id.get((connector_id or "").strip().lower())

    def list(self, *, category: ConnectorCategory | str | None = None) -> list[ConnectorSpec]:
        items = list(self._by_id.values())
        if category is not None:
            cat = ConnectorCategory(category) if not isinstance(category, ConnectorCategory) else category
            items = [c for c in items if c.category == cat]
        return sorted(items, key=lambda c: c.name.lower())

    def search(self, query: str) -> list[ConnectorSpec]:
        """Case-insensitive substring match over id, name, vendor and actions."""
        needle = (query or "").strip().lower()
        if not needle:
            return self.list()
        hits: list[ConnectorSpec] = []
        for spec in self._by_id.values():
            haystack = " ".join((spec.id, spec.name, spec.vendor, spec.description, *spec.actions)).lower()
            if needle in haystack:
                hits.append(spec)
        return sorted(hits, key=lambda c: c.name.lower())

    def ids(self) -> list[str]:
        return sorted(self._by_id)


__all__ = [
    "AuthKind",
    "BUILTIN_CONNECTORS",
    "ConnectorCatalog",
    "ConnectorCategory",
    "ConnectorSpec",
]
