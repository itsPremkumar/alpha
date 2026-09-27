// text-encoding.test.mjs — shipped frontend source must be clean UTF-8, and
// must not carry typographic debris.
//
// Board rule 8 / AGENTS.md: source and skill text is UTF-8, and code that
// reads or writes it must pass an explicit encoding rather than relying on the
// platform locale. A file that was once written through a cp1252 round-trip
// keeps its mojibake forever, because the damaged bytes are still *valid*
// UTF-8: decode() never complains, the garbage just reaches the UI.
//
// Detection is mechanical, not a style opinion. For each maximal run of
// non-ASCII characters, re-encode the run through cp1252 and see whether those
// bytes are valid UTF-8 that decodes to something *different and shorter* than
// what is in the file. If so, the file is displaying UTF-8-that-was-decoded-
// as-cp1252, and the decoded value is the original text.
//
// The mojibake samples below are built from codepoints on purpose, so this file
// does not itself contain the byte pattern it hunts for.
//
// A second, unrelated class of damage is checked here too: a *typographic*
// character that survived intact but was never meant to be there. Double-
// encoding is caught above; this catches a plain U+201D standing in for an em
// dash, or used as a "no value" placeholder, which no encoding round-trip
// would ever explain and which therefore slips past a mojibake-only scan.
// (This comment deliberately spells the codepoint instead of pasting the glyph,
// so the file does not trip the very rule it defines.)
//
// Pure Node test (node --test src/lib/text-encoding.test.mjs): reads files from
// disk only. No network, no browser, no build step.
import assert from "node:assert/strict";
import { readFileSync, readdirSync, unlinkSync, writeFileSync } from "node:fs";
import { join, relative, sep } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../", import.meta.url));

// cp1252 differs from latin1 only in 0x80-0x9F.
const CP1252_HIGH = [
  0x20ac, 0x0081, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021,
  0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008d, 0x017d, 0x008f,
  0x0090, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
  0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0x009d, 0x017e, 0x0178,
];

/** Unicode char -> cp1252 byte, or null when cp1252 cannot represent it. */
function cp1252Byte(ch) {
  const cp = ch.codePointAt(0);
  if (cp >= 0x00 && cp <= 0x7f) return cp;
  if (cp >= 0xa0 && cp <= 0xff) return cp;
  const i = CP1252_HIGH.indexOf(cp);
  return i === -1 ? null : 0x80 + i;
}

/** Build the mojibake a UTF-8 char turns into when decoded as cp1252. */
function corrupt(ch) {
  return [...Buffer.from(ch, "utf8")]
    .map((b) => (b < 0x80 || b >= 0xa0 ? String.fromCharCode(b) : String.fromCharCode(CP1252_HIGH[b - 0x80])))
    .join("");
}

/** Every maximal run of non-ASCII characters in a line, with its column. */
function nonAsciiRuns(line) {
  const runs = [];
  let cur = "";
  let start = 0;
  for (let i = 0; i <= line.length; i++) {
    const ch = line[i];
    if (ch !== undefined && ch.codePointAt(0) > 127) {
      if (!cur) start = i;
      cur += ch;
      continue;
    }
    if (cur) runs.push({ start, run: cur });
    cur = "";
  }
  return runs;
}

/**
 * If `run` is UTF-8 text that was decoded as cp1252, return the original.
 * Returns null for legitimately non-ASCII text (accented letters, CJK, a real
 * ellipsis, real curly quotes, ...).
 */
function unMojibake(run) {
  const bytes = [];
  for (const ch of run) {
    const b = cp1252Byte(ch);
    if (b === null) return null; // cp1252 could never have produced this
    bytes.push(b);
  }
  let fixed;
  try {
    fixed = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.from(bytes));
  } catch {
    return null; // not valid UTF-8, so it was never UTF-8-through-cp1252
  }
  if (fixed === run || fixed.length >= run.length) return null;
  return fixed;
}

