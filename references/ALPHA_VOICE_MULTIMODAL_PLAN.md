# Alpha — Voice & Multimodal Capability Plan (Mic / Speaker / Wake Word / Image / OCR / TTS / STT)

**Status:** DRAFT-for-review → implementation contract for one subagent wave
**Reality snapshot:** 2026-09-23, HEAD `4fa9a79`, working tree = pending wave-2 batches
**Package root:** `backend/packages/harness/alpha/` · **Frontend root:** `frontend/src/`
**Source request (user, verbatim intent):**
> "my ai agent need to have a mic and speaker and wake word … all this features
> implementation need to be completely free if possible use the llm provider available —
> this feature means use that feature; if llm provider not available that feature means
> fall back to the next feature provider available model … like in our llm list any llm
> provider image model means for that task it can fall back to the image model, or the
> system has complete free self-hosted complete free lightweight things for all these
> features — like for text-to-speech edge-tts, openwhisper, image OCR tesseract —
> do search for the best online and enhance this plan, trigger one subagent for all this task."

---

## 0. Requirements distilled

1. **Mic input, speaker output, wake word** for the agent (browser capture → agent, agent → speaker).
2. **Completely free**: every tier must be free — no paid key may be *required*.
3. **Capability-fallback router**: for each capability, walk an ordered chain:
   **T1** a model already in our `models:` list that declares the capability →
   **T2** the next free (keyless) provider that offers it →
   **T3** a free, self-hosted, lightweight local engine.
4. Capabilities required: `wake_word`, `stt` (speech→text), `tts` (text→speech),
   `vision` (image understanding — "image model"), `ocr` (image→text), `image_gen`.
5. Research the best free options online (done — §1) and enhance the plan accordingly.

---

## 1. Online research findings (2026-09-23, honest caveats included)

