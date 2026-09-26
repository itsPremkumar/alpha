"""Optional GitHub-backed peer rendezvous.

This adapter is deliberately read/write opt-in.  It is useful for a small free
public repository used as a bootstrap directory, but it is not used for normal
message transport: a public repository must never contain bearer pairing
credentials or private message bodies.  Direct HTTP/WebSocket delivery remains
the normal path.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .models import PeerCard

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_DIRECTORY_ITEMS = 256
_MAX_RECORD_BYTES = 256 * 1024


class GitHubRendezvousError(RuntimeError):
    """The configured free rendezvous repository could not be used."""


@dataclass(slots=True)
class GitHubRendezvous:
    repo: str = ""
    branch: str = "main"
    directory: str = ".alpha-network/peers"
    token: str | None = None
    timeout_seconds: float = 8.0
    last_error: str | None = None

    @classmethod
    def from_env(cls) -> GitHubRendezvous:
        repo = os.getenv("ALPHA_PEER_NETWORK_GITHUB_REPO", "").strip()
        branch = os.getenv("ALPHA_PEER_NETWORK_GITHUB_BRANCH", "main").strip() or "main"
        directory = os.getenv("ALPHA_PEER_NETWORK_GITHUB_DIRECTORY", ".alpha-network/peers").strip() or ".alpha-network/peers"
        token = os.getenv("ALPHA_PEER_NETWORK_GITHUB_TOKEN", "").strip() or None
        return cls(repo=repo, branch=branch, directory=directory, token=token)

    @property
    def configured(self) -> bool:
        return bool(_REPO_RE.fullmatch(self.repo))

    @property
    def writable(self) -> bool:
        return self.configured and bool(self.token)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.configured,
            "writable": self.writable,
            "running": False,
            "repo": self.repo if self.configured else None,
            "branch": self.branch if self.configured else None,
            "last_error": self.last_error or (None if self.configured else "ALPHA_PEER_NETWORK_GITHUB_REPO is not configured"),
        }

    def _validate_config(self) -> None:
        if not self.configured:
            raise GitHubRendezvousError("GitHub rendezvous is not configured")
        self._safe_api_path(self.directory)

    @staticmethod
    def _safe_api_path(path: str) -> str:
        """Return a validated relative API path, or refuse.

        Every request path — the configured directory, the published card path,
        and each ``path`` a directory listing hands back — goes through here.
        A listing entry is attacker-influenced (anyone who can commit to the
        rendezvous repository chooses it), and an unchecked ``../..`` in it
        would be resolved against the API base by the HTTP client, turning a
        read-only rendezvous into an arbitrary-repository request.
        """

        candidate = str(path).replace("\\", "/").strip()
        if not candidate or candidate.startswith("/"):
            raise GitHubRendezvousError("GitHub rendezvous path must be a safe relative path")
        if ":" in candidate.split("/")[0]:
            raise GitHubRendezvousError("GitHub rendezvous path must be a safe relative path")
        parts = [part for part in candidate.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." for part in parts):
            raise GitHubRendezvousError("GitHub rendezvous path must not traverse outside the configured directory")
        return "/".join(parts)

    def _headers(self, *, write: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if write:
            if not self.token:
                raise GitHubRendezvousError("GitHub rendezvous writes require ALPHA_PEER_NETWORK_GITHUB_TOKEN")
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _url(self, path: str) -> str:
        self._validate_config()
        safe_path = self._safe_api_path(path)
        encoded_repo = "/".join(quote(part, safe="") for part in self.repo.split("/"))
        encoded_path = "/".join(quote(part, safe="") for part in safe_path.split("/"))
        return f"https://api.github.com/repos/{encoded_repo}/contents/{encoded_path}?ref={quote(self.branch, safe='')}"

    async def _request(self, method: str, path: str, *, write: bool = False, payload: dict[str, Any] | None = None) -> Any:
        self._validate_config()
        url = self._url(path)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, trust_env=False) as client:
                response = await client.request(method, url, headers=self._headers(write=write), json=payload)
        except httpx.HTTPError as exc:
            self.last_error = str(exc)
            raise GitHubRendezvousError(f"GitHub rendezvous request failed: {exc}") from exc
        if len(response.content) > _MAX_RECORD_BYTES * 4:
            self.last_error = "GitHub rendezvous response exceeded the size limit"
            raise GitHubRendezvousError(self.last_error)
        if response.status_code == 404 and not write:
            return None
        if response.status_code >= 400:
            detail = response.text[:300]
            self.last_error = f"GitHub rendezvous returned HTTP {response.status_code}: {detail}"
            raise GitHubRendezvousError(self.last_error)
        try:
            return response.json()
        except ValueError as exc:
            self.last_error = "GitHub rendezvous returned invalid JSON"
            raise GitHubRendezvousError(self.last_error) from exc

    async def list_cards(self) -> list[PeerCard]:
        self._validate_config()
        listing = await self._request("GET", self.directory)
        if listing is None:
            return []
        if not isinstance(listing, list):
            raise GitHubRendezvousError("GitHub peer directory response was not a list")
        cards: list[PeerCard] = []
        for item in listing[:_MAX_DIRECTORY_ITEMS]:
            if not isinstance(item, dict) or item.get("type") != "file" or not str(item.get("name", "")).endswith(".json"):
                continue
            path = str(item.get("path") or "")
            if not path:
                continue
            try:
                record = await self._request("GET", path)
            except GitHubRendezvousError as exc:
                # A hostile or malformed listing entry must not abort the whole
                # directory read, and must never be requested as-is.
                self.last_error = str(exc)
                continue
            if not isinstance(record, dict):
                continue
            content = record.get("content")
            if not isinstance(content, str):
                continue
            try:
                raw_bytes = base64.b64decode("".join(content.split()), validate=True)
                if len(raw_bytes) > _MAX_RECORD_BYTES:
                    continue
                raw = json.loads(raw_bytes.decode("utf-8"))
                cards.append(PeerCard.model_validate(raw))
            except (ValueError, TypeError, UnicodeDecodeError, binascii.Error):
                self.last_error = "Skipped an invalid GitHub peer card"
                continue
        self.last_error = None
        return cards

    async def publish_card(self, card: PeerCard) -> dict[str, Any]:
        self._validate_config()
        if not self.writable:
            raise GitHubRendezvousError("GitHub rendezvous writes require a configured repository token")
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", card.agent_id)
        path = self._safe_api_path(f"{self.directory.rstrip('/')}/{safe_id}.json")
        encoded = base64.b64encode(json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True).encode("utf-8")).decode("ascii")
        existing = await self._request("GET", path)
        payload: dict[str, Any] = {
            "message": f"Publish Alpha peer card {card.agent_id}",
            "content": encoded,
            "branch": self.branch,
        }
        if isinstance(existing, dict) and isinstance(existing.get("sha"), str):
            payload["sha"] = existing["sha"]
        result = await self._request("PUT", path, write=True, payload=payload)
        self.last_error = None
        return result if isinstance(result, dict) else {"published": True, "path": path}


__all__ = ["GitHubRendezvous", "GitHubRendezvousError"]