function* sourceFiles(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === ".next") continue;
    const full = join(dir, entry.name);
    if (entry.isDirectory()) yield* sourceFiles(full);
    else if (/\.(ts|tsx|mjs|js|css)$/.test(entry.name)) yield full;
  }
}

const allSourceFiles = [...sourceFiles(SRC)];

test("the source walk actually finds the frontend tree", () => {
  // Guard so the scan below can never pass by scanning nothing.
  assert.ok(allSourceFiles.length > 20, `only found ${allSourceFiles.length} files`);
  assert.ok(
    allSourceFiles.some((f) => f.endsWith(join("lib", "api-client.ts"))),
    "must include src/lib/api-client.ts",
  );
});

test("the detector recognises real mojibake and spares legitimate text", () => {
  // Note: \u{...}, not Python's \U........ — "\U0001F464" is just "U0001F464" in JS.
  for (const good of ["\u2192", "\u2014", "\u2026", "\u26d4", "\u{1F464}", "\u{1F4C1}"]) {
    assert.equal(unMojibake(corrupt(good)), good, `round-trip failed for U+${good.codePointAt(0).toString(16)}`);
  }
  for (const clean of ["\u2026", "caf\u00e9", "\u65e5\u672c\u8a9e", "\u2192", "\u201cquoted\u201d", "\u2014"]) {
    assert.equal(unMojibake(clean), null, `false positive on U+${clean.codePointAt(0).toString(16)}`);
  }
  // Two mojibake runs separated by ASCII still repair as one byte sequence.
  assert.equal(unMojibake(`${corrupt("\u2192")} ${corrupt("\u2192")}`), "\u2192 \u2192");
  // ...and the line scanner splits them so each site is reported on its own.
  const split = nonAsciiRuns(`a ${corrupt("\u2192")} b ${corrupt("\u2192")} c`);
  assert.equal(split.length, 2);
  assert.deepEqual(split.map((r) => unMojibake(r.run)), ["\u2192", "\u2192"]);
});

