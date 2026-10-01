import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

/**
 * Regression coverage for two empty-state layout defects found in the live UI by
 * measuring the rendered DOM, not by reading the markup.
 *
 * 1. `ChatShellLanding` clipped its own header.
 *
 *    THE DEFECT. The landing block was a column flex container with
 *    `justify-center`, mounted inside the transcript scroller
 *    (`flex-1 overflow-y-auto px-4 py-6`). A flex container taller than its
 *    scroller centres its content by overflowing *both* ends, and the top
 *    overflow is unreachable - there is no scroll position that reveals it.
 *    Measured in the browser at a 912x670 viewport:
 *
 *        scroller clientHeight = 219
 *        scroller scrollHeight = 652
 *
 *    so roughly two thirds of the landing block sat above the visible area. The
 *    greeting and "How can I help you today?" were sliced through the middle of
 *    their glyphs and the top of the hero avatar was cut off entirely.
 *
 *    THE FIX. `justify-start` plus `my-auto`. The `my-auto` is what actually
 *    centres the block when there IS spare room, because on a column flex item
 *    `margin: auto` resolves in the vertical axis. `justify-center` cannot be
 *    kept: it is the thing that clips.
 *
 * 2. `VoiceControls` let a long status line overrun the control row.
 *
 *    THE DEFECT. The status span carried `truncate max-w-[240px]`, but
 *    `truncate` needs a bounded containing block to bite. Its parent was
 *    `flex ... flex-wrap` with `min-w-0`, and the span itself had no `min-w-0`,
 *    so the flex item refused to shrink below its content width. Measured: the
 *    "real-time voice unavailable - run `make voice-setup`, then check Voice"
 *    line rendered 368px wide inside a 240px cap and overlapped the controls to
 *    its left. `min-w-0` on the item is what lets `truncate` apply.
 *
 * These are source-shape guards on purpose. Both defects are pure CSS, so there
 * is no module behaviour to exercise; what is pinned is the property that
 * caused the bug, so a well-meaning re-centre cannot silently reintroduce it.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const COMPONENTS = join(HERE, "..", "components");

const landing = readFileSync(join(COMPONENTS, "chat-shell", "ChatShellLanding.tsx"), "utf8");
const voiceControls = readFileSync(join(COMPONENTS, "VoiceControls.tsx"), "utf8");

const landingRoot = landing.match(/<div[^>]*data-shell="landing"[^>]*>/)?.[0] ?? "";

test("the landing root does not centre with justify-center", () => {
  // A column flex container taller than its scroller overflows in both
  // directions and the top is unreachable. This is the clipping cause.
  assert.ok(landingRoot, "the landing root div carrying data-shell=\"landing\" must exist");
  assert.doesNotMatch(
    landingRoot,
    /justify-center/,
    "the landing block sits inside an overflow-y-auto scroller; justify-center "
      + "centres by overflowing both ends and slices the greeting and the hero "
      + "avatar off the top of the visible area",
  );
});

test("the landing root keeps justify-start and adds my-auto for centring", () => {
  assert.match(
    landingRoot,
    /justify-start/,
    "justify-start keeps the top of the landing block reachable by scrolling",
  );
  // `my-auto` is the replacement centring: on a column flex item `margin: auto`
  // resolves in the vertical axis, so the block still centres when there is
  // spare height without ever clipping.
  assert.match(
    landingRoot,
    /my-auto/,
    "my-auto centres the landing block when spare height exists, which is what "
      + "justify-center was being asked to do",
  );
});

test("the landing root keeps its vertical padding", () => {
  assert.match(landingRoot, /py-8/, "the landing block's own vertical padding must survive");
});

test("the voice status span can actually shrink enough for truncate to apply", () => {
  const statusSpan = voiceControls.match(/<span\s+role="status"[\s\S]*?>/)?.[0] ?? "";
  assert.ok(statusSpan, "VoiceControls must render a span with role=\"status\"");
  assert.match(
    statusSpan,
    /truncate/,
    "the status line is a single-line hint and must truncate rather than wrap",
  );
  assert.match(
    statusSpan,
    /max-w-\[/,
    "the status line needs a width cap so it cannot grow with its text",
  );
  // The regression itself: without `min-w-0` on the flex item, the item keeps
  // its content width and `truncate` has nothing to clip against.
  assert.match(
    statusSpan,
    /min-w-0/,
    "a flex item defaults to min-width:auto and refuses to shrink below its "
      + "content, so without min-w-0 the max-w cap and truncate are both inert "
      + "and a long status line overlaps the controls beside it",
  );
});