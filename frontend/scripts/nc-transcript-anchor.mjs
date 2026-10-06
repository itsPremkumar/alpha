// Negative controls for the `ui-legibility` transcript-anchor fix.
//
// Same discipline as nc-line-shape-tolerance.mjs: drive the same lookup and the
// same five assertions against hand-built sources. ChatView.tsx is NOT mutated,
// because a concurrent agent is actively rewriting it — and mutating a source
// file to prove a test works is how this session already shipped `if False:`
// into a shipped runner.
//
// Each case is a plausible wrong layout, and each must be REFUSED.

import assert from "node:assert/strict";

const DIV = /<div className="([^"]*)"/g;

/** The exact lookup + assertion set the test now performs. */
function check(source) {
  const at = source.indexOf("{/* Messages Viewport");
  if (at < 0) return { ok: false, why: "viewport marker missing" };

  const classLists = [...source.matchAll(DIV)].map((m) => ({
    at: m.index ?? 0,
    cls: m[1],
  }));
  const scroller = classLists.find(
    (d) => /\bflex-1\b/.test(d.cls) && /\boverflow-y-auto\b/.test(d.cls),
  );
  const column = classLists
    .filter((d) => d.at > at)
    .find((d) => /\bmin-h-full\b/.test(d.cls));

  if (!scroller)
    return { ok: false, why: "no flex-1 overflow-y-auto scroller" };
  if (!column)
    return { ok: false, why: "no min-h-full column after the viewport" };
  if (!(scroller.at < column.at))
    return { ok: false, why: "column is not inside the scroller region" };
  if (/space-y-/.test(scroller.cls))
    return { ok: false, why: "space-y- on the scroller" };
  if (!/justify-end/.test(column.cls))
    return { ok: false, why: "column lost justify-end" };
  if (!/gap-4/.test(column.cls)) return { ok: false, why: "column lost gap-4" };
  return { ok: true };
}

const MARK =
  "{/* Messages Viewport\n                The scroller is `flex-1`.\n              */}";

// --- the shape that must be ACCEPTED -------------------------------------
// NOTE: each entry is a `[label, source]` PAIR. Flattening these made
// `for (const [label, src] of cases)` destructure the two strings into their
// first two CHARACTERS, and every "must be refused" case then passed vacuously
// because garbage is refused. A negative-control harness that passes for the
// wrong reason is worse than no harness, so `check()` below is also asserted to
// return a specific `why` for each case rather than merely "not ok".
const good = [
  [
    "scroller adjacent to the column (the historical shape)",
    `${MARK}
       <div className="flex-1 overflow-y-auto px-4 py-5 w-full">
         <div className="min-h-full flex flex-col justify-end gap-4">`,
  ],
  [
    "scroller relocated far ABOVE the comment, column still inside the file",
    `<div className="flex-1 overflow-y-auto px-4 py-5 w-full">
       ${MARK}
       <div className="min-h-full flex flex-col justify-end gap-4">`,
  ],
];

// --- shapes that must be REFUSED ------------------------------------------
// --- shapes that must be REFUSED, each with the reason it must be refused ---
const bad = [
  [
    "scroller lost `flex-1` (the original bug: 235px scroller)",
    `${MARK}
       <div className="overflow-y-auto px-4 py-5 w-full">
         <div className="min-h-full flex flex-col justify-end gap-4">`,
    "no flex-1 overflow-y-auto scroller",
  ],
  [
    "column lost `justify-end` (top-anchored again, blank region returns)",
    `${MARK}
       <div className="flex-1 overflow-y-auto px-4 py-5 w-full">
         <div className="min-h-full flex flex-col gap-4">`,
    "column lost justify-end",
  ],
  [
    "column lost `min-h-full` (content cannot reach the bottom)",
    `${MARK}
       <div className="flex-1 overflow-y-auto px-4 py-5 w-full">
         <div className="flex flex-col justify-end gap-4">`,
    "no min-h-full column after the viewport",
  ],
  [
    "`space-y-4` back on the scroller (leading blank)",
    `${MARK}
       <div className="flex-1 overflow-y-auto space-y-4">
         <div className="min-h-full flex flex-col justify-end gap-4">`,
    "space-y- on the scroller",
  ],
  [
    "column rendered ABOVE the scroller (structural inversion)",
    `${MARK}
       <div className="min-h-full flex flex-col justify-end gap-4">
       <div className="flex-1 overflow-y-auto px-4 py-5 w-full">`,
    "column is not inside the scroller region",
  ],
  [
    "scroller has no `overflow-y-auto` at all",
    `${MARK}
       <div className="flex-1">
         <div className="min-h-full flex flex-col justify-end gap-4">`,
    "no flex-1 overflow-y-auto scroller",
  ],
  [
    "column lost `gap-4`",
    `${MARK}
       <div className="flex-1 overflow-y-auto px-4 py-5 w-full">
         <div className="min-h-full flex flex-col justify-end">`,
    "column lost gap-4",
  ],
  [
    "the whole viewport block deleted",
    `<div className="p-4">nothing here</div>`,
    "viewport marker missing",
  ],
];

let failed = 0;
const row = (ok, label) => {
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}`);
};

for (const [label, src] of good) {
  const r = check(src);
  row(r.ok, `ACCEPT  ${label}${r.ok ? "" : `  <-- wrongly refused: ${r.why}`}`);
}
for (const [label, src, wantWhy] of bad) {
  const r = check(src);
  // Both halves matter: it must be refused, AND for the named reason. Accepting
  // it is a hole in the test; refusing it for the wrong reason means the check
  // is not the one being believed.
  const rightReason = r.why === wantWhy;
  row(
    !r.ok && rightReason,
    `REFUSE  ${label}  (why: ${r.why}${rightReason ? "" : ` <-- expected "${wantWhy}"`})`,
  );
}

const total = good.length + bad.length;
console.log(
  `\n${total - failed}/${total} negative controls behaved as specified`,
);
process.exit(failed ? 1 : 0);
