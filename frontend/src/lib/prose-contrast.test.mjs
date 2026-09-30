// prose-contrast.test.mjs — the assistant's reply must be readable in the
// theme the app actually ships.
//
// The defect this pins: `.response-prose` wrote its palette out per selector in
// raw dark-surface values — `rgb(226 232 240)` body, `rgb(248 250 252)`
// headings, `rgb(125 211 252)` links — in both themes. `:root` is light
// (`--background: 220 20% 98%`) and `theme.ts` only adds `.dark` when the
// resolved theme is dark, so under the default theme an answer sat at roughly
// 1.2:1 against the transcript it renders on: the product's core output was
// invisible. Nothing caught it, because no suite rendered or measured the
// shipped stylesheet.
//
// Two kinds of assertion, both against the real files:
//   1. the *source* — the light-default `.response-prose` block may not carry a
//      colour literal; every text colour has to be a theme-derived token;
//   2. the *numbers* — WCAG contrast computed from the actual `:root`/`.dark`
//      HSL tokens, so a future token edit that quietly drops a theme below 4.5:1
//      fails here rather than in a screenshot.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const CSS = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

/** The declarations of the first rule matching `selector`, before `.dark` re-declarations. */
function rule(selector) {
  const start = CSS.indexOf(selector);
  assert.notEqual(start, -1, `expected a \`${selector}\` rule in globals.css`);
  const open = CSS.indexOf("{", start);
  const close = CSS.indexOf("}", open);
  return CSS.slice(open + 1, close);
}

/** Parse `--name: H S% L%;` triples (optionally space-separated) out of a rule body. */
function hslTokens(body) {
  const out = {};
  for (const line of body.split("\n")) {
    const m = /^\s*(--[a-z0-9-]+):\s*([\d.]+) ([\d.]+)% ([\d.]+)%\s*;/.exec(line);
    if (m) out[m[1]] = [Number(m[2]), Number(m[3]) / 100, Number(m[4]) / 100];
  }
  return out;
}

function rgbFromHsl([h, s, l]) {
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  const [r1, g1, b1] = hp < 1 ? [c, x, 0] : hp < 2 ? [x, c, 0] : hp < 3 ? [0, c, x] : hp < 4 ? [0, x, c] : hp < 5 ? [x, 0, c] : [c, 0, x];
  const m = l - c / 2;
  return [r1 + m, g1 + m, b1 + m];
}

function luminance(rgb) {
  const [r, g, b] = rgb.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Tokens for a theme root: `:root` overlaid with `.dark` where it re-declares. */
function themeTokens(dark) {
  const root = hslTokens(rule(":root {"));
  const darkBody = hslTokens(rule(".dark {"));
  return dark ? { ...root, ...darkBody } : root;
}

test("the reply palette is theme-derived, not a painted dark surface", () => {
  // The light-default block: from the first `.response-prose {` up to the
  // `.dark` re-declaration that legitimately carries code-block chrome.
  const first = CSS.indexOf(".response-prose {");
  const darkAt = CSS.indexOf(".dark .response-prose");
  assert.notEqual(first, -1, "globals.css must declare .response-prose");
  assert.notEqual(darkAt, -1, "the dark code chrome must be re-declared under .dark");
  const lightBlock = CSS.slice(first, darkAt);
  // Strip comments first: the block's own doc comment quotes the retired
  // literals in order to say why they are gone, and a guard that reads
  // comments as declarations would push the explanation out of the file.
  const declarations = lightBlock.replace(/\/\*[\s\S]*?\*\//g, "");

  assert.doesNotMatch(
    declarations,
    /rgb\(|#[0-9a-fA-F]{3,8}\b/,
    "the light-default .response-prose block may not paint a colour literal; " +
      "declare a var(--prose-*) token from the theme instead (raw slate here is " +
      "what made replies unreadable in the light theme)",
  );

  // Every text-bearing selector has to read a token, not inherit a literal
  // from somewhere further up the sheet.
  for (const selector of [".response-prose h1,", ".response-prose strong {", ".response-prose a {", ".response-prose li::marker {"]) {
    const at = CSS.indexOf(selector);
    assert.notEqual(at, -1, `expected \`${selector.trim()}\` to still exist`);
    const body = CSS.slice(at, CSS.indexOf("}", at));
    assert.match(body, /var\(--prose-/, `\`${selector.trim()}\` must use a theme token`);
  }
});

test("light theme keeps body text and links above 4.5:1 on the transcript", () => {
  for (const dark of [false, true]) {
    const tokens = themeTokens(dark);
    const surface = rgbFromHsl(tokens["--background"]);
    const label = dark ? "dark" : "light";

    const body = contrast(rgbFromHsl(tokens["--foreground"]), surface);
    assert.ok(
      body >= 4.5,
      `${label}: --foreground on --background is ${body.toFixed(2)}:1, below the 4.5:1 body-text minimum`,
    );

    // Links render at `var(--prose-link)` = `var(--primary)`.
    const link = contrast(rgbFromHsl(tokens["--primary"]), surface);
    assert.ok(
      link >= 4.5,
      `${label}: --primary (reply links) on --background is ${link.toFixed(2)}:1, below the 4.5:1 minimum`,
    );
  }
});
