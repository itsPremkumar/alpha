# System One calibration — measure before you trust it

System One's entire value proposition is a single claim: **a stated 0.9 means
"right about 90% of the time"**. That claim is what lets a call site skip the
LLM entirely — a guardrail that abstains at 0.9 is making a real bet, and a
router that sends a hard prompt to the flagship model on a 0.85 is spending real
money on the strength of a number.

The claim is also **site-specific**. A model can be well calibrated in general
and still be overconfident on Alpha's particular traces — its tool calls, its
memory snippets, its DOM tables. Nobody can tell you in advance. The only way to
know is to measure on your own data.

This document covers the two mechanisms that make that possible:
**shadow mode** (measure without changing behaviour) and the **calibration
harness** (turn the log into a verdict).

---

## 1. Shadow mode

With `system_one.shadow_mode: true`, every call site behaves *exactly* as it did
before System One existed:

1. The request is still sent to Jev.
2. The answer is parsed and recorded to the JSONL log.
3. `evaluate()` returns `None`.

Because `None` is the universal "fall back" signal, **every call site runs its
existing LLM/heuristic path while the decision is measured**. Nothing can
regress, because nothing is being acted on.

```yaml
system_one:
  enabled: true
  shadow_mode: true        # measure, don't act
  record_decisions: true   # implied by shadow_mode
  calibration_log_path: "system_one_decisions.jsonl"
```

`shadow_mode` implies recording — running shadow with no log would measure
nothing, which is the one thing shadow mode is for. If `calibration_log_path`
is unset it defaults to `system_one_decisions.jsonl` under the Alpha state
directory.

Shadow decisions are tagged `shadow: true` in the log and are never counted as
"acted on" in the report, so you can mix shadow and live decisions in one log
and still read the numbers correctly.

## 2. The decision log

One JSON line per question answered:

```json
{"ts": 1769000000.1, "site": "guardrail", "tier": "write", "question_id": "is_injection",
 "type": "boolean", "value": 0.93, "confidence": null, "threshold": 0.75,
 "latency_ms": 142.0, "model": "jev-1.13.0", "shadow": true, "outcome": null, "meta": {}}
```

| field | meaning |
|---|---|
| `site` | which call site asked (see the table below) |
| `tier` | risk tier → the confidence floor that was applied |
| `value` | the answer (probability, chosen option, or rubric level) |
| `confidence` | present for `choice`/`score`; `null` for `boolean` |
| `threshold` | what it had to clear to be used |
| `outcome` | filled in later, when the truth becomes known |
| `shadow` | was this measured-only? |

Recording is **best-effort in both directions**. A missing directory is created;
an unwritable path logs one warning and moves on. A failure to record can never
turn a usable answer into a fallback.

### Call-site labels

| label | module |
|---|---|
| `guardrail` | `guardrails/jev.py` — tool-call safety |
| `browser` | `browser/jev_policy.py` — next browser action |
| `injection` | `security/injection.py` — prompt injection in fetched content |
| `citation` | `agents/middlewares/citation_support.py` |
| `trace` / `trace:localise` | `tools/trace_verify.py` |
| `selection` / `selection:refine` | `tools/selection.py` |
| `skill_select`, `tool_select` | the shared ranker, called from the skill and tool catalogs |
| `rerank`, `session_search` | `memory/rerank.py` |
| `retention` | `context/retention.py` |
| `acceptance` | `subagents/jev_acceptance.py` |
| `epistemic` / `epistemic:likelihood` | `epistemics/jev_belief.py` |
| `escalation` | `models/escalation.py` |
| `deliberation` | `deliberation/router.py` |
| `goal` | `runtime/goal.py` |
| `memory_gate` | `memory/active_memory.py` |
| `security_scan` | `skills/security_scanner.py` |
| `autoconfig` | `autoconfig/engine.py` |

## 3. Reading the report

```bash
python backend/scripts/system_one_calibration.py                 # full report
python backend/scripts/system_one_calibration.py --json          # machine readable
python backend/scripts/system_one_calibration.py --site guardrail
python backend/scripts/system_one_calibration.py --tail 50
```

```
log: C:\...\state\system_one_decisions.jsonl
System One calibration
  decisions recorded : 412
  with outcomes      : 96
  would have acted   : 301
  mean latency       : 138ms
  Brier score        : 0.0912 (lower is better)
  calibration error  : 0.0431 (0 is perfect)

  stated             n   observed      gap
  0.00-0.50          4     0.250   -0.000
  0.50-0.60          9     0.556   +0.022
  0.60-0.70         14     0.643   +0.007
  0.70-0.80         21     0.762   -0.012
  0.80-0.90         28     0.857   +0.021
  0.90-1.00         20     0.800   +0.104   <-- overconfident

  site                       n   acted  scored
  browser                  118      87      22
  guardrail                 96      81      40
  rerank                    74      55      18
```

**How to read it**

* **stated / observed / gap.** `gap = stated − observed`. Positive means
  **overconfident**, which is the dangerous direction: the model says 0.9 and is
  wrong half the time. That is the row to look at first.
* **Brier score** — mean squared error of the probabilities. Lower is better.
  `0.25` is what you get by always answering 0.5, so anything at or above 0.25
  means the model is adding nothing.
