# Screenshot — Bots view after the cycle-5 fixes

**Captured:** 2026-10-05 · **Path:** `bots-after-fix.png` (161 965 bytes)
**URL:** `http://127.0.0.1:3000/?view=bots` (production build, `next start`)

## What this screenshot proves

Each claim below is cross-checked against a live HTTP response captured from the
same running Gateway, not read off the image alone.

| Visible in the image | Corroborating live evidence |
|---|---|
| **`6/7 ready`** in the subsystem strip | Before the fix the same strip read **`5/7 ready`**. The seventh row is the Autonomous company, whose honest state is a 404 — see below. |
| **`Safety watchdog` with a green dot** | `GET /api/supervision/fleet` → `200 {"observed":false,"observed_reason":"no_worker_has_posted_a_heartbeat_to_this_process","observed_worker_count":0,"watching":false}`. Previously this row rendered the client-side error *"The server returned an unreadable fleet payload."* — the server's own reason had been discarded by a parser that rejected the payload it documents. |
| **`60 Total bots / 44 Active / 0 Paused`** | `GET /api/bots` → `count: 60`; `GET /api/bots/health/overview` → `summary.total` agrees. The strip matches the server exactly. |
| **`Profiles (60)`**, `Showing 60 of 60 bots` | Same source. |
| **`Data Engineer (bot_ed5fc1)`** and its siblings | `GET /api/bots` returns **0 duplicate `name`s** but **8 duplicate `display_name`s covering 35 of 58 cards** (`Data Engineer` ×8, `Solidity_Security Specialist` ×7, `Cuda_Kernel_Opt Specialist` ×7, `Researcher`/`Coder`/`Tester` ×3, `Architect`/`Support` ×2). Before the fix every one of those 35 cards rendered an identical headline and `name` — the only distinguishing field — appeared nowhere. |
| **`Autonomous company` amber, with `— Requ…` clipped** | `GET /api/company/status` → `404 "No active organizations found. Bootstrap a company first."` The row fails its own probe and shows the server's sentence; no organization is fabricated. **The sentence is clipped by the viewport, not truncated in the data** — see the known defect below. |
| **`0 agents`** in the vitals strip | `GET /api/console/stats` → `total_agents: 0`. **This is correct and honest**: that counter counts custom agent *profiles*, not bots. Verified against the API rather than assumed to be a defect. |
| **`Avg reputation — none measured`**, **`Tasks done — no counter reported`** | Fields the server did not send, rendered as words rather than `0`. |
| **`Free`**, **`Gateway online`**, **`v2.1.0`**, **`~29 ms internet`** | `GET /api/ops/version`, `GET /health/ready`, `GET /api/ops/network`. |

## What this screenshot does NOT prove

- It is **one view** (Bots). The other 30 `WORKSPACE_TABS` ids were **not**
  re-verified this cycle — see `FULL_VERIFICATION_REPORT.md` §9 and §13 (§L5).
- It proves the **rendered DOM state**, which is the honest floor available here.
  It is not a visual-design review.
- The lion companion's tooltip ("The desk is quiet. I'm here when you need me.")
  **overlays the fleet health strip** and covers two cards. Recorded as §L4; not
  fixed. Visible in the image.

## Defects this image surfaced that the numbers alone did not

1. **`Architect (architect)`** — the disambiguator was firing on a case-only
   difference, adding noise beside `Architect (cto)` and making a clean card look
   broken. Fixed: a case-only id difference does not earn a qualifier. Measured
   after the fix: **30 of 35** colliding rows qualify; the 5 that do not are
   `researcher`, `coder`, `tester`, `architect`, `support` — each an exact
   case-only copy of its own label, so repeating it in parentheses told the
   operator nothing the adjacent card did not already show.
2. **The company row's reason is clipped** by the viewport at this width. The
   data is present in the DOM; only the visible strip truncates it.

Both are in `FULL_VERIFICATION_REPORT.md` §9 / §13.