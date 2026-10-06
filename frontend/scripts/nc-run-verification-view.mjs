// Negative controls for run-verification-view.
//
// Each case reimplements one plausible wrong reading AGAINST THE REAL MODULE and
// asserts the module's answer differs. The controls run the actual derivation, so
// they fail if a guard is removed rather than if a helper is renamed.
//
// The anchor is the measured payload: a `node_verification` event carrying
// `passed: false, status: "not_run"` for a check the host never executed. The
// temptation is to render that as a failure, which would report the host's
// missing executor as the work's inadequacy — a different and opposite claim.
import { readFileSync } from "node:fs";
import ts from "typescript";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;
const code = ts.transpileModule(read("../src/lib/run-verification-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const { nodeVerificationViews, runAcceptanceView, verificationPostureView, runVerificationView } = await import(
  toDataUrl(code)
);

const UNRUN = {
  event_type: "node_verification",
  payload: {
    node_id: "task_05_dynamic_implementation",
    passed: false,
    status: "not_run",
    command: "pytest -q",
    reason: "the command was not run: no verification executor is bound in this host",
  },
};
const REAL_MD = {
  acceptance_passed: false,
  acceptance_reason: "digest projection completed graph mechanics only; no domain task was executed",
  verification: { registered_verifiers: 0, executor_bound: false, declared_nodes: ["task_05_dynamic_implementation"] },
};

// --- the wrong readings -----------------------------------------------------

/** `passed: false` therefore the check failed. The core temptation. */
const defectFailedFromPassedFlag = (e) => {
  const p = e.payload;
  return p.passed ? "passed" : "verification failed";
};

/** `passed: false` therefore a count of zero verifications succeeded. */
const defectZeroPassed = (events) =>
  `${events.filter((e) => e.payload?.passed).length} verifications passed`;

/** No event therefore everything is fine. */
const defectSilenceIsSuccess = (events) =>
  events.some((e) => e.event_type === "node_verification") ? "checked" : "all checks passed";

/** An absent acceptance flag therefore the digest limitation applies. */
const defectAbsentIsLimitation = (md) =>
  md.acceptance_passed ? "accepted" : "graph mechanics completed only";

const CASES = [
  [
    "an unrun check is not rendered as a failure",
    () => {
      const wrong = defectFailedFromPassedFlag(UNRUN);
      const v = nodeVerificationViews([UNRUN])[0];
      return {
        caught: wrong === "verification failed" && !/failed/.test(v.sentence) && /^not run/.test(v.sentence),
        detail: `defect says "${wrong}", module says "${v.sentence.slice(0, 60)}…"`,
      };
    },
  ],
  [
    "a genuinely failed check still reads as failed",
    () => {
      const v = nodeVerificationViews([
        { event_type: "node_verification", payload: { node_id: "n", status: "failed", passed: false } },
      ])[0];
      return { caught: v.tone === "red" && /^failed/.test(v.sentence), detail: v.sentence };
    },
  ],
  [
    "an unrun check is not counted as a passed verification",
    () => {
      const wrong = defectZeroPassed([UNRUN]);
      const v = runVerificationView(REAL_MD, [UNRUN]);
      return {
        caught: wrong === "0 verifications passed" && v.hasUnrun === true,
        detail: `defect says "${wrong}"; module flags hasUnrun=${v.hasUnrun}`,
      };
    },
  ],
  [
    "no declaration is not 'all checks passed'",
    () => {
      const plain = [{ event_type: "node_completed" }];
      const wrong = defectSilenceIsSuccess(plain);
      const v = runVerificationView(REAL_MD, plain);
      return {
        caught: wrong === "all checks passed" && /no node declared a verification/.test(v.declaredNote ?? ""),
        detail: `defect says "${wrong}", module says "${v.declaredNote}"`,
      };
    },
  ],
  [
    "an absent acceptance flag is unknown, not the digest limitation",
    () => {
      const wrong = defectAbsentIsLimitation({});
      const a = runAcceptanceView({});
      return {
        caught: wrong === "graph mechanics completed only" && /not reported by the server/.test(a.sentence),
        detail: `defect says "${wrong}", module says "${a.sentence}"`,
      };
    },
  ],
  [
    "the real acceptance reason is quoted verbatim",
    () => {
      const a = runAcceptanceView(REAL_MD);
      return {
        caught: /digest projection completed graph mechanics only/.test(a.sentence),
        detail: a.sentence,
      };
    },
  ],
  [
    "a zero verifier count is preserved as 0, not nulled",
    () => {
      const p = verificationPostureView(REAL_MD);
      return {
        caught: p.registeredVerifiers === 0,
        detail: `registeredVerifiers=${JSON.stringify(p.registeredVerifiers)}`,
      };
    },
  ],
  [
    "an absent verification block is not reported, never 'no executor bound'",
    () => {
      const p = verificationPostureView({});
      return {
        caught: p.executorBound === null && !/no verification executor is bound/.test(p.sentence),
        detail: p.sentence,
      };
    },
  ],
  [
    "a missing status is unreported, never a pass",
    () => {
      const v = nodeVerificationViews([{ event_type: "node_verification", payload: { node_id: "n", passed: true } }])[0];
      return {
        caught: v.status === null && v.tone === "gray" && /outcome not reported/.test(v.sentence),
        detail: v.sentence,
      };
    },
  ],
  [
    "malformed inputs never throw",
    () => {
      let threw = null;
      for (const i of [undefined, null, "nope", 7, [], {}, { payload: null }]) {
        try {
          runVerificationView(i, i);
          nodeVerificationViews(i);
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