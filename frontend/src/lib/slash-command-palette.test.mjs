// Contract tests for the `/` palette's derivation.
//
// The defect this pins is a *regression*, not a style preference: the palette
// used to `.slice(0, 8)` its matches and to close on the first space. Against
// the real registry that meant typing `/` showed eight of the catalog with
// nothing disclosing the rest, and no multi-word subcommand (`/agent ask`,
// `/verify deep`, `/security lockdown`) could be reached at all — the
// overwhelming majority of the surface. Both limits are gone; these tests fail
// if either comes back.
//
// The fixture is read from the real `commands/catalog.py` rather than
// hand-written, so "every command" is asserted against the catalog the Gateway
// actually serves and a test cannot quietly agree with a smaller list.
//
// Pure Node test: transpiles the real module and imports it. No server, no
// browser, no React.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const CATALOG = join(HERE, "..", "..", "..", "backend", "packages", "harness", "alpha", "commands", "catalog.py");

const source = readFileSync(new URL("./slash-command-palette.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
}).outputText;
const mod = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(code)}`);
const { buildSlashCommandPalette, describePalette, normaliseCommandDraft, toPaletteCommand } = mod;

/**
 * Every catalog row, as the registry would serve it: the command string, its
 * category, and a handler flag. Parsed from the Python literal tuples so a new
 * command is covered the moment it is added upstream.
 */
function readCatalogRows() {
  const py = readFileSync(CATALOG, "utf8");
  const rows = [];
  const re = /^\s*\("(\/[^"]+)",\s*CommandCategory\.([A-Z_]+)/gm;
  let m;
  while ((m = re.exec(py)) !== null) {
    rows.push({
      command: m[1],
      category: m[2].toLowerCase(),
      description: `from ${m[1]}`,
      usage: m[1],
      has_handler: true,
    });
  }
  return rows;
}

const CATALOG_ROWS = readCatalogRows();

/**
 * The catalog registers `/learn` under both SKILLS and RSI, and `/usage` under
 * both CORE and OBSERVABILITY, so the raw row count is larger than the number
 * of distinct commands the registry can actually serve. Deduplicating here
 * keeps "every command is offered" an assertion about real commands rather than
 * about the catalog's own duplication.
 */
const DISTINCT = [...new Set(CATALOG_ROWS.map((r) => r.command))];

test("the catalog fixture is the real one, so these pins are not vacuous", () => {
  // Guards the parser above: if this regex ever stops matching, every assertion
  // below would pass against an empty list.
  assert.ok(CATALOG_ROWS.length > 100, `expected >100 catalog rows, parsed ${CATALOG_ROWS.length}`);
  const multiWord = CATALOG_ROWS.filter((r) => r.command.includes(" "));
  assert.ok(
    multiWord.length > 50,
    "the catalog must still be majority subcommands — that is what the old space rule hid",
  );
});

/* ── The reported defect ──────────────────────────────────────────────────── */

test("a bare `/` offers every command, not the first eight", () => {
  const p = buildSlashCommandPalette(CATALOG_ROWS, "/");
  assert.equal(p.applicable, true);
  assert.equal(p.rows.length, p.total);
  assert.equal(p.matched, DISTINCT.length);
  assert.ok(
    p.rows.length > 100,
    `the palette truncated the catalog to ${p.rows.length} rows — this is the reported bug`,
  );
  // Every distinct catalog command is reachable, none dropped.
  const offered = new Set(p.rows.map((r) => r.command));
  for (const command of DISTINCT) {
    assert.ok(offered.has(command), `${command} was not offered`);
  }
});

test("the palette never caps its result, whatever the registry size", () => {
  for (const size of [8, 9, 50, 125, 400]) {
    const many = Array.from({ length: size }, (_, i) => ({
      command: `/cmd${String(i).padStart(3, "0")}`,
      category: "test",
      description: "",
      usage: "",
    }));
    const p = buildSlashCommandPalette(many, "/");
    assert.equal(p.rows.length, size, `truncated at ${size} rows`);
  }
});

/* ── Subcommands, which the space rule made unreachable ───────────────────── */

test("`/agent ` lists the whole subcommand family, not just the bare row", () => {
  const p = buildSlashCommandPalette(CATALOG_ROWS, "/agent ");
  const names = p.rows.map((r) => r.command);
  assert.ok(names.includes("/agent"), "the family row itself must still be offered");
  assert.ok(names.includes("/agent ask"), "a subcommand the old rule could never reach");
  assert.ok(names.includes("/agent benchmark"));
  assert.ok(names.includes("/agent clone"));
  // `/agent` alone matches the family row; the trailing space is what opens it.
  const bare = buildSlashCommandPalette(CATALOG_ROWS, "/agent");
  assert.ok(bare.rows.length > 1, "`/agent` must already offer the family");
  // The bare row sorts first so Enter commits the command, not a subcommand.
  assert.equal(p.rows[0].command, "/agent");
});

test("a partially typed subcommand narrows to that subcommand", () => {
  const p = buildSlashCommandPalette(CATALOG_ROWS, "/agent as");
  assert.deepEqual(p.rows.map((r) => r.command), ["/agent ask"]);
});

test("`/verify ` reaches the verification subcommands", () => {
  const names = buildSlashCommandPalette(CATALOG_ROWS, "/verify ").rows.map((r) => r.command);
  for (const expected of ["/verify deep", "/verify tests", "/verify final", "/verify adversarial"]) {
    assert.ok(names.includes(expected), `${expected} unreachable`);
  }
});

/* ── Prefix boundaries ────────────────────────────────────────────────────── */

test("a prefix does not leak across a word boundary", () => {
  const rows = [
    { command: "/agent", category: "a", description: "", usage: "" },
    { command: "/agent ask", category: "a", description: "", usage: "" },
    { command: "/agents", category: "a", description: "", usage: "" },
  ];
  const names = buildSlashCommandPalette(rows, "/agent").rows.map((r) => r.command);
  assert.deepEqual(names.sort(), ["/agent", "/agent ask"]);
  assert.ok(!names.includes("/agents"), "`/agents` is a different command, not a match for `/agent`");
});

test("typing arguments closes the palette instead of matching nothing in silence", () => {
  const p = buildSlashCommandPalette(CATALOG_ROWS, "/goal create ship the thing");
  assert.equal(p.applicable, true, "it is still a `/` token, just past completion");
  assert.equal(p.rows.length, 0);
});

test("a draft that is not a slash token is not this palette's business", () => {
  for (const draft of ["", "hello", "email me at /goal", "  "]) {
    const p = buildSlashCommandPalette(CATALOG_ROWS, draft);
    assert.equal(p.applicable, false, `"${draft}" must not open the palette`);
    assert.equal(p.rows.length, 0);
  }
});

test("whitespace inside the draft is collapsed, so a written command still completes", () => {
  assert.equal(normaliseCommandDraft("/agent   ask "), "/agent ask");
  assert.equal(normaliseCommandDraft("  /agent\task"), "/agent ask");
  // Selecting a row writes `/<command> ` — that trailing space must reopen the
  // family rather than match nothing.
  assert.ok(buildSlashCommandPalette(CATALOG_ROWS, "/agent ask ").rows.length >= 1);
});

/* ── Determinism and honesty ──────────────────────────────────────────────── */

test("pick order is exact-family-row first, then alphabetical, and stable", () => {
  const shuffled = [...CATALOG_ROWS].reverse();
  const a = buildSlashCommandPalette(shuffled, "/mcp ").rows.map((r) => r.command);
  const b = buildSlashCommandPalette(CATALOG_ROWS, "/mcp ").rows.map((r) => r.command);
  assert.deepEqual(a, b, "registry ordering must not change what the operator sees");
  assert.equal(a[0], "/mcp");
  assert.deepEqual([...a.slice(1)].sort(), a.slice(1));
});

test("a handler-less row is marked, and an unreported flag stays unreported", () => {
  const rows = [
    { command: "/alpha", category: "t", description: "", usage: "", has_handler: false },
    { command: "/beta", category: "t", description: "", usage: "" },
    { command: "/gamma", category: "t", description: "", usage: "", has_handler: true },
  ];
  const byName = Object.fromEntries(
    buildSlashCommandPalette(rows, "/").rows.map((r) => [r.command, r.hasHandler]),
  );
  assert.equal(byName["/alpha"], false, "the registry said no handler");
  assert.equal(byName["/beta"], null, "absent is an absence, not a measurement");
  assert.equal(byName["/gamma"], true);
});

test("a malformed row is skipped rather than rendered as a blank command", () => {
  const rows = [null, "nope", {}, { command: "   " }, { command: "/real", description: "ok" }];
  const p = buildSlashCommandPalette(rows, "/");
  assert.deepEqual(p.rows.map((r) => r.command), ["/real"]);
  assert.equal(toPaletteCommand(null), null);
  assert.equal(toPaletteCommand({ command: "" }), null);
});

test("duplicate command strings are collapsed", () => {
  const rows = [
    { command: "/dup", category: "a", description: "first", usage: "/dup" },
    { command: "/dup", category: "b", description: "second", usage: "/dup" },
  ];
  const p = buildSlashCommandPalette(rows, "/");
  assert.equal(p.rows.length, 1);
  assert.equal(p.total, 1, "the registry size reported must match what is rendered");
  assert.equal(p.rows[0].description, "first");
});

/* ── The count sentence ───────────────────────────────────────────────────── */

test("the count sentence discloses what is shown and what exists", () => {
  const all = buildSlashCommandPalette(CATALOG_ROWS, "/");
  const text = describePalette(all);
  assert.ok(text.includes(String(all.matched)), "the shown count is stated");
  assert.ok(text.includes(String(all.total)), "the registry size is stated");
  assert.ok(text.includes("of"), "the ratio is explicit");

  const one = describePalette(buildSlashCommandPalette(CATALOG_ROWS, "/agent as"));
  assert.ok(one.startsWith("1 of "), one);

  const none = describePalette(buildSlashCommandPalette(CATALOG_ROWS, "/zzzz"));
  assert.match(none, /No command starts with \/zzzz/);

  assert.equal(describePalette(buildSlashCommandPalette(CATALOG_ROWS, "hello")), "");
  assert.match(describePalette(buildSlashCommandPalette([], "/")), /No commands were returned/);
});