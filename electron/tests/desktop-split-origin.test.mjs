// desktop-split-origin.test.mjs — the desktop shell's own-origin guarantee.
//
// electron/main.js strips a globally-exported split-origin override from the
// frontend child's environment, with this stated intent:
//
//   "Remove split-origin overrides if the user exported them globally:
//    the desktop app always talks to the Gateway through Next.js rewrites."
//
// It shipped deleting `NEXT_PUBLIC_BACKEND_BASE_URL` and
// `NEXT_PUBLIC_LANGGRAPH_BASE_URL`. Neither name is read anywhere in the
// product: the client is built on `GATEWAY_BASE`, derived from
// `NEXT_PUBLIC_GATEWAY_URL` in frontend/src/lib/api-client.ts. So the guard
// deleted two dead variables and left the live one in place — an operator who
// had `NEXT_PUBLIC_GATEWAY_URL=https://elsewhere.example` exported globally got
// a desktop app that sent every prompt, upload and thread read to that origin
// instead of the loopback Gateway, while the code claimed the opposite.
//
// These tests pin the guard to the variable the frontend actually reads, so the
// two cannot drift apart again.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..', '..');
const mainSource = fs.readFileSync(path.join(repoRoot, 'electron', 'main.js'), 'utf8');
const apiClientSource = fs.readFileSync(
  path.join(repoRoot, 'frontend', 'src', 'lib', 'api-client.ts'),
  'utf8',
);

function guardBody() {
  const start = mainSource.indexOf('function spawnFrontendProd');
  assert.ok(start > 0, 'spawnFrontendProd not found in electron/main.js');
  const end = mainSource.indexOf('\n}', start);
  return mainSource.slice(start, end);
}

test('the client base URL really does come from NEXT_PUBLIC_GATEWAY_URL', () => {
  // If the frontend ever moves to another variable, this file must move with it.
  assert.match(
    apiClientSource,
    /GATEWAY_BASE\s*=\s*normalizeGatewayBase\(process\.env\.NEXT_PUBLIC_GATEWAY_URL/,
    'GATEWAY_BASE is no longer built from NEXT_PUBLIC_GATEWAY_URL; update the desktop guard and this test',
  );
});

test('the desktop guard deletes the variable the client actually reads', () => {
  assert.match(
    guardBody(),
    /delete env\.NEXT_PUBLIC_GATEWAY_URL;/,
    'the split-origin guard must strip NEXT_PUBLIC_GATEWAY_URL or a global export redirects the desktop UI off-device',
  );
});

test('the guard does not strip names the product no longer reads', () => {
  // These two are rename drift. Keeping them would let the next reader believe
  // they still mean something.
  assert.doesNotMatch(
    mainSource,
    /NEXT_PUBLIC_LANGGRAPH_BASE_URL|NEXT_PUBLIC_BACKEND_BASE_URL/,
    'electron/main.js still references pre-consolidation base-URL variable names',
  );
});

test('no NEXT_PUBLIC_* split-origin override survives the frontend spawn', () => {
  // Belt and braces: whatever the client ends up reading, the desktop app must
  // not accept an operator's global override for it.
  const body = guardBody();
  const deleted = [...body.matchAll(/delete env\.(NEXT_PUBLIC_\w+);/g)].map((m) => m[1]);
  assert.ok(deleted.includes('NEXT_PUBLIC_GATEWAY_URL'), 'expected the live base-URL variable to be stripped');
  assert.equal(
    deleted.filter((name) => name !== 'NEXT_PUBLIC_GATEWAY_URL').length,
    0,
    `unexpected additional NEXT_PUBLIC_* deletions: ${deleted.join(', ')}`,
  );
});