* **calibration error** — weighted mean |stated − observed|. `0.0` is perfect;
  under ~0.05 is good enough to act on; above ~0.10 means raise the threshold or
  leave that site in shadow.
* **would have acted** — how many decisions cleared their threshold. If this is
  near zero the site is paying for latency and providing nothing.

**The 0.90–1.00 row is the one that matters.** Under-confidence is merely
wasteful; over-confidence is what gets a destructive tool call approved.

**Mind the sample size.** A bucket needs `MIN_BUCKET_COUNT` (20) decisions
before its observed rate means anything; below that the row is printed and
flagged `(too few to judge)`. This matters more than it sounds: two decisions at
0.9 that both happen to be right produce `observed 1.000, gap -0.100` — a row
that looks *better than perfect* and is pure noise. The report also lists sites
with thin evidence separately, so you cannot mistake "measured" for
"trustworthy".

## 4. Getting outcomes

Most sites have no automatic ground truth, so a fresh log reports coverage and
latency only — enough to answer "is this worth turning on?", not enough to
answer "is it calibrated?".

Sites that eventually learn the truth should record it. If the outcome is known
in the same process, use the timestamp the recorder already remembered:

```python
from alpha.evaluation.system_one_calibration import record_recent_outcome

record_recent_outcome("browser", outcome=True)   # newest decision at that site
```

If you have the timestamp across processes, use `record_outcome(path, ts, site,
outcome)` directly. Matching is by `(site, ts)` — that pair is unique per
request and survives the caller having no record ids. `record_recent_outcome` is
**in-process only** (the timestamp is a dict on the live recorder); it returns 0
harmlessly if nothing was recorded.

### Where ground truth actually exists

This is worth being blunt about, because it is easy to fool yourself here:

| site | outcome source | status |
|---|---|---|
| `browser` | did the executed step actually work? | **wired** — `jev_agent.py` records `step.ok` |
| `goal` | did the turn actually end afterwards? | weak proxy |
| `trace` | did deterministic checks independently flag it? | cross-check only |
| `rerank`, `selection` | which result did the user actually open? | needs UI telemetry |
| `acceptance` | — | **none, by construction** |
| `injection`, `security_scan` | — | **none, by construction** |

The last two are not an oversight. Those sites exist *precisely because*
determinism cannot decide the question: the acceptance pass only judges the
leaves the deterministic checker marked `undecidable`, and injection detection
is the one guardrail regex genuinely cannot do. There is no independent verdict
to compare them against — judging them requires a human or a stronger model,
which is a labelling exercise, not a code change.

So realistically **`browser` is the only site that produces free, unambiguous
outcomes today.** That is still enough to be worth doing: it is the site making
the most decisions per run, and the one where a confidently wrong action is most
expensive. If you need calibration elsewhere, sample and label by hand.

## 5. Recommended rollout

1. **Card on file.** Until the Vercel account has one, every request returns
   HTTP 403 `customer_verification_required` and every site falls back. Nothing
   below is measurable before this is cleared.
2. **Shadow everything.** `shadow_mode: true`, run real workloads for a day.
   You now have coverage and latency per site, with zero risk.
3. **Read the coverage first.** `browser` will start producing outcomes on its
   own (the agent records whether each step worked). Every other site reports
   coverage and latency only, and that is expected — not a bug to fix.
4. **Read the report per site.** A site whose 0.9+ bucket shows ≥0.9 observed
   is safe to act on. One showing 0.6 is not — either raise its threshold or
   leave it in shadow. Be careful not to read a site with *zero* outcomes as
   calibrated; it has not been measured at all.
5. **Turn shadow off per site.** There is no global kill switch for a reason:
   calibration is per site. Use the existing `enable_*` flags.
6. **Re-measure after any prompt change.** The questions are the model. Reword
   a rubric and the calibration changes with it.

## 6. Design notes

* **`None` means fall back, never `False`.** This is what makes shadow mode
  free: returning `None` is indistinguishable from "System One was unreachable",
  a state every call site already handles.
* **Narrowing-only.** No site uses System One to grant permission it would not
  otherwise have. It can only add denials and abstentions, so a miscalibrated
  model produces extra caution, never extra risk.
* **Thresholds degrade, never inflate.** An unknown risk tier falls back to
  `min_confidence`, never to something more permissive.
* **Measurement is non-invasive.** Recording failures are swallowed, the
  recorder never raises, and shadow mode cannot change behaviour.
* **The log is not silently lossy.** Attaching an outcome is a read-modify-write
  over a file another process may be appending to, so it takes a lock file and
  commits with an atomic `os.replace`. Losing decisions would quietly bias the
  sample towards whatever survived — the worst failure a measurement tool can
  have. The `.lock` file next to the log is that token; leave it alone.

---

## Related

* `docs/SYSTEM_ONE.md` — the model, the API, and the fallback contract
* `docs/SYSTEM_ONE_ALPHA_ROADMAP.md` — all 13 integration sites
* `docs/SYSTEM_ONE_BROWSER_AGENT_EVAL.md` — why the browser agent needs this
* `docs/SYSTEM_ONE_AGENT_USE_CASES.md` — where else System One applies
