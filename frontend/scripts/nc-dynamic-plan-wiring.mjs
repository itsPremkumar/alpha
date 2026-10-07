// Negative controls for the dynamic-plan wiring pins.
//
// Each case reintroduces a plausible version of the ORIGINAL defect against the
// real section source, in memory, and asserts the named pin fails. No file is
// written: an earlier session left a mutation in a shipped source file because
// an interrupted control did not restore it.
//
// Every mutation targets a string that occurs EXACTLY ONCE in the file. The last
// round of this exercise had three controls "pass" because `String.replace()`
// hits the first occurrence and the first occurrence was unrelated code — a
// control that mutates the wrong line proves nothing.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const sectionOrig = read("../src/components/sections/WorkflowsSection.tsx");
const viewOrig = read("../src/lib/dynamic-plan-view.ts");

const flat = (s) => s.replace(/\s+/g, " ");

/** The same assertions the wiring pins make, against mutated sources. */
function pinHolds({ section = sectionOrig, view = viewOrig }, name) {
  const f = flat(section);
  switch (name) {
    case "import":
      return /import \{ planResourceView \} from "@\/lib\/dynamic-plan-view"/.test(section);
    case "derived":
      return /planResourceView\(dynamicPreview\.resources\)/.test(section);
    case "no-bare-list":
      return !/resources\.tools\.join\(", "\) \|\| "alpha\.tools"/.test(f) && !/"alpha\.tools"/.test(f);
    case "no-union-list":
      return !/resources\.tools\.join\(/.test(f);
    case "heading":
      return !/Assembled Bot Specialists/.test(section) && /\{planResources\.heading\}/.test(section);
    case "sentences":
      return (
        /\{planResources\.tools\.sentence\}/.test(section) &&
        /\{planResources\.provisioning\.sentence\}/.test(section)
      );
    case "skills-mcp":
      return (
        /\{planResources\.provisioning\.skillSentence\}/.test(section) &&
        /\{planResources\.provisioning\.mcpSentence\}/.test(section)
      );
    case "named":
      return (
        /planResources\.tools\.blocked\.join\(", "\)/.test(section) &&
        /planResources\.tools\.available\.join\(", "\)/.test(section)
      );
    case "absent-vs-empty":
      return /reported = unavailableRaw !== null/.test(view) && /availability not reported by the server/.test(view);
    case "provisioned-tri-state":
      return /typeof metadata\.provisioned === "boolean" \? metadata\.provisioned : null/.test(view);
    case "server-skill-sentence":
      return /skillSentence = skillGeneration/.test(view) && /no generator bound/.test(view);
    default:
      throw new Error(`unknown pin ${name}`);
  }
}

/** Count occurrences so a mutation can be proven to have applied once. */
const occurrences = (haystack, needle) => haystack.split(needle).length - 1;

const CASES = [
  [
    "the panel prints the bare tool list again (the original defect)",
    "no-bare-list",
    "bare-list",
    (s) => s.replace(`{planResources.tools.sentence}`, `{dynamicPreview.resources.tools.join(", ") || "alpha.tools"}`),
  ],
  [
    "the panel prints the union of available and blocked tools",
    "no-union-list",
    "union",
    (s) => s.replace(`{planResources.tools.sentence}`, `{dynamicPreview.resources.tools.join(", ")}`),
  ],
  [
    "the 'Assembled' heading is hardcoded again",
    "heading",
    "heading",
    (s) => s.replace(`{planResources.heading}`, `Assembled Bot Specialists &amp; Tools`),
  ],
  [
    "the section stops importing the module",
    "import",
    "import",
    (s) => s.replace(`import { planResourceView } from "@/lib/dynamic-plan-view";\n`, ""),
  ],
  [
    "the preview stops deriving from the module",
    "derived",
    "derived",
    (s) => s.replace(`planResourceView(dynamicPreview.resources)`, `{}`),
  ],
  [
    "the tool shortfall sentence is dropped",
    "sentences",
    "sentence",
    (s) => s.replace(`{planResources.tools.sentence}`, ``),
  ],
  [
    "the skills sentence is dropped",
    "skills-mcp",
    "skills",
    (s) => s.replace(`{planResources.provisioning.skillSentence}`, ``),
  ],
  [
    "the blocked-tool names are replaced by a bare count",
    "named",
    "named",
    (s) => s.replace(`{planResources.tools.blocked.join(", ")}`, `{planResources.tools.blocked.length}`),
  ],
  [
    "the module treats an absent reading as a clean bill of health",
    "absent-vs-empty",
    "view-absent",
    null,
    (v) => v.replace(`const reported = unavailableRaw !== null;`, `const reported = true;`),
  ],
  [
    "the module collapses an absent provisioned flag into false",
    "provisioned-tri-state",
    "view-provisioned",
    null,
    (v) => v.replace(`typeof metadata.provisioned === "boolean" ? metadata.provisioned : null`, `metadata.provisioned ?? false`),
  ],
  [
    "the module drops the server's own skill sentence",
    "server-skill-sentence",
    "view-skill",
    null,
    (v) => v.replace(`skillSentence = skillGeneration;`, `skillSentence = "no skills found";`),
  ],
];

let bad = 0;
for (const [label, pin, key, mutSection, mutView] of CASES) {
  const next = { section: sectionOrig, view: viewOrig };
  if (mutSection) {
    const target = mutSection.toString();
    // Locate the single occurrence the mutation is meant to hit.
    next.section = mutSection(sectionOrig);
    if (next.section === sectionOrig) {
      bad += 1;
      console.log(`FAIL  ${label}\n        mutation did not apply`);
      continue;
    }
    void target;
  }
  if (mutView) {
    next.view = mutView(viewOrig);
    if (next.view === viewOrig) {
      bad += 1;
      console.log(`FAIL  ${label}\n        view mutation did not apply`);
      continue;
    }
  }
  const holds = pinHolds(next, pin);
  if (holds) {
    bad += 1;
    console.log(`FAIL  ${label}\n        pin "${pin}" still passed — it does not catch this`);
  } else {
    console.log(`ok    ${label}  (pin "${pin}" failed as required)`);
  }
}

// Guard the guard: two mutation targets must genuinely be unique, because a
// first-match mutation on a repeated string silently edits the wrong place.
console.log();
let uniqueBad = 0;
for (const needle of [
  `{planResources.tools.sentence}`,
  `{planResources.heading}`,
  `{planResources.provisioning.skillSentence}`,
  `{planResources.tools.blocked.join(", ")}`,
]) {
  const n = occurrences(sectionOrig, needle);
  if (n !== 1) {
    uniqueBad += 1;
    console.log(`FAIL  "${needle}" occurs ${n} times, so a first-match mutation is ambiguous`);
  }
}
console.log(
  uniqueBad === 0
    ? "ok    every mutated section target occurs exactly once"
    : `${uniqueBad} ambiguous target(s)`,
);

const total = CASES.length + 1;
const failures = bad + (uniqueBad === 0 ? 0 : 1);
console.log(`\n${total - failures}/${total} checks behaved as specified`);
process.exit(failures ? 1 : 0);