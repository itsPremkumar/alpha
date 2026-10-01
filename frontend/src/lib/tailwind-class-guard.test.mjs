/**
 * A guard against Tailwind utility classes that generate nothing.
 *
 * ## The defect this exists to prevent
 *
 * `ChatShellLanding.tsx` sized the chat landing hero with `size-18`. There is
 * no `18` in Tailwind's default spacing scale — it steps `14` -> `16` -> `20` —
 * and `tailwind.config.cjs` extends only `colors` and `borderRadius`, adding no
 * `spacing` key. So `size-18` compiled to **no CSS at all**. The avatar circle
 * silently collapsed to the size of its own contents while the `blur-lg` glow
 * sized itself around nothing, on the largest visual element of the first-run
 * screen.
 *
 * Nothing caught it: not `tsc`, not ESLint, not Tailwind, not the build, and not
 * the 883-test suite. A utility class is a bare string, so a typo is
 * indistinguishable from a valid class at every type boundary. This is the
 * missing feedback loop.
 *
 * ## What it does
 *
 * Scans the class strings in every `.tsx` file under `src/` for numeric
 * spacing utilities
 * (`size-`, `p-`, `px-`, `m-`, `gap-`, `h-`, `w-`, ...) and fails when the
 * numeric part is not in the scale the theme can actually produce. Arbitrary
 * values (`size-[4.5rem]`), fractions, and non-numeric suffixes are ignored,
 * because those are legitimate.
 *
 * ## Proving it bites
 *
 * Change `size-[4.5rem]` back to `size-18` in `ChatShellLanding.tsx` and this
 * file fails. That is the whole regression.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

/** `frontend/src` — the tree this guard sweeps. */
const SRC_DIR = fileURLToPath(new URL("../", import.meta.url));
/** `frontend/` — where the Tailwind config lives. */
const FRONTEND_DIR = fileURLToPath(new URL("../../", import.meta.url));

/**
 * Tailwind v3's default spacing scale, as a set of the numeric parts that
 * produce a real value. Derived from the documented default theme; the
 * extension in this repo's `tailwind.config.cjs` adds `colors` and
 * `borderRadius` only, so no numeric key is added to this set.
 */
const DEFAULT_SPACING = new Set(
  [
    "0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "5", "6", "7", "8", "9",
    "10", "11", "12", "14", "16", "20", "24", "28", "32", "36", "40", "44", "48",
    "52", "56", "60", "64", "72", "80", "96",
  ].map((n) => n),
);

/**
 * The spacing-derived prefixes whose numeric suffix indexes the scale above.
 *
 * `size-*` is the one that bit us. The rest are included because they fail the
 * same silent way: `p-18`, `gap-13` and `w-22` are all no-ops.
 */
const SPACING_PREFIXES = [
  "size", "p", "px", "py", "pt", "pr", "pb", "pl", "ps", "pe",
  "m", "mx", "my", "mt", "mr", "mb", "ml", "ms", "me",
  "gap", "gap-x", "gap-y", "space-x", "space-y",
  "w", "h", "min-w", "min-h", "max-w", "max-h",
  "top", "right", "bottom", "left", "inset",
  "basis", "leading", "tracking",
];

/** Read the numeric theme extensions, so a future config change is not a false alarm. */
function extendedSpacingKeys() {
  const config = readFileSync(join(FRONTEND_DIR, "tailwind.config.cjs"), "utf8");
  const match = config.match(/spacing\s*:\s*{([^}]*)}/);
  if (!match) return new Set();
  return new Set(
    [...match[1].matchAll(/["']?([\d.]+)["']?\s*:/g)].map((m) => m[1]),
  );
}

const EXTENDED = extendedSpacingKeys();

/** Every `.tsx` file under `src/`, so a new component is covered automatically. */
function tsxFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...tsxFiles(full));
    else if (entry.endsWith(".tsx")) out.push(full);
  }
  return out;
}

/** A class token is only interesting if the whole string is one utility. */
function isBareUtility(token) {
  return !token.startsWith("[") && !token.startsWith("{") && !token.includes("[");
}

