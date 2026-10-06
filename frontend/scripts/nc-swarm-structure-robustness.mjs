// Compact repro for the swarm-structure-view crash. Prints one line per case so
// a failure does not bury the answer in a 20 KB stack trace.
import { readFileSync } from "node:fs";
import ts from "typescript";

const read = (p) => readFileSync(new URL(p, import.meta.url), "utf8");
const toDataUrl = (s) => `data:text/javascript;charset=utf-8,${encodeURIComponent(s)}`;
const code = ts.transpileModule(read("../src/lib/swarm-structure-view.ts"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const m = await import(toDataUrl(code));

const task = { task_id: "t", objective: "x", state: "completed", dependencies: [] };

const cases = [
  ["swarmTaskRows(undefined)", () => m.swarmTaskRows(undefined)],
  ["swarmTaskRows(null)", () => m.swarmTaskRows(null)],
  ["swarmTaskRows(7)", () => m.swarmTaskRows(7)],
  ["swarmTaskRows('nope')", () => m.swarmTaskRows("nope")],
  ["swarmTaskRows([])", () => m.swarmTaskRows([])],
  ["swarmTaskRows({})", () => m.swarmTaskRows({})],
  ["swarmTaskRows(map)", () => m.swarmTaskRows({ t: task })],
  ["swarmTaskRows(list)", () => m.swarmTaskRows([task])],
  ["swarmTaskRows(list w/o task_id)", () => m.swarmTaskRows([{ objective: "x" }])],
  ["swarmTaskRows(nested array)", () => m.swarmTaskRows([[task]])],
  ["swarmStructureView(undefined,undefined)", () => m.swarmStructureView(undefined, undefined)],
  ["swarmStructureView(null,null)", () => m.swarmStructureView(null, null)],
  ["swarmStructureView([],[])", () => m.swarmStructureView([], [])],
  ["swarmStructureView(7,7)", () => m.swarmStructureView(7, 7)],
  ["swarmStructureView('nope','nope')", () => m.swarmStructureView("nope", "nope")],
  ["swarmStructureView({tasks:{}})", () => m.swarmStructureView({ tasks: {} }, [])],
  ["swarmStructureView({tasks:[]})", () => m.swarmStructureView({ tasks: [] }, [])],
  ["swarmStructureView({tasks:[task]})", () => m.swarmStructureView({ tasks: [task] }, [])],
  ["swarmEventRows(undefined)", () => m.swarmEventRows(undefined)],
  ["swarmEventRows('x')", () => m.swarmEventRows("x")],
  ["swarmEventRows([null])", () => m.swarmEventRows([null])],
  ["swarmEventRows(['x',7])", () => m.swarmEventRows(["x", 7])],
  ["swarmLeaderView(null)", () => m.swarmLeaderView(null)],
  ["swarmTeamView(null)", () => m.swarmTeamView(null)],
  ["swarmTeamView(7)", () => m.swarmTeamView(7)],
];

let bad = 0;
for (const [label, fn] of cases) {
  try {
    fn();
    console.log(`ok    ${label}`);
  } catch (e) {
    bad += 1;
    console.log(`THROW ${label}  ->  ${e.constructor.name}: ${e.message}`);
  }
}
console.log(`\n${cases.length - bad}/${cases.length} inputs handled without throwing`);
process.exit(bad ? 1 : 0);