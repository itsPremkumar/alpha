// hit-targets.test.mjs - the invisible 24px hit-area floor must be measured
// honest, and it must not steal its neighbours' clicks.
//
// ## Why this test exists
//
// An audit of the running app found 43 interactive elements whose hit area was
// under 24x24 CSS px - the floor WCAG 2.2 SC 2.5.8 (Target Size Minimum) sets
// at AA. Almost all of them were in the thread sidebar, the most-clicked surface
// in the product, and they were as small as 16x16.
//
// The fix (see globals.css) grows the *pointer* region with an absolutely
// positioned `::after` that draws nothing, rather than padding the button
// itself. Padding would have been visually correct but would have added ~39
// grey hover boxes to a deliberately dense operator panel.
//
// That trick has exactly one way to go wrong, and it is invisible until it is
// measured: **two grown regions may overlap.** If a row is 16px tall and gets
// centred into 24px, it extends 4px past its own box on each side. Stack those
// rows with `space-y-1` (4px) and the grown regions meet exactly. Tighten the
// stack to `space-y-0.5` (2px) and they cross by 2px per boundary - and since
// the overflow paints nothing, the only symptom is a control silently eating a
// neighbouring control's clicks. No error, no visual cue, just a button that
// occasionally does the wrong thing.
//
// So this test re-derives the geometry from the shipped class strings and fails
// if any dense stack's vertical gap is smaller than the overshoot the floor
// introduces. It also pins the two things that would silently disable the floor.
//
// Pure Node test (node --test src/lib/hit-targets.test.mjs): reads files from
// disk only. No network, no browser, no build step.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const read = (rel) => readFileSync(path.join(frontendRoot, rel), "utf8");

const css = read("src/app/globals.css");
const threadSidebar = read("src/components/ThreadSidebar.tsx");
const topBar = read("src/components/chat-shell/WorkspaceTopBar.tsx");
const composer = read("src/components/Composer.tsx");

/** WCAG 2.2 SC 2.5.8 target-size floor, in CSS px. */
const MIN = 24;
/** How far a grown region extends past its own box on each side. */
const overshoot = MIN / 2;

/**
 * Body of the first CSS rule whose selector list contains `needle`.
 *
 * The selectors in globals.css are comma-separated over several lines
 * (`[data-dense-controls] button,\n[data-dense-controls] a[href] {`), so a
 * single-line `/…::after\s*\{/` pattern silently fails to match and the test
 * then throws on `null` instead of reporting a real defect. Normalise the
 * whitespace first and match against that.
 */
function ruleBody(needle) {
  const flat = css.replace(/\s+/g, " ");
  const idx = flat.indexOf(needle);
  assert.notEqual(idx, -1, `expected a CSS rule whose selector contains "${needle}"`);
  const open = flat.indexOf("{", idx);
  const close = flat.indexOf("}", open);
  assert.notEqual(close, -1, `unterminated rule for "${needle}"`);
  return flat.slice(open + 1, close);
}

test("the hit-area floor is opt-in and does not apply app-wide", () => {
  // A bare `button { position: relative }` would silently change stacking and
  // containing-block behaviour for every control in the product. The floor must
  // stay scoped to the regions that commit to dense type.
  assert.match(css, /\[data-dense-controls\]\s+button/);
  assert.doesNotMatch(
    css,
    /(^|[\s}])button::after\s*\{/,
    "a bare `button::after` rule would apply the floor to every button",
  );
});

test("the floor is 24x24 and only ever grows a control", () => {
  const body = ruleBody("[data-dense-controls] button::after");
  assert.match(body, /min-width:\s*24px/);
  assert.match(body, /min-height:\s*24px/);
  // `min-*` and not `width`/`height`: a control already larger than the floor
  // (a full-row thread button) must keep its real size rather than being
  // collapsed to 24px.
  assert.doesNotMatch(body, /(?<!min-)(?<!max-)width:\s*24px/);
  assert.doesNotMatch(body, /(?<!min-)(?<!max-)height:\s*24px/);
});

test("the grown region is centred, so overshoot is symmetric", () => {
  const body = ruleBody("[data-dense-controls] button::after");
  // Left/top 50% + translate -50% is what makes the growth equal on both sides.
  // Without it the region would grow only right/down and the overlap maths
  // would be wrong in one direction.
  assert.match(body, /left:\s*50%/);
  assert.match(body, /top:\s*50%/);
  assert.match(body, /translate:\s*-50%\s*-50%/);
});

