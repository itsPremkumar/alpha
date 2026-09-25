import { apiFetch, apiUrl } from "./api-client";
import { enqueueSpeech, isSpeechCancellation } from "./speech";
import { synthesizeSpeech, transcribeUpload, MultimodalError } from "./multimodal";
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
// Speaker autoplay preference (localStorage; consumed by autoplaySpeak for
// replies created after page load — history is never replayed). Default OFF:
// absent storage ⇔ disabled, so playback never fires without an explicit opt-in.
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
// TTS playback + the tts.autoplay consumer. `speak()` performs one honest
// playback; `autoplaySpeak()` is THE gate that may invoke it after an
// assistant reply completes — and it never runs without an explicit enable.
// ---------------------------------------------------------------------------

/** Human-readable playback failure; a MultimodalError keeps its attempt chain. */
export function speakErrorMessage(err: unknown): string {
  if (err instanceof MultimodalError) {
    const attempts = err.attempts ?? [];
    return attempts.length > 0 ? `${err.message} — ${err.formatAttempts()}` : err.message;
  }
  return err instanceof Error ? err.message : String(err);
}

/** Same per-request cap the per-message speaker uses (MessageItem): 4000 chars. */
export const MAX_SPEECH_CHARS = 4000;

type AudioContextConstructor = new (options?: AudioContextOptions) => AudioContext;

let speakerContext: AudioContext | null = null;

function speakerAccessError(detail?: unknown): Error {
  const suffix = detail instanceof Error && detail.message ? ` (${detail.message})` : "";
  return new Error(`Speaker playback is blocked. Click the voice or speaker control once, then retry.${suffix}`);
}

function microphoneAccessMessage(error: unknown): string {
  const name = typeof DOMException !== "undefined" && error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access is blocked. Allow microphone access for this site in your browser, then click the mic again.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No microphone was found. Connect or select an input device, then click the mic again.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The microphone is unavailable or being used by another application. Close competing apps and retry.";
  }
  const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
  return `Microphone access is unavailable${detail}.`;
}

function audioContextConstructor(): AudioContextConstructor | null {
  const scope = globalThis as typeof globalThis & { webkitAudioContext?: AudioContextConstructor };
  return scope.AudioContext ?? scope.webkitAudioContext ?? null;
}

async function ensureSpeakerContext(): Promise<AudioContext> {
  const Constructor = audioContextConstructor();
  if (!Constructor) throw speakerAccessError(new Error("Web Audio is unavailable in this browser"));
  if (!speakerContext || speakerContext.state === "closed") {
    speakerContext = new Constructor({ latencyHint: "interactive" });
  }
  if (speakerContext.state !== "running") {
    try {
      await speakerContext.resume();
    } catch (error) {
      throw speakerAccessError(error);
    }
  }
  if (speakerContext.state !== "running") throw speakerAccessError();
  return speakerContext;
}

/** Unlock the browser's default speaker output from an explicit user gesture. */
export async function primeSpeakerPlayback(): Promise<void> {
  const context = await ensureSpeakerContext();
  const source = context.createBufferSource();
  const buffer = context.createBuffer(1, 1, context.sampleRate);
  source.buffer = buffer;
  source.connect(context.destination);
  source.onended = () => {
    try {
      source.disconnect();
    } catch {
      // Already detached.
    }
  };
  source.start(0);
}

export interface SpeakOptions {
  signal?: AbortSignal;
}

/**
 * One-shot local TTS playback through the shared Web Audio output. Resolves when
 * decoded audio finishes; rejects honestly on synthesis, decoding, cancellation,
 * or blocked-output failures. The same context is primed by the user's mic,
 * real-time, or speaker-button gesture before automatic playback begins.
 */
