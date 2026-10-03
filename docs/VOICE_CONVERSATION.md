# Real-Time Voice Conversation

Alpha can run a hands-free voice loop without sending audio to a paid speech API:

1. The browser captures microphone PCM locally.
2. Alpha's Gateway performs voice endpointing and interim transcription with a local **faster-whisper** model.
3. The final transcript enters the normal chat/run pipeline and streams back over the existing SSE transport.
4. Completed response sentences are synthesized locally with **Piper** and played while later text is still streaming. Kokoro is available as a more natural voice on faster hardware.
5. When playback finishes, Alpha resumes listening for the next turn.

Only the configured language model may use a paid API. Speech recognition and synthesis run on the Alpha host and require no speech-service key.

## Engines

Every engine below is free, offline, and open source. `make voice-setup` installs them all.

| Stage | Engine | Notes |
| --- | --- | --- |
| Speech to text | faster-whisper `small` | Interim + final transcripts; set `stt.model_size` for `base` |
| Text to speech (default) | **Piper** | Measured real time on CPU: ~1.7 s of synthesis per 3.4 s of audio |
| Text to speech (natural) | Kokoro 82M ONNX | Apache-2.0, 54 voices across 8 languages; **needs a much faster CPU** — see [Choosing a voice](#choosing-a-voice) |
| Wake word | openWakeWord | On-device scoring; see [Wake word](#wake-word) |
| Endpointing | WebRTC VAD | Decides when you stopped speaking |

Both speech engines are installed by one command, so switching is one config line.

### Choosing a voice

Kokoro is markedly more natural than Piper, but "natural" is not the constraint that
matters for a conversation loop — latency is. Measured on a 16-core CPU-only Windows
host, the same ~3.5-second sentence took:

| Engine | Load | Per sentence | Audio produced | Verdict |
| --- | --- | --- | --- | --- |
| Piper | ~2 s | **1.7 s** | 3.4 s | Real time, comfortably |
| Kokoro (int8) | ~15 s | **~70 s** | 3.9 s | ~18x slower than real time |

Kokoro's cost is the ONNX decoder, not phonemization (phonemization is ~2.5 s of it),
and it does not improve with more threads on this class of hardware — 2 threads and 16
threads measured within noise of each other. On a fast desktop or any machine with a
CUDA or DirectML provider, set `engine: kokoro` and it becomes the better-sounding
choice; on a modest or busy host, Piper is the one that actually keeps up with a
conversation.

Alpha therefore defaults to `engine: piper` and never silently switches engines for
you. `GET /api/multimodal/capabilities` reports both engines as separate rows, so
neither one's availability depends on the other.

## Install once

From the repository root:

```bash
make voice-setup
```

The command installs the optional speech packages and downloads pinned model assets under:

```text
$ALPHA_HOME/voice/models/
├── faster-whisper/small/
├── kokoro/kokoro-v1.0.int8.onnx
├── kokoro/voices-v1.0.bin
└── piper/en_US-lessac-medium.onnx
```

openWakeWord keeps its own pretrained weights in its package cache; setup
pre-fetches them so the first real wake word is not a download.

For the repository's normal `make dev` / `make up` launchers, the default resolves to
`backend/.alpha/voice/models/`.

The default download is approximately 640 MB (Kokoro adds ~120 MB on top of Whisper and Piper). It happens once; normal Gateway startup and voice requests do not download model weights.

Verify without downloading or loading weights:

```bash
make voice-verify
```

Then start Alpha normally:

```bash
make dev
```

Open `http://localhost:2026`, open a chat, and enable **Real-time voice** in the composer. Push-to-talk remains available when continuous conversation is not desired.

## Realtime wire protocol

The existing `WS /api/multimodal/voice` connection carries bounded JSON/base64 PCM16 audio. Conversation mode uses:

- client `conversation_start` to begin VAD endpointing;
- repeated client `audio` messages for captured PCM;
- server `transcript` messages with `final:false` for interim text;
- one server `transcript` with `final:true` and a monotonic `utterance_id` at the endpoint;
- client `conversation_stop` to cancel/discard capture and return the socket to idle.

The final transcript is submitted by the browser to the normal thread-run/SSE endpoint. The voice socket does not create an agent run. Response speech uses the separate binary `POST /api/multimodal/tts` endpoint, so chat events and audio cannot corrupt one another.

## Microphone and speaker access

Alpha serves the frontend with `Permissions-Policy: microphone=(self)`. The browser still
requires a secure context (`https://` or `localhost`) and an explicit user permission.

- **Real-time voice** and **push-to-talk** request microphone access only after their button
  is clicked.
- The same click primes a shared Web Audio output, so later sentence playback is not
  rejected by browser autoplay policy.
- The speaker/autoplay button audibly tests the local output with “Speaker output is ready,”
  then saves the autoplay preference when playback succeeds.
- Voice status reports when both microphone and speaker access are ready and distinguishes
  denied permission, missing input devices, and devices already in use.
- Playback uses the operating system's default browser output. Headphones reduce
  speaker-to-microphone echo during hands-free operation.

Alpha does not request camera access and does not use cloud speech recognition or browser
speech APIs.

In the Windows desktop shell, Electron applies the same boundary natively: the exact local
Alpha frontend origin may request microphone-only capture, while camera and mixed audio/video
requests are denied. The renderer still needs an explicit user click; native permission wiring
never silently starts capture.

## Privacy and network behavior

| Component | Default route | Cloud speech API |
| --- | --- | --- |
| Microphone capture | Browser → same-origin Alpha WebSocket | No |
| Speech-to-text | Local faster-whisper | No |
| Endpointing | Local WebRTC VAD | No |
| Agent response | Existing configured LLM | LLM cost may apply |
| Response speech synthesis | Local Piper (Kokoro on faster hosts) | No |
| Playback | Browser Web Audio | No |

`voice.routing.mode: local_only` is the default. In this mode, configured remote T1 models and keyless online T2 speech providers are skipped for TTS/STT, even if they are installed. This prevents a supposedly local voice turn from unexpectedly sending audio to a third party.

Model inference is local-only after setup. Runtime voice audio is held only in bounded memory while a turn is active and is not persisted as an artifact; only the resulting transcript and normal chat checkpoint follow the existing thread lifecycle. Alpha reports missing packages or model files honestly instead of silently fetching speech assets during a request.

## Configuration

The defaults are suitable for CPU/local use. An operator can override them in `config.yaml`:

```yaml
voice:
  enabled: true
  routing:
    mode: local_only              # local_only | automatic
  tts:
    autoplay: true                # used by the real-time conversation loop
    engine: piper                # piper (real-time default) | kokoro (natural, slower on CPU)
    voice: en_US-lessac-medium    # Piper safe voice ID, used when engine: piper
    kokoro_voice: af_bella         # Kokoro safe voice name (af_bella, af_sarah, am_adam, ...)
    model_path: null              # Piper: null -> <runtime_home>/voice/models/piper/<voice>.onnx
    kokoro_model_path: null       # null -> <runtime_home>/voice/models/kokoro/kokoro-v1.0.int8.onnx
    voices_path: null             # null -> <runtime_home>/voice/models/kokoro/voices-v1.0.bin
    length_scale: 1.0             # Piper speaking-time scale
    speed: 1.0                    # Kokoro speaking-speed multiplier
    volume: 0.9
  stt:
    model_size: small
    model_path: null              # null -> <runtime_home>/voice/models/faster-whisper/<size>
    language: null                # null -> automatic language detection
    device: auto
    compute_type: int8
    beam_size: 1
    local_files_only: true
  streaming:
    sample_rate: 16000
    frame_ms: 20
    endpoint_silence_ms: 700
    partial_interval_ms: 900
    max_utterance_seconds: 30
    max_frame_bytes: 65536
    max_sessions: 4
```

After changing `config.example.yaml` schema fields in a source checkout, upgrade an existing local configuration with:

```bash
make config-upgrade
```

Client requests can select a safe voice ID (a Piper id, or a Kokoro name) but cannot provide an arbitrary filesystem path. The configured/downloaded model allowlist remains server-owned.

The engine selects which voice field is read, so the two id namespaces cannot cross: `engine: kokoro` serves `kokoro_voice`, and `engine: piper` serves `voice`. A Piper id like `en_US-lessac-medium` is never handed to Kokoro, and a Kokoro name like `af_bella` is never handed to Piper.

Kokoro ships 54 voices across 8 languages (`af_*`, `am_*` American; `bf_*`, `bm_*` British; plus French, Hindi, Italian, Japanese, Portuguese, and Chinese). Any of them is a config change.

## Runtime safeguards

The real-time path includes:

- a process-local cap on simultaneous voice sockets;
- `runs:create` authorization and same-origin WebSocket validation;
- encoded and decoded PCM frame limits;
- maximum utterance duration and pre-roll/endpoint bounds;
- a cached Whisper model and a cached Kokoro ONNX session / Piper voice rather than rebuilding weights per request;
- bounded local speech inference;
- stale partial-transcript suppression after an utterance ends;
- one cancellable browser speech queue shared by streamed sentences, manual autoplay, and per-message replay;
- microphone cleanup on stop, thread changes, view changes, and unmount.

These limits prevent a stalled browser, malformed base64 payload, or overlapping turns from creating unbounded memory or model work.

## Docker

The model files are platform-independent and the normal runtime-home mount exposes them to the Gateway. Install them on the host first, then build the Gateway with the `voice` extra:

```bash
make voice-setup
UV_EXTRAS=voice make up
```

PowerShell equivalent:

```powershell
$env:UV_EXTRAS = "voice"
make up
```

The container installs the speech runtime packages; it does not download a second copy of the model assets at startup.

## Wake word

openWakeWord is installed by the `voice` extra, so a default install can wake on a spoken phrase. It is optional to *use* — real-time conversation works without arming it — and stays off by default for privacy: the microphone is only ever opened by a deliberate user click.

Arming scores streamed frames on-device. Every scored frame reports its score and threshold; a wake fires when the score reaches `wake_word.threshold` (default `0.5`) and latches until the score drops again, so the follow-up transcription runs exactly once per wake. Silence scores `0.0`.

```yaml
voice:
  wake_word:
    engine: openwakeword
    threshold: 0.5
    armed_default: false   # true = start armed without arming first
```

Alpha runs openWakeWord through its ONNX path only. `openwakeword` declares a
Linux-only `tflite-runtime` dependency that has no Python 3.12+ wheel, which would
make the workspace lock unresolvable on Linux; the workspace
`override-dependencies` in `backend/pyproject.toml` constrains that transitive
requirement to the interpreters that have wheels. Alpha never calls tflite, and
onnxruntime is already required by the `voice` extra.

Pretrained wake-word weights download once on first load and are cached on disk;
`make voice-setup` pre-fetches them so the first real wake word is not a download.

**Honest states.** A missing engine reports `not_installed` rather than a faked
armed state, and `armed` only becomes true once frames have actually flowed — a
silent session is never reported as wake-armed.

## Troubleshooting

### “No STT/TTS engine available”

Run:

```bash
make voice-verify
```

Then confirm the backend was synchronized with the voice extra:

```bash
cd backend
uv sync --locked --all-packages --extra voice
```

Set `UV_EXTRAS=voice` before running `make dev`, otherwise a plain sync removes the speech packages on the next start (see [Keeping the voice extra installed](#keeping-the-voice-extra-installed)).

### “Model assets are missing”

Run `make voice-setup` again. Downloads resume into the same runtime model directory, and completed files are verified before publication.

### The microphone does not start

Voice capture requires a secure browser context (`https://` or `localhost`). Click the mic
once and allow microphone permission for the site. If the device is missing or occupied,
Alpha reports that separately. You can also verify the site-level browser permission in
the address-bar site settings. Headphones reduce speaker-to-microphone echo.

### The speaker is blocked

Click the speaker/autoplay button or any voice control once to unlock browser audio, then
retry. If no sound is produced, verify the OS/browser default output device and volume;
Alpha routes audio through the browser's default Web Audio output.

### The first turn is slow

The first turn may pay model initialization. `make voice-setup` performs a one-time warm-up. Later turns reuse the same process-local model instances.

### A response is not spoken

Open **Settings → Voice & Speakers** and inspect the observed engine rows. Local TTS needs the engine package *and* its model assets — `kokoro-onnx` plus the `kokoro/` assets, or `piper-tts` plus the Piper voice. Text chat remains available when speech playback is unavailable.

### Switching voices

Change `kokoro_voice` (e.g. `af_sarah`, `bm_george`, `jf_alpha`) or `speed`, then send the next message — both are hot-reloadable, no restart needed. `GET /api/multimodal/capabilities` reports the effective config and each engine's observed status.

## Generating speech audio files

`backend/scripts/gen_speech.py` synthesizes a spoken welcome speech with the same
local engine and writes it to `logs/alpha-welcome-speech.wav` (about one
minute at the default voice). Run it from the backend directory:

```bash
cd backend
uv run --no-sync python scripts/gen_speech.py
```

The script reuses the cached model installed by `make voice-setup`, so it needs
no network and no API key. Edit `SPEECH_TEXT` in the script to change the spoken
content, or pass a different engine through `voice.tts.engine`.

### Keeping the voice extra installed

`make dev` runs `uv sync` on every start. A plain sync **removes** the voice-only
packages (kokoro-onnx, piper-tts, faster-whisper, openwakeword, webrtcvad)
because they are an optional extra. Set the `UV_EXTRAS` environment variable so
every dev start keeps them:

```powershell
# Windows (persistent for your user)
[Environment]::SetEnvironmentVariable("UV_EXTRAS", "voice", "User")
```

```bash
# Linux / macOS (add to your shell profile)
export UV_EXTRAS=voice
```

With that set, `make dev` syncs with `--extra voice` and the speech packages
survive restarts.

### Routing speech to a Bluetooth speaker

Alpha plays voice audio through the browser's default output device. To hear it
on a Bluetooth speaker, connect the speaker and make it the Windows default
playback device (Settings → System → Sound → Output, or the volume flyout in the
taskbar). The speaker button in the Alpha UI audibly confirms the path with
"Speaker output is ready." — if that phrase does not come through the speaker,
the default output device is still the built-in speakers.

## Upstream licenses

- faster-whisper and Whisper model assets: MIT.
- - Kokoro 82M ONNX runtime and weights: Apache-2.0 (commercial use permitted).
- openWakeWord runtime and pretrained wake-word models: MIT / Apache-2.0 by model.
- Piper runtime: GPL-3.0; the default `en_US-lessac-medium` voice asset is MIT.

Model weights are not committed to this repository. Their pinned source revisions and checksums are recorded by the setup utility and generated runtime manifest.
