import { apiFetch, apiUrl } from "./api-client";
import { transcribeUpload } from "./multimodal";
import type { CapabilitiesReport } from "./multimodal";

export interface VoiceCapabilities {
  enabled: boolean;
  stt: boolean;
  tts: boolean;
  detail?: unknown;
}

/**
 * Pure parsing of GET /api/multimodal/capabilities → boolean flags.
 * Honest defaults: nothing is claimed available unless the observed matrix says so
 * (`enabled` comes from the nested `voice.enabled` block; stt/tts require at least
 * one row with status `available`).
 */
export function parseVoiceCapabilities(data: unknown): VoiceCapabilities {
  const report = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  const voice = report.voice && typeof report.voice === "object" ? (report.voice as Record<string, unknown>) : {};
  const rows = Array.isArray(report.rows) ? (report.rows as Array<Record<string, unknown>>) : [];
  const engineAvailable = (capability: string) =>
    rows.some((row) => row && row.capability === capability && row.status === "available");
  const enabled = voice.enabled === true;
  return {
    enabled,
    stt: enabled && engineAvailable("stt"),
    tts: enabled && engineAvailable("tts"),
    detail: data,
  };
}

/** GET /api/multimodal/capabilities — honest: returns enabled=false when voice is off or unreachable. */
export async function fetchVoiceCapabilities(): Promise<VoiceCapabilities> {
  try {
    const res = await apiFetch(`/api/multimodal/capabilities`);
    if (!res.ok) return { enabled: false, stt: false, tts: false };
    return parseVoiceCapabilities(await res.json());
  } catch {
    return { enabled: false, stt: false, tts: false };
  }
}

/** POST /api/multimodal/stt with a recorded audio blob. Returns transcript text. */
export async function transcribeAudio(blob: Blob, filename = "dictation.webm"): Promise<string> {
  const result = await transcribeUpload(blob, filename);
  const text = (result.text || "").trim();
  if (!text) throw new Error("Empty transcript — try again.");
  return text;
}

// ---------------------------------------------------------------------------
// Speaker autoplay preference (localStorage; consumed by MessageItem for replies
// created after page load — history is never replayed).
// ---------------------------------------------------------------------------

export const AUTOPLAY_STORAGE_KEY = "alpha_voice_autoplay";

export function readAutoplayEnabled(): boolean {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(AUTOPLAY_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeAutoplayEnabled(enabled: boolean): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(AUTOPLAY_STORAGE_KEY, enabled ? "1" : "0");
  } catch {
    // Storage unavailable (private mode) — the toggle simply won't persist.
  }
}

// ---------------------------------------------------------------------------
// State machine: idle → wake_armed/listening → processing → speaking, with
// honest terminal states. Pure & fully unit-tested (voice.test.mjs).
// ---------------------------------------------------------------------------

export type VoiceState = "idle" | "wake_armed" | "listening" | "processing" | "speaking" | "engine_missing" | "auth_failed" | "ws_closed";

export type VoiceEvent =
  | { type: "arm_ok" }
  | { type: "disarm" }
  | { type: "listen_start" }
  | { type: "listen_stop" }
  | { type: "transcript" }
  | { type: "transcript_failed" }
  | { type: "play" }
  | { type: "play_end" }
  | { type: "engine_missing"; detail?: string }
  | { type: "auth_failed"; detail?: string }
  | { type: "ws_closed"; reason?: string }
  | { type: "reset" };

export const INITIAL_VOICE_STATE: VoiceState = "idle";

export const TERMINAL_VOICE_STATES = ["engine_missing", "auth_failed", "ws_closed"] as const;

export function isTerminalVoiceState(state: VoiceState): boolean {
  return state === "engine_missing" || state === "auth_failed" || state === "ws_closed";
}

/**
 * Pure transition function. Terminal states absorb every event except `reset`
 * (explicit user recovery); payloads (`reason`/`detail`) are carried by the
 * session, never by the state string itself.
 */
