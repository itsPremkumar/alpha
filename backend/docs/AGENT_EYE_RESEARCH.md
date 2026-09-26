# AgentEye live-source integration

Alpha integrates the AgentEye project as a **pinned source library**, not as an
unrestricted second agent runtime.

## Pinned upstream

- Repository: <https://github.com/itsPremkumar/AgentEye>
- Commit: `455404ab9fd2ebba3c4c2e1e07932ed738a0c510`
- Upstream package version: `6.5.1` in `pyproject.toml` (the repository currently has no Git tag; its `__version__` metadata is stale, so the commit is authoritative)
- License: MIT
- Published on PyPI: **no**; Alpha therefore locks the exact Git commit in
  `backend/pyproject.toml` and `backend/uv.lock`

The pin is intentional. Do not change it to `master`, a version-only
requirement, or the old `agent-search-lite` project name without re-auditing the
new source.

## What Alpha uses

`alpha.community.agent_eye` imports selected fixed-endpoint functions from
AgentEye and two pure helper areas:

- academic/search functions for arXiv, PubMed, Semantic Scholar, Crossref,
  OpenAlex, Wikipedia, Wikidata, and DBpedia;
- developer/package functions for GitHub, GitLab, Bitbucket, Stack Overflow,
  npm, PyPI, Docker Hub, crates.io, Packagist, Go packages, RubyGems, NuGet,
  Maven, CocoaPods, and pub.dev;
- knowledge, government, media, language, weather, finance, Hacker News,
  Lobsters, Lemmy, Mastodon, and Bluesky functions;
- AgentEye's `ranking.py` relevance ranker; and
- AgentEye's `extractors.py` smart HTML/JSON-LD extraction.

Alpha adds the controls the upstream project does not provide safely:

- a config-owned backend allowlist;
- separate opt-in for scraped or volunteer-instance sources;
- at most 20 final results, 12 source functions per query, 1,200-character
  snippets, a 60-second hard ceiling, and a bounded worker queue;
- per-backend error isolation, total timeout reporting, canonical URL
  deduplication, cross-source counts, relevance ordering, bounded response
  reads, defused XML parsing for arXiv, and the same model-visible
  neutralization applied to other remote search results; and
- no process-global AgentEye cache, destructive cache/index tool, or query
  expansion multiplier.

## What Alpha intentionally excludes

The upstream `AgentSearchLite` object and its orchestration are not called. The
integration also does not start AgentEye's HTTP API or MCP server and does not
expose its arbitrary URL crawler, document/image/video readers, global cache
controls, or advertised general fan-out.

Those surfaces have independent trust and correctness problems, including
unchecked SSRF, unauthenticated network services, unbounded downloads, local
path reads, dead/duplicate backends, request amplification, and a broken MCP
entry point. Alpha already has guarded `web_fetch`, browser, upload, OCR, and
sandbox implementations; those remain the boundary for interactive content.

## Configuration

The default `config.example.yaml` enables two supplemental tools in the `web`
group:

```yaml
- name: agent_eye_sources
  group: web
  use: alpha.community.agent_eye.tools:agent_eye_sources_tool

- name: agent_eye_search
  group: web
  use: alpha.community.agent_eye.tools:agent_eye_search_tool
  max_results: 8
  max_sources_per_query: 6
  timeout_seconds: 30
  use_for_deep_research: true
  allow_fragile_backends: false
```

`agent_eye_sources` makes no network request. It reports whether the pinned
runtime modules loaded, the categories and backend names currently allowed by
the operator, and the active limits. `agent_eye_search` accepts a
query, a category (`auto`, `web`, `academic`, `code`, `packages`, `news`,
`social`, `knowledge`, `government`, `science`, `media`, `finance`, or
`language`), and optional explicit backends.

To keep a source enabled, add it to `allowed_backends`. Scraped/volunteer
sources additionally require:

```yaml
allow_fragile_backends: true
```

The model cannot enable a blocked source by changing tool arguments. GitHub
search works without credentials and automatically uses `GITHUB_TOKEN` when the
server already provides it; the token is never returned in output.

## Deep research

When `use_for_deep_research: true`, Alpha's existing `deep_research` engine uses
the AgentEye adapter for each search lane and retains DuckDuckGo as a real
fallback. The built-in AgentEye HTML extractor runs only after Alpha's guarded
fetcher has:

1. validated the initial public HTTP(S) URL;
2. disabled automatic redirects;
3. revalidated every redirect target;
4. capped redirect count and downloaded bytes; and
5. rejected non-text responses.

If discovery returns no sources, the report status is `no_evidence`. Alpha does
not create a placeholder citation, claim, `example.org` URL, or unconditional
`success` status. `citations_registered` is the number of source URLs, while
`citations_verified` counts only sources whose sampled findings received a
semantic support verdict.

`deep_research` report destinations are filename-only and are written under the
current thread's outputs directory. Host paths, nested relative paths, and
symbolic-link targets are rejected.

## What “free” means

“No API key” does **not** mean unlimited, guaranteed, or unrestricted:

- public APIs have quotas and may require registration for higher limits;
- search-engine and social-site scrapers can break when markup or consent flows
  change;
- volunteer instances can be unavailable or untrusted;
- upstream terms, robots policies, and rate limits still apply; and
- a result is discovery evidence, not proof that its claim is correct.

Use original-source fetching and citation support for load-bearing claims.
Always report provider failures and timeouts honestly.

## Verification

Offline tests:

```bash
cd backend
uv run pytest tests/test_agent_eye_integration.py \
  tests/test_deep_research_engine.py \
  tests/test_deep_research_tool.py -q
uv run pytest tests/test_no_orphan_modules.py \
  tests/test_tool_name_references.py -q
```

Tool assembly and dependency checks:

```bash
uv run python scripts/check_tool_schemas.py
uv lock --check
```

A live smoke check is intentionally separate from the default suite because
public providers are rate-limited and can be temporarily unavailable:

```powershell
$env:AGENT_WORKSPACE_RUN_LIVE_TESTS="1"
uv run pytest tests/test_agent_eye_integration.py::test_agent_eye_academic_sources_live_smoke -q
```

Use a small, targeted provider check rather than a full upstream “doctor”
fan-out.