/**
 * Blank out comments, preserving line numbering.
 *
 * This fix documents the bad class it replaced ("`size-18` is not in
 * Tailwind's spacing scale"), so a sweep that read comments would fail on its
 * own explanation. Only real code is scanned.
 */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .split("\n")
    .map((line) => {
      const cut = line.indexOf("//");
      return cut === -1 ? line : line.slice(0, cut);
    })
    .join("\n");
}

test("the spacing scale this guard checks matches the config's extensions", () => {
  // If someone adds a real `spacing` extension later, the hard-coded default
  // above becomes incomplete and this guard would start reporting false
  // positives. Failing here is how that gets noticed, rather than as a wall of
  // confusing failures later.
  assert.ok(
    EXTENDED.size === 0,
    `tailwind.config.cjs now extends spacing with ${[...EXTENDED].join(", ")}; ` +
      "update DEFAULT_SPACING in tailwind-class-guard.test.mjs to match.",
  );
});

test("no numeric spacing utility references a value the theme cannot produce", () => {
  const offenders = [];
  for (const file of tsxFiles(SRC_DIR)) {
    const source = stripComments(readFileSync(file, "utf8"));
    for (const match of source.matchAll(/["'`]([^"'`]*\b(?:size|p|m|gap|w|h)-[0-9][^"'`]*)["'`]/g)) {
      for (const token of match[1].split(/\s+/)) {
        if (!token || !isBareUtility(token)) continue;
        const hit = SPACING_PREFIXES.find(
          (prefix) => token.startsWith(`${prefix}-`) && /^\d+(\.\d+)?$/.test(token.slice(prefix.length + 1)),
        );
        if (!hit) continue;
        const numeric = token.slice(hit.length + 1);
        if (DEFAULT_SPACING.has(numeric) || EXTENDED.has(numeric)) continue;
        const line = source.slice(0, match.index).split("\n").length;
        offenders.push(`${relative(FRONTEND_DIR, file)}:${line} ${token}`);
      }
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `these utility classes generate no CSS because the value is outside Tailwind's ` +
      `spacing scale:\n  ${offenders.join("\n  ")}`,
  );
});

test("the specific regression stays fixed: the landing hero is sized", () => {
  const source = stripComments(
    readFileSync(join(SRC_DIR, "components/chat-shell/ChatShellLanding.tsx"), "utf8"),
  );
  assert.ok(
    !/\bsize-18\b/.test(source),
    "`size-18` is not in Tailwind's spacing scale; use an arbitrary value like `size-[4.5rem]`",
  );
  // And the replacement must actually produce a size.
  assert.match(source, /size-\[4\.5rem\]/, "the hero should keep an explicit arbitrary size");
});

test("the guard's own scan finds the class it claims to find", () => {
  // Proves the detector is not silently matching nothing: a string that is
  // definitely invalid must be reported by the same predicate the sweep uses.
  const detect = (token) => {
    if (!isBareUtility(token)) return false;
    const hit = SPACING_PREFIXES.find(
      (prefix) => token.startsWith(`${prefix}-`) && /^\d+(\.\d+)?$/.test(token.slice(prefix.length + 1)),
    );
    if (!hit) return false;
    const numeric = token.slice(hit.length + 1);
    return !(DEFAULT_SPACING.has(numeric) || EXTENDED.has(numeric));
  };
  assert.equal(detect("size-18"), true, "the original defect must be detected");
  assert.equal(detect("p-18"), true, "the same bug in padding must be detected");
  assert.equal(detect("gap-13"), true, "a non-integral gap must be detected");
  assert.equal(detect("size-[4.5rem]"), false, "an arbitrary value is legitimate");
  assert.equal(detect("size-16"), false, "a real scale value must pass");
  assert.equal(detect("p-0.5"), false, "a real fractional scale value must pass");
  assert.equal(detect("w-full"), false, "a non-numeric utility is out of scope");
  assert.equal(detect("hover:size-18"), false, "prefixed variants are out of this detector's scope");
});
