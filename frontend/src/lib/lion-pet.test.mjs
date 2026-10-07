import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";
import { moduleUrl } from "./test-modules.mjs";

const source = readFileSync(new URL("../components/lion-pet/lion-pet-model.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const pet = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

const componentSource = readFileSync(new URL("../components/lion-pet/LionPet.tsx", import.meta.url), "utf8");
const chatSource = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");

test("lion settings are bounded and local-first", () => {
  const settings = pet.normalizeLionPetSettings({
    visible: false,
    scale: 99,
    sound: true,
    position: { right: -20, bottom: 400 },
  });
  assert.deepEqual(settings, {
    visible: false,
    scale: 1.35,
    sound: true,
    desktopOverlay: false,
    autonomousActions: true,
    skin: "golden",
    position: { right: 0, bottom: 96 },
  });
  assert.equal(pet.clampLionPetScale("not-a-number"), 1);
  assert.equal(pet.clampLionPetScale(null), 1);
  assert.equal(pet.readLionPetSettings(null).visible, true);
});

test("lion state messages are friendly and do not require prompt content", () => {
  assert.equal(pet.lionPetMessage("working"), "I'm on it. Roaring quietly.");
  assert.equal(pet.lionPetMessage("waiting"), "I found a decision point for you.");
  assert.equal(pet.lionPetActionLabel("run"), "Running");
  assert.equal(pet.lionPetActionLabel("prowl"), "Prowling");
  assert.equal(pet.lionPetActionMessage("jump"), "Up, up, and over the next task.");
  assert.equal(pet.lionPetActionMessage("hunt"), "I am studying the problem from every angle.");
  assert.deepEqual(pet.LION_PET_ACTIONS, [
    "idle", "walk", "run", "jump", "roar", "pounce", "play", "sleep", "stretch",
    "prowl", "hunt", "shake", "spin",
  ]);
  assert.equal(pet.isLionSkinId("midnight"), true);
  assert.equal(pet.isLionSkinId("not-a-skin"), false);
  assert.equal(pet.sanitizeLionPetMessage("  hello\nthere  ", "fallback"), "hello there");
  assert.equal(pet.sanitizeLionPetMessage("\u0000  ", "fallback"), "fallback");
  assert.equal(pet.sanitizeLionPetMessage("x".repeat(300), "fallback").length, 160);
});

test("lion component exposes state reactions, local controls, and desktop bridge wiring", () => {
  assert.match(componentSource, /LionIllustration/);
  assert.match(componentSource, /thinking/);
  assert.match(componentSource, /working/);
  assert.match(componentSource, /success/);
  assert.match(componentSource, /error/);
  assert.match(componentSource, /onPointerMove/);
  assert.match(componentSource, /setLionPetVisible/);
  assert.match(componentSource, /onLionPetVisibility/);
  assert.match(componentSource, /Desktop overlay/);
  assert.match(componentSource, /Windows app only/);
  assert.match(componentSource, /Sound cues/);
  assert.match(componentSource, /no prompt data stored/);
  assert.match(componentSource, /Lion look/);
  assert.match(componentSource, /Try an action/);
  assert.match(componentSource, /Automatic actions/);
  assert.match(componentSource, /lion-pet-skin-grid/);
  assert.match(componentSource, /lion-pet-action-grid/);
  assert.match(componentSource, /lion-upper-leg/);
  assert.match(componentSource, /lion-lower-leg/);
  assert.match(componentSource, /lion-roar-mouth/);
  assert.match(componentSource, /lion-whiskers/);
  assert.match(componentSource, /lion-torso/);
  assert.match(componentSource, /MOTION_RULES/);
  assert.match(componentSource, /requestAnimationFrame/);
  assert.match(componentSource, /lion-pet-travel-x/);
});

test("ChatView maps the real run lifecycle into companion states", () => {
  assert.match(chatSource, /updateLion\("thinking"/);
  assert.match(chatSource, /updateLion\("working"/);
  assert.match(chatSource, /updateLion\("success"/);
  assert.match(chatSource, /updateLion\("error"/);
  assert.match(chatSource, /updateLion\(\s*"waiting"/);
  assert.match(chatSource, /<LionPet/);
});

test("the main workspace consumes only the lion activity adapter", () => {
  assert.match(chatSource, /useLionPetActivity/);
  assert.doesNotMatch(chatSource, /LION_PET_SKINS|data-lion-action|lion-pet.css/);
});

test("the pet is a fixed, local companion without a remote asset URL", () => {
  assert.match(componentSource, /lion-pet-shell/);
  assert.doesNotMatch(componentSource, /https?:\/\//);
  assert.doesNotMatch(componentSource, /threadId|data-thread-id/);
  assert.match(componentSource, /viewBox="0 0 220 210"/);
});

/**
 * The companion's hit area sets `pointer-events: auto`, so wherever it sits it
 * intercepts clicks meant for whatever is under it. Measured in a live browser,
 * the pet's default position overlapped the chat composer, so it sat on the input
 * box and swallowed clicks aimed at the one control the user cannot work around.
 *
 * These pin the keep-out arithmetic; the wiring pins below check that the travel
 * commit, the drag handler and the mount/resize correction all go through it.
 */
test("lion-pet overlap detection honours a keep-out gap", () => {
  const a = { left: 339, right: 529, top: 417, bottom: 610 };
  assert.equal(pet.lionPetOverlaps(a, { left: 279, right: 889, top: 494, bottom: 530 }), true);

  // Resting exactly against the edge, with no overlap, is clear.
  assert.equal(pet.lionPetOverlaps(a, { left: 529, right: 900, top: 400, bottom: 600 }, 0), false);
  // A gap widens the exclusion band.
  assert.equal(pet.lionPetOverlaps(a, { left: 540, right: 900, top: 400, bottom: 600 }, 0), false);
  assert.equal(pet.lionPetOverlaps(a, { left: 540, right: 900, top: 400, bottom: 600 }, 12), true);
});

test("a pet sitting on the composer is moved off it", () => {
  // Real measured geometry: 1125x797 viewport, 190x193 pet, composer across the
  // middle. The requested offset is the shipped default, which overlapped.
  const composer = { left: 279, right: 889, top: 494, bottom: 530 };
  const safe = pet.resolveLionPetSafeRight({
    desiredRight: 596,
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 1125,
    viewportHeight: 797,
    petBottom: 187,
    keepOut: composer,
  });
  const rect = (right) => ({
    left: 1125 - right - 190,
    right: 1125 - right,
    top: 797 - 187 - 193,
    bottom: 797 - 187,
  });
  assert.equal(pet.lionPetOverlaps(rect(safe), composer), false, `still overlapping at right=${safe}`);
});

test("a pet already clear is left exactly where it is", () => {
  const out = pet.resolveLionPetSafeRight({
    desiredRight: 12,
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 1125,
    viewportHeight: 797,
    petBottom: 12,
    keepOut: { left: 279, right: 889, top: 494, bottom: 530 },
  });
  assert.equal(out, 12, "a clear position must not be nudged - that would make the pet drift on every resize");
});

test("no keep-out, or no viewport, leaves the requested position untouched", () => {
  const base = {
    desiredRight: 400,
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 1125,
    viewportHeight: 797,
    petBottom: 100,
  };
  assert.equal(pet.resolveLionPetSafeRight({ ...base, keepOut: null }), 400);
  assert.equal(pet.resolveLionPetSafeRight({ ...base, viewportWidth: 0, keepOut: { left: 0, right: 10, top: 0, bottom: 10 } }), 400);
});

test("an unreachable keep-out does not hide the pet", () => {
  // A keep-out wider than the viewport cannot be cleared. Returning the requested
  // offset keeps the companion visible rather than snapping it somewhere odd.
  const out = pet.resolveLionPetSafeRight({
    desiredRight: 200,
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 400,
    viewportHeight: 800,
    petBottom: 10,
    keepOut: { left: -500, right: 900, top: 0, bottom: 800 },
  });
  assert.equal(out, 200);
});

test("the safe offset never leaves the viewport", () => {
  for (let desired = 0; desired <= 1000; desired += 37) {
    const out = pet.resolveLionPetSafeRight({
      desiredRight: desired,
      petWidth: 190,
      petHeight: 193,
      viewportWidth: 1125,
      viewportHeight: 797,
      petBottom: 187,
      keepOut: { left: 279, right: 889, top: 494, bottom: 530 },
    });
    assert.ok(out >= 0 && out <= 1125 - 190, `right=${out} out of range for desired=${desired}`);
  }
});

test("the keep-out element is the composer, and every path that moves the pet goes through the guard", () => {
  const composerSource = readFileSync(new URL("../components/Composer.tsx", import.meta.url), "utf8");
  assert.match(composerSource, /data-lion-pet-keepout/, "the composer must be marked as a keep-out region");

  // Travel commit, drag, and mount/resize must each be corrected - a single
  // guarded path still lets the other two park the pet on the input. The mount
  // path uses the two-axis resolver because the horizontal sweep alone cannot
  // clear the real layout; the other two stay horizontal so a drag or a wander
  // never teleports the pet vertically.
  const guarded = (componentSource.match(/resolveLionPetSafeRight\(/g) || []).length
    + (componentSource.match(/resolveLionPetPlacement\(/g) || []).length;
  assert.equal(guarded, 3, `expected travel, drag and mount correction, found ${guarded}`);
  assert.match(componentSource, /findLionPetKeepOuts\(document\)/);
  assert.match(componentSource, /addEventListener\("resize"/);
});

/**
 * A modal drawer is the second keep-out, and the same `pointer-events: auto`
 * hazard applies: the pet is `z-90` and a drawer is `z-50`, so an open drawer
 * puts the companion on top of the drawer, not behind it.
 *
 * Found in the live UI: the bot profile drawer opened and the pet sat inside it,
 * covering the agent's Soul text, with its speech bubble over the drawer's own
 * copy.
 */
test("an open dialog is a keep-out region", () => {
  const dialog = { getBoundingClientRect: () => ({ left: 464, right: 912, top: 0, bottom: 670, width: 448, height: 670 }) };
  const doc = {
    // No marked element: this is the drawer case, not the composer/sidebar case.
    querySelector: () => null,
    querySelectorAll: (sel) => (String(sel).includes("role") ? [dialog] : []),
  };
  const rects = pet.findLionPetKeepOuts(doc);
  assert.equal(rects.length, 1, "an open dialog must be treated as a keep-out");
  assert.deepEqual(rects[0], { left: 464, right: 912, top: 0, bottom: 670 });

  // And the pet is moved clear of it, using real measured geometry.
  const safe = pet.resolveLionPetSafeRight({
    desiredRight: 341, // the pet sat at x=381..571, i.e. straddling the drawer edge
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 912,
    viewportHeight: 670,
    petBottom: 227,
    keepOut: rects,
  });
  const petRect = (right) => ({
    left: 912 - right - 190,
    right: 912 - right,
    top: 670 - 227 - 193,
    bottom: 670 - 227,
  });
  assert.equal(pet.lionPetOverlaps(petRect(safe), rects[0]), false, `still inside the drawer at right=${safe}`);
});

test("every keep-out counts, not just the first", () => {
  // The composer and the sidebar footer are two separate regions and the pet
  // must clear BOTH. A single-rect keep-out silently ignored the second, which
  // is how the pet ended up on top of Backup and Restore.
  const composer = { getBoundingClientRect: () => ({ left: 268, right: 988, top: 483, bottom: 651, width: 720, height: 168 }) };
  const sidebar = { getBoundingClientRect: () => ({ left: 0, right: 307, top: 560, bottom: 670, width: 307, height: 110 }) };
  const doc = {
    querySelector: () => null,
    querySelectorAll: (sel) => (String(sel).includes("role") ? [] : [composer, sidebar]),
  };
  const rects = pet.findLionPetKeepOuts(doc);
  assert.equal(rects.length, 2, `expected both regions, got ${rects.length}`);

  // Sweep every reachable offset and require a position clear of both.
  let cleared = 0;
  for (let right = 0; right <= 912 - 190; right += 1) {
    const rect = { left: 912 - right - 190, right: 912 - right, top: 670 - 227 - 193, bottom: 670 - 227 };
    if (!pet.lionPetOverlapsAny(rect, rects)) cleared += 1;
  }
  assert.ok(cleared > 0, "there is no position clear of both regions, so the guard cannot help");

  // The helper the guard uses must agree with that count.
  const bad = { left: 10, right: 200, top: 580, bottom: 700 };
  assert.equal(pet.lionPetOverlapsAny(bad, rects), true, "the sidebar region alone must be enough to disqualify");
});

test("an absent or unlaid-out region contributes nothing", () => {
  assert.deepEqual(pet.findLionPetKeepOuts(null), []);
  assert.deepEqual(pet.findLionPetKeepOuts({ querySelectorAll: () => [] }), []);
  // A collapsed element is not a region, and must not pin the pet in a corner.
  const collapsed = pet.findLionPetKeepOuts({
    querySelectorAll: () => [{ getBoundingClientRect: () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0 }) }],
  });
  assert.deepEqual(collapsed, []);
});

test("a single rect is still accepted, and a null keep-out changes nothing", () => {
  const base = {
    desiredRight: 400,
    petWidth: 190,
    petHeight: 193,
    viewportWidth: 1125,
    viewportHeight: 797,
    petBottom: 187,
  };
  const one = { left: 279, right: 889, top: 494, bottom: 530 };
  assert.equal(pet.resolveLionPetSafeRight({ ...base, keepOut: one }), pet.resolveLionPetSafeRight({ ...base, keepOut: [one] }));
  assert.equal(pet.resolveLionPetSafeRight({ ...base, keepOut: null }), 400);
  assert.equal(pet.resolveLionPetSafeRight({ ...base, keepOut: [] }), 400, "an empty list is no region at all");
});

test("the component notices a drawer opening, and a drawer has dialog semantics", () => {
  // A ResizeObserver on the composer never fires when a drawer opens: the
  // drawer appears without the composer changing size. Without this the pet
  // stays inside the panel until something else happens to resize the composer.
  assert.match(componentSource, /new MutationObserver\(/, "opening a drawer must re-check the pet's position");
  assert.match(componentSource, /mutations\.observe\(/);
  assert.match(componentSource, /mutations\?\.disconnect\(\)/, "and the observer must be torn down");

  // The bot panel is a hand-copied variant of the shared Modal and had lost the
  // dialog attributes, so nothing announced it and nothing could detect it.
  const panel = readFileSync(new URL("../components/bots/BotDetailPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /role="dialog"/, "the bot detail drawer is a dialog");
  assert.match(panel, /aria-modal="true"/);
});

test("the sidebar footer is a keep-out, so Backup and Restore stay clickable", () => {
  // Live hit-test: the pet's rect intersected both buttons. They move the whole
  // conversation archive, so being unclickable is not cosmetic.
  const sidebar = readFileSync(new URL("../components/ThreadSidebar.tsx", import.meta.url), "utf8");
  assert.match(sidebar, /data-lion-pet-keepout/, "the sidebar footer must be marked as a keep-out region");
  assert.match(sidebar, /Backup/, "the marked footer is the one holding Backup");
  assert.match(sidebar, /Restore/, "and Restore");
});
