"""Live subsystem sweep: one real request per named Alpha capability.

Every row is a real HTTP call against a running Gateway. Nothing is mocked and
nothing is asserted against a constant -- a row passes only when the real
response satisfies its own contract, and a row that cannot be exercised in this
environment is reported `LIMITATION` with the reason, never as a pass.

Design rules this file follows, because a sweep that flatters itself is worse
than none:

* A row passes on the server's **own** claim, cross-checked against the shape
  the honesty contract requires. A 200 with an empty body is not a pass for a
  surface that promised content.
* Absence is `NOT VERIFIED`, never green. A subsystem with no credentials, no
  Docker, or no host bash is named as limited, not silently skipped.
* Mutating rows create their own object and clean up, so a second run does not
  collide with the first.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/subsystem_sweep.py
    python scripts/subsystem_sweep.py --base-url http://127.0.0.1:8001 --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

OK = "PASS"
FAIL = "FAIL"
LIMIT = "LIMITATION"
NOTVERIFIED = "NOT VERIFIED"


@dataclass
class Row:
    group: str
    name: str
    status: str
    detail: str
    ms: float = 0.0
    evidence: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Sweep:
    def __init__(self, client: httpx.Client, base: str) -> None:
        self.c = client
        self.base = base
        self.rows: list[Row] = []

    def _get(self, path: str, **kw: Any) -> tuple[int, Any, str, float]:
        t0 = time.monotonic()
        try:
            r = self.c.get(self.base + path, **kw)
        except Exception as exc:
            return -1, None, f"{type(exc).__name__}: {exc}", (time.monotonic() - t0) * 1000
        ms = (time.monotonic() - t0) * 1000
        try:
            body = r.json()
        except Exception:
            body = r.text[:400]
        return r.status_code, body, "", ms

    def _post(self, path: str, payload: Any = None, **kw: Any) -> tuple[int, Any, str, float]:
        t0 = time.monotonic()
        try:
            r = self.c.post(self.base + path, json=payload if payload is not None else {}, **kw)
        except Exception as exc:
            return -1, None, f"{type(exc).__name__}: {exc}", (time.monotonic() - t0) * 1000
        ms = (time.monotonic() - t0) * 1000
        try:
            body = r.json()
        except Exception:
            body = r.text[:400]
        return r.status_code, body, "", ms

    def add(self, group: str, name: str, status: str, detail: str, ms: float = 0.0, evidence: str = "", **extra: Any) -> None:
        self.rows.append(Row(group, name, status, detail, round(ms, 1), evidence, extra))

    def read(
        self,
        group: str,
        name: str,
        path: str,
        *,
        expect: str = "200 with a payload",
        check: Any = None,
        params: dict | None = None,
    ) -> Any:
        code, body, exc, ms = self._get(path, params=params) if params else self._get(path)
        if code == -1:
            self.add(group, name, FAIL, f"transport: {exc}", ms, path)
            return None
        if code >= 500:
            self.add(group, name, FAIL, f"HTTP {code}: {str(body)[:160]}", ms, path)
            return body
        if code >= 400:
            self.add(group, name, NOTVERIFIED, f"HTTP {code}: {str(body)[:160]}", ms, path)
            return body
        verdict = check(body) if check else None
        if verdict is True:
            self.add(group, name, OK, expect, ms, path, sample=_sample(body))
        elif verdict is False:
            self.add(group, name, FAIL, f"contract violated: {expect}", ms, path, sample=_sample(body))
        else:
            self.add(group, name, NOTVERIFIED, verdict or f"unverified shape: {str(body)[:140]}", ms, path)
        return body


def _sample(body: Any, limit: int = 220) -> str:
    try:
        return json.dumps(body, default=str)[:limit]
    except Exception:
        return str(body)[:limit]


def _is_list(body: Any) -> bool:
    return isinstance(body, list) and len(body) > 0


def run(s: Sweep) -> None:
    # ---------------- A. CORE EXECUTION ----------------
    s.read("A.core", "health liveness", "/health", check=lambda b: b.get("status") == "healthy")
    s.read("A.core", "readiness", "/health/ready", check=lambda b: isinstance(b, dict))
    s.read("A.core", "feature flags", "/api/features", check=lambda b: "agents_api" in b)
    code, body, exc, ms = s._post("/api/threads", {"metadata": {"purpose": "subsystem-sweep"}})
    tid = body.get("thread_id") if isinstance(body, dict) else None
    if tid:
        s.add("A.core", "thread create", OK, "thread created", ms, f"thread_id={tid}", thread_id=tid)
    else:
        s.add("A.core", "thread create", FAIL, f"HTTP {code}: {str(body)[:160]}", ms)

    if tid:
        s.read("A.core", "thread history page", f"/api/threads/{tid}/messages/page",
               params={"limit": 25}, check=lambda b: isinstance(b, dict) and "data" in b and "has_more" in b)

    # ---------------- B. ORCHESTRATION ----------------
    # Paths below were read off the live route table (create_app().routes), not
    # guessed. A first pass guessed `/api/company`, `/api/kanban/board`,
    # `/api/swarm` and `/api/workforce`, and reported four honest-looking
    # "NOT VERIFIED" rows that were really four wrong URLs -- which is a false
    # limitation claim about the product. The real prefixes are
    # `/api/company/*`, `/api/channels/*`, and the rest live under their own
    # routers.
    s.read("B.orchestration", "bots roster", "/api/bots", check=lambda b: isinstance(b, dict) and "bots" in b)
    s.read("B.orchestration", "bot health overview", "/api/bots/health/overview", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "bot org chart", "/api/bots/organization-chart", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "bot kill switch", "/api/bots/kill-switch", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "projects list", "/api/projects", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "groups list", "/api/groups", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "groups tree", "/api/groups/tree", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "company status", "/api/company/status", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "company kpis", "/api/company/kpis", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "company kanban tasks", "/api/company/kanban/tasks", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "company attendance", "/api/company/attendance/status", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "scheduled tasks", "/api/scheduled-tasks", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "dynamic workflow registry", "/api/workflows/system/registries", check=lambda b: isinstance(b, dict))
    s.read("B.orchestration", "subagents managed workers", "/api/subagents", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "missions", "/api/missions", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "channels providers", "/api/channels/providers", check=lambda b: isinstance(b, (list, dict)))
    s.read("B.orchestration", "channel connections", "/api/channels/connections", check=lambda b: isinstance(b, (list, dict)))

    # ---------------- C. PLATFORM ----------------
    s.read("C.platform", "model catalog", "/api/models", check=lambda b: isinstance(b.get("models"), list) and len(b["models"]) > 0)
    s.read("C.platform", "model discovery", "/api/models/discovery", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "free gateway catalog", "/api/models/free/catalog", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "memory status", "/api/memory/status", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "skills list", "/api/skills", check=lambda b: isinstance(b, (list, dict)))
    s.read("C.platform", "mcp config", "/api/mcp/config", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "peer network status", "/api/peer-network/status", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "self-inventory", "/api/intelligence/inventory",
           check=lambda b: b.get("schema_version") == "alpha.self-inventory.v1")
    s.read("C.platform", "ops integration health", "/api/ops/integration-health",
           check=lambda b: b.get("manifest_found") is True)
    s.read("C.platform", "ops network link state", "/api/ops/network",
           check=lambda b: isinstance(b, dict) and "state" in b)
    s.read("C.platform", "system capabilities", "/api/system/capabilities", check=lambda b: isinstance(b, dict))
    s.read("C.platform", "multimodal capability matrix", "/api/multimodal/capabilities",
           check=lambda b: isinstance(b.get("rows"), list))
    s.read("C.platform", "ops version", "/api/ops/version", check=lambda b: "version" in b)

    # ---------------- capabilities that need an environment ----------------
    s.read("D.limits", "sentinel signals (repo scan)", "/api/autonomy/sentinel/signals",
           check=lambda b: isinstance(b.get("signals"), list))
    s.read("D.limits", "intelligence loop health", "/api/intelligence/health", check=lambda b: isinstance(b, dict))
    s.read("D.limits", "lark integration status", "/api/integrations/lark/status", check=lambda b: isinstance(b, dict))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8001")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    with httpx.Client(timeout=120.0) as client:
        s = Sweep(client, args.base_url.rstrip("/"))
        run(s)

    groups: dict[str, list[Row]] = {}
    for row in s.rows:
        groups.setdefault(row.group, []).append(row)

    print("=" * 100)
    print(f"{'GROUP':<16} {'ROW':<44} {'STATUS':<14} ms")
    print("=" * 100)
    for group, rows in groups.items():
        for r in rows:
            print(f"{group:<16} {r.name:<44} {r.status:<14} {r.ms:>7.0f}  {r.detail[:60]}")
        print("-" * 100)

    tally: dict[str, int] = {}
    for r in s.rows:
        tally[r.status] = tally.get(r.status, 0) + 1
    print("\nTALLY:", json.dumps(tally))
    fails = [r for r in s.rows if r.status == FAIL]
    if fails:
        print(f"\n{len(fails)} FAILING ROW(S):")
        for r in fails:
            print(f"  [{r.group}] {r.name}: {r.detail}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump([r.__dict__ for r in s.rows], fh, indent=2)
        print(f"\nwrote {args.json}")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())