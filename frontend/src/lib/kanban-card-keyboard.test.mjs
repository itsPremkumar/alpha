import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(
  new URL("./kanban-card-keyboard.ts", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
  },
});
const helper = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);
const component = readFileSync(
  new URL("../components/sections/KanbanSection.tsx", import.meta.url),
  "utf8",
);

function keyEvent(key, { repeat = false } = {}) {
  return {
    key,
    repeat,
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
  };
}

test("Kanban cards activate on Enter once and leave other keys inert", () => {
  const spacePress = { current: null };
  let activations = 0;

  const enter = keyEvent("Enter");
  helper.handleKanbanCardKeyDown(enter, "card-1", spacePress, () => activations++);
  assert.equal(activations, 1);
  assert.equal(enter.prevented, false);

  helper.handleKanbanCardKeyDown(
    keyEvent("Enter", { repeat: true }),
    "card-1",
    spacePress,
    () => activations++,
  );
  helper.handleKanbanCardKeyDown(keyEvent("ArrowDown"), "card-1", spacePress, () => activations++);
  helper.handleKanbanCardKeyUp(keyEvent("ArrowDown"), "card-1", spacePress, () => activations++);
  assert.equal(activations, 1);
});

test("Space prevents scrolling and activates once on keyup for the pressed card", () => {
  const spacePress = { current: null };
  let activations = 0;

  const down = keyEvent(" ");
  helper.handleKanbanCardKeyDown(down, "card-1", spacePress, () => activations++);
  assert.equal(down.prevented, true);
  assert.equal(activations, 0, "Space does not activate before keyup");

  const repeatedDown = keyEvent(" ", { repeat: true });
  helper.handleKanbanCardKeyDown(repeatedDown, "card-1", spacePress, () => activations++);
  assert.equal(repeatedDown.prevented, true);

  const wrongCardUp = keyEvent(" ");
  helper.handleKanbanCardKeyUp(wrongCardUp, "card-2", spacePress, () => activations++);
  assert.equal(wrongCardUp.prevented, true);
  assert.equal(activations, 0);

  const up = keyEvent(" ");
  helper.handleKanbanCardKeyUp(up, "card-1", spacePress, () => activations++);
  helper.handleKanbanCardKeyUp(keyEvent(" "), "card-1", spacePress, () => activations++);
  assert.equal(up.prevented, true);
  assert.equal(activations, 1);
  assert.equal(spacePress.current, null);
});

test("KanbanSection wires both keyboard phases to the card editor", () => {
  assert.match(component, /onKeyDown=\{\(e\) =>\s*handleKanbanCardKeyDown/);
  assert.match(component, /onKeyUp=\{\(e\) =>\s*handleKanbanCardKeyUp/);
});
