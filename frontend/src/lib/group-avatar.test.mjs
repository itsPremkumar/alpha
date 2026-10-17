// group-avatar.test.mjs — the group picture is DERIVED, deterministic and
// honestly labelled.
//
// There is no server-side group picture, so this module turns the room name into
// a glyph. The properties worth pinning are the ones that stop it from being read
// as a stored setting or as one of the group's members.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const requireFromHere = createRequire(import.meta.url);
const ts = requireFromHere("typescript");

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "group-avatar.ts"), "utf8");

const compiled = ts.transpileModule(src, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;

// Any import would be a second implementation of this derivation beside the
// real one, so the module must stay dependency-free.
const require = () => {
  throw new Error("group-avatar.ts must stay dependency-free");
};
const moduleShim = { exports: {} };
new Function("require", "module", "exports", compiled)(
  require,
  moduleShim,
  moduleShim.exports,
);

const { groupAvatarEmoji, GROUP_AVATAR_GLYPHS, GROUP_AVATAR_GENERATED_TITLE } =
  moduleShim.exports;

test("the same room name always yields the same picture", () => {
  for (const name of ["release-train", "platform-incidents", "brand-refresh"]) {
    assert.equal(
      groupAvatarEmoji(name),
      groupAvatarEmoji(name),
      `${name} must be stable across calls`,
    );
  }
});

test("every drawn picture comes from the palette", () => {
  const names = ["a", "release-train", "platform-incidents", "sample", "ops"];
  for (const name of names) {
    assert.ok(
      GROUP_AVATAR_GLYPHS.includes(groupAvatarEmoji(name)),
      `${name} produced a glyph outside the palette`,
    );
  }
});

test("different rooms do not all collapse to one picture", () => {
  // A palette that always returned the same glyph would be a decoration, not a
  // way to tell rooms apart.
  const drawn = new Set(
    ["release-train", "platform-incidents", "brand-refresh", "sample", "ops"].map(
      (n) => groupAvatarEmoji(n),
    ),
  );
  assert.ok(drawn.size > 1, "every room drew the same picture");
});

test("a nameless room gets no picture rather than a fabricated one", () => {
  // "" has no identity to derive from; drawing a glyph anyway would look chosen
  // but stand for nothing, so the caller keeps its generic group icon.
  assert.equal(groupAvatarEmoji(""), null);
  assert.equal(groupAvatarEmoji("   "), null);
  assert.equal(groupAvatarEmoji(null), null);
  assert.equal(groupAvatarEmoji(undefined), null);
});

test("whitespace does not change which picture a room gets", () => {
  assert.equal(
    groupAvatarEmoji("release-train"),
    groupAvatarEmoji(" release-train "),
  );
});

test("the palette stays away from people emoji", () => {
  // Agent profile pictures are people/object glyphs; a group picture that looked
  // like a member's face would be a claim the surface cannot support.
  const peopleish = ["👨", "👩", "👨‍💻", "👩‍💻", "🧑", "👤", "🙂"];
  for (const glyph of GROUP_AVATAR_GLYPHS) {
    assert.ok(
      !peopleish.includes(glyph),
      `${glyph} reads as a person, which a group picture must not`,
    );
  }
});

test("the palette glyphs are distinct", () => {
  assert.equal(
    new Set(GROUP_AVATAR_GLYPHS).size,
    GROUP_AVATAR_GLYPHS.length,
    "a repeated glyph would shrink the palette without meaning",
  );
});

test("the generated picture is labelled as generated", () => {
  // A generated picture must never read as one somebody chose.
  assert.match(GROUP_AVATAR_GENERATED_TITLE, /generated from the room name/i);
  assert.match(GROUP_AVATAR_GENERATED_TITLE, /no picture has been set/i);
});