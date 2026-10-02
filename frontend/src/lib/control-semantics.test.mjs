// Regression tests for control semantics.
//
// These exist because the layout audit found two defects that a type check and
// a render test both miss:
//
//   1. `ProjectDropdownMenu` wrapped its caller-supplied trigger in a bare
//      `<div onClick>`. That is not a control: no focus, no keyboard
//      activation, no name. `BotDropdownMenu` had already been corrected to a
//      focusable `role="button"` wrapper, and the project menu was the last
//      component still carrying the old shape.
//   2. The icon-only close button in `ProjectDetailPanel` had no accessible
//      name at all, so it is announced as a bare "button".
//
// Both are invisible to `tsc` and to any test that only asserts "it renders".
// So these read the source and assert the structure directly.
//
// Convention (per frontend/AGENTS.md): plain `node --test` against
// `src/lib/*.test.mjs`, reading files from disk. No network, no browser, no
// build step.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "..");
const read = (rel) => readFileSync(join(SRC, rel), "utf8");

/**
 * Remove JSX comment blocks so prose about a pattern is never mistaken for the
 * pattern itself. The fixes here are heavily commented, and a comment that
 * explains "this must not be a button" would otherwise trip the assertion that
 * no button is present.
 */
const stripComments = (src) => src.replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

/**
 * Extract the body of the JSX branch that renders `children` (the custom
 * trigger) versus the built-in button.
 *
 * This is deliberately narrow: it locates the `children ? (` branch by its
 * leading marker and cuts at the `: (` that starts the sibling branch. If
 * someone reorders the branches the test fails loudly rather than silently
 * passing on the wrong element.
 */
function childrenBranch(src) {
  const start = src.indexOf("{children ? (");
  assert.notEqual(start, -1, "expected a `{children ? (` branch in the source");
  const end = src.indexOf(") : (", start);
  assert.notEqual(end, -1, "expected the `) : (` sibling branch after it");
  return src.slice(start, end);
}

test("a custom dropdown trigger is a focusable, named control", () => {
  const src = read("components/chat-shell/ProjectDropdownMenu.tsx");
  const branch = childrenBranch(src);

  // Without `role`, assistive technology sees a group, not a button, so the
  // name below is never announced.
  assert.match(branch, /role="button"/, "the custom trigger must expose role=button");
  // Without tabIndex the element is unreachable by keyboard, which is the
  // whole reason this was a defect.
  assert.match(branch, /tabIndex=\{0\}/, "the custom trigger must be focusable");
  // The name has to mention the project, otherwise every project row is
  // announced identically as "button".
  assert.match(
    branch,
    /aria-label=\{`Project options for \$\{projectName\}`\}/,
    "the custom trigger must be named after the project it belongs to",
  );
});

test("a custom dropdown trigger mirrors the built-in button's menu state", () => {
  const src = read("components/chat-shell/ProjectDropdownMenu.tsx");
  const branch = childrenBranch(src);

  // `aria-haspopup` is what pairs the control with the popup it opens; it is
  // how a screen reader user knows there is more content behind it.
  assert.match(branch, /aria-haspopup="true"/, "the trigger must declare the popup it opens");
  // `aria-expanded` is state, not decoration. Without it the control is
  // indistinguishable from a static label.
  assert.match(branch, /aria-expanded=\{open\}/, "the trigger must report open state via aria-expanded");
});

test("a custom dropdown trigger opens from the keyboard", () => {
  const src = read("components/chat-shell/ProjectDropdownMenu.tsx");
  const branch = childrenBranch(src);

  assert.match(branch, /onKeyDown=/, "the trigger must handle keydown");
  // Both keys, or the control is only half-operable: Enter without Space is a
  // familiar trap for keyboard users.
  assert.match(branch, /e\.key === "Enter"/, "Enter must activate the trigger");
  assert.match(branch, /e\.key === " "/, "Space must activate the trigger");
  // Without preventDefault, Space scrolls the page instead of opening the menu.
  assert.match(branch, /e\.preventDefault\(\)/, "activation keys must suppress the default action");
});