export async function speak(text: string, options: SpeakOptions = {}): Promise<void> {
  const speech = text.slice(0, MAX_SPEECH_CHARS);
  if (!speech.trim()) throw new MultimodalError(400, "tts", "nothing to speak");
  if (options.signal?.aborted) throw abortError();
  // Called before synthesis so a direct speaker-button click still qualifies as
  // user activation. Later automatic turns reuse this already-running context.
  const context = await ensureSpeakerContext();
  const { blob } = await synthesizeSpeech(speech, { signal: options.signal });
  if (options.signal?.aborted) throw abortError();
  const playbackContext = await ensureSpeakerContext();

  let audioBuffer: AudioBuffer;
  try {
    audioBuffer = await playbackContext.decodeAudioData(await blob.arrayBuffer());
  } catch (error) {
    throw new MultimodalError(503, "tts", `the browser could not decode local speech audio: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (options.signal?.aborted) throw abortError();

  const source = playbackContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(playbackContext.destination);
  let settled = false;
  let cleaned = false;
  let rejectPlayback: ((error: unknown) => void) | null = null;
  const cleanup = () => {
    if (cleaned) return;
    cleaned = true;
    source.onended = null;
    try {
      source.disconnect();
    } catch {
      // Already detached.
    }
    notifyVoicePlayback(false);
  };
  const abort = () => {
    if (settled) return;
    settled = true;
    try {
      source.stop();
    } catch {
      // The source may already have ended.
    }
    cleanup();
    rejectPlayback?.(abortError());
  };
  options.signal?.addEventListener("abort", abort, { once: true });
  notifyVoicePlayback(true);
  try {
    await new Promise<void>((resolve, reject) => {
      rejectPlayback = reject;
      source.onended = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      try {
        source.start(0);
      } catch (error) {
        settled = true;
        reject(error instanceof Error ? error : new Error(String(error)));
      }
    });
  } finally {
    options.signal?.removeEventListener("abort", abort);
    if (!settled) settled = true;
    cleanup();
  }
}

function abortError(): Error {
  if (typeof DOMException !== "undefined") return new DOMException("Speech playback cancelled", "AbortError");
  const error = new Error("Speech playback cancelled");
  error.name = "AbortError";
  return error;
}

export interface AutoplaySpeakHooks {
  /** Playback implementation; tests inject a stub — the default is the real `speak`. */
  speak?: (text: string) => Promise<void>;
  /** Visible, non-blocking disclosure for a playback failure (e.g. the chat `flash`). */
  onFailure?: (message: string) => void;
}

/**
 * THE consumer of the autoplay preference (documented intent:
 * `if (readAutoplayEnabled()) speak(assistantReply)`).
 *
 * Default OFF: unless autoplay is explicitly enabled, the speak implementation
 * is NEVER invoked — nothing is ever hardcoded on. Resolves true only when
 * playback completed; false when autoplay is off or playback failed. A failure
 * is always handed to `onFailure` for visible disclosure — never swallowed
 * silently. This function never rejects.
 */
export async function autoplaySpeak(text: string, hooks: AutoplaySpeakHooks = {}): Promise<boolean> {
  if (!readAutoplayEnabled()) return false;
  try {
    if (hooks.speak) await hooks.speak(text);
    else await enqueueSpeech(text, { player: (value, signal) => speak(value, { signal }) });
    return true;
  } catch (err) {
    if (!isSpeechCancellation(err)) hooks.onFailure?.(speakErrorMessage(err));
    return false;
  }
}

export interface VoiceTranscriptMeta {
  engine: string | null;
  tier: string | null;
}

/** Pure protocol classification shared by the session and offline tests. */
export function classifyVoiceTranscriptEvent(message: Record<string, unknown>): "partial" | "final" | null {
  const type = typeof message.type === "string" ? message.type : "";
  if (["transcript_partial", "partial_transcript", "interim", "interim_transcript", "transcript_delta", "partial"].includes(type)) return "partial";
  if (["transcript", "final_transcript", "transcript_final"].includes(type)) {
    const finalValue = message.final;
    const isFinalValue = message.is_final;
    const partialValue = message.partial;
    const explicitlyPartial = finalValue === false || finalValue === 0 || finalValue === "false"
      || isFinalValue === false || isFinalValue === 0 || isFinalValue === "false"
      || partialValue === true || partialValue === 1 || partialValue === "true";
    return explicitlyPartial ? "partial" : "final";
  }
  return null;
}

// ---------------------------------------------------------------------------
// State machine: idle → wake_armed/listening → processing → speaking, with
// honest terminal states. Pure & fully unit-tested (voice.test.mjs).
// ---------------------------------------------------------------------------

export type VoiceState = "idle" | "wake_armed" | "listening" | "processing" | "speaking" | "engine_missing" | "auth_failed" | "ws_closed";

/** User-facing phase. `thinking` covers STT/agent work while a hands-free turn is paused. */
export type VoicePhase = "idle" | "waiting" | "listening" | "thinking" | "speaking";

export type VoiceEvent =
  | { type: "arm_ok" }
  | { type: "disarm" }
  | { type: "listen_start" }
  | { type: "listen_stop" }
  | { type: "transcript" }
  | { type: "transcript_failed" }
  | { type: "conversation_start" }
  | { type: "conversation_pause" }
  | { type: "conversation_resume" }
  | { type: "conversation_stop" }
  | { type: "conversation_transcript" }
  | { type: "conversation_play" }
  | { type: "conversation_play_end" }
  | { type: "play" }
  | { type: "play_end" }
  | { type: "engine_missing"; detail?: string }
  | { type: "auth_failed"; detail?: string }
  | { type: "ws_closed"; reason?: string }
  | { type: "reset" };

/** Keep the socket and its reconnect mailbox finite even on a slow server. */
export const MAX_VOICE_SOCKET_BUFFERED_BYTES = 256 * 1024;
export const MAX_VOICE_OUTBOX_MESSAGES = 128;
/** Legacy-safe upper bound when the server does not advertise a frame size. */
export const VOICE_AUDIO_BATCH_MS = 100;
export const DEFAULT_VOICE_FRAME_MS = 20;

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
    case "conversation_start":
      return state === "idle" || state === "wake_armed" ? "listening" : state;
    case "conversation_pause":
      return state === "listening" ? "processing" : state;
    case "conversation_resume":
      return state === "processing" || state === "idle" ? "listening" : state;
    case "conversation_stop":
      return state === "idle" || state === "wake_armed" || state === "listening" || state === "processing" || state === "speaking" ? "idle" : state;
    case "conversation_transcript":
      return state === "listening" || state === "processing" ? "processing" : state;
    case "conversation_play":
      return state === "processing" ? "speaking" : state;
    case "conversation_play_end":
      return state === "speaking" ? "processing" : state;
    case "transcript":
    case "transcript_failed":
      return state === "processing" ? "idle" : state;
    case "play":
      // Ordinary autoplay never hijacks an armed/capturing/processing session.
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
 * Box-filtered decimation to a requested mono rate (AudioContext rates are
 * typically 44.1/48 kHz). Identity at the target; refuses to silently upsample.
 */
export function downsampleToRate(samples: Float32Array, sourceRate: number, targetRate: number): Float32Array {
  if (!Number.isFinite(sourceRate) || sourceRate <= 0) {
    throw new Error(`invalid source sample rate: ${sourceRate}`);
  }
  if (!Number.isFinite(targetRate) || targetRate <= 0) {
    throw new Error(`invalid target sample rate: ${targetRate}`);
  }
  if (samples.length === 0) return new Float32Array(0);
  if (sourceRate === targetRate) return samples;
  if (sourceRate < targetRate) {
    const outLength = Math.ceil((samples.length * targetRate) / sourceRate);
    const out = new Float32Array(outLength);
    for (let i = 0; i < outLength; i += 1) {
      const position = (i * sourceRate) / targetRate;
      const left = Math.floor(position);
      const right = Math.min(left + 1, samples.length - 1);
      const fraction = position - left;
      out[i] = samples[left] * (1 - fraction) + samples[right] * fraction;
    }
    return out;
  }
  const ratio = sourceRate / targetRate;
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

/** Backwards-compatible 16 kHz helper used by the legacy wake scorer. */
export function downsampleTo16k(samples: Float32Array, sourceRate: number): Float32Array {
  if (Number.isFinite(sourceRate) && sourceRate > 0 && sourceRate < TARGET_SAMPLE_RATE) {
    throw new Error(`source sample rate ${sourceRate} < ${TARGET_SAMPLE_RATE}; upsampling is not supported`);
  }
  return downsampleToRate(samples, sourceRate, TARGET_SAMPLE_RATE);
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
  onPhase?: (phase: VoicePhase, previous: VoicePhase) => void;
  onCapabilities?: (report: CapabilitiesReport) => void;
  onScore?: (score: number, threshold: number) => void;
  onWake?: (score: number, threshold: number, engine: string | null) => void;
  /** Interim server transcript; safe to replace repeatedly while listening. */
  onPartialTranscript?: (text: string, meta: VoiceTranscriptMeta) => void;
  /** Final transcript for push-to-talk, wake, and hands-free modes. */
  onTranscript?: (text: string, meta: VoiceTranscriptMeta) => void;
  /** Explicit final-only alias for hosts that also consume legacy PTT callbacks. */
  onFinalTranscript?: (text: string, meta: VoiceTranscriptMeta) => void;
  onEngine?: (issue: { capability: string; status: string; detail: string }) => void;
  onMediaAccess?: (access: { microphone?: boolean; speaker?: boolean }) => void;
  onError?: (message: string) => void;
}

export class VoiceSession {
  private readonly handlers: VoiceSessionHandlers;
  private ws: WebSocket | null = null;
  private outbox: Array<{ raw: string; droppable: boolean }> = [];
  private stateValue: VoiceState = INITIAL_VOICE_STATE;
  private reasonValue: string | null = null;
  private modeValue: "idle" | "ptt" | "wake" | "conversation" = "idle";
  private armPhase: "idle" | "awaiting" | "awaiting_frames" = "idle";
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private pttChunks: Float32Array[] = [];
  private pttSamples = 0;
  private batchChunks: Float32Array[] = [];
  private batchSamples = 0;
  private conversationChunks: Float32Array[] = [];
  private conversationSamples = 0;
  private streamSampleRate = TARGET_SAMPLE_RATE;
  private streamFrameMs = DEFAULT_VOICE_FRAME_MS;
  private streamMaxFrameBytes = 65_536;
  private conversationPaused = false;
  private conversationTurnPending = false;
  private conversationUtteranceId: number | null = null;
  private suppressStaleConversationEvents = false;
  private droppedAudioFrames = 0;
  private outboxOverflowReported = false;

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

  get mode(): "idle" | "ptt" | "wake" | "conversation" {
    return this.modeValue;
  }

  get conversationActive(): boolean {
    return this.modeValue === "conversation";
  }

  get isConversationPaused(): boolean {
    return this.conversationPaused;
  }

  get phase(): VoicePhase {
    if (this.stateValue === "speaking") return "speaking";
    if (this.stateValue === "processing") return "thinking";
    if (this.stateValue === "listening") return "listening";
    if (this.modeValue === "conversation") return this.conversationPaused ? "thinking" : "waiting";
    return "idle";
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
      // Control messages are ordered before audio. If the socket is already
      // congested, stale audio is discarded rather than allowed to grow an
      // unbounded mailbox.
      const controls = queued.filter((item) => !item.droppable);
      const audio = queued.filter((item) => item.droppable);
      for (const item of [...controls, ...audio]) {
        if (item.droppable && this.socketBufferedAmount() >= MAX_VOICE_SOCKET_BUFFERED_BYTES) {
          this.noteDroppedAudio();
          continue;
        }
        try {
          ws.send(item.raw);
        } catch {
          this.dispatch({ type: "ws_closed", reason: "WebSocket send failed" });
          return;
        }
      }
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
      this.conversationPaused = false;
      this.conversationTurnPending = false;
      this.conversationUtteranceId = null;
      this.suppressStaleConversationEvents = true;
      if (event.code === 4401 || event.code === 4403) {
        this.dispatch({ type: "auth_failed", detail: `WebSocket closed (${event.code})` });
      } else {
        this.dispatch({ type: "ws_closed", reason: `WebSocket closed (${event.code})` });
      }
    };
  }

  close(): void {
    const ws = this.ws;
    if (this.modeValue === "conversation" && ws && this.isSocketOpen()) {
      try {
        ws.send(JSON.stringify({ type: "conversation_stop" }));
      } catch {
        // Socket is already gone; close below remains the cleanup boundary.
      }
    }
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
    this.conversationPaused = false;
    this.conversationTurnPending = false;
    this.conversationUtteranceId = null;
    this.suppressStaleConversationEvents = false;
    if (activeSession === this) activeSession = null;
  }

  /** Terminal-state recovery: back to idle + a fresh connection. */
  reset(): void {
    this.teardownMic();
    this.modeValue = "idle";
    this.armPhase = "idle";
    this.conversationPaused = false;
    this.conversationTurnPending = false;
    this.conversationUtteranceId = null;
    this.suppressStaleConversationEvents = false;
    this.outbox = [];
    this.droppedAudioFrames = 0;
    this.outboxOverflowReported = false;
    this.dispatch({ type: "reset" });
    this.connect();
  }

  /** SPEAKING-state hooks for playback driven outside the session (autoplay `speak`). */
  playbackStarted(): void {
    this.dispatch(this.modeValue === "conversation" ? { type: "conversation_play" } : { type: "play" });
  }

  playbackFinished(): void {
    this.dispatch(this.modeValue === "conversation" ? { type: "conversation_play_end" } : { type: "play_end" });
  }

  // -- Wake word ------------------------------------------------------------

  async arm(): Promise<boolean> {
    if (isTerminalVoiceState(this.stateValue) || this.stateValue !== "idle") return false;
    try {
      await primeSpeakerPlayback();
      this.handlers.onMediaAccess?.({ speaker: true });
    } catch (error) {
      this.handlers.onError?.(error instanceof Error ? error.message : String(error));
      return false;
    }
    this.armPhase = "awaiting";
    this.send({ type: "arm" });
    return true;
  }

  disarm(): void {
    if (isTerminalVoiceState(this.stateValue)) return;
    if (this.modeValue === "conversation") {
      this.stopConversation();
      return;
    }
    this.send({ type: "disarm" });
    this.armPhase = "idle";
    this.modeValue = "idle";
    this.teardownMic();
    this.dispatch({ type: "disarm" });
  }

  private failArm(err: unknown): void {
    this.send({ type: "disarm" }); // the server may hold an armed session — clean it up
    this.armPhase = "idle";
    this.modeValue = "idle";
    this.teardownMic();
    this.handlers.onError?.(microphoneAccessMessage(err));
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
    this.pttSamples = 0;
    try {
      await primeSpeakerPlayback();
      this.handlers.onMediaAccess?.({ speaker: true });
      await this.startMic();
      this.handlers.onMediaAccess?.({ microphone: true });
    } catch (err) {
      this.modeValue = "idle";
      const detail = err instanceof Error ? err.message : String(err);
      this.handlers.onError?.(detail.startsWith("Speaker playback") ? detail : microphoneAccessMessage(err));
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
    this.pttSamples = 0;
    const rate = this.ctx ? this.ctx.sampleRate : TARGET_SAMPLE_RATE;
    this.teardownMic();
    this.dispatch({ type: "listen_stop" });
    if (chunks.length === 0) {
      this.dispatch({ type: "transcript_failed" });
      this.handlers.onError?.("no audio captured (microphone produced no frames)");
      return;
    }
    const pcm = floatToPcm16(downsampleToRate(concatFloat32(chunks), rate, this.streamSampleRate));
    if (pcm.length === 0) {
      this.dispatch({ type: "transcript_failed" });
      this.handlers.onError?.("no audio captured (capture encoded to zero samples)");
      return;
    }
    this.send({ type: "transcribe", data: pcm16ToBase64(pcm) });
  }

  // -- Hands-free conversation ---------------------------------------------

  /**
   * Start a local, hands-free capture session. The Gateway performs endpointing
   * and STT; this method only owns PCM capture and the WebSocket protocol.
   */
  async startConversation(): Promise<boolean> {
    if (isTerminalVoiceState(this.stateValue) || this.modeValue !== "idle" || this.stateValue !== "idle" || this.armPhase !== "idle") return false;
    this.modeValue = "conversation";
    this.conversationPaused = false;
    this.conversationTurnPending = false;
    this.conversationUtteranceId = null;
    this.suppressStaleConversationEvents = false;
    this.conversationChunks = [];
    this.conversationSamples = 0;
    try {
      await primeSpeakerPlayback();
      this.handlers.onMediaAccess?.({ speaker: true });
    } catch (error) {
      this.modeValue = "idle";
      this.conversationPaused = false;
      this.handlers.onError?.(error instanceof Error ? error.message : String(error));
      return false;
    }
    this.send({
      type: "conversation_start",
      format: "pcm16",
      sample_rate: this.streamSampleRate,
      frame_ms: this.streamFrameMs,
      channels: 1,
    });
    if (isTerminalVoiceState(this.stateValue)) {
      this.modeValue = "idle";
      return false;
    }
    try {
      await this.startMic();
      this.handlers.onMediaAccess?.({ microphone: true });
    } catch (err) {
      this.modeValue = "idle";
      this.conversationPaused = false;
      this.send({ type: "conversation_stop" });
      this.handlers.onError?.(microphoneAccessMessage(err));
      return false;
    }
    if (this.modeValue !== "conversation" || this.conversationPaused || isTerminalVoiceState(this.stateValue)) {
      this.teardownMic();
      if (isTerminalVoiceState(this.stateValue)) this.modeValue = "idle";
      return false;
    }
    this.dispatch({ type: "conversation_start" });
    return true;
  }

  /** Stop hands-free capture and release every microphone resource. */
  stopConversation(): void {
    if (this.modeValue !== "conversation") return;
    this.modeValue = "idle";
    this.conversationPaused = false;
    this.conversationTurnPending = false;
    this.conversationUtteranceId = null;
    this.suppressStaleConversationEvents = true;
    this.send({ type: "conversation_stop" });
    this.teardownMic();
    this.dispatch({ type: "conversation_stop" });
  }

  /** Pause only the mic while a final transcript is being answered. */
  pauseConversation(): void {
    if (this.modeValue !== "conversation" || this.conversationPaused) return;
    this.conversationPaused = true;
    this.send({ type: "conversation_stop" });
    this.teardownMic();
    this.dispatch({ type: "conversation_pause" });
  }

  /** Resume the same hands-free session after a response has been spoken. */
  async resumeConversation(): Promise<boolean> {
    if (this.modeValue !== "conversation" || !this.conversationPaused || isTerminalVoiceState(this.stateValue)) return false;
    this.conversationPaused = false;
    this.conversationTurnPending = false;
    this.conversationUtteranceId = null;
    this.suppressStaleConversationEvents = false;
    this.conversationChunks = [];
    this.conversationSamples = 0;
    this.send({
      type: "conversation_start",
      format: "pcm16",
      sample_rate: this.streamSampleRate,
      frame_ms: this.streamFrameMs,
      channels: 1,
    });
    try {
      await this.startMic();
      this.handlers.onMediaAccess?.({ microphone: true });
    } catch (err) {
      this.conversationPaused = true;
      this.send({ type: "conversation_stop" });
      this.handlers.onError?.(microphoneAccessMessage(err));
      return false;
    }
    if (this.modeValue !== "conversation" || isTerminalVoiceState(this.stateValue)) {
      this.teardownMic();
      if (isTerminalVoiceState(this.stateValue)) this.modeValue = "idle";
      return false;
    }
    this.dispatch({ type: "conversation_resume" });
    return true;
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
    let ctx: AudioContext | null = null;
    let node: AudioWorkletNode | null = null;
    try {
      ctx = new AudioContext();
      const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
      try {
        await ctx.audioWorklet.addModule(moduleUrl);
      } finally {
        URL.revokeObjectURL(moduleUrl);
      }
      const source = ctx.createMediaStreamSource(stream);
      node = new AudioWorkletNode(ctx, "alpha-pcm-capture");
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
      if (node) {
        node.port.onmessage = null;
        try {
          node.disconnect();
        } catch {
          // Graph may not have been connected yet.
        }
      }
      if (ctx) await ctx.close().catch(() => undefined);
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
    this.pttChunks = [];
    this.pttSamples = 0;
    this.batchChunks = [];
    this.batchSamples = 0;
    this.conversationChunks = [];
    this.conversationSamples = 0;
  }

  private onFrame(data: unknown): void {
    if (!(data instanceof Float32Array) || data.length === 0) return;
    if (this.modeValue === "ptt") {
      this.pttChunks.push(data);
      this.pttSamples += data.length;
      const maxPttSamples = Math.floor((this.ctx ? this.ctx.sampleRate : TARGET_SAMPLE_RATE) * 60);
      let dropped = false;
      while (this.pttSamples > maxPttSamples && this.pttChunks.length > 0) {
        const removed = this.pttChunks.shift();
        this.pttSamples -= removed?.length ?? 0;
        dropped = true;
      }
      if (dropped) this.handlers.onError?.("push-to-talk audio exceeded 60 seconds; oldest audio was dropped");
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
      return;
    }
    if (this.modeValue === "conversation" && !this.conversationPaused) {
      this.conversationChunks.push(data);
      this.conversationSamples += data.length;
      const rate = this.ctx ? this.ctx.sampleRate : TARGET_SAMPLE_RATE;
      const frameMs = Math.min(this.streamFrameMs, VOICE_AUDIO_BATCH_MS);
      const byteLimitSamples = Math.max(1, Math.floor((this.streamMaxFrameBytes * rate) / (2 * this.streamSampleRate)));
      const batchSamples = Math.max(1, Math.min(Math.floor((rate * frameMs) / 1000), byteLimitSamples));
      // Keep only a short, bounded PCM batch. The server does endpointing;
      // the browser does not accumulate an entire utterance in memory.
      if (this.conversationSamples >= batchSamples || this.conversationSamples > rate * 2) {
        this.flushConversationBatch(rate);
      }
    }
  }

  private flushConversationBatch(rate: number): void {
    const chunks = this.conversationChunks;
    this.conversationChunks = [];
    this.conversationSamples = 0;
    if (chunks.length === 0) return;
    const pcm = floatToPcm16(downsampleToRate(concatFloat32(chunks), rate, this.streamSampleRate));
    if (pcm.length === 0) return;
    this.send({ type: "audio", data: pcm16ToBase64(pcm) }, true);
  }

  private flushWakeBatch(rate: number): void {
    const chunks = this.batchChunks;
    this.batchChunks = [];
    this.batchSamples = 0;
    if (chunks.length === 0) return;
    // WakeWordSession is the legacy fixed-16 kHz scorer; conversation/PTT use
    // the server-advertised rate, but wake frames must retain their contract.
    const pcm = floatToPcm16(downsampleTo16k(concatFloat32(chunks), rate));
    if (pcm.length === 0) return;
    this.send({ type: "audio", data: pcm16ToBase64(pcm) });
  }

  // -- Server events --------------------------------------------------------

  private socketBufferedAmount(): number {
    const amount = this.ws && typeof this.ws.bufferedAmount === "number" ? this.ws.bufferedAmount : 0;
    return Number.isFinite(amount) && amount > 0 ? amount : 0;
  }

  private noteDroppedAudio(): void {
    this.droppedAudioFrames += 1;
    if (this.droppedAudioFrames === 1 || this.droppedAudioFrames % 50 === 0) {
      this.handlers.onError?.("voice audio is falling behind; stale audio frames were dropped");
    }
  }

  private isSocketOpen(): boolean {
    if (!this.ws) return false;
    const open = typeof WebSocket !== "undefined" ? WebSocket.OPEN : 1;
    return this.ws.readyState === open;
  }

  private enqueueOutbox(item: { raw: string; droppable: boolean }): void {
    if (this.outbox.length >= MAX_VOICE_OUTBOX_MESSAGES) {
      const audioIndex = this.outbox.findIndex((queued) => queued.droppable);
      if (audioIndex >= 0) {
        this.outbox.splice(audioIndex, 1);
        this.noteDroppedAudio();
      } else {
        // Control messages are few and ordered. Refuse a new control rather
        // than allowing a reconnect loop to grow memory forever.
        this.outbox.shift();
        if (!this.outboxOverflowReported) {
          this.outboxOverflowReported = true;
          this.handlers.onError?.("voice control mailbox is full; reconnecting with a fresh session");
        }
      }
    }
    this.outbox.push(item);
  }

  private send(message: Record<string, unknown>, droppable = false): void {
    const raw = JSON.stringify(message);
    if (this.isSocketOpen()) {
      if (droppable && this.socketBufferedAmount() >= MAX_VOICE_SOCKET_BUFFERED_BYTES) {
        this.noteDroppedAudio();
        return;
      }
      try {
        this.ws!.send(raw);
      } catch {
        const failed = this.ws;
        this.ws = null;
        this.enqueueOutbox({ raw, droppable });
        this.teardownMic();
        this.modeValue = "idle";
        this.conversationPaused = false;
        this.conversationTurnPending = false;
        this.dispatch({ type: "ws_closed", reason: "WebSocket send failed" });
        try {
          failed?.close();
        } catch {
          // Socket already gone.
        }
      }
      return;
    }
    this.enqueueOutbox({ raw, droppable });
    this.connect();
  }

  private dispatch(event: VoiceEvent): void {
    const previous = this.stateValue;
    const previousPhase = this.phase;
    const next = voiceReducer(previous, event);
    if (event.type === "reset") {
      this.reasonValue = null;
      this.stateValue = next;
      this.handlers.onState?.(next, previous);
      this.handlers.onPhase?.(this.phase, previousPhase);
      return;
    }
    if (event.type === "conversation_start" || event.type === "conversation_resume") {
      this.conversationTurnPending = false;
    }
    if (next === previous && this.phase === previousPhase) return; // terminal states absorb; payloads never resurrect a state
    if (event.type === "ws_closed") this.reasonValue = event.reason ?? null;
    else if (event.type === "engine_missing" || event.type === "auth_failed") this.reasonValue = event.detail ?? null;
    this.stateValue = next;
    this.handlers.onState?.(next, previous);
    this.handlers.onPhase?.(this.phase, previousPhase);
  }

  private utteranceId(message: Record<string, unknown>): number | null {
    const value = message.utterance_id ?? message.utteranceId;
    const parsed = typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : NaN;
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
  }

  private acceptUtterance(message: Record<string, unknown>): boolean {
    if (this.modeValue !== "conversation") return true;
    const id = this.utteranceId(message);
    if (id === null) return true;
    if (this.conversationUtteranceId !== null && id < this.conversationUtteranceId) return false;
    this.conversationUtteranceId = id;
    return true;
  }

  private applyStreamingConfig(report: unknown): void {
    if (!report || typeof report !== "object") return;
    const voice = (report as Record<string, unknown>).voice;
    if (!voice || typeof voice !== "object") return;
    const streaming = (voice as Record<string, unknown>).streaming;
    if (!streaming || typeof streaming !== "object") return;
    const values = streaming as Record<string, unknown>;
    const sampleRate = Number(values.sample_rate);
    if ([8000, 16000, 32000, 48000].includes(sampleRate)) this.streamSampleRate = sampleRate;
    const frameMs = Number(values.frame_ms);
    if ([10, 20, 30].includes(frameMs)) this.streamFrameMs = frameMs;
    const maxFrameBytes = Number(values.max_frame_bytes);
    if (Number.isSafeInteger(maxFrameBytes) && maxFrameBytes >= 64) this.streamMaxFrameBytes = maxFrameBytes;
  }

  private emitFinalTranscript(text: string, meta: VoiceTranscriptMeta): void {
    if (this.handlers.onFinalTranscript) this.handlers.onFinalTranscript(text, meta);
    else this.handlers.onTranscript?.(text, meta);
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
        this.applyStreamingConfig(msg);
        this.handlers.onCapabilities?.(msg as unknown as CapabilitiesReport);
        return;
      case "status": {
        const state = typeof msg.state === "string" ? msg.state : "";
        const event = typeof msg.event === "string" ? msg.event : "";
        if (state === "listening") {
          const sampleRate = Number(msg.sample_rate);
          const frameMs = Number(msg.frame_ms);
          if ([8000, 16000, 32000, 48000].includes(sampleRate)) this.streamSampleRate = sampleRate;
          if ([10, 20, 30].includes(frameMs)) this.streamFrameMs = frameMs;
        }
        if (state === "wake_armed" && msg.status === "ready") {
          this.armPhase = "awaiting_frames";
          this.modeValue = "wake";
          this.startMic()
            .then(() => {
              if (this.modeValue !== "wake" || this.armPhase !== "awaiting_frames") this.teardownMic();
              else this.handlers.onMediaAccess?.({ microphone: true });
            })
            .catch((err: unknown) => this.failArm(err));
        } else if (this.modeValue === "conversation" && (event === "conversation_started" || event === "started" || event === "ready" || state === "speech_started" || state === "listening") && !this.conversationPaused && !this.conversationTurnPending) {
          // The acknowledgement is informational; capture is already local.
          // Dispatching here keeps the UI honest if a server reports readiness
          // after a reconnect without requiring a second mic permission prompt.
          this.dispatch({ type: "conversation_resume" });
        } else if (event === "conversation_stopped" || event === "stopped") {
          if (this.modeValue === "conversation" && this.conversationPaused) {
            this.dispatch({ type: "conversation_pause" });
          }
        } else if (event === "processing" || state === "processing") {
          if (this.modeValue === "conversation") this.pauseConversation();
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
      case "conversation_started": {
        if (this.modeValue === "conversation" && !this.conversationPaused) this.dispatch({ type: "conversation_resume" });
        return;
      }
      case "conversation_stopped": {
        if (this.modeValue === "conversation" && this.conversationPaused) this.dispatch({ type: "conversation_pause" });
        return;
      }
      case "score":
        this.handlers.onScore?.(Number(msg.score || 0), Number(msg.threshold || 0));
        return;
      case "wake":
        this.handlers.onWake?.(Number(msg.score || 0), Number(msg.threshold || 0), msg.engine == null ? null : String(msg.engine));
        return;
      case "transcript_partial":
      case "partial_transcript":
      case "interim":
      case "interim_transcript":
      case "transcript_delta":
      case "partial": {
        if (this.suppressStaleConversationEvents && this.utteranceId(msg) !== null) return;
        if (this.modeValue === "conversation" && (this.conversationPaused || this.conversationTurnPending)) return;
        if (!this.acceptUtterance(msg)) return;
        const text = typeof msg.text === "string" ? msg.text : typeof msg.transcript === "string" ? msg.transcript : typeof msg.delta === "string" ? msg.delta : typeof msg.partial_text === "string" ? msg.partial_text : typeof msg.partial === "string" ? msg.partial : "";
        const source = msg.source && typeof msg.source === "object" ? msg.source as Record<string, unknown> : {};
        const engineValue = msg.engine ?? source.engine ?? source.stt_engine ?? null;
        const tierValue = msg.tier ?? source.tier ?? source.stt_tier ?? null;
        const engine = engineValue == null ? null : String(engineValue);
        const tier = tierValue == null ? null : String(tierValue);
        this.handlers.onPartialTranscript?.(text, { engine, tier });
        return;
      }
      case "transcript":
      case "final_transcript":
      case "transcript_final": {
        if (this.suppressStaleConversationEvents && this.utteranceId(msg) !== null) return;
        if (!this.acceptUtterance(msg)) return;
        const text = typeof msg.text === "string" ? msg.text : typeof msg.transcript === "string" ? msg.transcript : typeof msg.delta === "string" ? msg.delta : typeof msg.partial_text === "string" ? msg.partial_text : typeof msg.partial === "string" ? msg.partial : "";
        const source = msg.source && typeof msg.source === "object" ? msg.source as Record<string, unknown> : {};
        const engineValue = msg.engine ?? source.engine ?? source.stt_engine ?? null;
        const tierValue = msg.tier ?? source.tier ?? source.stt_tier ?? null;
        const engine = engineValue == null ? null : String(engineValue);
        const tier = tierValue == null ? null : String(tierValue);
        const isFinal = classifyVoiceTranscriptEvent(msg) === "final";
        if (!isFinal) {
          this.handlers.onPartialTranscript?.(text, { engine, tier });
          return;
        }
        if (this.modeValue === "conversation") {
          // A final event pauses capture before notifying the host. The host
          // owns the normal chat run; this session must not start a second run.
          if (this.conversationTurnPending) return;
          this.conversationTurnPending = true;
          this.pauseConversation();
          if (!text.trim()) {
            this.conversationTurnPending = false;
            this.handlers.onError?.("voice transcript was empty");
            void this.resumeConversation();
            return;
          }
          this.emitFinalTranscript(text, { engine, tier });
          this.dispatch({ type: "conversation_transcript" });
        } else {
          this.emitFinalTranscript(text, { engine, tier });
          this.dispatch({ type: "transcript" });
        }
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
        if (this.modeValue === "conversation") this.send({ type: "conversation_stop" });
        this.armPhase = "idle";
        this.modeValue = "idle";
        this.conversationPaused = false;
        this.conversationTurnPending = false;
        this.conversationUtteranceId = null;
        this.teardownMic();
        return;
      }
      case "error": {
        const message = typeof msg.message === "string" ? msg.message : "unknown server error";
        if (this.stateValue === "processing" && this.modeValue !== "conversation") this.dispatch({ type: "transcript_failed" });
        this.handlers.onError?.(message);
        if (this.modeValue === "conversation" && this.conversationPaused && !this.conversationTurnPending) {
          void this.resumeConversation();
        } else if (this.modeValue === "conversation" && !this.conversationPaused) {
          this.stopConversation();
        }
        return;
      }
      default:
        // Unknown event: server is authoritative; surface nothing speculative.
        return;
    }
  }
}

// Single active session registry so playback outside the session (autoplay
// `speak`) can honestly mark the SPEAKING state without prop-drilling the tree.
let activeSession: VoiceSession | null = null;

export function notifyVoicePlayback(active: boolean): void {
  if (!activeSession) return;
  if (active) activeSession.playbackStarted();
  else activeSession.playbackFinished();
}