export function voiceReducer(state: VoiceState, event: VoiceEvent): VoiceState {
  if (event.type === "reset") return "idle";
  if (isTerminalVoiceState(state)) return state;
  switch (event.type) {
    case "arm_ok":
      return state === "idle" ? "wake_armed" : state;
    case "disarm":
      return state === "wake_armed" || state === "listening" ? "idle" : state;
    case "listen_start":
      return state === "idle" || state === "wake_armed" ? "listening" : state;
    case "listen_stop":
      return state === "listening" ? "processing" : state;
    case "transcript":
    case "transcript_failed":
      return state === "processing" ? "idle" : state;
    case "play":
      // Autoplay never hijacks an armed/capturing session.
      return state === "idle" ? "speaking" : state;
    case "play_end":
      return state === "speaking" ? "idle" : state;
    case "engine_missing":
      return "engine_missing";
    case "auth_failed":
      return "auth_failed";
    case "ws_closed":
      return "ws_closed";
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Frame encoding (pure; unit-tested in Node where no browser APIs exist).
// ---------------------------------------------------------------------------

export const TARGET_SAMPLE_RATE = 16000;

/** Float32 [-1,1] → Int16 PCM. Non-finite samples become silence; values clamp. */
export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i += 1) {
    const value = samples[i];
    const clamped = Number.isFinite(value) ? Math.max(-1, Math.min(1, value)) : 0;
    out[i] = Math.round(clamped < 0 ? clamped * 32768 : clamped * 32767);
  }
  return out;
}

/**
 * Box-filtered decimation to 16 kHz mono (AudioContext rates are typically 44.1/48 kHz).
 * Identity at 16 kHz; refuses to silently upsample — a wrong rate must fail loudly.
 */
export function downsampleTo16k(samples: Float32Array, sourceRate: number): Float32Array {
  if (!Number.isFinite(sourceRate) || sourceRate <= 0) {
    throw new Error(`invalid source sample rate: ${sourceRate}`);
  }
  if (sourceRate === TARGET_SAMPLE_RATE) return samples;
  if (sourceRate < TARGET_SAMPLE_RATE) {
    throw new Error(`source sample rate ${sourceRate} < ${TARGET_SAMPLE_RATE}; upsampling is not supported`);
  }
  const ratio = sourceRate / TARGET_SAMPLE_RATE;
  const outLength = Math.floor(samples.length / ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), samples.length);
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j += 1) {
      sum += samples[j];
      count += 1;
    }
    out[i] = count > 0 ? sum / count : 0;
  }
  return out;
}

/** Int16 PCM → base64 for the WebSocket wire format (LE bytes). */
export function pcm16ToBase64(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    for (let j = 0; j < chunk.length; j += 1) {
      binary += String.fromCharCode(chunk[j]);
    }
  }
  if (typeof btoa !== "function") throw new Error("base64 encoder unavailable in this environment");
  return btoa(binary);
}

/**
 * AudioWorklet processor source (loaded from a blob URL inside `startMic`).
 * Forwards mono Float32 frames to the main thread; browser APIs only ever run
 * inside session methods, never at module evaluation.
 */
export const WORKLET_SOURCE = `
class AlphaPcmCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    const channel = input && input.length > 0 ? input[0] : null;
    if (channel && channel.length > 0) {
      this.port.postMessage(channel.slice());
    }
    return true;
  }
}
registerProcessor("alpha-pcm-capture", AlphaPcmCaptureProcessor);
`;

