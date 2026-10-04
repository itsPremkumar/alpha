// peer-network-view.test.mjs — source pins for the Alpha Network surfaces.
//
// `PeerNetworkSection.tsx` had no view test at all before this file, which meant
// the peer plane's UI was source-pinned nowhere: `external-alpha` had
// `external-alpha-view.test.mjs` and `peers` had nothing, even though `peers` is
// the surface an operator uses to connect a second installation.
//
// These are *source* pins, not render tests, matching the convention
// `external-alpha-view.test.mjs` already established. They exist for the claims
// that are easy to break silently and invisible in a screenshot.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (file) => readFileSync(new URL(`../components/sections/${file}`, import.meta.url), "utf8");
const lib = (file) => readFileSync(new URL(`./${file}`, import.meta.url), "utf8");

const section = read("PeerNetworkSection.tsx");
const connect = read("PeerConnectPanel.tsx");

// ---------------------------------------------------------------------------
// Security: peer text is untrusted data from another machine.
// ---------------------------------------------------------------------------

test("no peer-plane surface renders remote text as raw HTML", () => {
  for (const [name, source] of [["PeerNetworkSection", section], ["PeerConnectPanel", connect]]) {
    assert.doesNotMatch(source, /dangerouslySetInnerHTML/, `${name} must not inject remote text as HTML`);
    assert.doesNotMatch(source, /<iframe/, `${name} must not embed peer content`);
  }
});

test("the connect panel is reachable from the Alpha Network view", () => {
  assert.match(section, /PeerConnectPanel/);
  // A panel that is defined and never mounted is dead code, and the failure would
  // be invisible: the tab would simply look unchanged.
  assert.match(section, /<PeerConnectPanel\s+status=\{status\}/);
});

// ---------------------------------------------------------------------------
// Honesty: a failed read is never an empty panel.
// ---------------------------------------------------------------------------

test("the connect panel keeps its own error rather than sharing one", () => {
  // The parent tab had a single `error` string, so any panel's failure blanked
  // the whole view. Each surface owning its error is what makes a partial read
  // present itself as partial.
  assert.match(connect, /const \[error, setError\] = useState<string \| null>\(null\)/);
  assert.match(connect, /<ErrorBox/);
});

test("a disabled plane is rendered as disabled, with the reason and the env var", () => {
  assert.match(connect, /ALPHA_PEER_NETWORK_ENABLED/);
  // `enabled` is derived once from the status and threaded down, rather than
  // re-derived per panel — one read, one rendering.
  assert.match(connect, /const enabled = status\?\.enabled === true;/);
});

// ---------------------------------------------------------------------------
// The credential is labelled by what it contains.
// ---------------------------------------------------------------------------

test("a full invite is labelled as containing the pairing code and as single use", () => {
  assert.match(connect, /contains your pairing code/);
  assert.match(connect, /safe to share publicly/);
  // "Works only once" is the property a leaked screenshot depends on, so it is
  // stated in the UI rather than only in the docs.
  assert.match(connect, /works only once/);
});

test("a blocked clipboard surfaces a worded remedy instead of claiming success", () => {
  assert.match(connect, /fullCopy\.failed/);
  assert.match(connect, /blocked clipboard access|copy it manually/);
});

test("the shared copy hook disables in flight and reports failure", () => {
  const hook = lib("use-copy-button.ts");
  assert.match(hook, /inFlightRef/);
  assert.match(hook, /if \(inFlightRef\.current\) return false/);
  // A control stuck showing "Copied" is indistinguishable from one that silently
  // failed, so the state must revert.
  assert.match(hook, /setTimeout/);
  assert.match(hook, /setFailed\(true\)/);
});

// ---------------------------------------------------------------------------
// Paste routing, and the QR honesty gate.
// ---------------------------------------------------------------------------

test("a paste is inspected before the textarea consumes it, and text falls through", () => {
  assert.match(connect, /onPaste=\{onPaste\}/);
  assert.match(connect, /looksLikeInvite/);
  // The fall-through branch is the load-bearing half: a handler that ate every
  // paste would make ordinary typing impossible.
  assert.match(connect, /Let the textarea have it|falls through|falls? through/);
});

test("the QR camera and screenshot paths are gated on canDecodeQr()", () => {
  assert.match(connect, /canDecodeQr\(\)/);
  // The reason must be visible, not just the disabled state — a disabled button
  // with no explanation reads as a bug.
  assert.match(connect, /not enabled in this build yet|not available in this build/);
  assert.match(connect, /Reading a QR code is not enabled/);
});

test("the decoder does not claim to work", () => {
  const decoder = lib("qr-decode.ts");
  assert.match(decoder, /export function canDecodeQr\(\): boolean \{\s*return false;/);
  // The status banner is what stops the next reader from trusting this module.
  assert.match(decoder, /NOT YET USABLE/);
});

// ---------------------------------------------------------------------------
// Delivery receipts stay per recipient.
// ---------------------------------------------------------------------------

test("the thread renders one badge per recipient receipt", () => {
  assert.match(section, /message\.deliveries\.map/);
});

// ---------------------------------------------------------------------------
// The view registry is complete in all three places.
// ---------------------------------------------------------------------------

test("the peers view is registered once in each of the three required places", () => {
  const nav = readFileSync(new URL("../components/NavTabs.tsx", import.meta.url), "utf8");
  const view = lib("workspace-view.ts");
  const chat = readFileSync(new URL("../components/ChatView.tsx", import.meta.url), "utf8");
  assert.equal((nav.match(/id: "peers"/g) ?? []).length, 1, "one WORKSPACE_TABS entry");
  assert.match(view, /"peers"/);
  assert.match(chat, /view === "peers"/);
});