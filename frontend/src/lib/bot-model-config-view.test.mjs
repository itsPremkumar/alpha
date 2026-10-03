// bot-model-config-view.test.mjs — the wiring of the per-bot model panel.
//
// The client contract (routes, verbs, CSRF, envelope mapping, the 422 issue
// list) is `bot-model-config.test.mjs`. These assertions are the WIRING and
// the rendered honesty claims, read from the real component sources:
//
//   * the panel is reachable — mounted on the bot detail page, which is the
//     one surface that is per-bot and full-detail;
//   * it renders the four states distinctly (loading / failed-with-reason /
//     present / unsaved), so a failed read never looks like an empty config;
//   * a rejected save shows the SERVER's issues per field, not a status line;
//   * Preview never saves;
//   * the picker is driven by the server's `known_models`, and an unreported
//     list is disclosed rather than rendered as an empty dropdown;
//   * Clear is behind an explicit confirm.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const panel = read("../components/bots/BotModelConfigPanel.tsx");
const detail = read("../components/bots/BotDetailView.tsx");
const client = read("./bot-model-config.ts");

// ------------------------------------------------------------- reachability

test("the panel is mounted on the bot detail page, not orphaned", () => {
  assert.match(detail, /import\s*\{[^}]*BotModelConfigPanel[^}]*\}\s*from\s*"\.\/BotModelConfigPanel"/);
  assert.match(detail, /<BotModelConfigPanel\s+name=\{name\}/);
  // It must receive the plain profile model so the hint can name what applies
  // when the override is empty — otherwise the panel would hide the fallback.
  assert.match(detail, /<BotModelConfigPanel[^>]*botModel=\{/);
});

test("the panel reads the real per-bot routes through the shared client", () => {
  for (const fn of ["fetchBotModelConfig", "saveBotModelConfig", "clearBotModelConfig", "previewBotModelConfig"]) {
    assert.match(panel, new RegExp(`\\b${fn}\\b`), `the panel must call ${fn}`);
  }
  // The client itself must not reach for a different transport or borrow the
  // roster client: this surface has exactly four routes of its own.
  assert.match(client, /createApiClient\(/);
  assert.doesNotMatch(client, /from "\.\/bots"|from "\.\/bot-detail"|fetchBots/);
});

// ------------------------------------------------------------- the four states

test("loading, failure and readiness are four distinct renderings", () => {
  assert.match(panel, /kind: "loading"/);
  assert.match(panel, /kind: "error"/);
  assert.match(panel, /kind: "ready"/);
  // A failed read must quote the gateway, never render an empty form.
  assert.match(panel, /Could not load the model configuration/);
  assert.match(panel, /The gateway said:/);
  assert.match(panel, /Loading the model configuration/);
  // Unsaved work is announced rather than silently diverging from the server.
  assert.match(panel, /Unsaved changes/);
  assert.match(panel, /In sync with the Gateway/);
});

test("a stored block the server rejects says so and names every issue", () => {
  assert.match(panel, /The stored block is invalid/);
  assert.match(panel, /the plain\s*<code>model<\/code>\s*field applies/);
  assert.match(panel, /issues\.map\(/, "every issue must be rendered, not just the first");
  assert.match(panel, /issue\.field/);
  assert.match(panel, /issue\.message/);
  // Severity is preserved rather than flattened to one colour.
  assert.match(panel, /severity === "warning"/);
});

test("a rejected save surfaces the Gateway's issues instead of a status line", () => {
  assert.match(panel, /ModelConfigValidationError/);
  assert.match(panel, /setIssues\(err\.issues\)/);
  assert.match(panel, /The Gateway rejected this configuration/);
  // And it must not swallow a non-validation failure into the same bucket.
  assert.match(panel, /setActionError\(reasonOf\(err\)\)/);
});

// ---------------------------------------------------------------- honesty

test("Preview is labelled as not saving and Save is gated on real changes", () => {
  assert.match(panel, /Preview \(no save\)/);
  assert.match(panel, /Preview — nothing has been saved/);
  assert.match(panel, /disabled=\{!dirty \|\| busy !== null\}/);
  assert.match(panel, /draftsEqual\(state\.draft, state\.saved\)/);
});

test("the picker is driven by the server's known models and discloses an unreported list", () => {
  assert.match(panel, /knownModels === null/);
  assert.match(panel, /did not report the declared model list/);
  assert.match(panel, /No models are declared in config\.yaml models\[\]/);
  // An existing value that is no longer declared is offered back rather than
  // silently dropped from the options — otherwise the panel would read blank.
  assert.match(panel, /\(not declared\)/);
  assert.match(panel, /knownModels\.map\(/);
});

test("the resolved plan shows provenance, the ladder and the server's own limits", () => {
  assert.match(panel, /plan\.primary_source/);
  assert.match(panel, /plan\.fallbacks_source/);
  assert.match(panel, /plan\.counsel_source/);
  assert.match(panel, /plan\.mixture_source/);
  assert.match(panel, /plan\.sampling_source/);
  assert.match(panel, /view\.resolved\.precedence\.join\(" > "\)/);
  assert.match(panel, /limits\.max_fallbacks/);
  assert.match(panel, /limits\.mixture_strategies\.join\("\/"\)/);
  // Limits cap the editors, so the UI cannot offer what the server refuses.
  assert.match(panel, /max=\{limits\?\.max_fallbacks \?\? 5\}/);
  assert.match(panel, /max=\{limits\?\.max_counsel_members \?\? 5\}/);
  assert.match(panel, /max=\{limits\?\.max_mixture_references \?\? 8\}/);
});

test("a panel left off by default reads as off, with the reason", () => {
  // Each toggle states its own cost model beside the control that enables it.
  const counselAt = panel.indexOf('checked={draft.counsel.enabled}');
  assert.notEqual(counselAt, -1, "the counsel toggle must be bound to the draft");
  const counselLabel = panel.slice(counselAt, counselAt + 600);
  assert.match(counselLabel, /off by default, spends nothing while off/);

  const mixtureAt = panel.indexOf('checked={draft.mixture.enabled}');
  assert.notEqual(mixtureAt, -1, "the mixture toggle must be bound to the draft");
  const mixtureLabel = panel.slice(mixtureAt, mixtureAt + 600);
  assert.match(mixtureLabel, /off by default, spends nothing while off/);
});

test("clearing overrides is an explicit two-step destructive action", () => {
  assert.match(panel, /setConfirmClear\(true\)/);
  assert.match(panel, /Confirm clear/);
  assert.match(panel, /variant="danger"/);
  assert.match(panel, /Remove this bot&apos;s block entirely/);
  // The confirm must be reset on a fresh read, or the next open starts armed.
  assert.match(panel, /setConfirmClear\(false\)/);
});

test("a disabled action states why rather than implying a pending state", () => {
  assert.match(panel, /disabled=\{!dirty \|\| busy !== null\}/);
  assert.match(panel, /title=\{dirty \? undefined : "No changes to save\."\}/);
  assert.match(panel, /The server refuses more than \$\{max\}\./);
});