| Capability | Chosen primary | Evidence consulted | Honest caveats |
|---|---|---|---|
| Wake word | **openWakeWord** (pip, `openwakeword`) — server-side scoring of mic frames | PyPI openwakeword: open-source wakeword framework, pretrained models ("hey jarvis" class), focus on performance/simplicity, CI'd releases | Pretrained model downloads on first use (network once, then cached in workspace runtime dir). Threshold tuning per-mic is inherent — expose configurable threshold, disclose scored value. |
| Wake word (browser alternative) | vosk-browser (WASM, 13 languages, mic in WebWorker) | GitHub vosk-browser README | Ships ~MB-scale WASM models to the browser; deferred as Phase-B alternative, not default (keeps server the source of truth). |
| TTS T2 (keyless network) | **edge-tts** (Microsoft Edge online neural voices, no key) | Widely-documented free endpoint, pip package | Microsoft endpoint, no SLA, needs network; honest failure → chain continues. Explicitly *not* self-hosted (that's T3). |
| TTS T3 (local offline) | **Piper** (pip `piper-tts`, MIT, "fast, local neural TTS… fully offline", runs on low-power devices) | olud.ai Piper-vs-Kokoro comparison (2026): Piper = fast local neural, MIT, fully offline, many voices; Kokoro = 82M params, Apache-2.0, near-instant synthesis | Voice model (~MB–100MB) downloads once. Kokoro noted as the higher-quality/lighter alternative if its wheels install on py3.12/Windows; whichever installs gets wired, the other stays an honest `not_installed` option. |
| STT T3 (local) | **faster-whisper** — *already integrated* in `alpha/media/stt.py` (reused, not duplicated); vosk kept as an optional lighter alternative | SYSTRAN/faster-whisper speed tests (issue #9): CTranslate2 int8 CPU inference well ahead of plain whisper | `stt_available()` is `False` until the voice extra is installed — chain must disclose honestly, never fake transcripts. |
| STT T2 (keyless network) | **None confirmed.** AI Horde's current v2 API listing exposes image/text generation and interrogation only — no STT endpoint visible | aihorde.net/api listing (fetched this session) | Tier simply does not exist for STT today → chain reports T2 as `skipped_no_provider`, not as a failure. Do not invent an endpoint. |
| OCR T3 (local) | **RapidOCR** (`rapidocr-onnxruntime` — PaddleOCR models in ONNX runtime) primary; **Tesseract** (`pytesseract` + system binary) if present | GitHub mftnakrsu/Comparison-of-OCR engine table: Tesseract tiny but needs system binary, best on clean scans; RapidOCR "the lightest way to get modern accuracy" (onnxruntime); EasyOCR/PaddleOCR heavy (torch/paddle) → excluded as defaults | Windows Tesseract needs UB-Mannheim installer → honest `not_installed` unless found on PATH. EasyOCR/PaddleOCR deliberately excluded (not lightweight). |
| OCR/Vision T1 | configured model with `supports_vision: true` (union-alpha has it today) | `model_config.py:66`, `view_image_middleware.py:306` (`image_url` block), `lead_agent/agent.py:681` | Vision wiring already exists — T1 reuses the chat path; OCR-via-vision prompt is a T1 `ocr` strategy. |
| Image gen T2 (keyless) | **AI Horde anonymous** — already proven in our free-router (`AI_HORDE_ANONYMOUS_KEY` documented constant `0000000000`) | AI Horde v2 API (`POST /v2/generate/async` + pop/status); our own `free_router/providers.py` uses it | Anonymous queue can be slow → bounded timeouts, honest `timeout` attempt label. |
| Image gen T2 rejected | Pollinations | gen.pollinations.ai docs fetched this session: *"All generation requests require an API key from enter.pollinations.ai"* | **No longer keyless** → excluded from T2. Listed as an optional T1-style provider only if the user configures a key themselves (never hardcoded). |
| Image gen T3 (local) | None by default — local diffusion is not lightweight | — | Honest `not_installed`/`no_local_engine` entry; out of default scope (§12). |

**Design consequence of the research:** the chain is genuinely free at every step —
T1 only fires for models the user already configured, T2 only uses keyless endpoints
(edge-tts, AI Horde anonymous), T3 only uses permissive-license self-hosted engines.

---

## 2. Reality mapping — what already exists (verified this session)

| Existing asset | Path (verified) | How the plan uses it |
|---|---|---|
| Local STT worker with honest degradation | `alpha/media/stt.py` (`Transcription{ok,text,language,engine,reason}`, `stt_available()`, `transcribe_file()`, suffix/size validation) | **Reuse verbatim as the STT T3 engine.** Chain calls it; never reimplement whisper loading. |
| Vision flag on models | `alpha/config/model_config.py:66` `supports_vision: bool` | Vision T1 selection + OCR-via-vision T1 strategy. |
| Image → message wiring | `alpha/agents/middlewares/view_image_middleware.py:306-307` (`"type":"image_url"`) | Understanding an image in-chat already works when a vision model is configured — chain delegates, does not duplicate. |
| Free-router patterns | `alpha/models/free_router/{providers,catalog,chat_model}.py` | Mirror: single invoke seam, honest `attempts` labels `(engine, class+status)`, `ConnectionError` subclass for exhaustion, tri-state availability, no fabricated success. |
| WebSocket auth pattern (auth untouched) | `browser.py:117 _authenticate_ws`, `browser.py:206 @router.websocket` | Voice WS reuses the *same helpers/pattern* by import/call only — **no auth module edits ever.** |
| Composer attach precedent | `Composer.tsx:41-250` (`onAttach`), `ChatView.tsx:674 handleAttach` | Mic button mounts beside the attach button; audio flows through the same composer ownership. |
| Optional-deps machinery | `backend/packages/harness/pyproject.toml` `[project.optional-dependencies]` (`tui`, `ollama`, `postgres`, …) | New `voice` extra (§9) — core install stays lean, honest `not_installed` without it. |
| Feature-manifest regen (official) | `backend/scripts/generate_feature_manifest.py`; `contracts/feature_manifest.json` keys include `routers`, `tools`=119 | New multimodal router → run official script; **gate: `tools` array byte-identical (119), only the new router entry appears** (+ generated_at/version). |
| Settings card pattern | `SettingsSection.tsx:216` "Workspace Profile" card | Voice card added beside existing cards (additive JSX). |
| Nothing exists yet for TTS/OCR/wake | `git grep -i "edge_tts\|tesseract\|wake_word\|image_gen"` → 0 hits in backend | Greenfield per capability — no duplication risk, no deletion. |

**Explicitly not forked:** `alpha/media/stt.py` (the user's "openwhisper" example is already
satisfied by faster-whisper integration that exists); vision message wiring; free-router;
browser WS auth.

---

## 3. Capability-chain architecture (the "fallback to next provider" requirement)

```
invoke(capability, payload)            ← THE single module-level seam
  ├─ T1 provider      models where capability ∈ model.capabilities (new additive flag)
  │                   OpenAI-compatible endpoints: /v1/audio/speech, /v1/audio/transcriptions,
  │                   /v1/images/generations; vision/ocr-via-vision → chat path (supports_vision)
  ├─ T2 keyless       tts → edge-tts        image_gen → AI Horde anonymous
  │                   (stt/ocr/image-understanding: none honest → tier reports skipped_no_provider)
  └─ T3 local         tts → piper           stt → alpha.media.stt (faster-whisper)
                      ocr → rapidocr → pytesseract   wake_word → openwakeword
                      vision → none by default (honest no_local_engine; T1 covers it)
```

- **Ordered failover:** T1 engines are tried in config order; retryable failures
  (`is_retryable_llm_error` — timeouts/429/5xx/connection) advance to the next engine;
  deterministic 4xx (bad key/request) **do not** silently skip to T3 — they surface in
  `attempts` and the chain advances only where the tier contract says so (provider 4xx
  → next provider; provider tier exhausted → T2). Every hop records
  `(tier, engine, class+status)`.
- **Exhaustion:** `MultimodalUnavailableError(ConnectionError)` carrying the full honest
  `attempts` list → HTTP 503 JSON for API callers, honest UI error for the frontend.
  **Never** emit placeholder audio/text/"success".
- **Availability probing:** `GET /api/multimodal/capabilities` returns one row per
  capability × tier × engine: `status ∈ available | not_installed | not_configured |
  skipped_no_provider | probe_failed` plus `detail` (e.g. `ImportError: …`). Observed
  only — availability is never asserted from config alone.
- **Lazy imports everywhere:** heavy libs import inside engine functions (keeps
  `test_cold_imports` green; `alpha/multimodal/__init__.py` re-exports thin symbols so
  `test_no_orphan_modules` sees the modules as referenced).

---

## 4. Backend design (paths, shapes)

```
backend/packages/harness/alpha/multimodal/
  __init__.py          # thin re-exports (Capability, CapabilityResult, MultimodalUnavailableError, invoke)
  capabilities.py      # Capability(StrEnum): WAKE_WORD STT TTS VISION OCR IMAGE_GEN
                       # CapabilityResult dataclass: ok, capability, tier, engine, data(dict),
                       #   attempts:list[{tier,engine,error}], note:str
  errors.py            # MultimodalUnavailableError(ConnectionError): attempts, capability, __str__ honest
  chain.py             # invoke(capability, payload) -> CapabilityResult  [single seam]
                       # module-level per-tier engine hooks tests monkeypatch:
                       #   _invoke_t1, _invoke_t2, _invoke_t3 (thin dispatchers, real impls import lazily)
  wav.py               # stdlib `wave` helpers: pcm16 frames -> WAV bytes, size/sanity checks
  wakeword.py          # WakeWordSession: push_frame(pcm16) -> events; threshold via openWakeWord if
                       #   installed else honest engine-not-installed status; scored value disclosed
  engines/
    __init__.py
    provider.py        # T1: resolve models with capability flag -> OpenAI-compatible calls via httpx
                       #   (same auth-error honesty rules as free_router; keys read from config only)
    keyless.py         # T2: edge_tts (synthesis to mp3 bytes), aihorde_image (anonymous generate+poll)
    local.py           # T3: piper_tts, faster_whisper (delegates to alpha.media.stt), rapidocr_ocr,
                       #   pytesseract_ocr, openwakeword_wake

backend/app/gateway/routers/multimodal.py   # NEW router module (authorized exception, §7)
backend/app/gateway/app.py                  # ONE include_router line (authorized exception, §7)
backend/packages/harness/alpha/config/voice_config.py   # VoiceConfig pydantic model
backend/packages/harness/alpha/config/model_config.py   # additive: capabilities: list[str] = []
backend/packages/harness/alpha/config/app_config.py     # additive: voice: VoiceConfig = VoiceConfig()
backend/tests/test_multimodal_chain.py
backend/tests/test_multimodal_router.py
backend/tests/test_multimodal_wakeword.py
```

`model.capabilities` validated against `{tts, stt, image_gen, vision, ocr}` at config
load (loud error on unknown value — misconfiguration must never silently no-op).

`VoiceConfig` (all defaulted → **no `config_version` bump**, consistent with the
`auto_promote` precedent; example/live YAML edits are done centrally by the main agent,
**not** by the subagent):

```yaml
voice:
  enabled: true
  wake_word: { engine: openwakeword, threshold: 0.5, armed_default: false }
  tts: { autoplay: false, voice: null }        # null → engine default
  stt: { model_size: small, language: null }
```

---

## 5. HTTP / WebSocket API (all additive)

| Method | Path | Contract |
|---|---|---|
| GET | `/api/multimodal/capabilities` | honest availability matrix (§3 probing row shape) |
| POST | `/api/multimodal/tts` | `{text, voice?, engine?}` → **binary audio** (mp3/wav) + `X-Alpha-Engine`, `X-Alpha-Tier` headers; 503 + attempts JSON on exhaustion; 422 on empty/oversized text |
| POST | `/api/multimodal/stt` | multipart `audio` → `{ok, text, language, engine, tier, attempts, note}`; 422 on bad suffix/oversize (reuse media limits: suffixes `.wav .mp3 .m4a .ogg .flac .opus .webm`, 25 MiB) |
| POST | `/api/multimodal/ocr` | multipart `image` → `{ok, text, engine, tier, attempts, note}` (engine = rapidocr/tesseract/vision) |
| POST | `/api/multimodal/image-gen` | `{prompt, size?}` → `{ok, url|b64, engine, tier, attempts, note}` (AI Horde anonymous unless a configured model declares `image_gen`) |
| WS | `/api/multimodal/voice` | frames **in:** `{"type":"audio","data":"<b64 pcm16 16k mono>"}`; events **out:** `{"type":"capabilities"…}` on connect, `{"type":"wake","score":0.83,…}` (score always disclosed), `{"type":"transcript","text":…}` after wake→STT, `{"type":"engine","capability":…,"status":…,"detail":…}` honest errors; **auth exactly like `browser.py _authenticate_ws` (import/call the same helpers; zero auth-file edits)** |

No new builtin tools; no router other than `multimodal.py`; no auth changes.

---

## 6. Frontend design

```
frontend/src/lib/multimodal.ts       # typed fetch client for the 5 HTTP endpoints (+ X-Alpha-Engine parsing)
frontend/src/lib/voice.ts            # VoiceSession: getUserMedia + AudioWorklet → PCM16 16 kHz mono frames
                                     #   → WS; state machine WAKE_ARMED → LISTENING → PROCESSING → SPEAKING;
                                     #   honest terminal states: engine_missing, auth_failed, ws_closed(reason)
frontend/src/lib/voice.test.mjs      # pure state-machine + frame-encoding tests (node --test, no browser)
frontend/src/components/VoiceControls.tsx   # mic button (push-to-talk + wake-arm toggle), speaker autoplay toggle,
                                     #   status line shows engine + honest errors (never fake "listening")
frontend/src/components/Composer.tsx        # mount VoiceControls beside the attach button (minimal diff)
frontend/src/components/MessageItem.tsx     # speaker button on assistant messages → POST /tts → <audio> playback
frontend/src/components/sections/SettingsSection.tsx  # "Voice & Speakers" card: wake threshold, autoplay,
                                             #   engine matrix read from GET /capabilities (observed statuses)
```

Playback: `URL.createObjectURL(blob)` + `<audio>`; revoke on cleanup. Wake-word arm is
**off by default in the UI** (mic stays user-initiated) — privacy-preserving, and
honest: no always-on listening unless the user arms it.

---

## 7. Authorized exceptions for THIS wave only

1. **New router module** `app/gateway/routers/multimodal.py` + **one** `include_router`
   line in `app.py` + official regen `cd backend && .venv\Scripts\python.exe scripts\generate_feature_manifest.py`.
   **Gate:** `git diff contracts/feature_manifest.json` shows only the new router entry
   (+ `generated_at`/`version`), `tools` stays 119 and byte-identical,
   `pytest tests\test_feature_manifest_wiring.py` green.
2. **Optional-deps block** in `backend/packages/harness/pyproject.toml` (append a `voice` extra).
3. Frontend files listed in §6.

Everything else in §11 remains forbidden.

---

## 8. Test plan & gates (what the subagent must run and report)

**New tests (seam-stubbed — no network, no model downloads inside pytest):**

1. `test_multimodal_chain.py` — tier order T1→T2→T3; retryable vs deterministic failover
   semantics; `attempts` carry real labels; exhaustion raises `MultimodalUnavailableError`
   whose message contains every attempt; `skipped_no_provider` for STT-T2/OCR-T2;
   unknown capability → honest error; a missing engine import skips its tier and the
   chain still proceeds; `capabilities` validation rejects unknown values.
2. `test_multimodal_router.py` — capabilities matrix rows for stubbed
   available/not_installed; TTS response bytes non-empty + engine headers honored
   (stub seam returns fixed bytes — never generated audio unless the real engine ran);
   STT/OCR upload validation (bad suffix, oversize → 422 honest detail); image-gen
   failover T1→T2 attempts; WS rejects unauthenticated handshake; 503 body = attempts JSON.
3. `test_multimodal_wakeword.py` — frame buffering & window math; threshold event with
   disclosed score; `engine=not_installed` honest path when openWakeWord absent;
   transcript handoff calls the STT seam exactly once per wake; corrupt/non-pcm frames
   → honest error, session survives.
4. `frontend/src/lib/voice.test.mjs` — state machine transitions + terminal-honesty states.

**Gates (all must pass, commands recorded in the report):**

```
cd backend
.venv\Scripts\python.exe -m pytest tests\test_multimodal_chain.py tests\test_multimodal_router.py ^
  tests\test_multimodal_wakeword.py tests\test_feature_manifest_wiring.py ^
  tests\test_no_orphan_modules.py tests\test_cold_imports.py -q --tb=short -p no:cacheprovider
.venv\Scripts\python.exe -m ruff check <touched .py files>          → All checks passed
.venv\Scripts\python.exe "C:\Users\PREM KUMAR\AppData\Local\Temp\opencode\ast_dup_gate.py" <touched .py> → ast_dup_gate_dups=0
.venv\Scripts\python.exe scripts\generate_feature_manifest.py       → manifest diff gate (§7.1)
cd ../frontend  → tsc --noEmit (must stay clean) + node --test src/lib/voice.test.mjs
```

Tests isolate `AGENT_WORKSPACE_HOME` to a temp dir (the test env does NOT isolate it).

**Install attempt (allowed, results reported honestly per package):**

```
.venv\Scripts\python.exe -m pip install edge-tts faster-whisper openwakeword rapidocr-onnxruntime piper-tts
```

A wheel that fails on py3.12/Windows is a *finding*, not a blocker: the chain must then
report that engine `not_installed` (its honest path is covered by tests either way).
Attempt an openWakeWord pretrained-model prefetch once; failure → documented.

**Real (network) smoke — python-level only, NO server/gateway restarts:**

- TTS: `chain.invoke("tts", {"text": "…"})` through real edge-tts → report byte size + engine.
- OCR: real rapidocr (or tesseract) on an existing repo image (e.g. `frontend/src/assets/images/*`) → report recovered text.
- Wake word: real openWakeWord over synthetic silence + a tone → report scores (no wake expected on silence is *valid* honest output).
- Image gen: one real AI Horde anonymous generation (bounded timeout) → report queue/wait honestly or the timeout attempt.
HTTP-endpoint live smoke happens later, centrally, after the full suite (main-agent queue).

---

## 9. Dependencies

`pyproject.toml → [project.optional-dependencies] voice = [...]` with version floors
verified at install time against py3.12/Windows (record what actually resolved):

- `edge-tts` (T2 TTS) · `faster-whisper` (T3 STT — also unlocks the pre-existing `media/stt.py`)
- `openwakeword` (wake word) · `rapidocr-onnxruntime` (T3 OCR)
- `piper-tts` (T3 offline TTS) — if its wheel does not resolve, leave it out of the extra
  and keep the engine wired to honest `not_installed` (Kokoro noted as alternative).

Core install stays lean; without the extra every capability still answers honestly
(`not_installed`) — the product must never pretend an engine exists.

---

## 10. Phases (single subagent, ordered)

- **P1** package skeleton + `capabilities.py`/`errors.py`/`wav.py` + chain with seam-only
  tier stubs → `test_multimodal_chain.py` green.
- **P2** real engines (`engines/*`) + installs + python-level smokes (§8) → chain tests still green.
- **P3** router + app.py line + manifest regen + `test_multimodal_router.py` + wiring/no-orphan/cold-import gates.
- **P4** `wakeword.py` + WS endpoint (browser.py auth pattern) + `test_multimodal_wakeword.py`.
- **P5** frontend lib + `VoiceControls` + Composer/MessageItem/Settings wiring + `voice.test.mjs` + `tsc --noEmit`.
- **P6** full gate sweep (§8) + honest report.

---

## 11. Hard constraints (unchanged repo rules)

- **Forbidden:** any auth module edit · any new *builtin tool* · any change to
  `tools`/manifest beyond the official regen delta of §7.1 · `config.example.yaml` and
  live `config.yaml` (main agent adds the `voice:` block centrally) · `docs/**` ·
  `references/**` (read-only) · disabling/weakening/skipping any test · hardcoded
  secrets (AI Horde anonymous sentinel stays the documented `0000000000` pattern) ·
  `config_version` bump (all new fields additive-with-defaults) · files owned by other
  in-flight agents: `alpha/security/memory_redaction.py`, memory-manager internals,
  `token_budget_config.py`, `token_budget_middleware.py`, `dag_engine.py`,
  `cost_governor.py`, `alpha/learning/experience/**`, `experience_tool.py`,
  `alpha/skills/**`, `alpha/models/free_router/**`.
- **Git:** no `commit`/`checkout`/`stash`/`reset`/`add` — leave everything unstaged;
  main agent reviews and commits centrally.
- **Servers:** no gateway/frontend/desktop restarts; no full test suite — targeted runs only.
- **Honesty:** single seam per feature · real evidence only (byte sizes, scores, import
  errors actually observed) · honest `not_installed`/`not_configured`/503+attempts over
  any fabricated success · engine names in responses must be engines that actually ran.

## 12. Out of scope (declared, not silently dropped)

- Local diffusion image generation (not lightweight) — honest `no_local_engine`.
- Browser-side WASM wake word (vosk-browser) — Phase-B alternative.
- Pollinations integration (now keyless-hostile) — only if the user configures their own key later.
- Barge-in/noise suppression/AEC beyond browser `echoCancellation` constraints.
- Electron/desktop-native capture (browser `getUserMedia` is the capture surface).

## 13. Final checklist (status = implementation reality, updated at completion)

| # | Item | Status |
|---|---|---|
| 1 | `alpha/multimodal/` chain with T1→T2→T3 + attempts exhaustion | TODO |
| 2 | Engines: edge-tts, AI Horde image, piper, faster-whisper(via media.stt), rapidocr, tesseract, openwakeword | TODO |
| 3 | Router: capabilities/tts/stt/ocr/image-gen + WS voice (browser.py auth pattern) | TODO |
| 4 | app.py include + manifest regen (tools 119 byte-identical) + wiring test | TODO |
| 5 | `VoiceConfig` + `ModelConfig.capabilities` (additive, no version bump) | TODO |
| 6 | `voice` optional-dep extra + install results reported | TODO |
| 7 | Frontend: VoiceControls, Composer mic, MessageItem speaker, Settings voice card, `voice.ts` | TODO |
| 8 | Tests: chain/router/wakeword/voice.test.mjs + no-orphan/cold-imports/wiring green | TODO |
| 9 | ruff clean + `ast_dup_gate_dups=0` + `tsc --noEmit` clean | TODO |
| 10 | Python-level real smokes with reported evidence (§8) | TODO |
| 11 | Honest report: files, commands, results, limitations, what did NOT install | TODO |
