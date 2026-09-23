// voice.test.mjs — state machine transitions + terminal-honesty + frame encoding.
// Pure Node test (node --test src/lib/voice.test.mjs): transpiles voice.ts and
// rewrites its two relative imports to data: URL stubs so no network/browser runs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const apiClientStub = `
export const GATEWAY_BASE = "/api";
export function apiUrl(path) { return path; }
export function csrfToken() { return undefined; }
export async function apiFetch() { throw new Error("apiFetch stub (no network in tests)"); }
`;

const multimodalStub = `
export class MultimodalError extends Error {}
export async function transcribeUpload() { throw new Error("transcribeUpload stub (no network in tests)"); }
`;

const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const voicePath = fileURLToPath(new URL("./voice.ts", import.meta.url));
let code = ts.transpileModule(readFileSync(voicePath, "utf8"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
code = code.replace(/from\s+"\.\/api-client"/, `from "${toDataUrl(apiClientStub)}"`);
code = code.replace(/from\s+"\.\/multimodal"/, `from "${toDataUrl(multimodalStub)}"`);

const {
  voiceReducer,
  isTerminalVoiceState,
  INITIAL_VOICE_STATE,
  TERMINAL_VOICE_STATES,
  floatToPcm16,
  downsampleTo16k,
  pcm16ToBase64,
  TARGET_SAMPLE_RATE,
  WORKLET_SOURCE,
  parseVoiceCapabilities,
  fetchVoiceCapabilities,
  readAutoplayEnabled,
} = await import(toDataUrl(code));

const ALL_STATES = ["idle", "wake_armed", "listening", "processing", "speaking", ...TERMINAL_VOICE_STATES];
const ALL_EVENTS = [
  { type: "arm_ok" },
  { type: "disarm" },
  { type: "listen_start" },
  { type: "listen_stop" },
  { type: "transcript" },
  { type: "transcript_failed" },
  { type: "play" },
  { type: "play_end" },
  { type: "engine_missing", detail: "edge" },
  { type: "auth_failed", detail: "4401" },
  { type: "ws_closed", reason: "gone" },
  { type: "reset" },
];

test("initial state is idle", () => {
  assert.equal(INITIAL_VOICE_STATE, "idle");
  assert.equal(isTerminalVoiceState("idle"), false);
  assert.equal(isTerminalVoiceState("engine_missing"), true);
  assert.equal(isTerminalVoiceState("auth_failed"), true);
  assert.equal(isTerminalVoiceState("ws_closed"), true);
});

test("wake-word flow: idle → wake_armed → disarm → idle", () => {
  let s = voiceReducer("idle", { type: "arm_ok" });
  assert.equal(s, "wake_armed");
  s = voiceReducer(s, { type: "disarm" });
  assert.equal(s, "idle");
});

test("push-to-talk flow: idle → listening → processing → idle (transcript)", () => {
  let s = voiceReducer("idle", { type: "listen_start" });
  assert.equal(s, "listening");
  s = voiceReducer(s, { type: "listen_stop" });
  assert.equal(s, "processing");
  s = voiceReducer(s, { type: "transcript" });
  assert.equal(s, "idle");
});

test("push-to-talk failure returns to idle (transcript_failed)", () => {
  let s = voiceReducer("listening", { type: "listen_stop" });
  assert.equal(s, "processing");
  s = voiceReducer(s, { type: "transcript_failed" });
  assert.equal(s, "idle");
});

test("wake_armed can enter listening when capture starts", () => {
  assert.equal(voiceReducer("wake_armed", { type: "listen_start" }), "listening");
});

test("speaking: idle → play → speaking → play_end → idle", () => {
  let s = voiceReducer("idle", { type: "play" });
  assert.equal(s, "speaking");
  s = voiceReducer(s, { type: "play_end" });
  assert.equal(s, "idle");
});

test("autoplay never hijacks an armed/capturing/processing session", () => {
  assert.equal(voiceReducer("wake_armed", { type: "play" }), "wake_armed");
  assert.equal(voiceReducer("listening", { type: "play" }), "listening");
  assert.equal(voiceReducer("processing", { type: "play" }), "processing");
});

test("every non-terminal state reaches every terminal state on terminal events", () => {
  for (const state of ALL_STATES.filter((s) => !isTerminalVoiceState(s))) {
    assert.equal(voiceReducer(state, { type: "engine_missing" }), "engine_missing", `${state} + engine_missing`);
    assert.equal(voiceReducer(state, { type: "auth_failed" }), "auth_failed", `${state} + auth_failed`);
    assert.equal(voiceReducer(state, { type: "ws_closed" }), "ws_closed", `${state} + ws_closed`);
  }
});

test("terminal states absorb every event except reset", () => {
  for (const state of TERMINAL_VOICE_STATES) {
    for (const event of ALL_EVENTS) {
      const next = voiceReducer(state, event);
      if (event.type === "reset") {
        assert.equal(next, "idle", `${state} + reset`);
      } else {
        assert.equal(next, state, `${state} + ${event.type} must be absorbed`);
      }
    }
  }
});

test("reset from every state returns to idle", () => {
  for (const state of ALL_STATES) {
    assert.equal(voiceReducer(state, { type: "reset" }), "idle", `${state} + reset`);
  }
});

test("invalid transitions are no-ops", () => {
  const noops = [
    ["idle", { type: "listen_stop" }],
    ["idle", { type: "transcript" }],
    ["idle", { type: "transcript_failed" }],
    ["idle", { type: "play_end" }],
    ["wake_armed", { type: "arm_ok" }],
    ["wake_armed", { type: "listen_stop" }],
    ["listening", { type: "arm_ok" }],
    ["listening", { type: "transcript" }],
    ["processing", { type: "listen_start" }],
    ["processing", { type: "arm_ok" }],
    ["speaking", { type: "listen_start" }],
    ["speaking", { type: "play" }],
  ];
  for (const [state, event] of noops) {
    assert.equal(voiceReducer(state, event), state, `${state} + ${event.type}`);
  }
});

test("event payloads (reason/detail) never change the transition result", () => {
  assert.equal(voiceReducer("idle", { type: "ws_closed" }), "ws_closed");
  assert.equal(voiceReducer("idle", { type: "ws_closed", reason: "x" }), "ws_closed");
  assert.equal(voiceReducer("listening", { type: "engine_missing", detail: "y" }), "engine_missing");
});

test("floatToPcm16 clamps, rounds, and silences non-finite samples", () => {
  const input = new Float32Array([0, 1, -1, 0.5, -0.5, 1.5, -1.5, NaN, Infinity, -Infinity]);
  const out = floatToPcm16(input);
  assert.ok(out instanceof Int16Array);
  assert.equal(out.length, input.length);
  assert.equal(out[0], 0);
  assert.equal(out[1], 32767);
  assert.equal(out[2], -32768);
  assert.equal(out[3], Math.round(0.5 * 32767));
  assert.equal(out[4], Math.round(-0.5 * 32768));
  assert.equal(out[5], 32767);
  assert.equal(out[6], -32768);
  assert.equal(out[7], 0);
  assert.equal(out[8], 0);
  assert.equal(out[9], 0);
});

test("downsampleTo16k: 48 kHz constant → 16 kHz length and values", () => {
  const rate = 48000;
  const input = new Float32Array(rate).fill(0.75);
  const out = downsampleTo16k(input, rate);
  assert.equal(out.length, TARGET_SAMPLE_RATE);
  for (const value of out) assert.equal(value, 0.75);
});

test("downsampleTo16k: 32 kHz ramp box-filters [0,2,4,6] → [1,5]", () => {
  const input = new Float32Array([0, 2, 4, 6]);
  const out = downsampleTo16k(input, 32000);
  assert.deepEqual(Array.from(out), [1, 5]);
});

test("downsampleTo16k: identity at 16 kHz, throws below and on invalid rates", () => {
  const input = new Float32Array([0.1, 0.2, 0.3]);
  assert.equal(downsampleTo16k(input, 16000), input);
  assert.throws(() => downsampleTo16k(input, 8000), /upsampling is not supported/);
  assert.throws(() => downsampleTo16k(input, 0), /invalid source sample rate/);
  assert.throws(() => downsampleTo16k(input, NaN), /invalid source sample rate/);
  assert.throws(() => downsampleTo16k(input, -48000), /invalid source sample rate/);
});

test("pcm16ToBase64 round-trips little-endian bytes via atob", () => {
  const b64 = pcm16ToBase64(new Int16Array([1, -1]));
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  assert.deepEqual(Array.from(bytes), [1, 0, 255, 255]);
  assert.equal(pcm16ToBase64(new Int16Array(0)), "");
  const big = pcm16ToBase64(new Int16Array(20000));
  assert.equal(Uint8Array.from(atob(big), (c) => c.charCodeAt(0)).length, 40000);
});

test("WORKLET_SOURCE registers the capture processor", () => {
  assert.equal(typeof WORKLET_SOURCE, "string");
  assert.match(WORKLET_SOURCE, /registerProcessor\("alpha-pcm-capture"/);
  assert.match(WORKLET_SOURCE, /postMessage/);
  assert.match(WORKLET_SOURCE, /return true/);
});

test("parseVoiceCapabilities reads the nested voice block honestly", () => {
  const report = {
    rows: [
      { capability: "tts", tier: "T2", engine: "edge-tts", status: "available", detail: "d" },
      { capability: "stt", tier: "T1", engine: "faster-whisper", status: "not_installed", detail: "d" },
      { capability: "stt", tier: "T3", engine: "alpha.media.stt", status: "available", detail: "d" },
    ],
    voice: { enabled: true },
    note: "n",
  };
  const caps = parseVoiceCapabilities(report);
  assert.equal(caps.enabled, true);
  assert.equal(caps.tts, true);
  assert.equal(caps.stt, true);
  // voice disabled dominates even when engines observe available
  const off = parseVoiceCapabilities({ ...report, voice: { enabled: false } });
  assert.deepEqual({ enabled: off.enabled, stt: off.stt, tts: off.tts }, { enabled: false, stt: false, tts: false });
  // missing/garbage payloads claim nothing
  assert.deepEqual(parseVoiceCapabilities(null), { enabled: false, stt: false, tts: false, detail: null });
  assert.deepEqual(parseVoiceCapabilities({ rows: report.rows }).enabled, false);
});

test("fetchVoiceCapabilities degrades to all-false when the request fails", async () => {
  const caps = await fetchVoiceCapabilities();
  assert.deepEqual({ enabled: caps.enabled, stt: caps.stt, tts: caps.tts }, { enabled: false, stt: false, tts: false });
});

test("readAutoplayEnabled is false when localStorage is unavailable (Node)", () => {
  assert.equal(readAutoplayEnabled(), false);
});
