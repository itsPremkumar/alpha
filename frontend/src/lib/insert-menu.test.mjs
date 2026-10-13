// insert-menu.test.mjs — the composer's insert rows must do what their labels
// claim, and a row that cannot act must not be offered.
//
// The bug this exists to stop: the composer rendered a paperclip and an image
// glyph side by side, and both called `fileRef.current?.click()` on the *same*
// `<input type="file" multiple>` — which carried no `accept`. So the control
// titled "Upload images or media" offered `Q3-report.pdf` with exactly the
// prominence it offered a PNG. A label naming a file class is a claim about
// what the picker will show, and nothing was enforcing it. `accept` is data in
// `lib/insert-menu.ts` now, so this suite can check each filter against the
// label that advertises it rather than trusting markup nobody re-reads.
//
// Pure Node (node --test src/lib/*.test.mjs): no server, no browser, no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");
const toDataUrl = (source) => `data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`;

const source = read("./insert-menu.ts");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;

// The module is deliberately import-free (a menu's rows are data, not a
// dependency graph). If a value import ever creeps in it cannot resolve from
// here, and this fails loudly instead of silently loading a second copy.
assert.doesNotMatch(code, /^import\s/m, "insert-menu.ts must stay import-free so its rows stay pure data");

const {
  INSERT_ACTIONS,
  INSERT_GROUP_ORDER,
  INSERT_GROUP_LABELS,
  visibleInsertActions,
  visibleInsertActionKeys,
  insertMenuSections,
  fileAcceptAttribute,
  acceptForActionKey,
  findInsertAction,
  insertActionTitle,
} = await import(toDataUrl(code));

const WIRED = { canUpload: true };
const UNWIRED = { canUpload: false };

/* ══ The row set is a contract ═══════════════════════════════════════════ */

test("the menu offers exactly these three actions, in this order", () => {
  // A fourth action must be a deliberate addition to this list, not a button
  // someone pastes into the JSX beside the others.
  assert.deepEqual(
    INSERT_ACTIONS.map((a) => a.key),
    ["upload-image", "upload-file", "code-block"],
  );
  assert.deepEqual(visibleInsertActionKeys(WIRED), ["upload-image", "upload-file", "code-block"]);
});

test("every action carries a label, a hint, a group and a declared picker kind", () => {
  for (const a of INSERT_ACTIONS) {
    assert.ok(a.label.trim().length > 0, `${a.key} needs a label`);
    assert.ok(a.hint.trim().length > 0, `${a.key} needs a hint`);
    assert.ok(INSERT_GROUP_ORDER.includes(a.group), `${a.key} has no group`);
    assert.equal(typeof a.isFilePicker, "boolean", `${a.key} must declare whether it opens a picker`);
  }
});

/* ══ The label-vs-filter pin (the bug) ════════════════════════════════════ */

test("the image row filters to images, because its label says images", () => {
  const image = findInsertAction("upload-image");
  assert.equal(image.isFilePicker, true);
  assert.equal(image.accept, "image/*", "a row advertising images must not offer every file type");
  assert.match(image.label, /image/i);
  // The hint names the classes so the claim is checkable before clicking.
  assert.match(image.hint, /image/i);
});

test("the file row is the one that deliberately offers everything", () => {
  const file = findInsertAction("upload-file");
  assert.equal(file.isFilePicker, true);
  assert.equal(file.accept, null, "null means 'any file' — this is the row that promises it");
  assert.match(file.label, /file|document/i);
});

test("a row's accept reaches the input as an absent attribute, never accept=''", () => {
  // React omits the attribute for `undefined`. `accept=""` would be a different
  // thing entirely: some browsers read an empty filter as matching nothing, so
  // the "any file" row could silently pick up a rejection the label promised
  // would not happen.
  assert.equal(fileAcceptAttribute(findInsertAction("upload-file")), undefined);
  assert.equal(fileAcceptAttribute(findInsertAction("upload-image")), "image/*");
});

test("a row that opens no picker reports no filter, distinct from 'any file'", () => {
  // Both return `undefined` at the input, but for opposite reasons — one row
  // opens no picker at all, the other opens one that accepts everything. The
  // flag is what keeps them apart for the suite and for the renderer.
  const code = findInsertAction("code-block");
  assert.equal(code.isFilePicker, false);
  assert.equal(code.accept, null);
  assert.equal(code.requiresUpload, false);
  assert.equal(fileAcceptAttribute(code), undefined);
});

