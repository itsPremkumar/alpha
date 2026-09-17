import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveStartUrl, rewriteGatewayDestinations } from '../lib/desktop-utils.js';

test('start URL defaults to the app root', () => {
  assert.equal(resolveStartUrl('http://127.0.0.1:3000', {}), 'http://127.0.0.1:3000/');
});

test('start URL honors AGENT_WORKSPACE_START_PATH', () => {
  assert.equal(
    resolveStartUrl('http://127.0.0.1:3000', { AGENT_WORKSPACE_START_PATH: '/workspace' }),
    'http://127.0.0.1:3000/workspace',
  );
});

test('start URL honors DEERFLOW_START_PATH fallback', () => {
  assert.equal(
    resolveStartUrl('http://127.0.0.1:3000', { DEERFLOW_START_PATH: '/workspace' }),
    'http://127.0.0.1:3000/workspace',
  );
});

test('non-http start URLs are returned untouched', () => {
  assert.equal(resolveStartUrl(null, {}), null);
  assert.equal(resolveStartUrl('file:///x', {}), 'file:///x');
});

test('rewrite patches stale loopback /api destinations', () => {
  const manifest = {
    rewrites: {
      beforeFiles: [{ source: '/api/:path*', destination: 'http://127.0.0.1:8001/api/:path*' }],
      afterFiles: [],
      fallback: [],
    },
  };
  const { patched, allMatch, manifest: out } = rewriteGatewayDestinations(manifest, 'http://127.0.0.1:8201');
  assert.equal(patched, 1);
  assert.equal(allMatch, false);
  assert.equal(out.rewrites.beforeFiles[0].destination, 'http://127.0.0.1:8201/api/:path*');
});

test('rewrite treats an already-matching manifest as a no-op success', () => {
  const manifest = {
    rewrites: {
      beforeFiles: [{ source: '/api/:path*', destination: 'http://127.0.0.1:8201/api/:path*' }],
      afterFiles: [],
      fallback: [],
    },
  };
  const { patched, allMatch } = rewriteGatewayDestinations(manifest, 'http://127.0.0.1:8201');
  assert.equal(patched, 0);
  assert.equal(allMatch, true);
});

test('rewrite fails closed when /api destinations are absent', () => {
  const manifest = {
    rewrites: {
      beforeFiles: [{ source: '/other/:path*', destination: 'http://127.0.0.1:8001/other/:path*' }],
      afterFiles: [],
      fallback: [],
    },
  };
  const { patched, allMatch } = rewriteGatewayDestinations(manifest, 'http://127.0.0.1:8201');
  assert.equal(patched, 0);
  assert.equal(allMatch, false);
});

test('rewrite leaves non-loopback /api destinations alone', () => {
  const manifest = {
    rewrites: {
      beforeFiles: [{ source: '/api/:path*', destination: 'https://api.example.com/api/:path*' }],
      afterFiles: [],
      fallback: [],
    },
  };
  const { patched, allMatch } = rewriteGatewayDestinations(manifest, 'http://127.0.0.1:8201');
  assert.equal(patched, 0);
  assert.equal(allMatch, false);
});

test('array-form rewrites are supported', () => {
  const manifest = { rewrites: [{ source: '/api/:path*', destination: 'http://localhost:8001/api/:path*' }] };
  const { patched, allMatch, manifest: out } = rewriteGatewayDestinations(manifest, 'http://127.0.0.1:8201');
  assert.equal(patched, 1);
  assert.equal(allMatch, false);
  assert.equal(out.rewrites[0].destination, 'http://127.0.0.1:8201/api/:path*');
});
