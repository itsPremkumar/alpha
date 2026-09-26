"""Persistent local identity and pairing secret for the Alpha peer network."""

from __future__ import annotations

import json
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import PeerCard, new_id, utc_now
from .storage import token_digest

DEFAULT_CAPABILITIES = ("chat", "task_delegation", "code", "research", "review")
DEFAULT_SKILLS = (
    {"id": "chat", "name": "Peer chat", "description": "Exchange bounded text messages with another Alpha."},
    {"id": "task_delegation", "name": "Task delegation", "description": "Request and return structured task results."},
    {"id": "research", "name": "Research", "description": "Collaborate on research and synthesis."},
    {"id": "review", "name": "Review", "description": "Ask another Alpha for an independent review."},
)


def _default_host() -> str:
    configured = os.getenv("ALPHA_PEER_NETWORK_ADVERTISED_HOST", "").strip()
    if configured:
        return configured
    try:
        address = socket.gethostbyname(socket.gethostname())
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    return "127.0.0.1"


def _default_http_port() -> int:
    raw = os.getenv("ALPHA_PEER_NETWORK_HTTP_PORT", "8001").strip()
    try:
        return max(1, min(int(raw), 65535))
    except ValueError:
        return 8001


def _default_ws_port() -> int:
    raw = os.getenv("ALPHA_PEER_NETWORK_WS_PORT", "").strip()
    if raw:
        try:
            return max(1, min(int(raw), 65535))
        except ValueError:
            pass
    return _default_http_port()


def _default_version() -> str:
    try:
        from importlib.metadata import version as package_version

        for distribution in ("agent-workspace-harness", "alpha"):
            try:
                return package_version(distribution)
            except Exception:
                continue
    except Exception:
        pass
    return os.getenv("ALPHA_PEER_NETWORK_VERSION", "unknown")


def _default_base_url() -> str:
    configured = os.getenv("ALPHA_PEER_NETWORK_ADVERTISED_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return f"http://{_default_host()}:{_default_http_port()}"


def _websocket_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/api/peer-network"):
        path = path[: -len("/api/peer-network")]
    return urlunsplit((scheme, parsed.netloc, f"{path}/api/peer-network/ws", "", ""))


@dataclass(slots=True)
class LocalIdentity:
    """The installation-level identity exposed in an Agent Card."""

    agent_id: str
    name: str
    description: str
    version: str
    created_at: str
    pairing_code: str
    storage_path: Path

    @classmethod
    def load_or_create(
        cls,
        directory: str | Path,
        *,
        name: str = "Alpha",
        description: str = "Alpha autonomous peer",
        version: str | None = None,
    ) -> LocalIdentity:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        effective_version = version or _default_version()
        path = directory / "identity.json"
        if path.exists():
            try:
                raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                agent_id = str(raw["agent_id"])
                created_at = str(raw.get("created_at") or utc_now())
                pairing_code = str(raw.get("pairing_code") or "")
                if not pairing_code:
                    pairing_code = secrets.token_urlsafe(32)
                    raw["pairing_code"] = pairing_code
                    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return cls(
                    agent_id=agent_id,
                    name=str(raw.get("name") or name),
                    description=str(raw.get("description") or description),
                    version=effective_version,
                    created_at=created_at,
                    pairing_code=pairing_code,
                    storage_path=path,
                )
            except (OSError, ValueError, KeyError, TypeError):
                # Do not destroy an unreadable identity. Preserve it for
                # operator recovery, then mint a fresh id rather than reusing a
                # potentially compromised one.
                try:
                    path.replace(path.with_suffix(".invalid.json"))
                except OSError:
                    pass

        identity = cls(
            agent_id=new_id("alpha"),
            name=name,
            description=description,
            version=effective_version,
            created_at=utc_now(),
            pairing_code=secrets.token_urlsafe(32),
            storage_path=path,
        )
        path.write_text(
            json.dumps(
                {
                    "agent_id": identity.agent_id,
                    "name": identity.name,
                    "description": identity.description,
                    "version": identity.version,
                    "created_at": identity.created_at,
                    "pairing_code": identity.pairing_code,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return identity

    def rotate_pairing_code(self) -> str:
        self.pairing_code = secrets.token_urlsafe(32)
        raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        raw["pairing_code"] = self.pairing_code
        self.storage_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(self.storage_path, 0o600)
        except OSError:
            pass
        return self.pairing_code

    def card(self) -> PeerCard:
        base = _default_base_url()
        return PeerCard(
            agent_id=self.agent_id,
            name=self.name,
            description=self.description,
            version=self.version,
            url=base,
            websocket_url=_websocket_url(base),
            preferred_transport="WebSocket",
            capabilities=list(DEFAULT_CAPABILITIES),
            skills=[dict(skill) for skill in DEFAULT_SKILLS],
            supports=["http", "websocket", "udp-discovery", "sqlite"],
            pairing_required=True,
            issued_at=utc_now(),
            alpha_instance="alpha",
        )

    def public_dict(self, *, include_pairing_code: bool = False) -> dict[str, Any]:
        result = {
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "created_at": self.created_at,
            "card": self.card().to_dict(),
            "pairing_code_hash": token_digest(self.pairing_code)[:16],
        }
        if include_pairing_code:
            result["pairing_code"] = self.pairing_code
        return result


__all__ = ["DEFAULT_CAPABILITIES", "DEFAULT_SKILLS", "LocalIdentity"]
