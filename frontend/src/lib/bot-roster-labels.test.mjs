// bot-roster-labels.test.mjs — a roster of 58 cards must not present 35 of them
// under the same headline.
//
// THE DEFECT. `GET /api/bots` was captured live from the running Gateway and
// returns 58 bots with ZERO duplicate `name`s and EIGHT colliding
// `display_name`s, covering 35 of the 58 cards:
//
//   "Data Engineer"                 x8   bot_ed5fc1, bot_f62dd2, bot_66e659,
//                                       bot_f9fbc9, bot_18484c, bot_a949d0,
//                                       bot_998449, bot_cb596e
//   "Solidity_Security Specialist"  x7   spec_solidity_security_47b1e0, ...
//   "Cuda_Kernel_Opt Specialist"    x7   spec_cuda_kernel_opt_131e8a, ...
//   "Researcher" / "Coder" / "Tester"  x3 each
//   "Architect" / "Support"         x2 each
//
// `botDisplayName` returns `display_name || name`, so every one of those cards
// rendered an identical headline, and `name` - the one field that actually
// separates them - appeared nowhere in the grid. Two bots with different names,
// different departments and different capability chips looked identical, so
// "which Data Engineer?" had no answer on screen. That is a UI hiding a real
// distinction, not a cosmetic nit.
//
// The fixture below is the real capture, reduced to name + display_name + a
// marker for the fields the grid varies by, and the counts are asserted against
// the live numbers so this test fails if the roster shape changes underneath it.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { moduleUrl } from "./test-modules.mjs";

