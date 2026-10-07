// Negative controls for the slash-palette runnable headline + toggle.
//
// Each case reimplements one plausible wrong reading AGAINST THE REAL MODULE and
// asserts the module's answer differs. No file is written: an earlier session
// left a mutation in a shipped source file because an interrupted control did
// not restore it.
import { readFileSync } from "node:fs";
import ts from "typescript";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;
const code = ts.transpileModule(read("../src/lib/slash-command-palette.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { splitRunnableRows, runnableHeadline } = await import(toDataUrl(code));

const row = (command, hasHandler) => ({ command, hasHandler });
// Live shape: 461 listed, 54 runnable.
const LIVE = [
  ...Array.from({ length: 54 }, (_, i) => row(`/r${i}`, true)),
  ...Array.from({ length: 400 }, (_, i) => row(`/u${i}`, false)),
  ...Array.from({ length: 7 }, (_, i) => row(`/n${i}`, null)),
];

// --- the wrong readings -----------------------------------------------------
const defectHideUnknownToo = (rows) => rows.filter((r) => r.hasHandler === true);
const defectCountFalseAsRunnable = (rows) => rows.filter((r) => r.hasHandler !== false).length;
const defectZeroRunnableOnFallback = () => ({ runnable: 0, listed: 16 });

const CASES = [
  [
    "the toggle hides only server-confirmed handler-less rows",
    () => {
      const wrong = defectHideUnknownToo(LIVE);
      const { visible, hidden } = splitRunnableRows(LIVE);
      return {
        caught: wrong.length === 54 && visible.length === 61 && hidden === 400,
        detail: `hide-unknown keeps ${wrong.length}, module keeps ${visible.length} and names ${hidden} hidden`,
      };
    },
  ],
  [
    "unreported rows are not counted runnable",
    () => {
      const wrong = defectCountFalseAsRunnable(LIVE);
      const h = runnableHeadline(LIVE);
      return {
        caught: wrong === 61 && h.runnable === 54 && h.listed === 461,
        detail: `defect counts ${wrong} runnable, module counts ${h.runnable} of ${h.listed}`,
      };
    },
  ],
  [
    "the fallback list never renders counts as health",
    () => {
      const wrong = defectZeroRunnableOnFallback();
      const src = read("../src/components/Composer.tsx");
      const hasFallbackDisclosure = /built-in fallback — registry unavailable/.test(src);
      const hasZeroClaim = /0 runnable of 16 listed/.test(src);
      return {
        caught: hasFallbackDisclosure && !hasZeroClaim,
        detail: `defect would print "${wrong.runnable} runnable of ${wrong.listed} listed"`,
      };
    },
  ],
  [
    "the footer discloses the hidden count instead of going quiet",
    () => {
      const src = read("../src/components/Composer.tsx");
      return {
        caught: /hiding \{hiddenByToggle\} row\(s\) the registry reports with no bound handler/.test(src),
        detail: "footer names the hidden rows",
      };
    },
  ],
  [
    "keyboard, scroll and render follow the filtered list",
    () => {
      const src = read("../src/components/Composer.tsx");
      const uses = [
        /visibleSuggestions\.length > 0 && \(/,
        /visibleSuggestions\[selectedIndex\]/,
        /visibleSuggestions\.map\(\(cmd, idx\)/,
        /\[selectedIndex, visibleSuggestions\]/,
      ].every((re) => re.test(src));
      return { caught: uses, detail: "all four consumers read visibleSuggestions" };
    },
  ],
  [
    "toggling resets the highlight",
    () => {
      const src = read("../src/components/Composer.tsx");
      return {
        caught: /setSelectedIndex\(0\);\s*\n\s*\}, \[runnableOnly\]\);/.test(src),
        detail: "dedicated reset effect on runnableOnly",
      };
    },
  ],
  [
    "malformed rows never throw the split",
    () => {
      let threw = null;
      for (const bad of [undefined, null, "x", 7, {}, [null, 3, { command: "" }]]) {
        try {
          splitRunnableRows(bad);
          runnableHeadline(bad);
        } catch (e) {
          threw = `${JSON.stringify(bad)} -> ${e.message}`;
        }
      }
      return { caught: threw === null, detail: threw ?? "all inputs handled" };
    },
  ],
];

let bad = 0;
for (const [label, run] of CASES) {
  const { caught, detail } = run();
  if (caught) console.log(`ok    ${label}\n        ${detail}`);
  else {
    bad += 1;
    console.log(`FAIL  ${label}\n        ${detail}`);
  }
}
console.log(`\n${CASES.length - bad}/${CASES.length} negative controls behaved as specified`);
process.exit(bad ? 1 : 0);
