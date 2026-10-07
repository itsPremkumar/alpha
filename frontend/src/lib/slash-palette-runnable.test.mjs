// slash-palette-runnable.test.mjs — the palette headline and "runnable only" toggle.
//
// Found on 2026-10-07. The backend returns 461 commands with 54 dispatchable,
// and the palette listed every row with only a per-row "no handler" badge. The
// headline count an operator reads first was the *match* count ("N of M commands
// match"), never the *dispatchable* count — so "461 commands" read as usable
// while ~407 answer `unimplemented`. Plan §3.T3 gates: the headline is the
// dispatchable count, the catalogued total beside it, labelled.
//
// Each test names the payload that would make the plausible wrong word appear.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const code = ts.transpileModule(read("./slash-command-palette.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { splitRunnableRows, runnableHeadline } = await import(toDataUrl(code));

const row = (command, hasHandler) => ({ command, hasHandler });

// ---------------------------------------------------------------------------
// splitRunnableRows: only server-confirmed handler-less rows are hidden
// ---------------------------------------------------------------------------

test("only hasHandler === false rows are hidden", () => {
  const { visible, hidden } = splitRunnableRows([
    row("/a", true),
    row("/b", false),
    row("/c", null),
    row("/d", undefined),
  ]);
  assert.deepEqual(visible.map((r) => r.command), ["/a", "/c", "/d"]);
  assert.equal(hidden, 1);
});

test("null (unreported) stays visible — unknown is not negative", () => {
  // Hiding unreported rows would present an unverified subset as "all runnable".
  const { visible } = splitRunnableRows([row("/a", null)]);
  assert.equal(visible.length, 1);
});

test("an all-runnable list hides nothing and says so", () => {
  const { visible, hidden } = splitRunnableRows([row("/a", true), row("/b", true)]);
  assert.equal(visible.length, 2);
  assert.equal(hidden, 0);
});

test("an empty list splits to empty without throwing", () => {
  assert.deepEqual(splitRunnableRows([]), { visible: [], hidden: 0 });
});

// ---------------------------------------------------------------------------
// runnableHeadline: only confirmed-runnable counts as runnable
// ---------------------------------------------------------------------------

test("headline counts only hasHandler === true as runnable", () => {
  // Mirrors the live registry shape: 461 listed, 54 with a bound handler.
  const rows = [
    ...Array.from({ length: 54 }, (_, i) => row(`/r${i}`, true)),
    ...Array.from({ length: 400 }, (_, i) => row(`/u${i}`, false)),
    ...Array.from({ length: 7 }, (_, i) => row(`/n${i}`, null)),
  ];
  const h = runnableHeadline(rows);
  assert.equal(h.runnable, 54);
  assert.equal(h.listed, 461);
});

test("unreported rows are neither runnable nor hidden-from-listed", () => {
  const h = runnableHeadline([row("/a", null), row("/b", undefined)]);
  assert.equal(h.runnable, 0);
  assert.equal(h.listed, 2);
});

// ---------------------------------------------------------------------------
// Wiring: the Composer actually uses the derivation
// ---------------------------------------------------------------------------

const composer = read("../components/Composer.tsx");

test("the palette header carries the runnable-of-listed headline", () => {
  assert.match(composer, /\$\{runnableCount\} runnable of \$\{listedCount\} listed/);
});

test("the fallback list never renders counts as health", () => {
  // 16 built-in rows with unreported handlers: "0 runnable" would claim a
  // health nobody measured.
  assert.match(composer, /built-in fallback — registry unavailable/);
});

test("the toggle is off by default and names what it does", () => {
  assert.match(composer, /const \[runnableOnly, setRunnableOnly\] = useState\(false\)/);
  assert.match(composer, /aria-label="Show only commands the registry reports as runnable"/);
  assert.match(composer, /runnable only/);
});

test("the footer names how many rows the toggle hid", () => {
  assert.match(composer, /hiding \{hiddenByToggle\} row\(s\) the registry reports with no bound handler/);
});

test("keyboard, scroll and render all follow the filtered list", () => {
  // If any consumer still read `suggestions`, the highlight, the Enter commit
  // and the pixels would disagree about which row is selected.
  assert.match(composer, /visibleSuggestions\.length > 0 && \(\s*$/m);
  assert.match(composer, /visibleSuggestions\[selectedIndex\]/);
  assert.match(composer, /visibleSuggestions\.map\(\(cmd, idx\)/);
  assert.match(composer, /\[selectedIndex, visibleSuggestions\]/);
});

test("toggling resets the highlight so Enter cannot commit a stale row", () => {
  assert.match(composer, /setSelectedIndex\(0\);\s*\n\s*\}, \[runnableOnly\]\);/);
});

test("the old bare match-count footer is not the only sentence", () => {
  // describePalette stays (match counts are still true), but it must not stand
  // alone while the toggle hides rows.
  assert.match(composer, /\{describePalette\(palette\)\}/);
});
