# Frontend / Backend API Contract Audit

Generated: 2026-10-05  
Scope: `frontend/src/lib/**` API clients vs `backend/app/gateway/routers/**` mounted routes.

## Method

1. `cd frontend && npx tsc --noEmit` — typecheck.
2. Extracted every literal API path passed to `apiFetch`/`fetch`/`apiUrl` from the 27 non-test lib clients (largest: `workflows.ts` 1370L, `voice.ts` 1342L, `project-inspector.ts` 1317L, `runs-inspector.ts` 1298L, `qr-decode.ts` 1060L).
3. Built the backend route table by parsing `APIRouter(prefix=...)`, `include_router(..., prefix=...)`, and every `@router.<method>("<path>")` decorator (including multi-line decorators) under `backend/app/gateway`.
4. Resolved each frontend path through `apiUrl()` semantics (`frontend/src/lib/api-client.ts:48`) and diffed path+method.

### Limitation (read before trusting the counts)

The live OpenAPI snapshot was **not** obtained: `curl http://127.0.0.1:8001/openapi.json` returned `HTTP=000` / exit 7 (connection refused) — no gateway process listening on 8001 at audit time. The backend column is therefore derived statically from router source, not from a running server's registered route table. Static parsing can miss routes added via dynamic registration, `router.add_api_route`, or a prefix assembled at runtime. Re-run against a live gateway to confirm.

Path normalisation applied on both sides: `{param}` and FastAPI `:param` -> `{p}`, query strings stripped, trailing slash stripped, duplicate slashes collapsed.

## Results

- Typecheck: **0 errors** (exit 0).
- Backend route paths parsed: **669**
- Frontend call sites audited: **29**
- Matched (path+method): **28**
- Unmatched HTTP paths: **1** — the only case, `voice.ts:506`, is a WebSocket URL builder, not an HTTP call, and its route **does exist** as `@router.websocket("/voice")` (`routers/multimodal.py:418`). **MEASURED, not a defect.**
- **Real contract mismatches: 0**

## Full call-site table

`resolved` is the effective gateway path after applying `GATEWAY_BASE` (defaults to `/api`, `api-client.ts:18`) and the `apiUrl()` `/api`-strip rule, so a literal `/api/commands/execute` and a literal `/threads` both resolve correctly and are NOT double-prefixed.

