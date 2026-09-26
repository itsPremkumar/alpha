# `alpha.community.search_federation`

Federated web search for Alpha: **keyless-first**, budget-aware, and honest about
failure. Adds four search providers to the existing keyless `web_search_tool`
(DuckDuckGo) and keyless `web_fetch_tool` (Jina Reader) without replacing either.

No new runtime dependency. The only third-party import is `httpx`, which the repo
already requires. No literal credential anywhere; every key comes from an
environment variable and an unset key is a *skip*, never an exception.

---

## Providers, in priority order

| # | provider | credential | allowance | cost per call |
|---|----------|-----------|-----------|---------------|
| 1 | **tavily_keyless** | none | anonymous rate limit | not billed |
| 2 | **tavily** | `TAVILY_API_KEY` | 1,000 credits/month (refreshes, no card) | **1** credit basic/fast/ultra-fast, **2** advanced |
| 3 | **exa** | `EXA_API_KEY` | $10, refreshes to $10 on the 1st monthly | **$0.007** ≤10 results, +**$0.001** per result above 10, +$0.001 per AI summary |
| 4 | **serper** | `SERPER_API_KEY` | 2,500 free queries, **one-time** (no refill) | **1** query ($1.00 per 1,000 after the grant) |
| 5 | **duckduckgo** | none | unmetered | not billed |

`duckduckgo` is the floor: it is keyless and unmetered, so search still answers
when every HTTP provider is down or out of quota.

### Tavily keyless — the zero-setup win

Sends the vendor-documented header and nothing else:

```
X-Tavily-Access-Mode: keyless
```

No account, no key, same response schema as a keyed call, and the response
carries `usage.credits` so the cost is observable even on the free tier. The
vendor documents that a supplied API key **takes precedence** over keyless, so
this package never sends both — otherwise a caller who thought they were on the
free tier would silently spend account credits.

Verified live against `api.tavily.com` on 2026-09-26 with `include_usage: true`:
`basic` → `usage.credits 1`, `fast` → `1`, `advanced` → `2`, `extract` of 2 URLs
→ `0` (extract bills 1 credit per 5 *successful* URLs).

---

## The quota-halving trap, and how it is made impossible

Tavily bills `advanced` at 2 credits and everything else at 1 — and
`auto_parameters: true` bills **2 regardless of the depth you request**. So a
defaulted depth, or letting Tavily choose, doubles the bill without any visible
signal. A 1,000-credit month becomes 500 searches.

Two structural defences, not a comment:

1. `tavily_credit_cost(search_depth=...)` takes the depth as a **required**
   keyword argument. It is not possible to write the call without deciding.
2. `_tavily_body()` always sends `search_depth` and `auto_parameters` explicitly
   on every request. There is no code path that omits them.

The charged amount (provider-reported when available, predicted otherwise) and
its reason string are returned in the tool payload, so the cost is visible
rather than inferred.

---

## Failure contract

The rule: **a search failure is never an empty result list.**

| situation | behaviour |
|---|---|
| provider has no key | *skipped*, reason returned (`not configured: set EXA_API_KEY`) |
| allowance spent | *skipped*, reason returned (`budget: local allowance spent (1000/1000 tavily_credit)`) |
| provider reports its balance is gone | typed `budget_exhausted`, recorded in the ledger, skipped from then on until the period rolls over |
| provider returns 5xx / times out | typed `unavailable`, cooldown, **fail over** to the next provider |
| provider returns 401/403 | typed `auth`, cooldown, fail over |
| provider returns 429 | typed `rate_limited`, `Retry-After` preserved, cooldown, fail over |
| provider returns 400/422 | typed `request_rejected`, fail over (retrying cannot help) |
| response is 2xx but unparseable, or has no results key | typed `contract`, **not** "0 results" |
| provider answers with 0 results | a real answer, kept as a last resort and labelled as such in `notes` |
| nothing can serve the query | `SearchExhaustedError` → `error.kind = "search_exhausted"` with every attempt and skip listed |

`SearchExhaustedError` subclasses `ConnectionError` so the repo's generic retry
layer treats "everything is down right now" as retryable rather than as a final
answer. `is_retryable()` separates transient (`rate_limited`, `unavailable`) from
deterministic (`auth`, `budget_exhausted`, `request_rejected`).

Failure labels are `ClassName(status=N)` only — never the response body, never a
key, never the query. Error text reaches agent context and logs, so it must not
be a covert exfiltration channel.

---

## Failover, health, budget

Reuses the pattern from `alpha/models/free_router/catalog.py` rather than
inventing a second one:

- **Tri-state health.** `True` = a call succeeded, `False` = a call failed,
  `None` = unknown. Nothing infers health from "not tried yet".
- **Cooldown** = `min(600s, 15s * 2**(n-1))` with ±20% jitter, after `n`
  consecutive failures. The constants are deliberately identical to the
  free-LLM router's and a test asserts they stay in sync *by parsing that file's
  source* — `import alpha.models` costs ~50s, which is not something a search
  tool should pay on a cold start.
- **Budget exhaustion is a skip, not an error.** Paying a guaranteed-error round
  trip on every search is exactly the behaviour that burns quota and hides the
  real problem.
