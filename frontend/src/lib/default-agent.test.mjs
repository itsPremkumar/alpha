// default-agent.test.mjs — the default agent's identity: one name, one face,
// and no surface writing either of them by hand.
//
// Why this file exists: the agent that answers when nobody picked a bot used to
// be labelled `Lead Agent` — an internal architecture term (the orchestrating
// agent of `alpha.agents.lead_agent`) leaking into the product as if it were a
// name, with a generic robot glyph standing in for its face. On a fresh
// install, for every user of this project, that IS who they are talking to, so
// it is named for the product and faces the product's own lion.
//
// The failure this pins is a *split* one, and no single file is wrong when it
// happens: the header says one word, the dropdown says another, and the avatar
// silently 404s as a broken image — all with a green suite, because each file
// still compiles and each assertion still holds in isolation. So the pins are
// written as cross-file agreements: the name against `branding.name`, the
// avatar against `branding.icons` *and* the file on disk, and the component
// tree against the literal it must no longer contain.
//
// Pure Node test (node --test src/lib/default-agent.test.mjs): transpiles the
// source modules with the repo's own TypeScript compiler and reads files from
// disk. No network, no browser, no bundler, no image decoding.
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const publicDir = path.join(frontendRoot, "public");
const roots = ["components", "app"].map((dir) => path.join(frontendRoot, "src", dir));

const transpile = (source) =>
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;

const asDataUrl = (code) => `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;

// `default-agent.ts` imports `./branding`, and a `data:` URL has no directory
// against which a relative specifier resolves. Rather than mock the dependency
// — which would test a stub instead of the values the app reads — the
// dependency is transpiled first and its own data URL is spliced into the
// import. Everything downstream is the shipped source.
const brandingUrl = asDataUrl(transpile(readFileSync(new URL("./branding.ts", import.meta.url), "utf8")));
const defaultAgentWired = transpile(readFileSync(new URL("./default-agent.ts", import.meta.url), "utf8")).replace(
  'from "./branding"',
  `from "${brandingUrl}"`,
);
const { branding } = await import(brandingUrl);
const { DEFAULT_AGENT_NAME, DEFAULT_AGENT_AVATAR, DEFAULT_AGENT_HINT, DEFAULT_AGENT_ROUTING_LINE } = await import(
  asDataUrl(defaultAgentWired)
);

/** Every `.tsx` in the render layer, sorted so a failure list is stable. */
function componentSources() {
  const files = [];
  for (const root of roots) {
    for (const rel of readdirSync(root, { recursive: true, encoding: "utf8" })) {
      if (rel.endsWith(".tsx")) files.push(path.join(root, rel));
    }
  }
  return files.sort();
}

/** Comments removed, because a comment may legitimately record what was removed. */
function uncommented(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

test("the default agent is named for the product, and the two agree today", () => {
  assert.equal(typeof DEFAULT_AGENT_NAME, "string");
  assert.ok(DEFAULT_AGENT_NAME.trim().length > 0, "the default agent must have a name");

  // Deliberately a value equality, not an alias. `default-agent.ts` keeps its
  // own constant so renaming the product cannot silently rename the agent (or
  // the reverse); this assertion is the thing that notices they have drifted
  // apart, and it is meant to fail loudly so a human decides which one moves.
  assert.equal(
    DEFAULT_AGENT_NAME,
    branding.name,
    "the default agent and the product are both '" +
      branding.name +
      "' today. If one is renamed, rename the other on purpose or change this " +
      "assertion on purpose - never leave the header and the dropdown saying " +
      "different words.",
  );
});

test("the default agent's face is a generated brand mark that exists on disk", () => {
  assert.equal(
    DEFAULT_AGENT_AVATAR,
    branding.icons.pwa512,
    "the avatar must be read out of branding.icons, never written as a second copy of a path; " +
      "a path here would be invisible to branding.test.mjs's generator-agreement pin",
  );

  const file = path.join(publicDir, DEFAULT_AGENT_AVATAR.replace(/^\//, ""));
  assert.ok(existsSync(file), `the default agent's avatar is missing from public/: ${file}`);

  // Why this mark and not one of the others: `assets/images/alpha.png` is the
  // poster, which carries the "ALPHA" wordmark and the AUTONOMOUS · INTELLIGENT
  // · EVOLVING tagline — unreadable at a 24px avatar — and the maskable mark
  // reserves a launcher safe zone that would shrink the lion inside the circle.
  // The 512 PWA mark is the same poster cropped to the mane, with the type
  // removed. The claim "legible at avatar size" is NOT machine-checked here:
  // this suite decodes no images, so it is recorded as a visual observation
  // rather than presented as a passing assertion.
  assert.equal(DEFAULT_AGENT_AVATAR, branding.icons.pwa512);
});

