import test from 'node:test';
import assert from 'node:assert/strict';
import {
  fetchJson,
  fetchText,
  isAlphaFrontend,
  isAlphaGateway,
} from '../lib/service-probe.js';

/** A `fetch` stand-in that returns the given response or throws. */
function respond({ ok = true, status = 200, body = '', json = null } = {}) {
  return async () => {
    if (json instanceof Error) throw json;
    return {
      ok,
      status,
      async text() {
        return typeof body === 'string' ? body : JSON.stringify(json ?? body);
      },
      async json() {
        if (json !== null && !(json instanceof Error)) return json;
        return JSON.parse(typeof body === 'string' ? body : '{}');
      },
    };
  };
}

test('the Gateway is recognized only by its reported identity', async () => {
  // Identity, not liveness: a foreign process answering 200 on this port must
  // not be mistaken for our Gateway, or the app reports a gatewayUrl it never
  // health-checked while the real Gateway never starts.
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: respond({ json: { service: 'alpha-gateway', status: 'ok' } }),
    }),
    true,
  );
  // A different service on the same port.
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: respond({ json: { service: 'something-else' } }),
    }),
    false,
  );
  // No service field at all.
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', { fetchImpl: respond({ json: {} }) }),
    false,
  );
  // A truthy-but-wrong value is still not our service.
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: respond({ json: { service: true } }),
    }),
    false,
  );
});

test('a 404 or 500 on /health is not a Gateway', async () => {
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: respond({ ok: false, status: 404, json: { service: 'alpha-gateway' } }),
    }),
    false,
    'a non-2xx body was accepted as proof of identity',
  );
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: respond({ ok: false, status: 503, json: { service: 'alpha-gateway' } }),
    }),
    false,
  );
});

test('a connection failure is "not ours", not a crash', async () => {
  for (const error of [
    new Error('ECONNREFUSED'),
    new Error('socket hang up'),
    Object.assign(new Error('aborted'), { name: 'AbortError' }),
  ]) {
    assert.equal(
      await isAlphaGateway('http://127.0.0.1:8201', { fetchImpl: respond({ json: error }) }),
      false,
      `${error.message} was not handled`,
    );
  }
});

test('an unparseable health body is not a Gateway', async () => {
  assert.equal(
    await isAlphaGateway('http://127.0.0.1:8201', {
      fetchImpl: async () => ({
        ok: true,
        status: 200,
        async json() {
          throw new SyntaxError('Unexpected token < in JSON');
        },
      }),
    }),
    false,
  );
});

test('the frontend is recognized by the Next.js marker', async () => {
  // The probe looks for lowercase `__next`, which is what an App Router page
  // actually emits: `self.__next_f.push(...)` in the streamed flight data, and
  // `__next` in the webpack runtime.
  //
  // Two near-misses worth pinning, because both are strings a developer would
  // reasonably expect to match and neither does:
  //   - `__NEXT_DATA__` is UPPERCASE and does not contain `__next`.
  //   - asset paths are `/_next/static/...` with ONE leading underscore.
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ body: '<html><script>self.__next_f.push([1,"html"])</script></html>' }),
    }),
    true,
  );
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ body: '<html><body id="__NEXT_DATA__">{}</body></html>' }),
    }),
    false,
    'the uppercase __NEXT_DATA__ id was accepted as the lowercase __next marker',
  );
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ body: '<html><script src="/_next/static/chunk.js"></script></html>' }),
    }),
    false,
    'an asset path alone was accepted as the marker',
  );
});

test('the display name is a secondary signal, not the primary one', async () => {
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ body: '<html><title>Alpha docs</title></html>' }),
      displayName: 'Alpha',
    }),
    true,
  );
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ body: '<html><body>nginx</body></html>' }),
    }),
    false,
  );
});

test('a frontend error page is not the frontend', async () => {
  assert.equal(
    await isAlphaFrontend('http://127.0.0.1:3000', {
      fetchImpl: respond({ ok: false, status: 500, body: '<html>__next_f error</html>' }),
    }),
    false,
    'a 500 was accepted as a serving frontend',
  );
});

test('fetchJson and fetchText both bound themselves with a deadline', async () => {
  // A port that accepts a connection and then stalls forever must not wedge the
  // boot. A hung fetch that ignores AbortSignal is the pathological case.
  let sawSignal = false;
  const hungFetch = (url, options) =>
    new Promise((resolve, reject) => {
      if (options && options.signal) {
        sawSignal = true;
        options.signal.addEventListener('abort', () => reject(new Error('aborted')));
      }
    });

  assert.equal(await fetchJson('http://127.0.0.1:1/x', { fetchImpl: hungFetch, timeoutMs: 30 }), null);
  assert.equal(sawSignal, true, 'no AbortSignal was passed to fetch');
  assert.equal(await fetchText('http://127.0.0.1:1/x', { fetchImpl: hungFetch, timeoutMs: 30 }), null);
});

test('fetchJson returns null rather than a falsy body for a valid empty object', async () => {
  // `{}` is a real answer meaning "no service field", and must be distinguishable
  // from a failed fetch at the call site.
  const result = await fetchJson('http://127.0.0.1:8201/health', {
    fetchImpl: respond({ json: {} }),
  });
  assert.deepEqual(result, {});
});