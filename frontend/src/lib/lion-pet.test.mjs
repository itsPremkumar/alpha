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
  assert.match(chatSource, /updateLion\("waiting"/);
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
