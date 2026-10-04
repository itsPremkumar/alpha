import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const transpile = (file) =>
  ts.transpileModule(readFileSync(new URL(`./${file}`, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

function load(source) {
  const exports = {};
  new Function("exports", "require", source)(exports, () => {
    throw new Error("invite must not depend on anything");
  });
  return exports;
}

const invite = load(transpile("invite.ts"));

const FULL = "alpha://connect?v=1&a=alpha-7f3a2b&u=http%3A%2F%2F192.168.1.20%3A8001&w=ws%3A%2F%2F192.168.1.20%3A8001%2Fws&n=Priya%27s+laptop&e=1700000900&ep=3&k=s3cret-value";

test("a full invite parses into every field", () => {
  const parsed = invite.parseInvite(FULL);
  assert.equal(parsed.agent_id, "alpha-7f3a2b");
  assert.equal(parsed.url, "http://192.168.1.20:8001");
  assert.equal(parsed.websocket_url, "ws://192.168.1.20:8001/ws");
  assert.equal(parsed.name, "Priya's laptop");
  assert.equal(parsed.expires_at, 1700000900);
  assert.equal(parsed.epoch, 3);
  assert.equal(parsed.pairing_code, "s3cret-value");
});

test("looksLikeInvite only matches the connection-string prefix", () => {
  assert.equal(invite.looksLikeInvite(FULL), true);
  assert.equal(invite.looksLikeInvite("  alpha://connect?v=1&a=x"), true);
  assert.equal(invite.looksLikeInvite("https://example.com"), false);
  assert.equal(invite.looksLikeInvite("just some words"), false);
  assert.equal(invite.looksLikeInvite(""), false);
});

test("an address-only invite parses but is reported as not redeemable, with the fix", () => {
  const parsed = invite.parseInvite("alpha://connect?v=1&a=alpha-1&u=http%3A%2F%2F10.0.0.5%3A8001");
  assert.equal(parsed.pairing_code, null);
  const problem = invite.redeemabilityProblem(parsed);
  assert.match(problem, /address-only/);
  assert.match(problem, /pairing code/, "the refusal must say what to do about it");
});

test("a non-invite paste is refused as such rather than as a field error", () => {
  assert.throws(() => invite.parseInvite("hello world"), /does not look like an Alpha connection string/);
});

test("an unknown version is refused and names the field", () => {
  assert.throws(() => invite.parseInvite(FULL.replace("v=1", "v=4")), /version/);
});

test("an unknown field is refused, listing what this build understands", () => {
  assert.throws(() => invite.parseInvite(`${FULL}&future=1`), /unrecognised field/);
});

test("a duplicated field is refused", () => {
  assert.throws(() => invite.parseInvite(`${FULL}&a=alpha-elsewhere`), /more than once/);
});

test("a missing query part says so", () => {
  assert.throws(() => invite.parseInvite("alpha://connect"), /query part/);
});

test("a malformed or hostile endpoint is refused and names the address field", () => {
  // The client mirror checks *shape* — scheme, credentials, host presence. The
  // metadata-address and blocked-host rules belong to the server's
  // `validate_endpoint`, which runs on the pair request; duplicating that table
  // here would be a second list to keep in step with no security benefit, since a
  // client-side pass proves nothing about what the Gateway accepts. These cases
  // are therefore "rejected locally" or "accepted locally and rejected by the
  // server", and the test asserts only what this layer actually decides.
  for (const hostile of [
    "ftp%3A%2F%2F10.0.0.5",
    "http%3A%2F%2Fuser%3Apw%4010.0.0.5",
    "not-a-url",
    "%3A%2F%2F",
  ]) {
    assert.throws(
      () => invite.parseInvite(`alpha://connect?v=1&a=alpha-1&u=${hostile}`),
      /gateway address/,
      `expected ${hostile} to be refused`,
    );
  }
});

test("a websocket endpoint must be ws or wss", () => {
  assert.throws(
    () => invite.parseInvite(`alpha://connect?v=1&a=alpha-1&u=http%3A%2F%2F10.0.0.5%3A8001&w=http%3A%2F%2F10.0.0.5`),
    /websocket address/,
  );
});

test("expiry is advisory locally and tolerant of skew", () => {
  const nowMs = 1_700_000_000_000;
  const parsed = invite.parseInvite(FULL); // expires at 1700000900
  assert.equal(invite.looksExpired(parsed, nowMs), false, "not yet expired");
  assert.equal(
    invite.looksExpired(parsed, (parsed.expires_at + invite.CLOCK_SKEW_TOLERANCE_SECONDS + 1) * 1000),
    true,
    "past the expiry plus skew tolerance",
  );
  // Inside the tolerance window it is still accepted, matching the server.
  assert.equal(
    invite.looksExpired(parsed, (parsed.expires_at + invite.CLOCK_SKEW_TOLERANCE_SECONDS - 1) * 1000),
    false,
  );
});

test("a claim with no expiry never reports itself expired", () => {
  const parsed = invite.parseInvite("alpha://connect?v=1&a=alpha-1&u=http%3A%2F%2F10.0.0.5%3A8001");
  assert.equal(invite.looksExpired(parsed, Date.now()), false);
  assert.equal(invite.describeExpiry(null), "no expiry reported");
});

test("expiry is described in words, never as a bare timestamp", () => {
  const nowMs = 1_700_000_000_000;
  assert.match(invite.describeExpiry(1_700_000_900, nowMs), /expires in \d+ minutes/);
  assert.match(invite.describeExpiry(1_699_999_000, nowMs), /expired \d+ minutes ago/);
});

test("unwrapPastedText strips the quoting chat clients add", () => {
  assert.equal(invite.unwrapPastedText(`<${FULL}>`), FULL);
  assert.equal(invite.unwrapPastedText(`  ${FULL}\n`), FULL);
});

test("a percent-encoded value that is not valid encoding is refused", () => {
  assert.throws(() => invite.parseInvite("alpha://connect?v=1&a=%E0%A4%A"), /correctly encoded|unrecognised/);
});