- **State persists** to `runtime_home()/search_provider_health.json` and
  `runtime_home()/search_budget.json` (atomic tmp + replace). A read-only runtime
  dir or a corrupt file degrades to in-memory / empty, never to *wrong*.
- **Units are never mixed.** The ledger refuses a charge whose unit does not
  match the provider's allowance (a Tavily credit can never be subtracted from a
  Serper query count). That is how a "budget" turns into fiction.

### Routing

Provider order is a **static, documented policy** — a free anonymous tier does
not emit the usage data a learned ranker would need, and inventing one would be a
fabricated ranking.

| mode | chain | why |
|---|---|---|
| `auto` (default) | `tavily_keyless → tavily → exa → serper → duckduckgo` | best ranked result per unit cost |
| `web` | `tavily_keyless → tavily → serper → exa → duckduckgo` | Google's own ranking first |
| `semantic` | `exa → tavily → tavily_keyless → duckduckgo` | conceptual similarity |
| `news` | `tavily_keyless → tavily → exa → serper → duckduckgo` | news topic / news category / `/news` |
| `similar` | `exa` | find-similar is Exa-only; **never** faked with a keyword search |

`merge: true` queries every eligible provider and unions the results
(deduplicated by URL). Better recall, one paid call per provider — off by
default.

---

## Hardware truth, encoded

Provider status lives in `truth.py` as data with sources and check dates, and
`truth.assert_selectable()` **refuses** anything that is not `keyless` or
`free_tier`. A provider absent from the table is refused too, not assumed fine.

| provider | status | encoded as |
|---|---|---|
| **Bing Search API** | **RETIRED** | `availability="retired"`, evidence: shutdown 2025-08-11, endpoint returns HTTP 410. `assert_selectable` raises `ProviderRetiredError(status_code=410)`. |
| **Google Custom Search** | **CLOSED** | `availability="retired"`: 100 queries/day, closed to new customers, migrate before 2027-01-01. |
| **Brave Search API** | **UNVERIFIED / CONTESTED** | `availability="contested"`. Sources disagree (2,000 queries/month at 1 q/s vs. a retired free tier replaced by a $5/month credit). Refused by default; requires `allow_unverified_providers: true`. **Not built on.** |
| **SearXNG** | licence-blocked | AGPL-3.0. Documented as an operator-run separate HTTP service only. **See below.** |
| **Firecrawl** | licence-blocked | terms are not a permissive OSS licence. Not a dependency, not vendored. |

A test asserts none of these appears in `PROVIDER_ORDER`, in `PROVIDERS`, or in
any mode chain.

### Licence gate — the owner's call, not this package's

The repo accepts MIT/Apache-2.0/BSD and treats AGPL as ideas-only. SearXNG is
AGPL-3.0, so it is **neither vendored nor a dependency**. It may still be run as
the operator's own separate HTTP service and reached over `base_url`, which keeps
the AGPL code out of this repository and out of this process.

**Taking that decision is the repository owner's call.** This package records the
option, its licence, and its consequences; it does not make the decision and does
not treat either answer as settled. `truth.licensing_notes()` returns exactly
that pair for the owner to rule on.

---

## Registering the tools

```yaml
# config.yaml
- name: web_search
  group: web
  use: alpha.community.search_federation.tools:web_search_tool
  max_results: 5
  search_depth: basic     # advanced = 2x credits
  mode: auto

- name: web_find_similar
  group: web
  use: alpha.community.search_federation.tools:web_find_similar_tool
  max_results: 5

- name: web_search_providers   # diagnostics
  group: web
  use: alpha.community.search_federation.tools:web_search_providers_tool
```

With no keys set, the default chain still works via `tavily_keyless` with
`duckduckgo` as the floor.

Credentials are read at call time:

```
TAVILY_API_KEY=...   # optional, 1,000 credits/month
EXA_API_KEY=...      # optional, semantic + find-similar
SERPER_API_KEY=...   # optional, raw Google SERP JSON
```

---

## Programmatic use

```python
from alpha.community.search_federation import SearchFederation, SearchRequest
from alpha.community.search_federation.errors import SearchExhaustedError

fed = SearchFederation()
try:
    outcome = fed.search(SearchRequest(query="...", max_results=5, search_depth="basic"))
    print(outcome.provider, outcome.cost_amount, outcome.cost_unit)
    for hit in outcome.hits:
        print(hit.rank, hit.title, hit.url, hit.score)
except SearchExhaustedError as exc:
    print("nothing could serve this:", exc.attempts, exc.skipped)

print(fed.describe())   # health, budget, chains, and what was left out
```

## Architecture

| module | responsibility |
|---|---|
| `errors.py` | the closed typed-error taxonomy |
| `truth.py` | provider status + licence table; the selection guard |
| `budgets.py` | credit economics as data; the persisted allowance ledger |
| `providers.py` | one HTTP seam (`request`) + four adapters + the DuckDuckGo adapter |
| `federation.py` | tri-state health, cooldown, planning, failover, honest exhaustion |
| `tools.py` | LangChain tools and the JSON error contract |

`providers.request` is the **only** thing that touches the network; tests stub
that one function and exercise every layer above it for real.

## License

Same license as the rest of the repository. No third-party code is vendored.
