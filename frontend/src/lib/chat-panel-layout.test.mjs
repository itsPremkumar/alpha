import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("../components/ChatView.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "ChatView.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const declarations = new Map();
function visit(node) {
  if (
    ts.isVariableDeclaration(node) &&
    ["openWorkspace", "openInspector", "toggleInspector"].includes(
      node.name.getText(ast),
    )
  ) {
    declarations.set(node.name.getText(ast), node.initializer?.getText(ast));
  }
  ts.forEachChild(node, visit);
}
visit(ast);

test("opening the agent workspace gives it the side-panel space exclusively", () => {
  assert.match(
    declarations.get("openWorkspace"),
    /setWorkspaceDismissed\(false\)[\s\S]*setInspectorOpen\(false\)[\s\S]*setWorkspaceOpen\(true\)/,
  );
  assert.match(
    source,
    /if \(workspaceContent\.total > 0 && !workspaceDismissed && !workspaceOpen\)\s*\{\s*setInspectorOpen\(false\);\s*setWorkspaceOpen\(true\);/,
  );
});

test("reopening project details switches out of the workspace and stays accessible", () => {
  assert.match(
    declarations.get("openInspector"),
    /setWorkspaceDismissed\(true\)[\s\S]*setWorkspaceOpen\(false\)[\s\S]*setInspectorOpen\(true\)/,
  );
  assert.match(
    declarations.get("toggleInspector"),
    /if \(inspectorOpen\)[\s\S]*setInspectorOpen\(false\)[\s\S]*openInspector\(\)/,
  );
  assert.match(source, /onToggleInspector=\{toggleInspector\}/);
});
