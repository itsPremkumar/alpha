"""Local OpenAI-compatible endpoint support: health probes and model switching.

The model factory already accepts ``base_url`` for OpenAI-compatible
clients (Ollama, LM Studio, llama.cpp server, vLLM) — this module adds the
operational half: probe whether a local endpoint is alive and which models
it serves, guarded against SSRF (loopback by default plus an explicit
allowlist; never arbitrary outbound probes from an API parameter).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0


@dataclass
class LocalEndpointHealth:
    base_url: str
    reachable: bool
    models: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _host_allowed(host: str | None, *, allowlist: tuple[str, ...] = ()) -> bool:
    if not host:
        return False
    lowered = host.lower().split("%")[0]
    if lowered in ("localhost",) or lowered.endswith(".localhost"):
        return True
    if lowered in {a.lower() for a in allowlist}:
        return True
    try:
        return ipaddress.ip_address(lowered).is_loopback
    except ValueError:
        return False


def probe_openai_compatible(base_url: str, *, allowlist: tuple[str, ...] = (), timeout_seconds: float = PROBE_TIMEOUT_SECONDS) -> LocalEndpointHealth:
    """GET {base_url}/models. Refuses non-loopback hosts outside the allowlist."""
    cleaned = (base_url or "").strip().rstrip("/")
    try:
        host = urllib.parse.urlparse(cleaned).hostname if cleaned else None
    except ValueError:
        host = None
    if not cleaned or not _host_allowed(host, allowlist=allowlist):
        return LocalEndpointHealth(base_url=base_url, reachable=False, reason="refused: host is not loopback or allowlisted (SSRF guard).")
    try:
        request = urllib.request.Request(cleaned + "/models", method="GET")
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return LocalEndpointHealth(base_url=cleaned, reachable=False, reason=f"endpoint HTTP {exc.code}.")
    except (OSError, ValueError) as exc:
        return LocalEndpointHealth(base_url=cleaned, reachable=False, reason=f"endpoint unreachable: {exc}")
    models: list[str] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for entry in data:
            if isinstance(entry, dict) and entry.get("id"):
                models.append(str(entry["id"]))
    return LocalEndpointHealth(base_url=cleaned, reachable=True, models=models)