test("no frontend source file contains UTF-8 decoded as cp1252", () => {
  const offenders = [];
  for (const file of allSourceFiles) {
    const lines = readFileSync(file, "utf8").split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      for (const { start, run } of nonAsciiRuns(lines[i])) {
        const fixed = unMojibake(run);
        if (fixed) {
          offenders.push(
            `${relative(SRC, file)}:${i + 1}:${start + 1}  ${JSON.stringify(run)} should be ${JSON.stringify(fixed)}`,
          );
        }
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `mojibake in shipped source (${offenders.length} site(s)):\n  ${offenders.join("\n  ")}`,
  );
});

test("the kanban board stores and renders real characters, not mojibake", () => {
  // The concrete user-visible damage: a status move writes its history note
  // into localStorage, so the corruption is persisted, not just displayed.
  const src = readFileSync(join(SRC, "components", "sections", "KanbanSection.tsx"), "utf8");

  const history = src.match(/saveCard\(next, `\$\{labelOf\(prev\)\}(.+?)\$\{labelOf\(to\)\}`\)/);
  assert.ok(history, "the status-move history note must still be written");
  assert.equal(
    history[1].trim(),
    "\u2192",
    `card history note must separate the two columns with U+2192, found ${JSON.stringify(history[1])}`,
  );

  for (const [what, ch] of [["agent avatar", "\u{1F464}"], ["project folder", "\u{1F4C1}"], ["blocked sign", "\u26d4"]]) {
    assert.ok(src.includes(ch), `the ${what} (U+${ch.codePointAt(0).toString(16)}) must be present`);
  }
  assert.match(src, /placeholder="pytest -q \u2192 42 passed"/);

  // And nothing in the file is double-encoded any more.
  const stillBroken = [];
  for (const [i, line] of src.split(/\r?\n/).entries()) {
    for (const { start, run } of nonAsciiRuns(line)) {
      if (unMojibake(run)) stillBroken.push(`${i + 1}:${start + 1} ${JSON.stringify(run)}`);
    }
  }
  assert.deepEqual(stillBroken, [], `KanbanSection.tsx still has mojibake: ${stillBroken.join(", ")}`);
});

test("no frontend source file contains C0 control characters", () => {
  // Tab, newline and carriage return are the only controls a source file may
  // contain. A stray NUL or ESC means a text-mangling tool round-tripped the
  // file: decode() stays silent, but the bytes ride along into the bundle.
  const offenders = [];
  for (const file of allSourceFiles) {
    const text = readFileSync(file, "utf8");
    // Split on real line terminators: several files are CRLF, and a trailing
    // CR is a terminator, not a control character in the line's content.
    const lines = text.split(/\r\n|\n|\r/);
    for (let i = 0; i < lines.length; i++) {
      for (let j = 0; j < lines[i].length; j++) {
        const code = lines[i].charCodeAt(j);
        if (code < 0x20 && code !== 0x09) {
          offenders.push(
            `${relative(SRC, file)}:${i + 1}:${j + 1}  ` +
              `U+${code.toString(16).padStart(4, "0").toUpperCase()}`,
          );
        } else if (code === 0x7f) {
          offenders.push(`${relative(SRC, file)}:${i + 1}:${j + 1}  U+007F (DEL)`);
        }
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `C0/DEL control characters in source (${offenders.length}):\n  ${offenders.slice(0, 20).join("\n  ")}`,
  );
});

test("source files are read as UTF-8 without a BOM surprise", () => {
  // A BOM is legitimate in the six PowerShell scripts the board calls out, so
  // this only asserts the byte order mark is absent from frontend TS/TSX, where
  // it breaks bundlers and inflates the first identifier.
  const offenders = allSourceFiles
    .filter((f) => /\.(ts|tsx|mjs|js)$/.test(f))
    .filter((f) => readFileSync(f).subarray(0, 3).equals(Buffer.from([0xef, 0xbb, 0xbf])))
    .map((f) => relative(SRC, f));
  assert.deepEqual(offenders, [], `unexpected UTF-8 BOM (sep=${JSON.stringify(sep)}): ${offenders.join(", ")}`);
});

// ── Typographic debris: an unpaired curly quote ────────────────────────────
//
// A real quotation is always a pair, U+201C ... U+201D, on one line. A U+201D
// with no opening partner is not a quotation at all: in this tree it is always
// either an em dash that got typed as a right quote, or a placeholder emitted
// for "no value" so the user sees a floating U+201D glyph instead of an empty
// field. Neither is a matter of taste - it is text that renders wrong.
//
// This check is mechanical (count the two marks, require balance) and it is
// deliberately independent of the mojibake scan above, which is correctly silent
// on these characters because they were never mis-encoded.

const LDQUO = "\u201c";
const RDQUO = "\u201d";

/** Sites where an unpaired U+201D is correct, with the reason it is correct. */
const UNPAIRED_QUOTE_ALLOWED = new Map([
  [
    join("lib", "speech.ts"),
    (line) =>
      // Sentence-boundary punctuation class: /[.!?"<RDQUO><LRSQUO>)}\]]/.
      // A curly quote here is a character the *matching* regex must recognise,
      // so removing it would break sentence detection rather than fix anything.
      /\[[^\]]*\u201d[^\]]*\]/.test(line),
  ],
]);

/** Every line in `file` whose curly double quotes do not balance. */
function unbalancedQuoteLines(file) {
  const out = [];
  const lines = readFileSync(file, "utf8").split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const open = [...line].filter((c) => c === LDQUO).length;
    const close = [...line].filter((c) => c === RDQUO).length;
    if (open !== close) out.push({ lineNo: i + 1, line, open, close });
  }
  return out;
}

