// Negative controls for the two line-shape tolerance fixes, run WITHOUT touching
// ChatView.tsx — a concurrent agent is actively editing that file, and mutating
// it to prove a test works is how this session already shipped one defect
// (an interrupted negative control left `if False:` in a source file).
//
// These drive the same regexes the suites use against hand-built strings, so
// they prove the assertions still DISCRIMINATE rather than merely match.

const POSITIVE_DYNAMIC =
  /dynamic\(\s*\(\) =>\s*import\("@\/components\/sections\/WarRoomRunsSection"\)/;
const NEGATIVE_LAZY =
  /\blazy\(\s*\(\) =>\s*import\("@\/components\/sections\/WarRoomRunsSection"\)/;

const POSITIVE_SIG =
  /function ChatView\(\s*\{\s*initialView,?\s*\}\s*:\s*\{\s*initialView\?:\s*WorkspaceView\s*[,}]?\s*\}\s*=\s*\{\s*\}\s*\)/;
const NEGATIVE_REQUIRED =
  /function ChatView\([\s\S]{0,160}?\binitialView\s*:\s*WorkspaceView\b/;

const Q = String.fromCharCode(34);
const IMP = `import(${Q}@/components/sections/WarRoomRunsSection${Q})`;

const cases = [];

// --- the war-room on-demand-load assertion -------------------------------
cases.push([
  "war-room: multi-line `dynamic(` matches (the real shape)",
  POSITIVE_DYNAMIC.test(`const S = dynamic(\n  () =>\n    ${IMP},\n);`),
  true,
]);
cases.push([
  "war-room: single-line `dynamic(` still matches",
  POSITIVE_DYNAMIC.test(`const S = dynamic(() => ${IMP});`),
  true,
]);
cases.push([
  "war-room: `lazy(` is still refused (the defect it exists to catch)",
  NEGATIVE_LAZY.test(`const S = lazy(() => ${IMP});`),
  true,
]);
cases.push([
  "war-room: `React.lazy(` is still refused",
  NEGATIVE_LAZY.test(`const S = React.lazy(() => ${IMP});`),
  true,
]);
cases.push([
  "war-room: multi-line `lazy(` is still refused",
  NEGATIVE_LAZY.test(`const S = lazy(\n  () => ${IMP},\n);`),
  true,
]);
cases.push([
  "war-room: an unrelated section's lazy() must NOT trip the guard",
  NEGATIVE_LAZY.test(`const S = lazy(() => import(${Q}@/components/x${Q}));`),
  false,
]);

// --- the ChatView optional-prop assertion --------------------------------
cases.push([
  "signature: multi-line optional prop matches (the real shape)",
  POSITIVE_SIG.test(
    `export default function ChatView({\n  initialView,\n}: { initialView?: WorkspaceView } = {}) {`,
  ),
  true,
]);
cases.push([
  "signature: single-line optional prop still matches",
  POSITIVE_SIG.test(
    `function ChatView({ initialView }: { initialView?: WorkspaceView } = {}) {`,
  ),
  true,
]);
cases.push([
  "signature: trailing comma type still matches",
  POSITIVE_SIG.test(
    `function ChatView({ initialView }: { initialView?: WorkspaceView, } = {}) {`,
  ),
  true,
]);
cases.push([
  "signature: a REQUIRED prop is refused (the defect it exists to catch)",
  NEGATIVE_REQUIRED.test(
    `function ChatView({ initialView }: { initialView: WorkspaceView }) {`,
  ),
  true,
]);
cases.push([
  "signature: optional prop with NO default is refused (mounts would break)",
  POSITIVE_SIG.test(
    `function ChatView({ initialView }: { initialView?: WorkspaceView }) {`,
  ),
  false,
]);
cases.push([
  "signature: required prop fails the positive assertion too",
  POSITIVE_SIG.test(
    `function ChatView({ initialView }: { initialView: WorkspaceView }) {`,
  ),
  false,
]);

let failed = 0;
for (const [label, got, want] of cases) {
  const ok = got === want;
  if (!ok) failed += 1;
  console.log(`${ok ? "ok  " : "FAIL"}  ${label}  (got ${got}, want ${want})`);
}
console.log(
  `\n${cases.length - failed}/${cases.length} negative controls behaved as specified`,
);
process.exit(failed ? 1 : 0);
