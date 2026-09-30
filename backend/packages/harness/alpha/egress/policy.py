"""Egress routing policy and browser-profile references.

Two gaps this closes for "operate any website like a real user":

1. **Egress routing.** Some sites block datacenter IPs. An :class:`EgressPolicy`
   maps domains to a route (direct / residential proxy / datacenter proxy) with
   first-match-wins rules and a default fallback.
2. **Browser-profile import.** A :class:`BrowserProfileRef` records a *reference*
   to an authenticated Chrome profile (path + label + which domains it is logged
   into). It deliberately has **no field for cookies, tokens or passwords** —
   credentials stay in the browser profile on disk, never in Alpha state. This
   keeps the trust boundary explicit: Alpha routes and points, it does not hoard
   secrets.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final, Mapping

EGRESS_STORE_SCHEMA_VERSION: Final = 1

#: Keys that would imply inline credential storage. Presence is refused loudly.
_FORBIDDEN_SECRET_KEYS: Final = frozenset({"cookies", "password", "passwd", "token", "access_token", "refresh_token", "secret", "api_key", "authorization"})

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class EgressError(RuntimeError):
    """Base class for egress-policy failures."""


class EgressValidationError(EgressError, ValueError):
    """A policy or profile reference is malformed."""


class EgressStoreUnreadable(EgressError):
    """The on-disk egress store exists but could not be read/validated."""


class EgressRoute(str, Enum):
    DIRECT = "direct"
    RESIDENTIAL_PROXY = "residential_proxy"
    DATACENTER_PROXY = "datacenter_proxy"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_domain(domain: str) -> str:
    clean = (domain or "").strip().lower()
    if not clean:
        raise EgressValidationError("domain must be non-empty")
    if not _DOMAIN_RE.match(clean):
        raise EgressValidationError(f"{domain!r} is not a valid domain")
    return clean


@dataclass(frozen=True)
class EgressRule:
    """A first-match-wins routing rule for a domain glob."""

    domain_pattern: str
    route: EgressRoute

    def __post_init__(self) -> None:
        if not isinstance(self.domain_pattern, str) or not self.domain_pattern.strip():
            raise EgressValidationError("domain_pattern must be a non-empty string")
        if not isinstance(self.route, EgressRoute):
            object.__setattr__(self, "route", EgressRoute(self.route))

    def matches(self, domain: str) -> bool:
        return fnmatch.fnmatchcase(domain.lower(), self.domain_pattern.lower())

    def to_dict(self) -> dict[str, Any]:
        return {"domain_pattern": self.domain_pattern, "route": self.route.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EgressRule:
        return cls(domain_pattern=data["domain_pattern"], route=EgressRoute(data["route"]))


@dataclass
class EgressPolicy:
    """Ordered rules plus a default route."""

    default_route: EgressRoute = EgressRoute.DIRECT
    rules: list[EgressRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.default_route, EgressRoute):
            self.default_route = EgressRoute(self.default_route)

    def add_rule(self, domain_pattern: str, route: EgressRoute | str, *, prepend: bool = False) -> EgressRule:
        rule = EgressRule(domain_pattern, route if isinstance(route, EgressRoute) else EgressRoute(route))
        if prepend:
            self.rules.insert(0, rule)
        else:
            self.rules.append(rule)
        return rule

    def resolve(self, domain: str) -> EgressRoute:
        """Return the route for a domain: first matching rule, else the default."""
        clean = (domain or "").strip().lower()
        for rule in self.rules:
            if rule.matches(clean):
                return rule.route
        return self.default_route

    def to_dict(self) -> dict[str, Any]:
        return {"default_route": self.default_route.value, "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EgressPolicy:
        return cls(
            default_route=EgressRoute(data.get("default_route", "direct")),
            rules=[EgressRule.from_dict(r) for r in data.get("rules", [])],
        )


@dataclass
class BrowserProfileRef:
    """A pointer to an authenticated browser profile. Holds NO credentials."""

    profile_id: str
    label: str = ""
    source_path: str = ""
    imported_at: str = field(default_factory=_now)
    authenticated_domains: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise EgressValidationError("profile_id must be a non-empty string")
        self.profile_id = self.profile_id.strip()
        self.authenticated_domains = [_validate_domain(d) for d in self.authenticated_domains]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BrowserProfileRef:
        payload = dict(data)
        _reject_inline_secrets(payload)
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(payload) - known)
        if unknown:
            raise EgressValidationError(f"profile reference has unknown field(s): {unknown}")
        if "profile_id" not in payload:
            raise EgressValidationError("profile reference is missing 'profile_id'")
        return cls(**payload)


def _reject_inline_secrets(mapping: Mapping[str, Any]) -> None:
    """Refuse any mapping that tries to smuggle credential material."""
    for key, value in mapping.items():
        if str(key).strip().lower() in _FORBIDDEN_SECRET_KEYS:
            raise EgressValidationError(f"refusing to store credential field {key!r}: profiles are references, not secret stores")
        if isinstance(value, Mapping):
            _reject_inline_secrets(value)


def import_profile(profile_id: str, *, label: str = "", source_path: str = "", authenticated_domains: list[str] | None = None) -> BrowserProfileRef:
    """Create a validated profile reference.

    ``source_path`` must be a filesystem path reference (a string), never an
    inline credential blob. A JSON-looking blob is refused.
    """
    if source_path:
        stripped = source_path.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            raise EgressValidationError("source_path must be a filesystem path reference, not an inline JSON/credential blob")
        if any(sep in stripped for sep in ("cookie", "token", "password", "sessionid")):
            raise EgressValidationError("source_path looks like it embeds credential material; pass only the profile directory path")
    return BrowserProfileRef(
        profile_id=profile_id,
        label=label,
        source_path=source_path,
        authenticated_domains=list(authenticated_domains or []),
    )


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "egress" / "egress.json"
    except Exception:
        return Path.cwd() / ".alpha" / "egress" / "egress.json"


class EgressStore:
    """Durable store for the egress policy and imported profile references."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self.storage_path = Path(storage_path).resolve() if storage_path else _default_store_path()
        self._lock = threading.RLock()
        self._policy = EgressPolicy()
        self._profiles: dict[str, BrowserProfileRef] = {}
        self._load()

    def store_label(self) -> str:
        return str(self.storage_path)

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise EgressStoreUnreadable(f"egress store {self.store_label()} exists but could not be read: {exc}.") from exc
        if not raw.strip():
            raise EgressStoreUnreadable(f"egress store {self.store_label()} is empty.")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EgressStoreUnreadable(f"egress store {self.store_label()} is not valid JSON: {exc}.") from exc
        if not isinstance(data, dict):
            raise EgressStoreUnreadable(f"egress store {self.store_label()} must contain an object.")
        if data.get("schema_version") != EGRESS_STORE_SCHEMA_VERSION:
            raise EgressStoreUnreadable(f"egress store {self.store_label()} has schema_version {data.get('schema_version')!r}; expected {EGRESS_STORE_SCHEMA_VERSION}.")
        try:
            self._policy = EgressPolicy.from_dict(data.get("policy", {}))
            for item in data.get("profiles", []):
                ref = BrowserProfileRef.from_dict(item)
                self._profiles[ref.profile_id] = ref
        except EgressValidationError as exc:
            raise EgressStoreUnreadable(f"egress store {self.store_label()} failed validation: {exc}.") from exc

    def _save(self) -> None:
        payload = {
            "schema_version": EGRESS_STORE_SCHEMA_VERSION,
            "policy": self._policy.to_dict(),
            "profiles": [p.to_dict() for p in self._profiles.values()],
            "updated_at": _now(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.storage_path)
        except OSError as exc:
            raise EgressStoreUnreadable(f"could not persist egress store {self.store_label()}: {exc}") from exc

    # -- policy -----------------------------------------------------------
    def policy(self) -> EgressPolicy:
        with self._lock:
            return self._policy

    def set_default_route(self, route: EgressRoute | str) -> EgressPolicy:
        with self._lock:
            self._policy.default_route = route if isinstance(route, EgressRoute) else EgressRoute(route)
            self._save()
            return self._policy

    def add_rule(self, domain_pattern: str, route: EgressRoute | str, *, prepend: bool = False) -> EgressPolicy:
        with self._lock:
            self._policy.add_rule(domain_pattern, route, prepend=prepend)
            self._save()
            return self._policy

    def resolve(self, domain: str) -> EgressRoute:
        with self._lock:
            return self._policy.resolve(domain)

    # -- profiles ---------------------------------------------------------
    def add_profile(self, ref: BrowserProfileRef) -> BrowserProfileRef:
        with self._lock:
            self._profiles[ref.profile_id] = ref
            self._save()
            return ref

    def get_profile(self, profile_id: str) -> BrowserProfileRef | None:
        with self._lock:
            return self._profiles.get((profile_id or "").strip())

    def list_profiles(self) -> list[BrowserProfileRef]:
        with self._lock:
            return sorted(self._profiles.values(), key=lambda p: p.profile_id)


__all__ = [
    "BrowserProfileRef",
    "EGRESS_STORE_SCHEMA_VERSION",
    "EgressError",
    "EgressPolicy",
    "EgressRoute",
    "EgressRule",
    "EgressStore",
    "EgressStoreUnreadable",
    "EgressValidationError",
    "import_profile",
]
