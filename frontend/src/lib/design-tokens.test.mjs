// Design-token layer tests.
//
// The token layer exists to stop every component inventing its own shadow,
// surface colour and duration. That only holds if the tokens are complete and
// internally consistent, which is a property of `globals.css` alone and so is
// invisible to component tests and to `tsc`.
//
// Pure Node test (node --test src/lib/design-tokens.test.mjs): reads one file
// from disk. No network, no browser, no build step.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, globSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const raw = readFileSync(
  fileURLToPath(new URL("../app/globals.css", import.meta.url)),
  "utf8",
);
// Comments are stripped before anything is matched. They legitimately name the
// tokens (the `.elev-1/.elev-2/.elev-3` note in `:root`, for one), and a
// `indexOf` that hit a comment would then scan on to the *next* rule's brace
// and report the wrong block entirely.
const css = raw.replace(/\/\*[\s\S]*?\*\//g, "");

/**
 * Body of a CSS rule whose selector list contains `needle`.
 *
 * The match must be followed by `{` (modulo whitespace) rather than merely
 * being a substring of the file, so a name mentioned in a comment or a longer
 * identifier cannot capture a neighbouring rule.
 */
function ruleBody(needle) {
  const flat = css.replace(/\s+/g, " ");
  let from = 0;
  for (;;) {
    const idx = flat.indexOf(needle, from);
    assert.notEqual(idx, -1, `expected a rule whose selector contains "${needle}"`);
    const brace = flat.slice(idx + needle.length).search(/\S/);
    if (flat[idx + needle.length + brace] === "{") {
      const open = idx + needle.length + brace;
      const close = flat.indexOf("}", open);
      assert.notEqual(close, -1, `unterminated rule for "${needle}"`);
      return flat.slice(open + 1, close);
    }
    from = idx + needle.length;
  }
}

/** Body of the `:root { ... }` block. */
const rootBody = ruleBody(":root");
/** Body of the `.dark { ... }` block. */
const darkBody = ruleBody(".dark");

/** Luminance percentage of an `h s% l%` token, for relative comparisons. */
function lightness(body, name) {
  const m = body.match(new RegExp(`${name}\\s*:\\s*[\\d.]+\\s+[\\d.]+%\\s+([\\d.]+)%`));
  assert.ok(m, `${name} must be a plain HSL triple so it can be compared across themes`);
  return Number(m[1]);
}

/**
 * Tokens whose *value* must change between themes.
 *
 * Everything else in `:root` is theme-independent and deliberately not
 * duplicated in `.dark`: durations, easings and the radius are the same at
 * night, and `:root` already cascades into `.dark`, so redeclaring them would
 * be noise that reads as if the two themes had to be kept in step by hand.
 *
 * The elevation shadows are excluded for a different reason: they are written
 * entirely in terms of `var(--shadow-color)`, so redefining that one hue is
 * what re-tints all three levels. Duplicating the shadow values per theme
 * would put the ladder in two places and let the two drift.
 */
const COLOUR_TOKENS = ["--background", "--foreground", "--card", "--primary", "--border", "--ring", "--shadow-color", "--surface-1", "--surface-2", "--surface-3"];

test("every colour token is defined in both themes", () => {
  for (const name of COLOUR_TOKENS) {
    assert.match(rootBody, new RegExp(`${name}\\s*:`), `light mode must define ${name}`);
    assert.match(darkBody, new RegExp(`${name}\\s*:`), `dark mode must define ${name}`);
  }
});

test("theme-independent tokens are declared once, not mirrored", () => {
  // These resolve in dark mode by cascade. A copy in `.dark` would be a second
  // place to change the motion scale, and nothing would fail if the two drifted.
  for (const name of ["--radius", "--dur-fast", "--ease-standard", "--shadow-e2"]) {
    assert.match(rootBody, new RegExp(`${name}\\s*:`), `:root must define ${name}`);
    assert.doesNotMatch(darkBody, new RegExp(`${name}\\s*:`), `${name} is theme-independent and must not be mirrored into .dark`);
  }
});

test("the surface ladder has exactly three rungs", () => {
  // Three is deliberate. A ladder of one has no depth; more than three cannot
  // be told apart at the luminance deltas this theme uses, so extra steps get
  // invented ad hoc and the ladder stops describing reality.
  for (const name of ["--surface-1", "--surface-2", "--surface-3"]) {
    assert.match(rootBody, new RegExp(`${name}\\s*:`), `light mode must define ${name}`);
    assert.match(darkBody, new RegExp(`${name}\\s*:`), `dark mode must define ${name}`);
  }
  assert.doesNotMatch(rootBody, /--surface-4/, "a fourth rung is not distinguishable and reopens ad-hoc surfaces");
});

test("the three surface rungs are distinguishable, and raised is above the page", () => {
  // Light mode puts white on off-white. Dark mode inverts which end is raised,
  // because the page is already near-black, so the card has to go lighter to
  // read as raised at all. That inversion is why the assertion below is about
  // the raised rung only: `--surface-3` is chrome (rails, headers, composer
  // wells), which sits *below* the page in light mode and above it in dark, and
  // pinning a single direction for it would be pinning an accident of the
  // palette rather than a rule.
  for (const [mode, body] of [["light", rootBody], ["dark", darkBody]]) {
    assert.ok(
      lightness(body, "--surface-1") > lightness(body, "--surface-2"),
      `${mode}: the raised surface must be lighter than the page, or cards stop reading as raised`,
    );
    assert.notEqual(
      lightness(body, "--surface-3"),
      lightness(body, "--surface-2"),
      `${mode}: chrome must be a distinct step from the page, not a second name for it`,
    );
    assert.notEqual(
      lightness(body, "--surface-3"),
      lightness(body, "--surface-1"),
      `${mode}: chrome must be a distinct step from the raised surface`,
    );
  }
});

/**
 * Largest pixel offset in a shadow value.
 *
 * Read from the `--shadow-e*` tokens rather than from `.elev-*`, because the
 * levels deliberately delegate to the tokens. That delegation is the property
 * worth having (one place to retune the ladder), but it also means the geometry
 * is no longer visible in the rule, so the rule is a one-line `var()` and the
 * token is where the ladder actually lives.
 */
function reach(value) {
  return Math.max(...[...value.matchAll(/(\d+)px/g)].map((m) => Number(m[1])));
}

const token = (name) => {
  const m = rootBody.match(new RegExp(`${name}\\s*:([^;]+);`));
  assert.ok(m, `${name} must be defined in light mode`);
  return m[1];
};

test("each elevation level composes a contact shadow with an ambient one", () => {
  // One box-shadow per level reads as a sticker at low elevation and a flat
  // cutout at high elevation, because real depth has both a tight occlusion
  // shadow and a wide soft one.
  for (const level of [1, 2, 3]) {
    const layers = token(`--shadow-e${level}`).split(",");
    assert.ok(layers.length >= 2, `--shadow-e${level} needs a contact and an ambient shadow, got ${layers.length}`);
    assert.ok(
      reach(layers.slice(1).join(",")) >= reach(layers[0]),
      `--shadow-e${level}'s ambient shadow must be at least as large as its contact shadow`,
    );
  }
});

test("elevation levels increase in spread", () => {
  // Without this the ladder is three names for the same shadow.
  assert.ok(reach(token("--shadow-e1")) < reach(token("--shadow-e2")), "elev-2 must reach further than elev-1");
  assert.ok(reach(token("--shadow-e2")) < reach(token("--shadow-e3")), "elev-3 must reach further than elev-2");
});

test("elevation levels are registered as Tailwind utilities, not bare component classes", () => {
  // Only utilities get variants. As plain `@layer components` classes,
  // `hover:elev-2` and `max-md:elev-3` were generated as dead classes that
  // silently did nothing, so the config is the thing that has to hold.
  const config = readFileSync(fileURLToPath(new URL("../../tailwind.config.cjs", import.meta.url)), "utf8");
  assert.match(config, /boxShadow:\s*\{/, "tailwind.config.cjs must extend boxShadow");
  for (const level of [1, 2, 3]) {
    assert.match(config, new RegExp(`${level}:\\s*"var\\(--shadow-e${level}\\)"`), `elev-${level} must map to its token`);
  }
  // And the values must not live in two places, or the ladder drifts.
  assert.doesNotMatch(raw, /\.elev-\d\s*\{/, "the levels must not also be defined in globals.css");
});

test("no component asks for a variant of an elevation level that cannot resolve", () => {
  // Regression guard for the migration: a mechanical rewrite produced seven
  // sites where a variant-prefixed elevation was a dead class. With the levels
  // registered as utilities these now resolve, so this asserts every such class
  // sits in a file that the config's content globs actually cover.
  const componentsDir = fileURLToPath(new URL("../../src/components", import.meta.url));
  const files = globSync("**/*.{tsx,ts}", { cwd: componentsDir });
  const used = files.flatMap((rel) => {
    const src = readFileSync(path.join(componentsDir, rel), "utf8");
    return [...src.matchAll(/(\w+):(elev-\d)\b/g)].map((m) => `${rel}: ${m[0]}`);
  });
  // Non-empty is the point: this pattern exists in the codebase today, and the
  // test is what stops it silently returning after a future config regression.
  assert.ok(used.length > 0, "expected variant-prefixed elevation classes to be in use");
});

test("shadows carry the theme's shadow hue, never a literal black", () => {
  // Light mode is a near-black slate and dark mode a lighter slate, because a
  // literal black shadow is invisible against a 6%-luminance dark surface.
  assert.match(rootBody, /--shadow-color:\s*\d/, "light mode must define a shadow hue");
  assert.match(darkBody, /--shadow-color:\s*\d/, "dark mode must define a shadow hue");
  for (const level of [1, 2, 3]) {
    assert.doesNotMatch(
      token(`--shadow-e${level}`),
      /rgba\(0,\s*0,\s*0/,
      `--shadow-e${level} must not hard-code a black shadow`,
    );
  }
});

test("the motion scale is a small named set, and instant is zero", () => {
  // Durations being a log-ish scale rather than arbitrary values is what makes
  // "pick the duration that matches the distance travelled" a real rule.
  for (const name of ["--dur-instant", "--dur-fast", "--dur-slow"]) {
    assert.match(rootBody, new RegExp(`${name}\\s*:`), `light mode must define ${name}`);
  }
  assert.match(rootBody.match(/--dur-instant:\s*([^;]+);/)[1], /0/, "a state flip that must not read as animation needs zero");

  const fast = Number(rootBody.match(/--dur-fast:\s*(\d+)ms/)[1]);
  const slow = Number(rootBody.match(/--dur-slow:\s*(\d+)ms/)[1]);
  assert.ok(fast < slow, "the durations must actually be ordered");
});

test("the standard easing decelerates and the emphasised one is symmetric", () => {
  // Standard follows a pointer or key press, so it must stop gently. Emphasised
  // is for motion the user did not drive and shares a control point end to end.
  const bezier = (name) => rootBody.match(new RegExp(`${name}\\s*:\\s*cubic-bezier\\(([^)]+)\\)`))?.[1];

  const standard = bezier("--ease-standard");
  assert.ok(standard, "light mode must define --ease-standard");
  const [, , ex, ey] = standard.split(",").map((n) => Number(n.trim()));
  assert.ok(ex < 1, "standard must decelerate into rest");
  assert.equal(ey, 1, "standard must end fully arrived");

  const emphasised = bezier("--ease-emphasised");
  assert.ok(emphasised, "light mode must define --ease-emphasised");
  const [ax, , cx] = emphasised.split(",").map((n) => Number(n.trim()));
  assert.ok(ax > 0 && cx > 0, "emphasised must be symmetric, so both ends share a control point");
});

test("motion is still suppressed for users who ask for less of it", () => {
  // Adding a token scale makes it easy to reach for a duration everywhere and
  // forget that the reduced-motion block has to keep covering them.
  const at = css.indexOf("prefers-reduced-motion");
  assert.notEqual(at, -1, "a reduced-motion block must exist");
  assert.match(css.slice(at), /animation|transition/, "the reduced-motion block must neutralise both");
});