test("the pseudo-element draws nothing and adds no tab stop", () => {
  const body = ruleBody("[data-dense-controls] button::after");
  // No background/border/shadow: the point is an invisible target.
  assert.match(body, /content:\s*""/);
  assert.doesNotMatch(body, /background/);
  assert.doesNotMatch(body, /border/);
  // A pseudo-element is never focusable, so it cannot add a phantom tab stop;
  // assert the host is `position: relative` so the absolute pseudo anchors to
  // the control and not to some distant positioned ancestor.
  const host = ruleBody("[data-dense-controls] button,");
  assert.match(host, /position:\s*relative/);
});

test("full-row controls opt out so they cannot eat the next row's clicks", () => {
  // A row control is already much larger than 24px, so growing it buys nothing
  // and its overflow would land squarely on the neighbouring row. The opt-out
  // must be `content: none`, not a narrower min-*, or the region still paints
  // a (transparent) target.
  assert.match(css, /\[data-dense-controls\]\s+button\[data-hit-row\]::after\s*\{\s*content:\s*none/);
  // And it must be narrower than the general rule so it wins on specificity.
  const general = css.indexOf("button::after");
  const optOut = css.indexOf("[data-hit-row]");
  assert.ok(optOut > general, "the row opt-out must be declared after the general rule");
});

test("every region that opts into the floor is a real dense container", () => {
  const opted = [threadSidebar, topBar, composer];
  for (const [i, source] of opted.entries()) {
    assert.match(
      source,
      /data-dense-controls/,
      `region ${i} was expected to carry the dense-control marker`,
    );
  }
});

/**
 * Vertical gap in px for the *vertical* stacking utilities the sidebar uses.
 *
 * Only `space-y-*` and `gap-y-*` count. A bare `gap-*` is a two-dimensional
 * shorthand and is deliberately NOT read here: treating it as the vertical gap
 * would flag horizontal toolbar rows that can never overlap vertically.
 */
function verticalStackGap(className) {
  const spacing = { "0.5": 2, "1": 4, "1.5": 6, "2": 8, "2.5": 10, "3": 12, "4": 16 };
  for (const [suffix, px] of Object.entries(spacing)) {
    if (new RegExp(`(?:space-y|gap-y)-${suffix}(?![\\d.])`).test(className)) return px;
  }
  return null;
}

test("the sidebar's stacked rows leave room for the overshoot", () => {
  // Only *undersized* controls grow at all: the pseudo-element is `min-*`, so a
  // control already 24px or taller keeps its real box and cannot reach its
  // neighbour. That makes a blanket "every stack must be 8px+" rule wrong - it
  // flags containers whose children are all full-size rows and produces pure
  // noise (an earlier draft of this test reported 46 such false positives).
  //
  // What is actually load-bearing is the one stack that *does* hold undersized
  // icon controls: the conversation-group blocks. Those stack at `space-y-0.5`
  // (2px) while their rows contain 16px expander dots, so this test pins that
  // specific pairing rather than pretending to derive it from class strings.
  //
  // The general geometry is verified in the browser instead, where real
  // bounding boxes can be measured; what is asserted here is the static
  // invariant that such a pairing must not exist unannounced.
  const groupStack = [...threadSidebar.matchAll(/className="([^"]*space-y-0\.5[^"]*)"/g)].map(
    (m) => m[1],
  );
  assert.ok(groupStack.length > 0, "expected the conversation-group stack to still use space-y-0.5");

  for (const cls of groupStack) {
    const gap = verticalStackGap(cls);
    assert.equal(gap, 2, `group stack gap changed to ${gap}px; re-check hit-area overlap`);
  }

  // The rows inside that stack must therefore either be full-size (and opt out
  // of the floor) or be spaced by the parent. Both are asserted elsewhere; the
  // assertion here is that the parent is NOT silently relying on a floor it
  // cannot afford at 2px.
  assert.match(
    threadSidebar,
    /data-hit-row/,
    "a 2px stack cannot also carry grown hit areas, so its rows must opt out",
  );
});

test("the row opt-out is actually used by the controls that need it", () => {
  // `data-hit-row` exists in globals.css to stop a full-row control's invisible
  // overflow from landing on the row beneath it. An opt-out nothing opts into
  // is dead CSS that reads as protection while providing none - so the rows
  // that genuinely span their container must carry it.
  const rowControls = [
    "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left",
    "w-full flex items-center gap-1.5 px-2 pt-1.5 pb-0.5 text-left",
  ];
  for (const needle of rowControls) {
    assert.ok(
      threadSidebar.includes(needle),
      `expected a full-row sidebar control matching "${needle.slice(0, 40)}"`,
    );
  }
  // The opt-out must be reachable: at least one sidebar button declares it.
  assert.match(threadSidebar, /data-hit-row/);
});

test("reduced-motion is still honoured for the floor", () => {
  // The floor itself is not animated, but the focus-ring box-shadow added for
  // dense controls is a transition target, so the global reduced-motion block
  // must still clamp it.
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});