test("the project dropdown never nests a button inside its trigger", () => {
  // The defect was not just a missing role. The caller in `BotWorkspaceRail`
  // passed a real `<button>` as `children`, so the wrapper plus the child made
  // one interactive control inside another. That is invalid HTML, it drops the
  // inner control from the accessibility tree in several screen readers, and it
  // makes activation ambiguous.
  const rail = stripComments(read("components/chat-shell/BotWorkspaceRail.tsx"));
  const at = rail.indexOf("<ProjectDropdownMenu");
  assert.notEqual(at, -1, "the rail must render a ProjectDropdownMenu");

  // Cut the JSX that is passed as children: from the opening tag to its close.
  const close = rail.indexOf("</ProjectDropdownMenu>", at);
  assert.notEqual(close, -1, "the ProjectDropdownMenu must be closed");
  const passed = rail.slice(at, close);

  assert.doesNotMatch(
    passed,
    /<button/,
    "a <button> must not be passed as children: it would be nested inside the trigger control",
  );
});

test("an icon-only trigger glyph is hidden from assistive technology", () => {
  // The wrapper now carries the accessible name. If the glyph is left visible
  // to the accessibility tree as well, the control is announced twice.
  const rail = stripComments(read("components/chat-shell/BotWorkspaceRail.tsx"));
  const at = rail.indexOf("<ProjectDropdownMenu");
  const close = rail.indexOf("</ProjectDropdownMenu>", at);
  const passed = rail.slice(at, close);

  assert.match(
    passed,
    /<MoreHorizontal[^>]*aria-hidden="true"/,
    "the trigger glyph must be aria-hidden so it is not announced alongside the wrapper's label",
  );
});

test("an icon-only trigger keeps a visible hover/focus affordance", () => {
  // Removing the inner <button> also removed its hover styling, which would
  // have left the control looking inert. The wrapper must restore it.
  const rail = stripComments(read("components/chat-shell/BotWorkspaceRail.tsx"));
  const at = rail.indexOf("triggerClassName");
  assert.notEqual(at, -1, "the rail must supply trigger styling to the wrapper it no longer nests a button in");
  const line = rail.slice(at, rail.indexOf("\n", at));
  assert.match(line, /hover:/, "the custom trigger must keep a hover affordance");
  assert.match(line, /focus-visible:/, "the custom trigger must keep a keyboard focus affordance");
});

test("icon-only close buttons carry an accessible name", () => {
  // Scanned across the chat shell rather than pinned to one file, so the next
  // icon-only button added anywhere fails here instead of at the next audit.
  const files = [
    "components/chat-shell/ProjectDetailPanel.tsx",
    "components/chat-shell/WorkspaceTopBar.tsx",
    "components/ThreadSidebar.tsx",
  ];

  for (const rel of files) {
    const src = read(rel);
    // Buttons whose only content is an icon element and whitespace. A button
    // with a `title` is still unnamed for a screen reader, so title does not
    // count as a name here.
    const iconOnly = /<button\b[^>]*>\s*<[A-Z][A-Za-z0-9]*\b[^>]*\/>\s*<\/button>/g;
    for (const match of src.matchAll(iconOnly)) {
      const snippet = match[0];
      const self = snippet.slice(0, snippet.indexOf(">") + 1);
      assert.match(
        self,
        /aria-label=/,
        `${rel}: an icon-only <button> has no accessible name.\n${self}`,
      );
    }
  }
});

test("no dropdown trigger is a bare div with only an onClick", () => {
  // The original shape, asserted negatively across both menus so a third
  // dropdown cannot reintroduce it. `role="button"` on the same element is the
  // accepted replacement, which is why the pattern only matches when no role
  // is present.
  for (const rel of [
    "components/chat-shell/ProjectDropdownMenu.tsx",
    "components/chat-shell/BotDropdownMenu.tsx",
  ]) {
    const src = read(rel);
    assert.doesNotMatch(
      src,
      /<div\s+onClick=\{[^}]*\}\s+className="cursor-pointer"/,
      `${rel}: a custom trigger must not be a bare <div onClick>; it needs role, tabIndex and a name`,
    );
  }
});