test("the unpaired-quote detector recognises a stray quote and spares a real one", () => {
  // Balanced prose is not flagged, on both sides of the pair.
  const tmpBalanced = join(SRC, "lib", ".tmp-balanced-probe.ts");
  writeFileSync(tmpBalanced, `const a = \`He said \u201chello\u201d loudly\`;\nconst b = 1;\n`, "utf8");
  try {
    assert.deepEqual(unbalancedQuoteLines(tmpBalanced), [], "a balanced pair must not be reported");
  } finally {
    unlinkSync(tmpBalanced);
  }

  // The two shapes that are the whole point: a stray close, and a stray open.
  writeFileSync(tmpBalanced, `const a = "reason \u201d because";\nconst b = "\u201c lead in";\n`, "utf8");
  try {
    const hits = unbalancedQuoteLines(tmpBalanced);
    assert.equal(hits.length, 2, `expected both stray lines, got ${JSON.stringify(hits)}`);
    assert.deepEqual(hits.map((h) => h.close), [1, 0]);
  } finally {
    unlinkSync(tmpBalanced);
  }

  // And the speech.ts exemption is a real predicate, not a blanket pass.
  const allowed = UNPAIRED_QUOTE_ALLOWED.get(join("lib", "speech.ts"));
  assert.ok(allowed, "the speech.ts exemption must exist");
  assert.equal(allowed('const p = /[.!?"\u201d\u2019)}\\]]+/g;'), true, "regex char class must be allowed");
  assert.equal(allowed('const s = "reason \u201d because";'), false, "a stray quote must NOT be allowed");
});

test("no frontend source file leaves a curly double quote unpaired", () => {
  const offenders = [];
  for (const file of allSourceFiles) {
    const key = relative(SRC, file);
    const exempt = UNPAIRED_QUOTE_ALLOWED.get(key);
    for (const hit of unbalancedQuoteLines(file)) {
      if (exempt && exempt(hit.line)) continue;
      offenders.push(
        `${key}:${hit.lineNo}  U+201C x${hit.open}, U+201D x${hit.close}  ${JSON.stringify(hit.line.trim().slice(0, 100))}`,
      );
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `unpaired curly quotes (${offenders.length} site(s)) — a real quotation is a balanced ` +
      `U+201C/U+201D pair; a lone U+201D here is a mistyped em dash or a "no value" placeholder:\n  ` +
      offenders.join("\n  "),
  );
});

test("the allowlist holds no stale entries", () => {
  // A stale exemption is a hole nobody is watching. Each allowed file must
  // still contain a U+201D that the predicate actually excuses.
  for (const [key, exempt] of UNPAIRED_QUOTE_ALLOWED) {
    const file = join(SRC, key);
    const excused = unbalancedQuoteLines(file).filter((h) => exempt(h.line));
    assert.ok(
      excused.length > 0,
      `allowlist entry ${key} no longer matches anything — remove it instead of leaving a silent exemption`,
    );
  }
});

test("the kanban card footer renders no value rather than a stray quote", () => {
  // The concrete user-visible damage. `c.createdAt` is optional, so the
  // fallback branch runs for any card the server has not stamped yet, and it
  // used to render a literal U+201D straight into the card footer.
  const src = readFileSync(join(SRC, "components", "sections", "KanbanSection.tsx"), "utf8");

  const footer = src.match(/Created \{c\.createdAt \? new Date\(c\.createdAt\)\.toLocaleString\(\) : (.*?)\}/);
  assert.ok(footer, "the card footer must still render the created timestamp");
  assert.equal(
    footer[1].trim(),
    '""',
    `an absent createdAt must render an empty string, not ${JSON.stringify(footer[1])}`,
  );

  // The sibling separators in the same file: the parenthetical dash role that
  // lines 62/151/157 fill with a real em dash.
  for (const expected of [
    'setError("A blocked card needs a reason \u2014 added it in the editor.");',
    'setError("Blocked cards need a reason \u2014 what is stopping it?");',
  ]) {
    assert.ok(src.includes(expected), `expected the em-dash form: ${JSON.stringify(expected)}`);
  }
});
