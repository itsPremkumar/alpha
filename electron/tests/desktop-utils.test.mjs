import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {
  resolveStartUrl,
  rewriteGatewayDestinations,
  shouldGrantDesktopMediaPermission,
} from '../lib/desktop-utils.js';

test('start URL defaults to the app root', () => {
  assert.equal(resolveStartUrl('http://127.0.0.1:3000', {}), 'http://127.0.0.1:3000/');
});

test('start URL honors AGENT_WORKSPACE_START_PATH', () => {
  assert.equal(
    resolveStartUrl('http://127.0.0.1:3000', { AGENT_WORKSPACE_START_PATH: '/workspace' }),
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

test('desktop media permission grants microphone-only capture from the exact Alpha origin', () => {
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: 'http://127.0.0.1:3000',
      trustedFrontendUrl: 'http://127.0.0.1:3000/workspace',
      details: { mediaTypes: ['audio'] },
    }),
    true,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'audioCapture',
      requestingOrigin: 'http://127.0.0.1:3000',
      trustedFrontendUrl: 'http://127.0.0.1:3000/',
    }),
    true,
  );
});

test('desktop media permission denies camera, mixed capture, and foreign origins', () => {
  const trustedFrontendUrl = 'https://127.0.0.1:8443/';
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'camera',
      requestingOrigin: trustedFrontendUrl,
      trustedFrontendUrl,
    }),
    false,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: trustedFrontendUrl,
      trustedFrontendUrl,
      details: { mediaTypes: ['video'] },
    }),
    false,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: trustedFrontendUrl,
      trustedFrontendUrl,
      details: { mediaTypes: ['audio', 'video'] },
    }),
    false,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: 'https://example.com/',
      trustedFrontendUrl,
      details: { mediaTypes: ['audio'] },
    }),
    false,
  );
});

test('desktop media permission handles singular mediaType and fails closed when unknown', () => {
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: 'http://localhost:3000',
      trustedFrontendUrl: 'http://localhost:3000/',
      details: { mediaType: 'audio' },
    }),
    true,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: 'http://localhost:3000',
      trustedFrontendUrl: 'http://localhost:3000/',
      details: { mediaType: 'video' },
    }),
    false,
  );
  assert.equal(
    shouldGrantDesktopMediaPermission({
      permission: 'media',
      requestingOrigin: 'http://localhost:3000',
      trustedFrontendUrl: 'http://localhost:3000/',
    }),
    false,
  );
});

test('the Electron main process wires native request and check handlers', () => {
  const source = fs.readFileSync(new URL('../main.js', import.meta.url), 'utf8');
  assert.match(source, /configureDesktopMediaPermissions\(mainWindow\.webContents, targetUrl\)/);
  assert.match(source, /setPermissionRequestHandler/);
  assert.match(source, /setPermissionCheckHandler/);
  assert.match(source, /permission === 'camera'/);
});