test("the hint answers the question the un-labelled state poses", () => {
  assert.ok(
    DEFAULT_AGENT_HINT.includes(DEFAULT_AGENT_NAME),
    "the tooltip must name who is handling the conversation, or it explains the absence without naming a presence",
  );
  assert.match(DEFAULT_AGENT_HINT, /auto-routes/);
  assert.match(DEFAULT_AGENT_HINT, /No specialist selected/);
  assert.equal(
    DEFAULT_AGENT_ROUTING_LINE,
    `${DEFAULT_AGENT_NAME} auto-routes`,
    "the routing line must be derived from the name, never a second literal copy of it",
  );
});

test("every no-bot branch renders that face, not a generic glyph", () => {
  // The avatar constant existing proves nothing about whether it is wired; a
  // rename that leaves the picker on a robot icon would still pass every other
  // assertion here. These are the sites an operator sees when no specialist has
  // been selected - each one was caught by looking at the rendered page rather
  // than at the grep for the name.
  const sites = {
    "../components/chat-shell/Honest.tsx": "LeadGlyph, shared by the header, the rail and the breadcrumb",
    "../components/chat-shell/ProjectContextHeader.tsx": "the large agent tile and the breadcrumb glyph",
    "../components/bots/ActiveBotPicker.tsx": "the header's collapsed picker",
    "../components/chat-shell/ChatShellLanding.tsx": "the empty-state hero",
    "../components/chat-shell/BotDropdownMenu.tsx": "the dropdown trigger, its summary header and its default-agent row",
    "../components/chat-shell/BotWorkspaceRail.tsx": "the rail's current-agent tile",
  };
  for (const [rel, why] of Object.entries(sites)) {
    const src = readFileSync(new URL(rel, import.meta.url), "utf8");
    assert.match(src, /DEFAULT_AGENT_AVATAR/, `${rel} must render the default agent's avatar (${why})`);
  }

  // `LeadGlyph` is the shared glyph the header, the rail and the project
  // context all render, so it is the one that must actually carry the image.
  const honest = readFileSync(new URL("../components/chat-shell/Honest.tsx", import.meta.url), "utf8");
  assert.match(honest, /<img src=\{DEFAULT_AGENT_AVATAR\}/, "LeadGlyph must render the lion image");
  assert.doesNotMatch(
    honest,
    /<Bot className="size-3\.5 text-primary"/,
    "the generic robot glyph must not come back as the default agent's face",
  );
});

test("no surface in the render layer writes the old internal name inline", () => {
  // The literal this replaces was `Lead Agent`. It must not reappear as a
  // hand-written string anywhere an operator can read, because a second copy of
  // the name is exactly how the header and the dropdown start disagreeing —
  // and nothing fails when that happens.
  const files = componentSources();
  assert.ok(files.length > 50, `expected the component tree, found only ${files.length} files`);

  const offenders = [];
  for (const file of files) {
    if (/\bLead Agent\b/.test(uncommented(readFileSync(file, "utf8")))) {
      offenders.push(path.relative(frontendRoot, file));
    }
  }
  assert.deepEqual(
    offenders,
    [],
    "renders the internal term 'Lead Agent' to an operator; the default agent's name " +
      `is DEFAULT_AGENT_NAME (${DEFAULT_AGENT_NAME}): ${offenders.join(", ")}`,
  );
});

test("every surface that shows the name reads it from the constant", () => {
  // A list, not a scan: these are the sites where "who is talking" is stated,
  // each paired with what an operator reads there. A surface dropped from the
  // constant keeps compiling and keeps rendering — it just renders the wrong
  // word, which is the split failure this whole file exists to catch.
  const surfaces = {
    "components/bots/ActiveBotPicker.tsx": "the header's agent picker, collapsed and expanded",
    "components/chat-shell/BotDropdownMenu.tsx": "the dropdown's default-agent row",
    "components/chat-shell/BotWorkspaceRail.tsx": "the rail's current-agent label",
    "components/chat-shell/ChatShell.tsx": "the chat header",
    "components/chat-shell/ChatShellLanding.tsx": "the empty-state hero",
    "components/chat-shell/ProjectContextHeader.tsx": "the breadcrumb's agent segment",
    "components/chat-shell/ProjectDetailPanel.tsx": "the project's agent row",
    "components/sections/ProjectsSection.tsx": "the project list's agent label",
    "components/sections/ScheduledSection.tsx": "the scheduler's target picker",
    "components/ChatView.tsx": "the reset action",
    "lib/chat-shell.ts": "the context sentence under a new conversation",
  };
  const missing = [];
  for (const [rel, why] of Object.entries(surfaces)) {
    const src = readFileSync(path.join(frontendRoot, "src", rel), "utf8");
    if (!uncommented(src).includes("DEFAULT_AGENT_NAME")) missing.push(`${rel} (${why})`);
  }
  assert.deepEqual(missing, [], `these surfaces show the default agent but do not read its name: ${missing.join(", ")}`);
});