function concatFloat32(chunks: Float32Array[]): Float32Array {
  let total = 0;
  for (const chunk of chunks) total += chunk.length;
  const out = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

function voiceWsUrl(): string {
  const base = apiUrl("/api/multimodal/voice");
  if (/^https?:\/\//.test(base)) return base.replace(/^http/, "ws");
  const origin =
    typeof location !== "undefined" ? `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}` : "ws://localhost:3000";
  return `${origin}${base}`;
}

// ---------------------------------------------------------------------------
// VoiceSession: WebSocket + microphone bridge driving the reducer.
// ---------------------------------------------------------------------------

export interface VoiceSessionHandlers {
  onState?: (state: VoiceState, previous: VoiceState) => void;
  onCapabilities?: (report: CapabilitiesReport) => void;
  onScore?: (score: number, threshold: number) => void;
  onWake?: (score: number, threshold: number, engine: string | null) => void;
  onTranscript?: (text: string, meta: { engine: string | null; tier: string | null }) => void;
  onEngine?: (issue: { capability: string; status: string; detail: string }) => void;
  onError?: (message: string) => void;
}

export class VoiceSession {
  private readonly handlers: VoiceSessionHandlers;
  private ws: WebSocket | null = null;
  private outbox: string[] = [];
  private stateValue: VoiceState = INITIAL_VOICE_STATE;
  private reasonValue: string | null = null;
  private modeValue: "idle" | "ptt" | "wake" = "idle";
  private armPhase: "idle" | "awaiting" | "awaiting_frames" = "idle";
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private pttChunks: Float32Array[] = [];
  private batchChunks: Float32Array[] = [];
  private batchSamples = 0;

  constructor(handlers: VoiceSessionHandlers = {}) {
    this.handlers = handlers;
    activeSession = this;
  }

  get state(): VoiceState {
    return this.stateValue;
  }

  get reason(): string | null {
    return this.reasonValue;
  }

  get mode(): "idle" | "ptt" | "wake" {
    return this.modeValue;
  }

  // -- WebSocket ------------------------------------------------------------

  connect(): void {
    if (this.ws) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(voiceWsUrl());
    } catch (err) {
      this.dispatch({ type: "ws_closed", reason: err instanceof Error ? err.message : String(err) });
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      const queued = this.outbox.splice(0);
      for (const raw of queued) ws.send(raw);
    };
    ws.onmessage = (event) => this.handleServerEvent(String(event.data));
    ws.onerror = () => {
      // onclose carries the code; nothing honest to add here.
    };
    ws.onclose = (event) => {
      if (this.ws === ws) this.ws = null;
      this.outbox = [];
      this.teardownMic();
      this.modeValue = "idle";
      this.armPhase = "idle";
      if (event.code === 4401 || event.code === 4403) {
        this.dispatch({ type: "auth_failed", detail: `WebSocket closed (${event.code})` });
      } else {
        this.dispatch({ type: "ws_closed", reason: `WebSocket closed (${event.code})` });
      }
    };
  }

  close(): void {
    const ws = this.ws;
    this.ws = null;
    this.outbox = [];
    if (ws) {
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      try {
        ws.close();
      } catch {
        // Socket already gone — nothing to release.
      }
    }
    this.teardownMic();
    this.modeValue = "idle";
    this.armPhase = "idle";
    if (activeSession === this) activeSession = null;
  }

  /** Terminal-state recovery: back to idle + a fresh connection. */
  reset(): void {
    this.dispatch({ type: "reset" });
    this.connect();
  }

  /** SPEAKING-state hooks for playback driven outside the session (MessageItem). */
  playbackStarted(): void {
    this.dispatch({ type: "play" });
  }

  playbackFinished(): void {
    this.dispatch({ type: "play_end" });
  }

  // -- Wake word ------------------------------------------------------------

  arm(): void {
    if (isTerminalVoiceState(this.stateValue) || this.stateValue !== "idle") return;
    this.armPhase = "awaiting";
    this.send({ type: "arm" });
  }

  disarm(): void {
    if (isTerminalVoiceState(this.stateValue)) return;
    this.send({ type: "disarm" });
    this.armPhase = "idle";
    this.modeValue = "idle";
    this.teardownMic();
    this.dispatch({ type: "disarm" });
  }

  private failArm(err: unknown): void {
    const detail = err instanceof Error ? err.message : String(err);
    this.send({ type: "disarm" }); // the server may hold an armed session — clean it up
    this.armPhase = "idle";
    this.modeValue = "idle";
    this.teardownMic();
    this.handlers.onError?.(`wake arm failed: ${detail}`);
  }

  // -- Push-to-talk ---------------------------------------------------------

  async startPushToTalk(): Promise<void> {
    if (isTerminalVoiceState(this.stateValue)) return;
    if (this.modeValue === "wake") {
      this.handlers.onError?.("disarm the wake word before push-to-talk");
      return;
    }
    if (this.modeValue === "ptt" || this.stateValue !== "idle") return;
    this.modeValue = "ptt";
    this.pttChunks = [];
    try {
      await this.startMic();
    } catch (err) {
      this.modeValue = "idle";
      this.handlers.onError?.(`microphone unavailable: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    if (this.modeValue !== "ptt") {
      // Disarmed/closed while the permission prompt was open — don't keep the mic hot.
      this.teardownMic();
      return;
    }
    this.dispatch({ type: "listen_start" });
  }

  async stopPushToTalk(): Promise<void> {
    if (this.modeValue !== "ptt") return;
    this.modeValue = "idle";
    const chunks = this.pttChunks;
    this.pttChunks = [];
    const rate = this.ctx ? this.ctx.sampleRate : TARGET_SAMPLE_RATE;
    this.teardownMic();
    this.dispatch({ type: "listen_stop" });
    if (chunks.length === 0) {
      this.dispatch({ type: "transcript_failed" });
      this.handlers.onError?.("no audio captured (microphone produced no frames)");
      return;
    }
    const pcm = floatToPcm16(downsampleTo16k(concatFloat32(chunks), rate));
    if (pcm.length === 0) {
      this.dispatch({ type: "transcript_failed" });
      this.handlers.onError?.("no audio captured (capture encoded to zero samples)");
      return;
    }
    this.send({ type: "transcribe", data: pcm16ToBase64(pcm) });
  }

  // -- Microphone pipeline --------------------------------------------------

  private async startMic(): Promise<void> {
    if (this.ctx) return;
    if (typeof navigator === "undefined" || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("getUserMedia is unavailable in this browser");
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    try {
      const ctx = new AudioContext();
      const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
      try {
        await ctx.audioWorklet.addModule(moduleUrl);
      } finally {
        URL.revokeObjectURL(moduleUrl);
      }
      const source = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "alpha-pcm-capture");
      node.port.onmessage = (event: MessageEvent) => this.onFrame(event.data);
      // Zero-gain tail: keeps the worklet pulled without monitoring the mic.
      const mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(node);
      node.connect(mute);
      mute.connect(ctx.destination);
      if (ctx.state === "suspended") await ctx.resume();
      this.ctx = ctx;
      this.stream = stream;
      this.node = node;
    } catch (err) {
      for (const track of stream.getTracks()) track.stop();
      throw err;
    }
  }

  private teardownMic(): void {
    if (this.node) {
      this.node.port.onmessage = null;
      try {
        this.node.disconnect();
      } catch {
        // Graph already torn down.
      }
    }
    this.node = null;
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
    }
    this.stream = null;
    if (this.ctx) {
      const ctx = this.ctx;
      this.ctx = null;
      ctx.close().catch(() => undefined);
    }
    this.batchChunks = [];
    this.batchSamples = 0;
  }

  private onFrame(data: unknown): void {
    if (!(data instanceof Float32Array) || data.length === 0) return;
    if (this.modeValue === "ptt") {
      this.pttChunks.push(data);
      return;
    }
    if (this.modeValue === "wake") {
      this.batchChunks.push(data);
      this.batchSamples += data.length;
      const rate = this.ctx ? this.ctx.sampleRate : TARGET_SAMPLE_RATE;
      const firstArmFrame = this.armPhase === "awaiting_frames";
      // ~100 ms frames → the server's 50-frame window ≈ 5 s of audio.
      if (firstArmFrame || this.batchSamples >= Math.floor(rate / 10)) {
        this.flushWakeBatch(rate);
      }
      if (firstArmFrame) {
        // wake_armed is claimed only after a frame actually reached the wire.
        this.armPhase = "idle";
        this.dispatch({ type: "arm_ok" });
      }
    }
  }

  private flushWakeBatch(rate: number): void {
    const chunks = this.batchChunks;
    this.batchChunks = [];
    this.batchSamples = 0;
    if (chunks.length === 0) return;
    const pcm = floatToPcm16(downsampleTo16k(concatFloat32(chunks), rate));
    if (pcm.length === 0) return;
    this.send({ type: "audio", data: pcm16ToBase64(pcm) });
  }

  // -- Server events --------------------------------------------------------

  private send(message: Record<string, unknown>): void {
    const raw = JSON.stringify(message);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(raw);
      return;
    }
    this.outbox.push(raw);
    this.connect();
  }

  private dispatch(event: VoiceEvent): void {
    const previous = this.stateValue;
    const next = voiceReducer(previous, event);
    if (event.type === "reset") {
      this.reasonValue = null;
      this.stateValue = next;
      this.handlers.onState?.(next, previous);
      return;
    }
    if (next === previous) return; // terminal states absorb; payloads never resurrect a state
    if (event.type === "ws_closed") this.reasonValue = event.reason ?? null;
    else if (event.type === "engine_missing" || event.type === "auth_failed") this.reasonValue = event.detail ?? null;
    this.stateValue = next;
    this.handlers.onState?.(next, previous);
  }

  private handleServerEvent(raw: string): void {
    let msg: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
      msg = parsed as Record<string, unknown>;
    } catch {
      this.handlers.onError?.("server sent a malformed voice event");
      return;
    }
    const type = typeof msg.type === "string" ? msg.type : "";
    switch (type) {
      case "capabilities":
        this.handlers.onCapabilities?.(msg as unknown as CapabilitiesReport);
        return;
      case "status": {
        const state = typeof msg.state === "string" ? msg.state : "";
        const event = typeof msg.event === "string" ? msg.event : "";
        if (state === "wake_armed" && msg.status === "ready") {
          this.armPhase = "awaiting_frames";
          this.modeValue = "wake";
          this.startMic()
            .then(() => {
              if (this.modeValue !== "wake" || this.armPhase !== "awaiting_frames") this.teardownMic();
            })
            .catch((err: unknown) => this.failArm(err));
        } else if (event === "disarmed") {
          this.armPhase = "idle";
          if (this.modeValue === "wake") {
            this.modeValue = "idle";
            this.teardownMic();
          }
          this.dispatch({ type: "disarm" });
        }
        // pong: informational only.
        return;
      }
      case "score":
        this.handlers.onScore?.(Number(msg.score || 0), Number(msg.threshold || 0));
        return;
      case "wake":
        this.handlers.onWake?.(Number(msg.score || 0), Number(msg.threshold || 0), msg.engine == null ? null : String(msg.engine));
        return;
      case "transcript": {
        const text = typeof msg.text === "string" ? msg.text : "";
        const engine = msg.engine == null ? null : String(msg.engine);
        const tier = msg.tier == null ? null : String(msg.tier);
        this.handlers.onTranscript?.(text, { engine, tier });
        this.dispatch({ type: "transcript" });
        return;
      }
      case "engine": {
        const capability = typeof msg.capability === "string" ? msg.capability : "";
        const status = typeof msg.status === "string" ? msg.status : "";
        const detail = typeof msg.detail === "string" ? msg.detail : "";
        this.handlers.onEngine?.({ capability, status, detail });
        // No engine served the attempted capability — honest terminal state.
        this.dispatch({ type: "engine_missing", detail: `${capability} ${status || "unavailable"}${detail ? ` — ${detail}` : ""}` });
        this.send({ type: "disarm" }); // drop any server-side session; its ack is absorbed
        this.armPhase = "idle";
        this.modeValue = "idle";
        this.teardownMic();
        return;
      }
      case "error": {
        const message = typeof msg.message === "string" ? msg.message : "unknown server error";
        if (this.stateValue === "processing") this.dispatch({ type: "transcript_failed" });
        this.handlers.onError?.(message);
        return;
      }
      default:
        // Unknown event: server is authoritative; surface nothing speculative.
        return;
    }
  }
}

// Single active session registry so playback elsewhere (MessageItem autoplay)
// can honestly mark the SPEAKING state without prop-drilling the whole tree.
let activeSession: VoiceSession | null = null;

export function notifyVoicePlayback(active: boolean): void {
  if (!activeSession) return;
  if (active) activeSession.playbackStarted();
  else activeSession.playbackFinished();
}