const { botRosterLabel, collidingBotLabels, botDisplayName } = await import("../types/bots.ts")
  .catch(() => import(moduleUrl("../types/bots")))
  .catch(async () => {
    // `types/bots.ts` lives one directory up from `lib/`, and `moduleUrl`
    // resolves siblings by bare name, so transpile it directly.
    const ts = (await import("typescript")).default;
    const { pathToFileURL } = await import("node:url");
    const source = readFileSync(new URL("../types/bots.ts", import.meta.url), "utf8");
    const compiled = ts.transpileModule(source, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    }).outputText;
    return import(
      `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
    );
  });

const bot = (name, display_name) => ({ name, display_name });

/**
 * The live capture: every bot whose display name collides, plus two unique
 * ones so the "do not over-disambiguate" half is actually exercised.
 */
const LIVE_ROSTER = [
  ...["bot_ed5fc1", "bot_f62dd2", "bot_66e659", "bot_f9fbc9", "bot_18484c", "bot_a949d0", "bot_998449", "bot_cb596e"].map((n) =>
    bot(n, "Data Engineer"),
  ),
  ...["spec_solidity_security_47b1e0", "spec_solidity_security_d787c6", "spec_solidity_security_65be9e", "spec_solidity_security_3da5c6", "spec_solidity_security_8d3209", "spec_solidity_security_097adb", "spec_solidity_security_be3c29"].map((n) =>
    bot(n, "Solidity_Security Specialist"),
  ),
  ...["spec_cuda_kernel_opt_131e8a", "spec_cuda_kernel_opt_ef2984", "spec_cuda_kernel_opt_c0df91", "spec_cuda_kernel_opt_d628ad", "spec_cuda_kernel_opt_4c60f3", "spec_cuda_kernel_opt_3e3f37", "spec_cuda_kernel_opt_0c12bb"].map((n) =>
    bot(n, "Cuda_Kernel_Opt Specialist"),
  ),
  ...["researcher", "product-manager", "research-lead"].map((n) => bot(n, "Researcher")),
  ...["coder", "backend-lead", "frontend-lead"].map((n) => bot(n, "Coder")),
  ...["tester", "qa-lead", "probe-employee"].map((n) => bot(n, "Tester")),
  ...["architect", "cto"].map((n) => bot(n, "Architect")),
  ...["support", "support-lead"].map((n) => bot(n, "Support")),
  // Unique: these must come through untouched.
  bot("ceo", "CEO"),
  bot("writer", "Writer"),
];

test("the live roster really does collide, or this suite is testing nothing", () => {
  // If the Gateway ever de-duplicates display names, the fix becomes a no-op and
  // the tests below would keep passing against a shape that no longer occurs.
  // Assert the premise so that day is visible instead of silent.
  const names = LIVE_ROSTER.map((b) => b.name);
  assert.equal(new Set(names).size, names.length, "bot.name must be unique (it is the identity)");

  const labels = collidingBotLabels(LIVE_ROSTER);
  assert.equal(labels.size, 8, "expected exactly 8 colliding display names, as captured live");
  assert.equal(LIVE_ROSTER.length, 37);
  const involved = LIVE_ROSTER.filter((b) => labels.has(botDisplayName(b))).length;
  assert.equal(involved, 35, "35 of the 37 fixture rows collide, as captured live");
});

test("a colliding card is labelled with the id that separates it", () => {
  const labels = collidingBotLabels(LIVE_ROSTER);
  const first = LIVE_ROSTER[0];
  assert.equal(botRosterLabel(first, labels), "Data Engineer (bot_ed5fc1)");
  const second = LIVE_ROSTER[1];
  assert.equal(
    botRosterLabel(second, labels),
    "Data Engineer (bot_f62dd2)",
    "two colliding cards must never render the same string again",
  );
});

test("a unique card is left exactly as the server sent it", () => {
  // Over-disambiguation is its own defect: appending an id to every row would
  // imply the server thinks they are ambiguous, and would make the genuinely
  // colliding rows stand out less.
  const labels = collidingBotLabels(LIVE_ROSTER);
  assert.equal(botRosterLabel(bot("ceo", "CEO"), labels), "CEO");
  assert.equal(botRosterLabel(bot("writer", "Writer"), labels), "Writer");
});

test("the whole colliding set is disambiguated, and nothing else is", () => {
  const labels = collidingBotLabels(LIVE_ROSTER);
  const rendered = LIVE_ROSTER.map((b) => botRosterLabel(b, labels));

  // Every rendered headline is distinct - the actual requirement.
  assert.equal(
    new Set(rendered).size,
    rendered.length,
    "every card in the live roster must be distinguishable by its headline",
  );

  // And the qualifier appears for exactly the colliding rows whose id actually
  // differs from the label. Measured from the fixture, not guessed:
  //
  //   Data Engineer                  8 ids -> 8 qualified
  //   Solidity_Security Specialist   7 ids -> 7 qualified
  //   Cuda_Kernel_Opt Specialist     7 ids -> 7 qualified
  //   Researcher                     3 ids -> 2  (`researcher` is case-only)
  //   Coder                          3 ids -> 2  (`coder` is case-only)
  //   Tester                         3 ids -> 2  (`tester` is case-only)
  //   Architect                      2 ids -> 1  (`architect` is case-only)
  //   Support                        2 ids -> 1  (`support` is case-only)
  //                                35 rows    -> 30 qualified
  //
  // All 35 headlines stay distinct either way: within a case-only pair the label
  // differs from the OTHER id (`Architect` vs `cto`), so the pair is still
  // separable - it is just that repeating the label back in parentheses next to
  // the other member adds noise and no information.
  const qualified = rendered.filter((r) => /\(.+\)$/.test(r));
  assert.equal(qualified.length, 30, "35 rows collide; 5 of those ids are case-only copies of their label");
  assert.ok(rendered.includes("Architect"), "the case-only pair keeps the plain label");
  assert.ok(rendered.includes("Architect (cto)"), "and the genuinely different id still qualifies");
  assert.ok(rendered.includes("Researcher"), "same for Researcher");
  assert.ok(rendered.includes("Researcher (research-lead)"), "and its distinct sibling qualifies");
});

test("an absent collision set is unknown, not 'unique'", () => {
  // `collidingBotLabels` is computed from a loaded roster. Before it resolves
  // (or if the roster read failed) it is `null`, and `null` means "we do not
  // know" - never "this bot is the only one".
  const b = bot("bot_ed5fc1", "Data Engineer");
  assert.equal(botRosterLabel(b, null), "Data Engineer");
  assert.equal(
    botRosterLabel(b, null),
    botDisplayName(b),
    "an unknown collision must degrade to the server's own label, not invent a distinction",
  );
});

test("a colliding label with no usable id is not given an empty qualifier", () => {
  const labels = new Set(["Data Engineer"]);
  // `display_name` present but `name` blank: appending "( )" would be noise.
  assert.equal(botRosterLabel({ name: "", display_name: "Data Engineer" }, labels), "Data Engineer");
  // name identical to the label: the qualifier would repeat the headline.
  assert.equal(botRosterLabel({ name: "Data Engineer", display_name: "Data Engineer" }, labels), "Data Engineer");
});

test("a case-only id difference is not a distinction", () => {
  // Found in the live roster by looking at the screenshot: `architect` and `cto`
  // are both displayed as "Architect", so the gallery rendered "Architect
  // (architect)" next to "Architect (cto)". The first adds two inches of noise
  // beside the second while telling the operator nothing - and it made a
  // readable card look broken.
  const labels = new Set(["Architect"]);
  assert.equal(
    botRosterLabel({ name: "architect", display_name: "Architect" }, labels),
    "Architect",
    "a case-only difference must not earn a qualifier",
  );
  assert.equal(
    botRosterLabel({ name: "cto", display_name: "Architect" }, labels),
    "Architect (cto)",
    "a genuinely different id still earns its qualifier",
  );
});

test("BotGallery computes collisions from the FULL roster, not the filtered view", () => {
  // A search that hides five of the eight "Data Engineer" cards must not make the
  // remaining three look unique - the qualifier vanishing as the user types
  // would be a new bug introduced by the fix.
  const src = readFileSync(new URL("../components/bots/BotGallery.tsx", import.meta.url), "utf8");
  assert.match(
    src,
    /collidingBotLabels\(bots\)/,
    "collisions are a property of the fleet, not of the current filter",
  );
  assert.match(src, /rosterLabel=\{botRosterLabel\(bot, collidingLabels\)\}/);
});