test("the code row's hint states what it inserts rather than naming the glyph", () => {
  const code = findInsertAction("code-block");
  assert.match(code.hint, /```/, "the hint should show the literal fence the row inserts");
  assert.match(insertActionTitle(code), /^Insert code block — /);
});

/* ══ Availability: no dead rows ═══════════════════════════════════════════ */

test("a composer with no onAttach offers no upload rows at all", () => {
  // Removed, not disabled. A greyed "Upload image…" implies a pending state
  // that does not exist here and invites a click that can only do nothing.
  const keys = visibleInsertActionKeys(UNWIRED);
  assert.deepEqual(keys, ["code-block"]);
  assert.equal(keys.includes("upload-image"), false);
  assert.equal(keys.includes("upload-file"), false);
});

test("an unwired composer still offers the insert row, which needs nothing", () => {
  const sections = insertMenuSections(UNWIRED);
  assert.deepEqual(
    sections.map((s) => s.key),
    ["insert"],
    "the upload section is dropped whole rather than rendering a heading over nothing",
  );
  assert.equal(sections[0].actions.length, 1);
});

test("a heading over an empty group never renders", () => {
  for (const availability of [WIRED, UNWIRED]) {
    for (const section of insertMenuSections(availability)) {
      assert.ok(section.actions.length > 0, `${section.key} rendered as an empty heading`);
      assert.ok(section.label.length > 0);
    }
  }
});

/* ══ Grouping ═════════════════════════════════════════════════════════════ */

test("sections render in the declared order with their labels", () => {
  const sections = insertMenuSections(WIRED);
  assert.deepEqual(
    sections.map((s) => s.key),
    [...INSERT_GROUP_ORDER],
  );
  assert.equal(sections[0].label, INSERT_GROUP_LABELS.upload);
  assert.equal(sections[1].label, INSERT_GROUP_LABELS.insert);
  assert.equal(sections[0].actions.length, 2, "both uploads belong to the device group");
  assert.equal(sections[1].actions.length, 1);
});

test("an unknown key raises instead of quietly offering every file", () => {
  // The tempting reading of "no such row" is "no filter" — and that would put
  // an unfiltered picker behind a label promising images, which is the exact
  // defect this module replaced. A renamed row must fail here, not ship.
  assert.throws(() => acceptForActionKey("nope"), /no action named "nope"/);
  assert.equal(acceptForActionKey("upload-image"), "image/*");
  assert.equal(acceptForActionKey("upload-file"), undefined, "the any-file row stays unfiltered by data, not by error");
  assert.equal(findInsertAction("nope"), undefined, "the lookup itself stays non-throwing for callers that branch on it");
});

/* ══ Source pins on the component ═════════════════════════════════════════ */

const menu = read("../components/InsertMenu.tsx");
const composer = read("../components/Composer.tsx");

test("InsertMenu keeps one file input per class, and derives both filters from the lib", () => {
  // Two separate elements is the whole fix: the old pair shared one, which is
  // why both did everything. And neither input writes a MIME string inline —
  // the filter comes from `fileAcceptAttribute`, so the label/filter pairing
  // can only be changed in `lib/insert-menu.ts`, which the suite drives.
  // Counted as `<input` at end-of-line so the docstring's own mention of
  // `<input type="file" multiple>` (which describes the bug) is not read as an
  // element — prose about the input is not an input.
  const inputs = menu.match(/<input\s*$/gm) || [];
  assert.equal(inputs.length, 2, "the image row and the file row must not share an input");
  // Scoped to the returned markup: the docstring legitimately writes
  // `accept="image/*"` while explaining the bug it replaced, and prose about a
  // filter is not a filter.
  const jsx = menu.slice(menu.indexOf("  return ("));
  assert.equal(
    (jsx.match(/accept=\{acceptForActionKey\(/g) || []).length,
    2,
    "both inputs must take their filter from the lib",
  );
  assert.doesNotMatch(jsx, /accept="[^"]/, "no inline MIME string to drift from the label");
  assert.doesNotMatch(jsx, /accept=\{"/, "no inline MIME string to drift from the label");
});

test("InsertMenu derives its rows from the lib, never a hand-written list", () => {
  assert.match(menu, /insertMenuSections\(\{ canUpload \}\)/);
  assert.match(menu, /insertActionTitle\(action\)/);
  // Icons are the only thing it maps itself — a glyph is not a claim.
  assert.match(menu, /ACTION_ICONS/);
});

test("InsertMenu resets the input so picking the same file twice still fires", () => {
  // Without this the second pick of an unchanged selection is silent, which
  // reads as "my file did nothing".
  assert.match(menu, /e\.target\.value = ""/);
});

test("InsertMenu closes before opening the OS dialog, and returns focus on Escape", () => {
  assert.match(menu, /close\(\);\s*\(action\.key === "upload-image"[\s\S]*?\.click\(\)/);
  assert.match(menu, /event\.key === "Escape"/);
  assert.match(menu, /triggerRef\.current\?\.focus\(\)/);
  // Both refs, because the panel is portalled out of the trigger's wrapper.
  assert.match(menu, /triggerRef\.current\?\.contains\(target\) \|\| panelRef\.current\?\.contains\(target\)/);
});

test("Composer renders one InsertMenu instead of the three duplicated controls", () => {
  assert.match(composer, /<InsertMenu\b/);
  // The duplicate buttons and their shared unfiltered input are gone.
  assert.doesNotMatch(composer, /Upload images or media/);
  assert.doesNotMatch(composer, /fileRef/);
  assert.doesNotMatch(composer, /Insert code block \(\s*<\/>/);
  // The code fence still gets inserted, now through the menu's callback.
  assert.match(composer, /onInsertCode=\{\(\) => \{/);
});
