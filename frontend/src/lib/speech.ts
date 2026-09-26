/**
 * Small, dependency-free building blocks for real-time local speech.
 *
 * The chat stream gives us snapshots, not a transcript API: a sentence can be
 * complete in one update and replaced by a longer snapshot in the next one.
 * `SpeechSegmenter` therefore accepts ordinary deltas and also exposes
 * `pushSnapshot` for callers that receive cumulative text. It deliberately
 * releases only completed sentences/clauses while a response is streaming;
 * `flush()` is the explicit end-of-response boundary.
 *
 * `SpeechQueue` is intentionally independent of the browser/audio code. Tests
 * can inject a player, while the chat view injects the local TTS `speak`
 * function. A queue instance is a singleton at the module boundary so a new
 * turn, stop action, or stream error can cancel all pending speech.
 */

export const MAX_SPEECH_SEGMENT_CHARS = 240;
export const MAX_SPEECH_QUEUE_TASKS = 128;
export const DEFAULT_MIN_CLAUSE_CHARS = 48;

export interface SpeechSegmenterOptions {
  maxChars?: number;
  minClauseChars?: number;
}

function boundedPositive(value: number | undefined, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.max(8, Math.floor(value))
    : fallback;
}

function normalizeText(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function isFence(line: string): { char: string; length: number } | null {
  const match = line.match(/^\s{0,3}(`{3,}|~{3,})/);
  return match ? { char: match[1][0], length: match[1].length } : null;
}

function looksLikeTable(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes("|") || trimmed.length < 3) return false;
  const cells = trimmed.split("|");
  if (cells.length < 3) return false;
  // Markdown tables have a pipe on both sides (possibly omitted on the outer
  // edges). A separator row is unambiguously noise; ordinary prose containing
  // a pipe is uncommon in a spoken answer, so treating multi-cell rows as
  // table content is the safer/less surprising default.
  return cells.slice(1, -1).some((cell) => cell.trim().length > 0) &&
    (cells.filter((cell) => /^:?-{3,}:?$/.test(cell.trim())).length >= 2 || trimmed.replace(/\|/g, "").trim().length > 0);
}

/** Remove presentation/markup noise while preserving natural link labels. */
export function cleanSpeechMarkdown(value: string): string {
  let text = value;
  text = text.replace(/```[\s\S]*?```/g, " ");
  text = text.replace(/~~~[\s\S]*?~~~/g, " ");
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, " ");
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  text = text.replace(/<[^>]*>/g, " ");
  text = text.replace(/`[^`]*`/g, " ");
  text = text.replace(/\b(?:https?:\/\/|www\.)[^\s<>()]+/gi, " ");
  text = text.replace(/^\s{0,3}#{1,6}\s+/, "");
  text = text.replace(/^\s{0,3}>\s?/, "");
  text = text.replace(/^\s{0,3}(?:[-+*]|\d+[.)])\s+/, "");
  text = text.replace(/^\s*(?:[-*_]\s*){3,}$/g, " ");
  text = text.replace(/[*_~]/g, "");
  return normalizeText(text);
}

function sentenceBoundary(text: string): number {
  const punctuation = /[.!?](?:["'”’)}\]]+)?(?=\s|$)/g;
  for (const match of text.matchAll(punctuation)) {
    const index = (match.index ?? 0) + match[0].length;
    const before = text.slice(0, index).replace(/["'”’)}\]]+$/, "");
    const token = before.match(/([A-Za-z][A-Za-z.]*)$/)?.[1] || "";
    // Avoid releasing common abbreviations and initials as complete turns.
    if (/^(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|u\.s|a\.m|p\.m)$/i.test(token)) continue;
    if (token.length === 2 && token.endsWith(".")) continue;
    if (/\d\.$/.test(before)) continue;
    return index;
  }
  return -1;
}

function clauseBoundary(text: string, minimum: number): number {
  if (text.trim().length < minimum) return -1;
  const match = /[,;:](?=\s|$)/.exec(text);
  return match ? match.index + match[0].length : -1;
}

function boundedCut(text: string, maximum: number): number {
  if (text.length <= maximum) return text.length;
  const window = text.slice(0, maximum + 1);
  const whitespace = window.lastIndexOf(" ");
  if (whitespace >= Math.floor(maximum * 0.55)) return whitespace;
  return maximum;
}

/**
 * Incremental sentence/clause segmenter for a streamed assistant response.
 *
 * The class has no browser dependencies, which keeps its behavior easy to
 * exercise with `node --test` and avoids coupling the chat renderer to a
 * particular markdown parser.
 */
export class SpeechSegmenter {
  private readonly maxChars: number;
  private readonly minClauseChars: number;
  private rawLine = "";
  private pending = "";
  private partialPending = "";
  private partialRawSeen = "";
  private inFence = false;
  private fenceChar = "";
  private fenceLength = 0;
  private sourceSnapshot = "";

  constructor(options: SpeechSegmenterOptions = {}) {
    this.maxChars = boundedPositive(options.maxChars, MAX_SPEECH_SEGMENT_CHARS);
    this.minClauseChars = typeof options.minClauseChars === "number" && Number.isFinite(options.minClauseChars) && options.minClauseChars > 0
      ? Math.max(1, Math.floor(options.minClauseChars))
      : DEFAULT_MIN_CLAUSE_CHARS;
  }

  /** Append streamed text and return segments that are safe to speak now. */
  push(delta: string): string[] {
    if (!delta) return [];
    this.sourceSnapshot += delta;
    this.rawLine += delta;
    const released: string[] = [];
    let newline: number;
    while ((newline = this.rawLine.indexOf("\n")) >= 0) {
      const line = this.rawLine.slice(0, newline).replace(/\r$/, "");
      this.rawLine = this.rawLine.slice(newline + 1);
      this.consumeLine(line, released);
    }
    if (this.rawLine && !this.inFence && !isFence(this.rawLine)) {
      this.consumePartialLine(this.rawLine, released);
    }
    return released;
  }

  /**
   * Feed a cumulative stream snapshot. If the backend replaces the visible
   * text (rather than appending), the old segmenter state is discarded so a
   * revised answer is never spoken twice.
   */
  pushSnapshot(snapshot: string): string[] {
    if (snapshot === this.sourceSnapshot) return [];
    if (!snapshot.startsWith(this.sourceSnapshot)) this.reset();
    return this.push(snapshot.slice(this.sourceSnapshot.length));
  }

  /** Release the final incomplete sentence/clause at stream completion. */
  flush(): string[] {
    const released: string[] = [];
    if (this.rawLine) {
      if (this.partialRawSeen && !this.inFence && !isFence(this.rawLine) && !looksLikeTable(this.rawLine)) {
        this.pending = normalizeText(`${this.pending} ${this.partialPending}`);
      } else {
        const line = this.rawLine;
        this.partialPending = "";
        this.partialRawSeen = "";
        this.consumeLine(line, released);
      }
    }
    this.rawLine = "";
    this.partialPending = "";
    this.partialRawSeen = "";
    const drained = this.drainBuffer(this.pending, true);
    this.pending = drained.remainder;
    released.push(...drained.released);
    return released;
  }

  reset(): void {
    this.rawLine = "";
    this.pending = "";
    this.partialPending = "";
    this.partialRawSeen = "";
    this.inFence = false;
    this.fenceChar = "";
    this.fenceLength = 0;
    this.sourceSnapshot = "";
  }

  get bufferedText(): string {
    return normalizeText(`${this.pending} ${this.partialPending}`);
  }

  get sourceText(): string {
    return this.sourceSnapshot;
  }

  private consumeLine(line: string, released: string[]): void {
    // A partial line may already have released complete sentences. Its
    // unreleased tail is joined exactly once when the newline arrives.
    if (this.partialPending) {
      this.pending = normalizeText(`${this.pending} ${this.partialPending}`);
      this.partialPending = "";
    }
    this.partialRawSeen = "";
    const fence = isFence(line);
    if (this.inFence) {
      if (fence && fence.char === this.fenceChar && fence.length >= this.fenceLength) {
        this.inFence = false;
        this.fenceChar = "";
        this.fenceLength = 0;
      }
      return;
    }
    if (fence) {
      this.inFence = true;
      this.fenceChar = fence.char;
      this.fenceLength = fence.length;
      return;
    }
    if (looksLikeTable(line) || /^\s*(?:[-*_]\s*){3,}$/.test(line)) return;
    const cleaned = cleanSpeechMarkdown(line);
    if (!cleaned) return;
    this.pending = normalizeText(`${this.pending} ${cleaned}`);
    const drained = this.drainBuffer(this.pending, false);
    this.pending = drained.remainder;
    released.push(...drained.released);
  }

  private consumePartialLine(line: string, released: string[]): void {
    if (this.inFence || isFence(line) || looksLikeTable(line)) return;
    const previous = this.partialRawSeen;
    const delta = previous && line.startsWith(previous) ? line.slice(previous.length) : line;
    this.partialRawSeen = line;
    const joined = previous && line.startsWith(previous)
      ? `${this.partialPending}${/^[\s\p{P}]/u.test(delta) || !this.partialPending ? "" : " "}${delta}`
      : `${this.partialPending} ${delta}`;
    const input = cleanSpeechMarkdown(this.pending
      ? `${this.pending}${/^[\s\p{P}]/u.test(joined) ? "" : " "}${joined}`
      : joined);
    if (!input) {
      this.pending = "";
      this.partialPending = "";
      return;
    }
    const drained = this.drainBuffer(input, false);
    this.pending = drained.remainder;
    this.partialPending = "";
    released.push(...drained.released);
  }

  private drainBuffer(value: string, final: boolean): { released: string[]; remainder: string } {
    let remainder = normalizeText(value);
    const released: string[] = [];
    for (;;) {
      remainder = normalizeText(remainder);
      if (!remainder) break;
      let boundary = sentenceBoundary(remainder);
      if (boundary < 0) boundary = clauseBoundary(remainder, this.minClauseChars);
      if (boundary > 0) {
        const segment = remainder.slice(0, boundary).trim();
        remainder = remainder.slice(boundary).trim();
        if (segment) released.push(segment);
        continue;
      }
      if (remainder.length > this.maxChars) {
        const cut = boundedCut(remainder, this.maxChars);
        const segment = remainder.slice(0, cut).trim();
        remainder = remainder.slice(cut).trim();
        if (segment) released.push(segment);
        continue;
      }
      if (final) {
        const tail = remainder.trim();
        remainder = "";
        if (tail) released.push(tail);
      }
      break;
    }
    return { released, remainder };
  }
}

/** One-shot helper useful for tests and non-streaming callers. */
export function segmentSpeechText(text: string, options: SpeechSegmenterOptions = {}): string[] {
  const segmenter = new SpeechSegmenter(options);
  return [...segmenter.push(text), ...segmenter.flush()];
}

export type SpeechPlayer = (text: string, signal: AbortSignal) => Promise<void>;

export interface SpeechEnqueueOptions {
  player: SpeechPlayer;
  signal?: AbortSignal;
}

interface SpeechTask {
  text: string;
  player: SpeechPlayer;
  resolve: () => void;
  reject: (error: unknown) => void;
  externalSignal?: AbortSignal;
  externalAbort?: () => void;
  settled: boolean;
  controller: AbortController;
}

export class SpeechCancelledError extends Error {
  readonly reason: string;

  constructor(reason = "Speech playback cancelled") {
    super(reason);
    this.name = "SpeechCancelledError";
    this.reason = reason;
  }
}

export function isSpeechCancellation(error: unknown): boolean {
  return error instanceof SpeechCancelledError || (typeof DOMException !== "undefined" && error instanceof DOMException && error.name === "AbortError") || (typeof error === "object" && error !== null && "name" in error && (error as { name?: unknown }).name === "AbortError");
}

export interface SpeechQueueOptions {
  /** Stop and discard pending work after a player failure. Defaults to true. */
  stopOnError?: boolean;
}

/**
 * Serial, cancellable speech queue. Cancellation rejects queued/current
 * callers, while the active player is given an AbortSignal; a new task cannot
 * start until that player has actually settled, preventing overlap.
 */
export class SpeechQueue {
  private readonly stopOnError: boolean;
  private queue: SpeechTask[] = [];
  private current: SpeechTask | null = null;
  private running = false;
  private generation = 0;
  private idleWaiters: Array<{ resolve: () => void }> = [];

  constructor(options: SpeechQueueOptions = {}) {
    this.stopOnError = options.stopOnError ?? true;
  }

  get size(): number {
    return this.queue.length + (this.current ? 1 : 0);
  }

  get active(): boolean {
    return this.current !== null;
  }

  enqueue(text: string, options: SpeechEnqueueOptions): Promise<void> {
    const value = text.trim();
    if (!value) return Promise.resolve();
    if (options.signal?.aborted) return Promise.reject(new SpeechCancelledError());
    let resolveTask!: () => void;
    let rejectTask!: (error: unknown) => void;
    const promise = new Promise<void>((resolve, reject) => {
      resolveTask = resolve;
      rejectTask = reject;
    });
    const task: SpeechTask = {
      text: value,
      player: options.player,
      resolve: resolveTask,
      reject: rejectTask,
      externalSignal: options.signal,
      settled: false,
      controller: new AbortController(),
    };
    if (options.signal) {
      const abort = () => {
        this.remove(task);
        this.settle(task, "reject", new SpeechCancelledError());
        if (this.current === task) {
          try {
            task.controller.abort();
          } catch {
            // An injected player may not expose a useful AbortController.
          }
        }
      };
      task.externalAbort = abort;
      options.signal.addEventListener("abort", abort, { once: true });
    }
    if (this.queue.length >= MAX_SPEECH_QUEUE_TASKS) {
      const stale = this.queue.shift();
      if (stale) this.settle(stale, "reject", new SpeechCancelledError("Speech queue overflow; stale segment dropped"));
    }
    this.queue.push(task);
    void this.drain();
    return promise;
  }

  cancel(reason = "Speech playback cancelled"): void {
    this.generation += 1;
    const pending = this.queue.splice(0);
    for (const task of pending) this.settle(task, "reject", new SpeechCancelledError(reason));
    if (this.current) {
      this.settle(this.current, "reject", new SpeechCancelledError(reason));
      try {
        this.current.controller.abort();
      } catch {
        // An injected player may not expose a useful AbortController.
      }
    }
  }

  async waitForIdle(): Promise<void> {
    if (!this.running && this.queue.length === 0) return;
    await new Promise<void>((resolve) => this.idleWaiters.push({ resolve }));
  }

  private remove(task: SpeechTask): void {
    const index = this.queue.indexOf(task);
    if (index >= 0) this.queue.splice(index, 1);
  }

  private settle(task: SpeechTask, kind: "resolve" | "reject", error?: unknown): void {
    if (task.settled) return;
    task.settled = true;
    if (task.externalSignal && task.externalAbort) task.externalSignal.removeEventListener("abort", task.externalAbort);
    if (kind === "resolve") task.resolve();
    else task.reject(error);
  }

  private async drain(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const runGeneration = this.generation;
    try {
      while (this.queue.length > 0) {
        if (runGeneration !== this.generation) break;
        const task = this.queue.shift()!;
        this.current = task;
        try {
          await task.player(task.text, task.controller.signal);
          this.settle(task, "resolve");
        } catch (error) {
          this.settle(task, "reject", error);
          if (this.stopOnError && !isSpeechCancellation(error)) {
            this.cancel("Speech playback failed");
            break;
          }
        } finally {
          if (this.current === task) this.current = null;
        }
      }
    } finally {
      this.running = false;
      if (this.queue.length === 0) {
        const waiters = this.idleWaiters.splice(0);
        for (const waiter of waiters) waiter.resolve();
      }
      if (this.queue.length > 0) void this.drain();
    }
  }
}

// One queue per page/module. Callers still inject the actual local player.
const speechQueue = new SpeechQueue();

export function getSpeechQueue(): SpeechQueue {
  return speechQueue;
}

export function enqueueSpeech(text: string, options: SpeechEnqueueOptions): Promise<void> {
  return speechQueue.enqueue(text, options);
}

export function cancelSpeech(reason?: string): void {
  speechQueue.cancel(reason);
}

export function waitForSpeechIdle(): Promise<void> {
  return speechQueue.waitForIdle();
}
