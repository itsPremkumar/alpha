# Real-Time Voice Conversation

Alpha can run a hands-free voice loop without sending audio to a paid speech API:

1. The browser captures microphone PCM locally.
2. Alpha's Gateway performs voice endpointing and interim transcription with a local **faster-whisper** model.
3. The final transcript enters the normal chat/run pipeline and streams back over the existing SSE transport.
4. Completed response sentences are synthesized locally with **Piper** and played while later text is still streaming.
5. When playback finishes, Alpha resumes listening for the next turn.

Only the configured language model may use a paid API. Speech recognition and synthesis run on the Alpha host and require no speech-service key.

## Install once

From the repository root:

```bash
make voice-setup
```

The command installs the optional speech packages and downloads pinned model assets under:

```text
$AGENT_WORKSPACE_HOME/voice/models/
├── faster-whisper/small/
└── piper/en_US-lessac-medium.onnx
```

For the repository's normal `make dev` / `make up` launchers, the default resolves to
`backend/.agent-workspace/voice/models/`.

The default download is approximately 550 MB. It happens once; normal Gateway startup and voice requests do not download model weights.

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
| Response speech synthesis | Local Piper | No |
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
    voice: en_US-lessac-medium    # safe model ID, never a client-supplied path
    model_path: null              # null -> <runtime_home>/voice/models/piper/<voice>.onnx
    length_scale: 1.0
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

Client requests can select a safe Piper voice ID but cannot provide an arbitrary filesystem path. The configured/downloaded model allowlist remains server-owned.

## Runtime safeguards

The real-time path includes:

- a process-local cap on simultaneous voice sockets;
- `runs:create` authorization and same-origin WebSocket validation;
- encoded and decoded PCM frame limits;
- maximum utterance duration and pre-roll/endpoint bounds;
- a cached Whisper model and cached Piper voice rather than rebuilding weights per request;
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

Wake-word arming is a legacy optional control and is not required for real-time conversation. The default `voice` extra deliberately does not install `openwakeword`; this keeps Python 3.12/Windows installation reliable. If a deployment has a compatible openWakeWord installation, the existing arm/disarm controls remain available.

## Troubleshooting

### “No STT/TTS engine available”

Run:

```bash
make voice-verify
```

Then confirm the backend was synchronized with the voice extra:

```bash
cd backend
uv sync --locked --extra voice
```

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

Open **Settings → Voice & Speakers** and inspect the observed engine rows. Local TTS requires both `piper-tts` and a valid Piper model asset. Text chat remains available when speech playback is unavailable.

## Upstream licenses

- faster-whisper and Whisper model assets: MIT.
- Piper runtime: GPL-3.0; the default `en_US-lessac-medium` voice asset is MIT.
- WebRTC VAD binary wheel: BSD-style WebRTC VAD distribution.

Model weights are not committed to this repository. Their pinned source revisions and checksums are recorded by the setup utility and generated runtime manifest.