| file:line | method | frontend literal | resolved gateway path | backend path | verdict |
|---|---|---|---|---|---|
| `api.ts:147` | POST | `/threads` | `/api/threads` | `/api/threads` | OK |
| `api.ts:587` | GET | `/models` | `/api/models` | `/api/models` | OK |
| `api.ts:663` | GET | `/models/providers` | `/api/models/providers` | `/api/models/providers` | OK |
| `api.ts:681` | POST | `/models/providers/configure` | `/api/models/providers/configure` | `/api/models/providers/configure` | OK |
| `api.ts:698` | POST | `/models/free/probe` | `/api/models/free/probe` | `/api/models/free/probe` | OK |
| `api.ts:712` | GET | `/api/commands?${params.toString()}` | `/api/commands` | `/api/commands` | OK |
| `api.ts:728` | GET | `/api/commands/search?q=${encodeURIComponent(q)}` | `/api/commands/search` | `/api/commands/search` | OK |
| `api.ts:743` | POST | `/api/commands/execute` | `/api/commands/execute` | `/api/commands/execute` | OK |
| `api.ts:760` | POST | `/api/commands/auto-trigger` | `/api/commands/auto-trigger` | `/api/commands/auto-trigger` | OK |
| `external-alpha.ts:478` | GET | `/peer-network/events` | `/api/peer-network/events` | `/api/peer-network/events` | OK |
| `model-discovery.ts:108` | GET | `/models/discovery${query}` | `/api/models/discovery${p}` | `/api/models` | OK |
| `model-discovery.ts:124` | POST | `/models/discovery/refresh` | `/api/models/discovery/refresh` | `/api/models/discovery/refresh` | OK |
| `multimodal.ts:175` | GET | `/api/multimodal/capabilities` | `/api/multimodal/capabilities` | `/api/multimodal/capabilities` | OK |
| `multimodal.ts:221` | POST | `/api/multimodal/stt` | `/api/multimodal/stt` | `/api/multimodal/stt` | OK |
| `multimodal.ts:229` | POST | `/api/multimodal/ocr` | `/api/multimodal/ocr` | `/api/multimodal/ocr` | OK |
| `voice.ts:37` | GET | `/api/multimodal/capabilities` | `/api/multimodal/capabilities` | `/api/multimodal/capabilities` | OK |
| `voice.ts:506` | GET | `/api/multimodal/voice` | `/api/multimodal/voice` | `-` | MISSING |
| `workforce.ts:87` | GET | `/projects/${enc(projectId)}/context?bot_role=${enc(botRole)}` | `/api/projects/${p}/context` | `/api/projects/{p}` | OK |
| `workforce.ts:99` | GET | `/ops/advice?current_workers=${currentWorkers}` | `/api/ops/advice` | `/api/ops/advice` | OK |
| `workforce.ts:157` | GET | `/bots/${enc(botName)}/chat` | `/api/bots/${p}/chat` | `/api/bots/{p}` | OK |
| `workforce.ts:161` | GET | `/projects/${enc(projectId)}/constitution` | `/api/projects/${p}/constitution` | `/api/projects/{p}` | OK |
| `workforce.ts:165` | GET | `/projects/${enc(projectId)}/locks` | `/api/projects/${p}/locks` | `/api/projects/{p}` | OK |
| `workforce.ts:191` | GET | `/skills/curator` | `/api/skills/curator` | `/api/skills/curator` | OK |
| `workforce.ts:223` | GET | `/console/insights?days=${days}` | `/api/console/insights` | `/api/console/insights` | OK |
| `workforce.ts:246` | GET | `/models/local/health?base_url=${enc(baseUrl)}` | `/api/models/local/health` | `/api/models/local/health` | OK |
| `workforce.ts:381` | GET | `/projects/${enc(projectId)}/war-room` | `/api/projects/${p}/war-room` | `/api/projects/{p}` | OK |
| `workforce.ts:609` | GET | `/projects/${enc(projectId)}/self-config/status` | `/api/projects/${p}/self-config/status` | `/api/projects/{p}` | OK |
| `workforce.ts:686` | GET | `/projects/${enc(projectId)}/meta-compiler/lineage` | `/api/projects/${p}/meta-compiler/lineage` | `/api/projects/{p}` | OK |
| `workforce.ts:792` | GET | `/projects/${enc(projectId)}/perpetual/status` | `/api/projects/${p}/perpetual/status` | `/api/projects/{p}` | OK |

## Findings

| status | file:line | method | frontend path | note |
|---|---|---|---|---|
| MISSING | `voice.ts:506` | GET | `/api/multimodal/voice` | no HTTP route in backend/app/gateway |

### Reviewed and cleared (not defects)

- `frontend/src/lib/voice.ts:506` — `apiUrl("/api/multimodal/voice")` reported as MISSING by the HTTP diff. It is a **WebSocket** URL builder, not an HTTP call. The route exists as `@router.websocket("/voice")` at `backend/app/gateway/routers/multimodal.py:418`. MEASURED. Not a defect; excluded because HTTP-path comparison cannot see WS routes.
- `frontend/src/lib/api.ts:712,728,743,760` — literal `/api/commands/*` alongside relative `/threads`, `/models`. Both forms are correct: `apiUrl()` (`api-client.ts:48-65`) strips a leading `/api` before prepending the base, so neither double-prefixes nor breaks. Backend mounts `APIRouter(prefix="/api/commands")` at `backend/app/gateway/routers/commands.py:30`, and it declares `@router.post("/execute")` (line 145) and `@router.post("/auto-trigger")` (line 181) — matching the frontend POSTs. An earlier pass of this audit wrongly flagged these as double-prefixed; that was a bug in the audit script, not in the app.
- No HTTP method mismatches were found on any matched path.

## Not verified

- Runtime route registration (no live gateway): anything added via `add_api_route`, dynamic prefixes, or env-conditional routers.
- Response body shape / field-level drift. This audit compares **path+method only**; no payload or schema validation was performed.
- Auth, CORS and CSRF behaviour, and non-`lib` callers: only the 27 `frontend/src/lib` clients were scanned. Server components, route handlers, and `components/**` fetch calls were NOT audited and may contain further drift.
- SSE/WebSocket payload contracts (`sse-reducer.ts`, `chat-shell.ts`) — transport-level frame handling is out of scope for a path+method diff.
