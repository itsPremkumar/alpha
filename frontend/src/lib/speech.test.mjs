// Pure speech segmentation/queue coverage. No browser, network, or audio APIs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(fileURLToPath(new URL("./speech.ts", import.meta.url)), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const module = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const {
  SpeechSegmenter,
  SpeechQueue,
  SpeechCancelledError,
  segmentSpeechText,
  isSpeechCancellation,
  MAX_SPEECH_SEGMENT_CHARS,
  MAX_SPEECH_QUEUE_TASKS,
} = module;

test("releases a completed sentence while the stream is still open", () => {
  const segmenter = new SpeechSegmenter();
  assert.deepEqual(segmenter.push("Hello there. How are"), ["Hello there."]);
  assert.deepEqual(segmenter.flush(), ["How are"]);
});

test("flush releases the complete final clause and sentence", () => {
  assert.deepEqual(segmentSpeechText("One. Two, three!"), ["One.", "Two, three!"]);
});

test("skips fenced code and markdown tables/URLs", () => {
  const text = [
    "Here is the answer.",
    "```ts",
    "const secret = 'do not speak';",
    "```",
    "| name | value |",
    "| --- | --- |",
    "| x | y |",
    "See https://example.test/docs for details.",
  ].join("\n");
  assert.deepEqual(segmentSpeechText(text), ["Here is the answer.", "See for details."]);
});

test("removes inline markdown noise while retaining link labels", () => {
  assert.deepEqual(segmentSpeechText("**Bold** and [the docs](https://example.test)."), ["Bold and the docs."]);
});

test("chunks long unpunctuated text at bounded speakable sizes", () => {
  const segmenter = new SpeechSegmenter({ maxChars: 20, minClauseChars: 1000 });
  const text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau";
  const segments = [...segmenter.push(text), ...segmenter.flush()];
  assert.ok(segments.length > 1);
  assert.ok(segments.every((segment) => segment.length <= 20));
  assert.equal(segments.join(" "), text);
});

test("releases a completed natural clause when configured for low latency", () => {
  const segmenter = new SpeechSegmenter({ minClauseChars: 5 });
  assert.deepEqual(segmenter.push("Here is a clause, and this is still streaming"), ["Here is a clause,"]);
  assert.deepEqual(segmenter.flush(), ["and this is still streaming"]);
});

test("final flush preserves order when a prior line is still pending", () => {
  const segmenter = new SpeechSegmenter();
  assert.deepEqual(segmenter.push("A complete line without punctuation\nThe next sentence."), ["A complete line without punctuation The next sentence."]);
  assert.deepEqual(segmenter.flush(), []);
});

test("snapshot updates do not duplicate or insert spaces before punctuation", () => {
  const segmenter = new SpeechSegmenter();
  const released = [
    ...segmenter.pushSnapshot("Hello there. How are"),
    ...segmenter.pushSnapshot("Hello there. How are you"),
    ...segmenter.pushSnapshot("Hello there. How are you? Fine."),
    ...segmenter.flush(),
  ];
  assert.deepEqual(released, ["Hello there.", "How are you?", "Fine."]);
});

test("does not release an incomplete sentence until final flush", () => {
  const segmenter = new SpeechSegmenter();
  assert.deepEqual(segmenter.push("A partial thought"), []);
  assert.equal(segmenter.bufferedText, "A partial thought");
  assert.deepEqual(segmenter.flush(), ["A partial thought"]);
});

test("queue serializes players and never overlaps speech requests", async () => {
  const queue = new SpeechQueue({ stopOnError: false });
  let active = 0;
  let maximum = 0;
  const order = [];
  const player = async (text) => {
    active += 1;
    maximum = Math.max(maximum, active);
    order.push(`start:${text}`);
    await new Promise((resolve) => setTimeout(resolve, 2));
    order.push(`end:${text}`);
    active -= 1;
  };
  await Promise.all([
    queue.enqueue("one", { player }),
    queue.enqueue("two", { player }),
    queue.enqueue("three", { player }),
  ]);
  assert.equal(maximum, 1);
  assert.deepEqual(order, ["start:one", "end:one", "start:two", "end:two", "start:three", "end:three"]);
});

test("queue cancellation rejects pending work and aborts the active player", async () => {
  const queue = new SpeechQueue();
  const started = [];
  const player = (text, signal) => new Promise((resolve, reject) => {
    started.push(text);
    if (text === "active") {
      signal.addEventListener("abort", () => reject(new SpeechCancelledError()), { once: true });
    } else {
      resolve();
    }
  });
  const active = queue.enqueue("active", { player });
  const pending = queue.enqueue("pending", { player });
  await new Promise((resolve) => setImmediate(resolve));
  queue.cancel("new turn");
  await assert.rejects(active, (error) => isSpeechCancellation(error));
  await assert.rejects(pending, (error) => isSpeechCancellation(error));
  await queue.waitForIdle();
  assert.deepEqual(started, ["active"]);
});

test("queue stops after a player error instead of overlapping a later segment", async () => {
  const queue = new SpeechQueue();
  const played = [];
  const failing = queue.enqueue("bad", {
    player: async (text) => {
      played.push(text);
      throw new Error("local TTS failed");
    },
  });
  const later = queue.enqueue("later", {
    player: async (text) => {
      played.push(text);
    },
  });
  await assert.rejects(failing, /local TTS failed/);
  await assert.rejects(later, (error) => isSpeechCancellation(error));
  assert.deepEqual(played, ["bad"]);
});

test("segment and queue bounds are exported for callers and tests", () => {
  assert.equal(MAX_SPEECH_SEGMENT_CHARS, 240);
  assert.equal(MAX_SPEECH_QUEUE_TASKS, 128);
});
