// Negative controls for dynamic-plan-view.
//
// Each case reimplements one plausible wrong reading AGAINST THE REAL MODULE and
// asserts the module's answer differs. The controls run the actual derivation,
// so they fail if the module's guard is removed — not merely if a helper name
// changes.
//
// The original defect is the anchor: printing `resources.tools` with no
// qualifier. Measured live, that printed 12-17 names of which at most 3 could
// resolve.
import { readFileSync } from "node:fs";
import ts from "typescript";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;
const code = ts.transpileModule(read("../src/lib/dynamic-plan-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { planToolAvailability, planProvisioning, planResourceView } = await import(toDataUrl(code));

const REAL = {
  tools: ["search_web", "deep_research", "read_file", "write_file", "run_command"],
  metadata: {
    provisioned: false,
    total_bots: 3,
    total_skills: 0,
    unavailable_tools: ["search_web", "read_file", "write_file", "run_command"],
    skill_generation: "not requested",
    mcp_generation: "not requested",
  },
};

/**
 * The defect: attribute every selected tool to the plan.
 * Returns the string the old panel printed.
 */
const defectBareToolList = (resources) => (resources.tools || []).join(", ") || "alpha.tools";

/** The defect: treat an absent availability reading as a clean bill of health. */
const defectAbsentAsHealthy = (resources) => {
  const blocked = (resources.metadata?.unavailable_tools || []).length;
  return blocked === 0 ? "all tools available" : `${blocked} unavailable`;
};

/** The defect: call the plan "assembled" regardless of provisioning. */
const defectAssembledAlways = () => "Assembled Bot Specialists & Tools";

/** The defect: collapse a request count into a catalogue fact. */
const defectZeroMeansMissing = (resources) =>
  (resources.metadata?.total_skills || 0) === 0 ? "no skills found" : "skills ready";

const CASES = [
  [
    "the bare tool list prints 5 names when only 1 can resolve",
    () => {
      const printed = defectBareToolList(REAL);
      const honest = planToolAvailability(REAL);
      return {
        caught: printed.split(",").length - 1 === 4 && honest.available.length === 1,
        detail: `printed "${printed}" but available=${JSON.stringify(honest.available)}`,
      };
    },
  ],
  [
    "an absent availability reading is not a clean bill of health",
    () => {
      const absent = { tools: ["a", "b"] };
      const wrong = defectAbsentAsHealthy(absent);
      const honest = planToolAvailability(absent);
      return {
        caught: wrong === "all tools available" && honest.reported === false && /not reported/.test(honest.sentence),
        detail: `defect says "${wrong}", module says "${honest.sentence}"`,
      };
    },
  ],
  [
    "a measured empty unavailable list IS a clean bill of health",
    () => {
      const empty = { tools: ["a", "b"], metadata: { unavailable_tools: [] } };
      const honest = planToolAvailability(empty);
      return {
        caught: honest.reported === true && /all 2 selected tool\(s\) are available/.test(honest.sentence),
        detail: honest.sentence,
      };
    },
  ],
  [
    "provisioned:false is contradiction of the 'Assembled' heading",
    () => {
      const wrong = defectAssembledAlways();
      const honest = planResourceView(REAL).heading;
      return {
        caught: wrong === "Assembled Bot Specialists & Tools" && honest === "Planned Bot Specialists & Tools",
        detail: `defect heading "${wrong}", module heading "${honest}"`,
      };
    },
  ],
  [
    "an absent provisioned flag is not reported, not false",
    () => {
      const honest = planProvisioning({ metadata: {} });
      return {
        caught: honest.provisioned === null && !/not provisioned/.test(honest.sentence),
        detail: honest.sentence,
      };
    },
  ],
  [
    "total_skills:0 is not a catalogue fact",
    () => {
      const wrong = defectZeroMeansMissing(REAL);
      const honest = planProvisioning(REAL).skillSentence;
      return {
        caught: wrong === "no skills found" && !/skills found|exist/i.test(honest),
        detail: `defect says "${wrong}", module says "${honest}"`,
      };
    },
  ],
  [
    "the server's blocked-generator sentence survives verbatim",
    () => {
      const s = "unavailable — no generator bound";
      const honest = planProvisioning({ metadata: { total_skills: 0, skill_generation: s } }).skillSentence;
      return {
        caught: honest === s,
        detail: honest,
      };
    },
  ],
  [
    "a blocked tool that was never selected cannot inflate the shortfall",
    () => {
      const honest = planToolAvailability({ tools: ["a"], metadata: { unavailable_tools: ["a", "b", "c"] } });
      return {
        caught: honest.blocked.length === 1 && honest.sentence === "1 of 1 selected tool(s) are unavailable",
        detail: honest.sentence,
      };
    },
  ],
  [
    "the empty-selection case never emits the 'alpha.tools' placeholder",
    () => {
      const wrong = defectBareToolList({ tools: [] });
      const honest = planToolAvailability({ tools: [], metadata: { unavailable_tools: [] } }).sentence;
      return {
        caught: wrong === "alpha.tools" && !/alpha\.tools/.test(honest),
        detail: `defect says "${wrong}", module says "${honest}"`,
      };
    },
  ],
  [
    "malformed payloads never throw",
    () => {
      const inputs = [undefined, null, "nope", 7, [], { tools: "x" }, { metadata: { unavailable_tools: "y" } }];
      let threw = null;
      for (const i of inputs) {
        try {
          planResourceView(i);
        } catch (e) {
          threw = `${JSON.stringify(i)} -> ${e.message}`;
        }
      }
      return { caught: threw === null, detail: threw ?? "all inputs handled" };
    },
  ],
];

let bad = 0;
for (const [label, run] of CASES) {
  const { caught, detail } = run();
  if (caught) {
    console.log(`ok    ${label}\n        ${detail}`);
  } else {
    bad += 1;
    console.log(`FAIL  ${label}\n        ${detail}`);
  }
}
console.log(`\n${CASES.length - bad}/${CASES.length} negative controls behaved as specified`);
process.exit(bad ? 1 : 0);