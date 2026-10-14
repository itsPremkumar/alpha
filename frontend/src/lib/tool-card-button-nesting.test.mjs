import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const here = dirname(fileURLToPath(import.meta.url));
const componentDir = join(here, "../components/agent-ui");

function tagName(node) {
  if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return null;
  return ts.isIdentifier(node.tagName) ? node.tagName.text : null;
}

function nestedInteractiveElements(sourceFile, parentTags = [], found = []) {
  function visit(node, tags) {
    if (ts.isJsxElement(node)) {
      const name = tagName(node.openingElement);
      if (name === "button" && tags.includes("button")) {
        found.push("<button> inside <button>");
      }
      if (name === "CopyButton" && tags.includes("button")) {
        found.push("<CopyButton> inside <button>");
      }
      const nextTags = name ? [...tags, name] : tags;
      for (const child of node.children) visit(child, nextTags);
      return;
    }
    if (ts.isJsxSelfClosingElement(node) && tagName(node) === "CopyButton" && tags.includes("button")) {
      found.push("<CopyButton /> inside <button>");
      return;
    }
    ts.forEachChild(node, (child) => visit(child, tags));
  }

  visit(sourceFile, parentTags);
  return found;
}

test("agent tool cards never nest interactive copy controls inside disclosure buttons", () => {
  const files = readdirSync(componentDir).filter((name) => name.endsWith(".tsx"));
  const violations = [];
  for (const name of files) {
    const path = join(componentDir, name);
    const source = readFileSync(path, "utf8");
    const sourceFile = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    for (const violation of nestedInteractiveElements(sourceFile)) {
      violations.push(`${name}: ${violation}`);
    }
  }
  assert.deepEqual(violations, [], violations.join("\n"));
});
