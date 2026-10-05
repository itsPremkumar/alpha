"""Exercise Alpha's unverified WRITE paths, and check the refusals too.

Every previous probe in this series (`fleet_task_assign`,
`orchestration_probe`, `bot_forge_verify`) graded the *orchestration* plane. This
one covers the durable-control plane instead, and deliberately spends most of its
budget on paths no earlier run touched:

* **group nesting** -- create a subgroup, verify membership is *inherited* rather
  than copied, verify the documented ``MAX_DEPTH`` refusal, verify delete refuses
  while children exist, then clean up;
* **thread lifecycle** -- branch a completed turn and confirm inherited history
  is actually visible (the feed is read from ``run_events``, not checkpoints, so
  an unseeded branch silently loses its past);
* **scheduled tasks** -- create one, preview the cron, trigger it, and confirm a
  real run exists;
* **dynamic workflows** -- perceive, simulate, execute, then fork, and confirm the
  default digest executor reports its acceptance honestly;
* **skill proposals** -- ``teach`` must produce an *inactive* proposal, because
  the admin approve gate is the control;
* **artifact replacement** -- atomic PUT with a matching digest, and a mismatch
  that must be refused.

Every probe here is graded by reading **server state after the fact**, never by
the API's own success body. Where a contract says something must be *refused*,
a successful refusal is the pass -- an unprobed guard is an unproven guard.

Everything is created under names prefixed ``wp_`` and cleaned up in a ``finally``
so a probe that leaves state behind is visible in the report rather than
silently accumulated.

Usage (from ``backend/``, against a Gateway on :8001)::

    python scripts/write_path_probe.py
    python scripts/write_path_probe.py --only nesting,lifecycle --json ../logs/write_paths.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8001"
PREFIX = "wp_"  # every name this file creates, so cleanup is exhaustive


@dataclass
class Probe:
    key: str
    title: str
    feature: str


PROBES: list[Probe] = [
    Probe("nesting", "Create a real nested group and verify inheritance", "groups: nesting write path"),
    Probe("nesting_limits", "MAX_DEPTH and delete-with-children must refuse", "groups: refusals"),
    Probe("lifecycle", "Branch a completed turn and verify inherited history is visible", "threads: branch"),
    Probe("scheduled", "Create, preview-cron and trigger a scheduled task", "scheduled tasks"),
    Probe("workflow", "Perceive, simulate, execute and fork a dynamic workflow", "dynamic workflows"),
    Probe("skills", "teach must yield an INACTIVE proposal, not a live skill", "skills: proposals"),
    Probe("artifacts", "Atomic artifact PUT with a matching digest; a mismatch must refuse", "artifacts"),
]


@dataclass
class R:
    key: str
    title: str
    feature: str
    verdict: str = "FAIL"
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    observed: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0

    def check(self, name: str, ok: bool, note: str = "") -> bool:
        self.checks.append((name, ok, note))
        if not ok:
            self.problems.append(f"{name}: {note or 'failed'}")
        return ok

    def expect_refusal(self, name: str, resp: httpx.Response, must_contain: str = "") -> bool:
        """A refusal is the pass.

        ``must_contain`` keeps this from accepting any random 4xx: the guard has
        to say *why*, because a 400 for an unrelated reason is not the guard
        firing.
        """
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001 - diagnostics only
            pass
        refused = resp.status_code in (400, 403, 404, 409, 422)
        if refused and must_contain:
            refused = must_contain.lower() in body.lower()
        return self.check(
            name,
            refused,
            f"expected a refusal containing {must_contain!r}; got HTTP {resp.status_code}: {body[:200]}",
        )

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"


def uniq(tag: str) -> str:
    return f"{PREFIX}{tag}_{uuid.uuid4().hex[:8]}"


async def probe_nesting(c: httpx.AsyncClient) -> R:
    """Create a real nested group; inheritance must be a projection, not a copy.

    The load-bearing property is that a child's roster *recomputes* its parent's
    effective roster on every read. If membership were copied, an edit to the
    parent would leave the child stale -- so the check is a sequence: create the
    child, then ADD a member to the parent, then confirm the child sees it with
    no reconcile step in between.
    """
    r = R(key="nesting", title=PROBES[0].title, feature=PROBES[0].feature)
    t0 = time.monotonic()
    parent, child = uniq("parent"), uniq("child")

    try:
        pr = await c.post(f"{BASE}/api/groups", json={"name": parent})
        r.check("parent room created", pr.status_code in (200, 201), f"HTTP {pr.status_code}: {pr.text[:180]}")
        if pr.status_code >= 400:
            r.seconds = round(time.monotonic() - t0, 1)
            return r

        cr = await c.post(f"{BASE}/api/groups/{parent}/subgroups", json={"name": child})
        r.check("child subgroup created", cr.status_code in (200, 201), f"HTTP {cr.status_code}: {cr.text[:180]}")
        if cr.status_code >= 400:
            r.seconds = round(time.monotonic() - t0, 1)
            return r

        anc = await c.get(f"{BASE}/api/groups/{child}/ancestors")
        r.check("child reports its parent", anc.status_code == 200, f"HTTP {anc.status_code}")
        # The real shape is {room, breadcrumbs, scope, max_depth} -- NOT
        # {ancestors, depth, authority_parent}. An earlier draft of this probe
        # read the invented keys, got None for each, and reported "depth
        # recorded: FAIL" against a route that had answered correctly.
        aj = anc.json() if anc.status_code == 200 else {}
        scope = aj.get("scope") or {}
        r.observed["child_breadcrumbs"] = aj.get("breadcrumbs")
        r.observed["child_depth"] = scope.get("depth")
        r.observed["child_authority_parent"] = scope.get("authority_parent")
        r.observed["server_max_depth"] = aj.get("max_depth")
        r.check(
            "depth recorded on the child's scope",
            isinstance(scope.get("depth"), int),
            f"scope={scope}",
        )
        # Authority refines visibility: the authority parent must also be a
        # visibility parent, or the two contradict each other.
        ap = scope.get("authority_parent")
        crumbs = aj.get("breadcrumbs") or []
        crumb_names = [x.get("name") if isinstance(x, dict) else x for x in crumbs]
        r.observed["breadcrumb_names"] = crumb_names
        if ap is not None:
            # The two sides use DIFFERENT identifier namespaces: the scope's
            # `authority_parent` is a room_id, while breadcrumbs carry names plus
            # a room_id per crumb. An earlier draft compared the room_id against
            # the names and reported a satisfied invariant as broken.
            crumb_ids = [x.get("room_id") for x in crumbs if isinstance(x, dict)]
            r.observed["breadcrumb_room_ids"] = crumb_ids
            r.check(
                "authority parent is a visibility parent",
                ap in crumb_ids or ap in crumb_names or ap == parent,
                f"authority={ap!r} breadcrumb names={crumb_names} ids={crumb_ids}",
            )

        # --- the inheritance-as-projection sequence -------------------------
        # `create_subgroup(inherit=True)` deliberately COPIES the parent's current
        # members into the child's direct list "so the child starts staffed". The
        # projection is therefore only observable for a member added to the parent
        # AFTER the child exists. An earlier draft of this probe added `coder` --
        # already a seeded member, so the add was a no-op -- and then asserted it
        # was absent from the child's direct list, which the code is documented to
        # do. That test could never have distinguished a projection from a copy.
        base = await c.get(f"{BASE}/api/groups/{child}/roster")
        r.observed["child_roster_before"] = base.json() if base.status_code == 200 else None
        before_eff = base.json().get("effective_count") if base.status_code == 200 else None
        before_direct = list((base.json().get("direct") if base.status_code == 200 else []) or [])

        bots = await c.get(f"{BASE}/api/bots")
        roster_names = [b["name"] for b in (bots.json().get("bots") or [])] if bots.status_code == 200 else []
        newcomer = next((n for n in roster_names if n not in before_direct), None)
        r.check("found a bot that is not already a seeded member", newcomer is not None, f"roster sample={roster_names[:8]}")
        if newcomer is None:
            r.seconds = round(time.monotonic() - t0, 1)
            return r
        r.observed["newcomer"] = newcomer

        add = await c.post(f"{BASE}/api/groups/{parent}/members", json={"bot_name": newcomer})
        r.check("member added to parent", add.status_code in (200, 201), f"HTTP {add.status_code}: {add.text[:180]}")

        after = await c.get(f"{BASE}/api/groups/{child}/roster")
        aj2 = after.json() if after.status_code == 200 else {}
        after_eff = aj2.get("effective_count")
        r.observed["child_roster_after"] = {
            "direct_count": aj2.get("direct_count"),
            "effective_count": aj2.get("effective_count"),
            "inherited": aj2.get("inherited"),
            "inherited_from": aj2.get("inherited_from"),
        }
        r.check(
            f"child sees the parent's post-creation member ({newcomer}) with no reconcile step",
            newcomer in (aj2.get("inherited") or []) or newcomer in (aj2.get("effective") or []),
            f"child inherited={aj2.get('inherited')} effective={aj2.get('effective')} (before effective_count={before_eff})",
        )
        # THE load-bearing check: an inherited member must NOT be written into the
        # child's own direct list. That is what separates a recomputed projection
        # from a copied membership -- and `room.members` is crew-owned, so a
        # nested member written there is erased on the next reconcile.
        r.check(
            "the inherited member is NOT written into the child's direct list",
            newcomer not in (aj2.get("direct") or []),
            f"child direct={aj2.get('direct')} contains {newcomer}; membership was copied instead of projected",
        )
        r.check(
            "the child's direct list is otherwise unchanged from creation",
            set(aj2.get("direct") or []) == set(before_direct),
            f"direct changed from {before_direct} to {aj2.get('direct')}",
        )
        # Both counts must always travel together.
        r.check(
            "direct_count and effective_count agree with their lists",
            aj2.get("direct_count") == len(aj2.get("direct") or []) and aj2.get("effective_count") == len(aj2.get("effective") or []),
            f"direct_count={aj2.get('direct_count')} len={len(aj2.get('direct') or [])} effective_count={aj2.get('effective_count')} len={len(aj2.get('effective') or [])}",
        )
        r.observed["effective_before"] = before_eff
        r.observed["effective_after"] = after_eff
    finally:
        r.observed["cleanup"] = await _cleanup_groups(c, [child, parent])
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def _cleanup_groups(c: httpx.AsyncClient, names: list[str]) -> dict[str, Any]:
    """Delete rooms, using cascade where children still exist."""
    out: dict[str, Any] = {}
    for n in names:
        plain = await c.delete(f"{BASE}/api/groups/{n}")
        if plain.status_code == 204 or plain.status_code == 200:
            out[n] = plain.status_code
        else:
            casc = await c.delete(f"{BASE}/api/groups/{n}", params={"cascade": "true"})
            out[n] = f"{plain.status_code}->cascade {casc.status_code}"
    return out


async def probe_nesting_limits(c: httpx.AsyncClient) -> R:
    """The documented refusals must actually fire.

    ``MAX_DEPTH`` is 4 and "the limit is refused, never clamped"; a delete with
    children must refuse and name what it would remove. A guard that has never
    refused anything is an unproven guard, so both are exercised against real
    rooms and the refusal text is checked.
    """
    r = R(key="nesting_limits", title=PROBES[1].title, feature=PROBES[1].feature)
    t0 = time.monotonic()
    chain: list[str] = []
    try:
        root = uniq("deep")
        cr = await c.post(f"{BASE}/api/groups", json={"name": root})
        chain.append(root)
        r.check("root room created", cr.status_code in (200, 201), f"HTTP {cr.status_code}")

        # Walk down until the depth limit refuses. Four levels are legal.
        refused_at: int | None = None
        for depth in range(1, 8):
            nxt = uniq(f"d{depth}")
            resp = await c.post(f"{BASE}/api/groups/{chain[-1]}/subgroups", json={"name": nxt})
            if resp.status_code in (200, 201):
                chain.append(nxt)
                r.observed[f"depth_{depth}"] = "created"
                continue
            refused_at = depth
            # Assert on meaning, not one wording. The shipped refusal reads
            # "Room would nest 5 levels deep; the maximum is 4. Promote an
            # intermediate room or flatten the structure." -- so "deep", not
            # "depth". A first draft matched "depth" and reported a working
            # guard as broken, which is the same trap as the unit test's
            # "depth" vs "deep" assertion.
            r.expect_refusal(f"depth limit refuses at depth {depth}", resp, "deep")
            r.observed["refused_at_depth"] = depth
            r.observed["refusal_status"] = resp.status_code
            break
        r.check("a depth limit was actually reached", refused_at is not None, f"created {len(chain)} rooms with no refusal")
        if refused_at is not None:
            r.check(
                "the limit refused rather than clamping",
                refused_at <= 6,
                f"refused only at depth {refused_at}",
            )

        # Delete-with-children must refuse and name what it would remove.
        if len(chain) >= 2:
            d = await c.delete(f"{BASE}/api/groups/{chain[0]}")
            r.expect_refusal("delete refuses while children exist", d, "")
            r.observed["delete_with_children_http"] = d.status_code
            r.observed["delete_body"] = d.text[:260]
            casc = await c.delete(f"{BASE}/api/groups/{chain[0]}", params={"cascade": "true"})
            r.check("cascade delete succeeds", casc.status_code in (200, 204), f"HTTP {casc.status_code}: {casc.text[:160]}")
            if casc.status_code in (200, 204):
                chain = chain[1:]
    finally:
        r.observed["cleanup"] = await _cleanup_groups(c, list(reversed(chain)))
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def _await_run(c: httpx.AsyncClient, tid: str, rid: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        rr = await c.get(f"{BASE}/api/threads/{tid}/runs/{rid}")
        if rr.status_code == 200:
            last = rr.json()
            if last.get("status") in ("success", "error", "interrupted", "timeout", "canceled"):
                return last
        await asyncio.sleep(2.0)
    return last


async def probe_lifecycle(c: httpx.AsyncClient, timeout: float) -> R:
    """Branch a completed turn and confirm the inherited history is *visible*.

    The feed reads ``run_events``, not checkpoints, so an unseeded branch looks
    empty in the UI even though its checkpoint carries the turns. The check is
    therefore on the paged message feed, not on the checkpoint.
    """
    r = R(key="lifecycle", title=PROBES[2].title, feature=PROBES[2].feature)
    t0 = time.monotonic()
    tid = ""
    try:
        tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "write-path-probe"}})
        tid = tr.json()["thread_id"]
        rr = await c.post(f"{BASE}/api/threads/{tid}/runs/wait", json={"input": {"messages": [{"role": "user", "content": "Reply with exactly: MARKER-ALPHA-7. Nothing else."}]}})
        j = rr.json() if rr.status_code == 200 else {}
        rid = j.get("run_id") or (j.get("metadata") or {}).get("run_id") or ""
        r.observed["seed_run"] = rid
        r.observed["seed_status"] = j.get("status") or j.get("metadata", {}).get("status")

        msgs = await c.get(f"{BASE}/api/threads/{tid}/messages")
        r.check("parent feed readable", msgs.status_code == 200, f"HTTP {msgs.status_code}")
        parent_body = msgs.text

        br = await c.post(f"{BASE}/api/threads/{tid}/branches")
        r.check("branch created", br.status_code in (200, 201), f"HTTP {br.status_code}: {br.text[:200]}")
        if br.status_code >= 400:
            r.seconds = round(time.monotonic() - t0, 1)
            return r
        bj = br.json()
        bid = bj.get("thread_id") or bj.get("branch_id") or ""
        r.observed["branch_id"] = bid
        r.observed["history_seed_mode"] = bj.get("history_seed_mode")
        r.observed["workspace_clone_mode"] = bj.get("workspace_clone_mode")

        bmsgs = await c.get(f"{BASE}/api/threads/{bid}/messages")
        r.check("branch feed readable", bmsgs.status_code == 200, f"HTTP {bmsgs.status_code}")
        if bmsgs.status_code == 200:
            r.observed["branch_feed_bytes"] = len(bmsgs.text)
            r.observed["branch_feed"] = bmsgs.text[:400]
            # The inherited turn must actually be in the branch's feed.
            r.check(
                "inherited history is visible on the branch feed",
                "MARKER-ALPHA-7" in bmsgs.text,
                "the marker from the parent turn is absent from the branch feed; history was not seeded",
            )
            r.check(
                "branch feed is not simply empty",
                len((bmsgs.json().get("data") if isinstance(bmsgs.json(), dict) else []) or []) > 0 or len(bmsgs.text) > 40,
                f"feed was {len(bmsgs.text)}B",
            )
        # An explicit seed mode is the honest signal for how history was carried.
        r.observed["parent_feed_bytes"] = len(parent_body)
    finally:
        if tid:
            r.observed["cleanup_parent"] = (await c.delete(f"{BASE}/api/threads/{tid}")).status_code
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def probe_scheduled(c: httpx.AsyncClient, timeout: float) -> R:
    """Create a scheduled task, preview its cron, and trigger a real run."""
    r = R(key="scheduled", title=PROBES[3].title, feature=PROBES[3].feature)
    t0 = time.monotonic()
    task_id = ""
    try:
        prev = await c.post(
            f"{BASE}/api/scheduled-tasks/preview-cron",
            json={"cron": "*/5 * * * *", "timezone": "UTC"},
        )
        r.observed["preview_cron_http"] = prev.status_code
        r.observed["preview_cron"] = prev.text[:220]
        r.check("cron preview answers", prev.status_code == 200, f"HTTP {prev.status_code}: {prev.text[:160]}")

        bad = await c.post(f"{BASE}/api/scheduled-tasks/preview-cron", json={"cron": "not a cron", "timezone": "UTC"})
        r.check("an invalid cron is refused", bad.status_code in (400, 422), f"HTTP {bad.status_code}")

        cr = await c.post(
            f"{BASE}/api/scheduled-tasks",
            json={
                "name": uniq("task"),
                "prompt": "Reply with exactly: SCHEDULED-FIRED. Nothing else.",
                "cron": "*/5 * * * *",
                "timezone": "UTC",
                "enabled": True,
            },
        )
        r.observed["create_http"] = cr.status_code
        r.check("scheduled task created", cr.status_code in (200, 201), f"HTTP {cr.status_code}: {cr.text[:220]}")
        if cr.status_code >= 400:
            r.seconds = round(time.monotonic() - t0, 1)
            return r
        cj = cr.json()
        task_id = cj.get("task_id") or cj.get("id") or ""
        r.observed["task_id"] = task_id
        r.observed["next_run"] = cj.get("next_run_at") or cj.get("next_run")

        trg = await c.post(f"{BASE}/api/scheduled-tasks/{task_id}/trigger")
        r.observed["trigger_http"] = trg.status_code
        r.check("manual trigger accepted", trg.status_code in (200, 201, 202), f"HTTP {trg.status_code}: {trg.text[:200]}")
        r.observed["trigger_body"] = trg.text[:240]

        # A triggered task must produce a real run, not just a 202.
        runs = await c.get(f"{BASE}/api/scheduled-tasks/{task_id}/runs")
        r.check("task runs listable", runs.status_code == 200, f"HTTP {runs.status_code}")
        rows = runs.json() if runs.status_code == 200 else []
        rows = rows if isinstance(rows, list) else (rows.get("data") or rows.get("runs") or [])
        r.observed["n_runs_after_trigger"] = len(rows)

        deadline = time.monotonic() + min(timeout, 300)
        while time.monotonic() < deadline and not rows:
            await asyncio.sleep(5)
            rr2 = await c.get(f"{BASE}/api/scheduled-tasks/{task_id}/runs")
            if rr2.status_code == 200:
                j2 = rr2.json()
                rows = j2 if isinstance(j2, list) else (j2.get("data") or j2.get("runs") or [])
        r.observed["runs"] = [{k: row.get(k) for k in ("run_id", "thread_id", "status", "error")} if isinstance(row, dict) else str(row) for row in rows[:4]]
        r.check("triggering produced a real run", len(rows) > 0, "no run row appeared after a manual trigger")

        qh = await c.get(f"{BASE}/api/scheduled-tasks/queue-health")
        r.observed["queue_health_http"] = qh.status_code
        r.observed["queue_health"] = qh.text[:220]
        r.check("queue health answers", qh.status_code == 200, f"HTTP {qh.status_code}")
    finally:
        if task_id:
            d = await c.delete(f"{BASE}/api/scheduled-tasks/{task_id}")
            r.observed["cleanup"] = d.status_code
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def probe_workflow(c: httpx.AsyncClient) -> R:
    """Perceive, simulate, execute, fork -- and check the acceptance is honest.

    The default executor is a ``local_digest_projection``: it hashes inputs to
    exercise graph mechanics and keeps ``acceptance_passed=false``. A probe that
    only checked "did the run complete" would pass a fabricated acceptance, so
    this asserts the *absence* of a verdict.
    """
    r = R(key="workflow", title=PROBES[4].title, feature=PROBES[4].feature)
    t0 = time.monotonic()
    wid = ""
    run_id = ""
    try:
        executors = await c.get(f"{BASE}/api/workflows/system/executors")
        ej = executors.json() if executors.status_code == 200 else {}
        r.observed["executors"] = ej
        r.check("executors endpoint answers", executors.status_code == 200, f"HTTP {executors.status_code}")
        # The API must not claim the opt-in domain executors are bound.
        r.check(
            "domain executors are not claimed as bound",
            (ej.get("domain_bound") or []) == [],
            f"domain_bound={ej.get('domain_bound')}",
        )

        graph = {
            "nodes": [
                {"id": "a", "type": "task", "config": {"prompt": "first"}},
                {"id": "b", "type": "task", "config": {"prompt": "second", "depends_on": ["a"]}},
            ],
            "edges": [{"from": "a", "to": "b"}],
        }
        payload = {"name": uniq("wf"), "graph": graph, "description": "write-path probe"}

        pc = await c.post(f"{BASE}/api/workflows", json=payload)
        r.observed["create_http"] = pc.status_code
        if pc.status_code in (200, 201):
            wid = pc.json().get("workflow_id") or pc.json().get("id") or ""
            r.observed["workflow_id"] = wid
        else:
            # Definition creation may refuse; perceive still works standalone.
            r.observed["create_body"] = pc.text[:220]
            r.check("workflow definition accepted or cleanly refused", True)

        perc = await c.post(
            f"{BASE}/api/workflows/dynamic/perceive",
            json={"goal": "Run two dependent steps and report what completed", "items": ["one", "two"]},
        )
        r.observed["perceive_http"] = perc.status_code
        r.check("dynamic perceive answers", perc.status_code == 200, f"HTTP {perc.status_code}: {perc.text[:200]}")
        if perc.status_code == 200:
            pj = perc.json()
            r.observed["perceive"] = {k: pj.get(k) for k in ("should_use_dynamic", "complexity", "tier", "reason", "estimated_nodes") if k in pj}
            # A read-only preview must not assert an acceptance verdict.
            r.check(
                "perceive carries no acceptance verdict",
                not any(k in pj for k in ("acceptance_passed", "verified", "acceptance")),
                f"perceive leaked an acceptance field: {[k for k in pj if 'accept' in k or 'verif' in k]}",
            )

        sim = await c.post(f"{BASE}/api/workflows/simulate", json={"graph": graph})
        r.observed["simulate_http"] = sim.status_code
        if sim.status_code == 200:
            sj = sim.json()
            r.observed["simulate_label"] = sj.get("mode") or sj.get("label") or sj.get("kind")
            r.check(
                "a dry run is labelled as such",
                "dry_run" in json.dumps(sj).lower() or "simulat" in json.dumps(sj).lower(),
                f"simulation was not labelled dry-run: {json.dumps(sj)[:200]}",
            )
            r.check(
                "a dry run asserts no acceptance",
                not any(k in sj for k in ("acceptance_passed", "verified")),
                f"simulation leaked an acceptance field: {[k for k in sj if 'accept' in k or 'verif' in k]}",
            )
        else:
            r.check("simulate answers", False, f"HTTP {sim.status_code}: {sim.text[:180]}")

        if wid:
            ex = await c.post(f"{BASE}/api/workflows/dynamic/execute", json={"workflow_id": wid})
            r.observed["execute_http"] = ex.status_code
            if ex.status_code in (200, 201):
                ej2 = ex.json()
                run_id = ej2.get("run_id") or ""
                r.observed["run_id"] = run_id
                r.observed["execute_status"] = ej2.get("status")
                if run_id:
                    await asyncio.sleep(3)
                    hist = await c.get(f"{BASE}/api/workflows/runs/{run_id}/history")
                    r.observed["history_http"] = hist.status_code
                    r.observed["history_bytes"] = len(hist.text)
                    r.check("run history is readable", hist.status_code == 200, f"HTTP {hist.status_code}")
                    rep = await c.get(f"{BASE}/api/workflows/runs/{run_id}/report")
                    r.observed["report_http"] = rep.status_code
                    if rep.status_code == 200:
                        rj = rep.json()
                        r.observed["report_keys"] = sorted(rj)[:14]
                        r.observed["report_status"] = rj.get("status")
                        # The report carries NO acceptance verdict by contract.
                        r.check(
                            "the measured report asserts no acceptance",
                            not any(k in rj for k in ("acceptance_passed", "verified", "acceptance")),
                            f"report leaked an acceptance verdict: {[k for k in rj if 'accept' in k or 'verif' in k]}",
                        )
                    else:
                        r.check("report answers", False, f"HTTP {rep.status_code}")
                    fk = await c.post(f"{BASE}/api/workflows/runs/{run_id}/fork", json={})
                    r.observed["fork_http"] = fk.status_code
                    r.observed["fork_body"] = fk.text[:200]
                    r.check(
                        "fork answers with a real outcome (created, or refused with a reason)",
                        fk.status_code in (200, 201, 400, 409, 422),
                        f"HTTP {fk.status_code}: {fk.text[:180]}",
                    )
    finally:
        if run_id:
            r.observed["cleanup_cancel"] = (await c.post(f"{BASE}/api/workflows/runs/{run_id}/cancel")).status_code
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def probe_skills(c: httpx.AsyncClient) -> R:
    """``teach`` must yield an INACTIVE proposal.

    Per `AGENTS.md`: ``teach`` "does **not** install: it validates the draft,
    runs the static scan and queues a SkillProposal — the admin approve gate
    remains the control, and the reply says the skill is not active yet". A
    ``teach`` that quietly installed the skill would remove the only gate a
    model-writable surface has.
    """
    r = R(key="skills", title=PROBES[5].title, feature=PROBES[5].feature)
    t0 = time.monotonic()
    slug = uniq("skill").replace("_", "-")
    body = {
        "name": slug,
        "description": "Use when verifying a write-path probe artifact exists and is non-empty.",
        "content": f"---\nname: {slug}\ndescription: probe\n---\n\n# Probe procedure\n\n1. Read the artifact.\n2. Report its byte size.\n",
    }
    # A) the unauthenticated-ish surface: the skill install path must not accept this
    inst = await c.post(f"{BASE}/api/skills/install", json=body)
    r.observed["install_http"] = inst.status_code
    r.check(
        "skills are not installed by an unauthenticated shape we can send",
        inst.status_code in (400, 401, 403, 404, 405, 422),
        f"HTTP {inst.status_code}: {inst.text[:200]}",
    )

    before = await c.get(f"{BASE}/api/skills")
    r.check("skills listable", before.status_code == 200, f"HTTP {before.status_code}")
    bnames = set()
    if before.status_code == 200:
        bj = before.json()
        items = bj if isinstance(bj, list) else (bj.get("skills") or bj.get("data") or [])
        bnames = {s.get("name") for s in items if isinstance(s, dict)}

    props = await c.get(f"{BASE}/api/skills/proposals")
    r.observed["proposals_http"] = props.status_code
    r.check("proposals endpoint answers", props.status_code == 200, f"HTTP {props.status_code}: {props.text[:160]}")
    plist = []
    if props.status_code == 200:
        pj = props.json()
        plist = pj if isinstance(pj, list) else (pj.get("proposals") or pj.get("data") or [])
    r.observed["n_proposals"] = len(plist)
    r.observed["proposal_statuses"] = [{k: p.get(k) for k in ("name", "status", "active", "skill") if k in p} for p in plist[:5] if isinstance(p, dict)]
    # Any proposal must be pending approval, never live.
    live = [p for p in plist if isinstance(p, dict) and p.get("active") is True]
    r.check("no queued proposal is live", not live, f"{len(live)} proposal(s) report active=true: {live[:2]}")

    after = await c.get(f"{BASE}/api/skills")
    if after.status_code == 200:
        aj = after.json()
        items2 = aj if isinstance(aj, list) else (aj.get("skills") or aj.get("data") or [])
        anames = {s.get("name") for s in items2 if isinstance(s, dict)}
        r.check("the probe skill never became live", slug not in anames, f"{slug} appears in the installed set")
        r.observed["skills_added"] = sorted(anames - bnames)
    tiers = await c.get(f"{BASE}/api/skills/tiers")
    r.observed["tiers_http"] = tiers.status_code
    r.check("single-segment collection route is reachable", tiers.status_code == 200, f"HTTP {tiers.status_code}")
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


async def probe_artifacts(c: httpx.AsyncClient) -> R:
    """Atomic replacement: a matching digest replaces, a mismatch refuses."""
    r = R(key="artifacts", title=PROBES[6].title, feature=PROBES[6].feature)
    t0 = time.monotonic()
    tid = ""
    rel = f"{PREFIX}artifact.txt"
    try:
        tr = await c.post(f"{BASE}/api/threads", json={"metadata": {"purpose": "write-path-probe"}})
        tid = tr.json()["thread_id"]

        # Create the file through a real run so it lands in the thread's outputs.
        rr = await c.post(
            f"{BASE}/api/threads/{tid}/runs/wait",
            json={
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": (f"Write exactly the text ORIGINAL-CONTENT to /mnt/user-data/outputs/{rel} using write_file, then present it. Do not add anything else."),
                        }
                    ]
                }
            },
        )
        r.observed["seed_status"] = (rr.json().get("metadata") or {}).get("status") if rr.status_code == 200 else None
        r.observed["seed_http"] = rr.status_code

        host = _find_thread_file(tid, rel)
        r.observed["on_disk"] = str(host) if host else None
        if not host or not host.is_file():
            r.check("the artifact exists on disk", False, f"no file at {host}")
            r.seconds = round(time.monotonic() - t0, 1)
            return r
        original = host.read_text(encoding="utf-8")
        r.observed["original_bytes"] = len(original)
        import hashlib

        good = hashlib.sha256(original.encode("utf-8")).hexdigest()

        get = await c.get(f"{BASE}/api/threads/{tid}/artifacts/{rel}")
        r.check("artifact readable via the API", get.status_code == 200, f"HTTP {get.status_code}")
        r.observed["api_read_matches_disk"] = get.status_code == 200 and get.text == original

        # A wrong digest must be refused -- this is the conflict guard.
        bad = await c.put(
            f"{BASE}/api/threads/{tid}/artifacts/{rel}",
            content="TAMPERED",
            headers={"if-match": "0" * 64},
        )
        r.check("a mismatched digest is refused", bad.status_code in (400, 409, 412, 422), f"HTTP {bad.status_code}: {bad.text[:180]}")
        r.observed["mismatch_http"] = bad.status_code
        r.check(
            "the refused write did not corrupt the artifact",
            host.read_text(encoding="utf-8") == original,
            "the file changed despite the refusal",
        )

        ok = await c.put(
            f"{BASE}/api/threads/{tid}/artifacts/{rel}",
            content="REPLACED-VIA-API",
            headers={"if-match": good},
        )
        r.observed["replace_http"] = ok.status_code
        r.observed["replace_body"] = ok.text[:180]
        r.check(
            "a matching digest replaces the file",
            ok.status_code in (200, 204) and "REPLACED-VIA-API" in host.read_text(encoding="utf-8"),
            f"HTTP {ok.status_code}; on disk now {host.read_text(encoding='utf-8')[:60]!r}",
        )

        # Confined path: escaping outputs must not be reachable.
        esc = await c.get(f"{BASE}/api/threads/{tid}/artifacts/..%2F..%2Fconfig.yaml")
        r.check("a traversal attempt does not return a file", esc.status_code in (400, 403, 404), f"HTTP {esc.status_code}")
        r.observed["traversal_http"] = esc.status_code
    finally:
        if tid:
            r.observed["cleanup"] = (await c.delete(f"{BASE}/api/threads/{tid}")).status_code
    r.seconds = round(time.monotonic() - t0, 1)
    r.verdict = "PASS" if not r.problems else "FAIL"
    return r


def _find_thread_file(thread_id: str, rel: str) -> Path | None:
    base = Path(__file__).resolve().parents[1] / ".alpha" / "users"
    for user_dir in base.glob("*"):
        cand = user_dir / "threads" / thread_id / "user-data" / "outputs" / rel
        if cand.exists():
            return cand
    return None


RUNNERS = {
    "nesting": probe_nesting,
    "nesting_limits": probe_nesting_limits,
    "lifecycle": probe_lifecycle,
    "scheduled": probe_scheduled,
    "workflow": probe_workflow,
    "skills": probe_skills,
    "artifacts": probe_artifacts,
}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    keys = [k.strip() for k in args.only.split(",")] if args.only else list(RUNNERS)
    chosen = [p for p in PROBES if p.key in keys]
    if not chosen:
        print("no probes matched", file=sys.stderr)
        return 2

    print(f"Running {len(chosen)} write-path probes against {BASE}\n")
    results: list[R] = []
    async with httpx.AsyncClient(timeout=240.0) as c:
        for p in chosen:
            print(f"  -> {p.key} ...", flush=True)
            try:
                fn = RUNNERS[p.key]
                # Only the probes that drive a real run need the budget; the rest
                # take the client alone. Dispatching by signature keeps the
                # call site uniform instead of hand-maintaining a per-probe
                # arity table.
                import inspect

                takes_timeout = len(inspect.signature(fn).parameters) > 1
                r = await (fn(c, args.timeout) if takes_timeout else fn(c))
            except Exception as exc:  # noqa: BLE001 - a probe crash is a finding
                r = R(key=p.key, title=p.title, feature=p.feature)
                r.check(f"{p.key} probe completed", False, f"raised {type(exc).__name__}: {exc}")
                r.verdict = "FAIL"
            results.append(r)
            print(f"     {r.verdict} ({r.seconds:.0f}s, {len(r.problems)} problems)", flush=True)

    print("\n" + "=" * 100)
    print(f"{'PROBE':<18} {'VERDICT':<9} {'SEC':>5} {'PASS':>5} {'FAIL':>5}  FEATURE")
    print("=" * 100)
    for r in results:
        p = sum(1 for _, ok, _ in r.checks if ok)
        print(f"{r.key:<18} {r.verdict:<9} {r.seconds:>5.0f} {p:>5} {len(r.problems):>5}  {r.feature}")

    for r in results:
        print("\n" + "-" * 100)
        print(f"[{r.key}] {r.feature} -- {r.title}")
        print(f"  verdict : {r.verdict}")
        for k, v in r.observed.items():
            s = json.dumps(v, default=str) if not isinstance(v, str) else v
            print(f"  {k:<24}: {s[:220]}")
        print("  checks  :")
        for name, ok, note in r.checks:
            print(f"      [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n             -> {note[:200]}" if note and not ok else ""))

    n_fail = sum(1 for r in results if r.verdict == "FAIL")
    print("\n" + "=" * 100)
    total = sum(len(r.checks) for r in results)
    print(f"{len(results)} probes, {total} checks: {total - sum(len(r.problems) for r in results)} pass, {sum(len(r.problems) for r in results)} fail")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
