// workforce-project-picker.test.mjs — the project picker must not invent a
// project.
//
// The Workforce war-room tab initialised its selection to the literal string
// `"default"` and rendered `<option value="default">default</option>` whenever
// the project list came back empty. On an installation with no projects that
// presented a project the server has never heard of as a real, selectable
// choice, and four `useAsync` reads fired against it on mount:
//
//   GET /projects/default/war-room
//   GET /projects/default/self-config/status
//   GET /projects/default/meta-compiler/lineage
//   GET /projects/default/perpetual/status
//
// All four 404. So an installation with zero projects rendered four failure
// states instead of saying there is nothing to inspect — and the failure was
// indistinguishable, to anyone reading the UI, from a broken backend.
//
// Measured on an install with no projects; every one of those 404s appeared in
// the `scripts/reliability/ui_audit.py` run for the `workforce` view.
//
// The rule: an empty project list is an absence to disclose, not a fiction to
// fill. `""` means "nothing chosen", the reads stay dormant until a real id
// arrives, and the picker says so in words.
//
// node --test src/lib/*.test.mjs — no server, no browser, no DOM shim.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../components/sections/WorkforceSection.tsx", import.meta.url),
  "utf8",
);

/** Comments quote the fabricated option verbatim, so strip them before asserting. */
function code(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/[ \t]+\/\/.*$/gm, "");
}

const tsx = code(source);

test("the picker does not start on a fabricated project id", () => {
  assert.match(tsx, /useState<string>\(""\)/, "an empty string means 'nothing chosen'");
  assert.doesNotMatch(
    tsx,
    /useState<string>\("default"\)/,
    "no project id may be invented as an initial selection",
  );
});

test("no option offers a project the server did not report", () => {
  // The exact fabrication this replaces: an <option> the user could pick, whose
  // every read 404s.
  assert.doesNotMatch(tsx, /<option value="default">/);
  assert.match(
    tsx,
    /projects\.length === 0 && \(\s*<option value="" disabled>/,
    "an empty list must render a disabled, honest placeholder",
  );
  assert.match(tsx, /No projects exist yet/);
});

test("every automatic read is dormant until a real project is chosen", () => {
  // Four reads, each previously fired on mount against the fabricated id.
  for (const call of [
    "fetchWarRoomData",
    "fetchSelfConfigStatus",
    "fetchMetaLineage",
    "fetchPerpetualStatus",
  ]) {
    assert.match(
      tsx,
      new RegExp(`selectedProject \\? ${call}\\(selectedProject\\) : Promise\\.resolve\\(null\\)`),
      `${call} must not run without a selected project`,
    );
  }
});

test("the picker is labelled for assistive technology", () => {
  // The visible <label> has no htmlFor/id, so the combo box was announced as
  // unlabelled — measured as `unlabelled_inputs: SELECT` on this view.
  assert.match(tsx, /<select\s+aria-label="Project workspace"/);
});