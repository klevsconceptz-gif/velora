// Unit tests for cloudflare/worker.js, run with `node --test` (no dependencies).
import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { parseOrigin, proxyApi, readEdgeSecret } from '../worker.js';

const SECRET = 's'.repeat(40);
const ENV = { ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: SECRET };

function withFetch(handler, fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  return Promise.resolve(fn(calls)).finally(() => { globalThis.fetch = original; });
}

const ok = () => new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json', 'x-powered-by': 'x' } });

test('proxies /api/* to the origin with the edge secret and client address', async () => {
  await withFetch(ok, async (calls) => {
    const request = new Request('https://velora.test/api/bootstrap?x=1', {
      headers: { 'cf-connecting-ip': '203.0.113.9', cookie: 'velora_session=abc' },
    });
    const response = await worker.fetch(request, ENV);
    assert.equal(response.status, 200);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, 'https://api.velora.test/api/bootstrap?x=1');
    const sent = calls[0].init.headers;
    assert.equal(sent.get('x-velora-edge-secret'), SECRET);
    assert.equal(sent.get('x-velora-client-ip'), '203.0.113.9');
    assert.equal(sent.get('x-forwarded-host'), 'velora.test');
    assert.equal(sent.get('x-forwarded-proto'), 'https');
    assert.equal(sent.get('cookie'), 'velora_session=abc');
    assert.equal(response.headers.get('x-powered-by'), null);
  });
});

test('client-supplied forwarding and edge headers never reach the origin', async () => {
  await withFetch(ok, async (calls) => {
    const request = new Request('https://velora.test/api/auth/login', {
      method: 'POST',
      body: '{}',
      headers: {
        'x-forwarded-for': '1.2.3.4', 'x-real-ip': '1.2.3.4', 'true-client-ip': '1.2.3.4',
        'x-velora-edge-secret': 'guess', 'x-velora-client-ip': '9.9.9.9', 'cf-ray': 'fake',
        'cf-connecting-ip': '203.0.113.9', forwarded: 'for=1.2.3.4', 'x-forwarded-host': 'evil.test',
      },
    });
    await worker.fetch(request, ENV);
    const sent = calls[0].init.headers;
    assert.equal(sent.get('x-velora-edge-secret'), SECRET);
    assert.equal(sent.get('x-velora-client-ip'), '203.0.113.9');
    assert.equal(sent.get('x-forwarded-for'), null);
    assert.equal(sent.get('x-real-ip'), null);
    assert.equal(sent.get('true-client-ip'), null);
    assert.equal(sent.get('forwarded'), null);
    assert.equal(sent.get('cf-ray'), null);
    assert.equal(sent.get('cf-connecting-ip'), null);
    assert.equal(sent.get('x-forwarded-host'), 'velora.test');
  });
});

test('without a client address no spoofable value is invented', async () => {
  await withFetch(ok, async (calls) => {
    await worker.fetch(new Request('https://velora.test/api/x', { headers: { 'x-velora-client-ip': '9.9.9.9' } }), ENV);
    assert.equal(calls[0].init.headers.get('x-velora-client-ip'), null);
  });
});

test('request bodies are streamed through untouched (webhook signatures stay valid)', async () => {
  await withFetch(ok, async (calls) => {
    const body = '{"type":"InvoiceSettled","raw":  "keep   spacing"}';
    const request = new Request('https://velora.test/api/payments/btcpay/webhook', {
      method: 'POST', body, headers: { 'btcpay-sig': 'sha256=abc' },
    });
    await worker.fetch(request, ENV);
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.headers.get('btcpay-sig'), 'sha256=abc');
    const forwarded = new Response(calls[0].init.body);
    assert.equal(await forwarded.text(), body);
    assert.equal(calls[0].init.redirect, 'manual');
  });
});

test('GET requests carry no body', async () => {
  await withFetch(ok, async (calls) => {
    await worker.fetch(new Request('https://velora.test/api/health'), ENV);
    assert.equal(calls[0].init.body, undefined);
  });
});

test('origin responses, including errors and Set-Cookie, pass through', async () => {
  const handler = () => {
    const headers = new Headers({ 'content-type': 'application/json' });
    headers.append('set-cookie', 'velora_session=x; Path=/; HttpOnly; SameSite=Lax');
    return new Response('{"error":{"code":"csrf_required"}}', { status: 403, headers });
  };
  await withFetch(handler, async () => {
    const response = await worker.fetch(new Request('https://velora.test/api/auth/logout', { method: 'POST', body: '{}' }), ENV);
    assert.equal(response.status, 403);
    assert.match(response.headers.get('set-cookie'), /HttpOnly/);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await response.json()).error.code, 'csrf_required');
  });
});

test('an unreachable origin yields a safe 502 that does not reveal the origin', async () => {
  await withFetch(() => { throw new TypeError('connect ECONNREFUSED api.velora.test'); }, async () => {
    const response = await worker.fetch(new Request('https://velora.test/api/bootstrap'), ENV);
    assert.equal(response.status, 502);
    const text = await response.text();
    assert.ok(!text.includes('api.velora.test'));
    assert.equal(JSON.parse(text).error.code, 'origin_unreachable');
  });
});

test('fails closed when ORIGIN_URL or EDGE_SECRET are missing or unsafe', async () => {
  const request = () => new Request('https://velora.test/api/bootstrap');
  await withFetch(ok, async (calls) => {
    const cases = [
      [{ EDGE_SECRET: SECRET }, 'missing origin'],
      [{ ORIGIN_URL: '', EDGE_SECRET: SECRET }, 'empty origin'],
      [{ ORIGIN_URL: 'not a url', EDGE_SECRET: SECRET }, 'garbage origin'],
      [{ ORIGIN_URL: 'http://api.velora.test', EDGE_SECRET: SECRET }, 'plain http'],
      [{ ORIGIN_URL: 'https://api.velora.test/base', EDGE_SECRET: SECRET }, 'path'],
      [{ ORIGIN_URL: 'https://user:pw@api.velora.test', EDGE_SECRET: SECRET }, 'credentials'],
      [{ ORIGIN_URL: 'https://velora.test', EDGE_SECRET: SECRET }, 'loop'],
      [{ ORIGIN_URL: 'https://api.velora.test' }, 'no secret'],
      [{ ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: 'short' }, 'short secret'],
    ];
    for (const [env, label] of cases) {
      const response = await worker.fetch(request(), env);
      assert.equal(response.status, 503, label);
      assert.equal((await response.json()).error.code, 'edge_not_configured', label);
    }
    assert.equal(calls.length, 0, 'nothing may be forwarded when misconfigured');
  });
});

test('plain http is accepted only for localhost (wrangler dev)', () => {
  const url = new URL('https://velora.test/api/x');
  assert.ok(parseOrigin({ ORIGIN_URL: 'http://127.0.0.1:8001' }, url) instanceof URL);
  assert.ok(parseOrigin({ ORIGIN_URL: 'http://localhost:8001' }, url) instanceof URL);
  assert.ok(parseOrigin({ ORIGIN_URL: 'http://example.com' }, url) instanceof Response);
});

test('methods the API does not use are refused before any request is made', async () => {
  await withFetch(ok, async (calls) => {
    const response = await proxyApi(new Request('https://velora.test/api/x', { method: 'OPTIONS' }), ENV);
    assert.equal(response.status, 405);
    assert.equal(calls.length, 0);
  });
});

test('non-API paths are handed to static assets', async () => {
  const seen = [];
  const env = { ...ENV, ASSETS: { fetch: async (request) => { seen.push(new URL(request.url).pathname); return new Response('shell'); } } };
  const response = await worker.fetch(new Request('https://velora.test/c/someone'), env);
  assert.equal(await response.text(), 'shell');
  assert.deepEqual(seen, ['/c/someone']);
});

// --- EDGE_SECRET: plain string or Secrets Store binding -------------------

/** A stand-in for a Secrets Store binding: an object whose get() resolves to the value. */
const storeBinding = (value) => ({ get: async () => value });

test('readEdgeSecret accepts a plain string of at least 32 characters', async () => {
  assert.equal(await readEdgeSecret({ EDGE_SECRET: SECRET }), SECRET);
});

test('readEdgeSecret reads a Secrets Store binding via get()', async () => {
  let calls = 0;
  const binding = { get: async () => { calls += 1; return SECRET; } };
  assert.equal(await readEdgeSecret({ EDGE_SECRET: binding }), SECRET);
  assert.equal(calls, 1);
});

test('readEdgeSecret accepts a value of exactly 32 characters and refuses 31', async () => {
  const exact = 'x'.repeat(32);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: exact }), exact);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding(exact) }), exact);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: 'x'.repeat(31) }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding('x'.repeat(31)) }), null);
});

test('readEdgeSecret fails closed (null) when the secret is missing or empty', async () => {
  assert.equal(await readEdgeSecret({}), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: undefined }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: null }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: '' }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding(null) }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding(undefined) }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding('') }), null);
  assert.equal(await readEdgeSecret(undefined), null);
});

test('readEdgeSecret fails closed when the binding throws or returns a non-string', async () => {
  const throwing = { get: async () => { throw new Error('store unavailable: secret APITOKEN'); } };
  assert.equal(await readEdgeSecret({ EDGE_SECRET: throwing }), null);
  const syncThrow = { get() { throw new Error('boom'); } };
  assert.equal(await readEdgeSecret({ EDGE_SECRET: syncThrow }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: storeBinding(12345678901234567890123456789012345) }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: { notAGetter: true } }), null);
  assert.equal(await readEdgeSecret({ EDGE_SECRET: 42 }), null);
});

test('proxyApi forwards the value read from a Secrets Store binding', async () => {
  await withFetch(ok, async (calls) => {
    const response = await worker.fetch(
      new Request('https://velora.test/api/bootstrap'),
      { ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: storeBinding(SECRET) },
    );
    assert.equal(response.status, 200);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].init.headers.get('x-velora-edge-secret'), SECRET);
  });
});

test('proxyApi fails closed with 503 and forwards nothing when the binding errors', async () => {
  const throwing = { get: async () => { throw new Error('network down'); } };
  await withFetch(ok, async (calls) => {
    const response = await proxyApi(new Request('https://velora.test/api/bootstrap'),
      { ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: throwing });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, 'edge_not_configured');
    assert.equal(calls.length, 0);
  });
});

test('proxyApi fails closed when the binding returns a secret shorter than 32 characters', async () => {
  await withFetch(ok, async (calls) => {
    const response = await proxyApi(new Request('https://velora.test/api/bootstrap'),
      { ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: storeBinding('short') });
    assert.equal(response.status, 503);
    assert.equal(calls.length, 0);
  });
});

test('proxyApi fails closed when the binding returns nothing', async () => {
  await withFetch(ok, async (calls) => {
    const response = await proxyApi(new Request('https://velora.test/api/bootstrap'),
      { ORIGIN_URL: 'https://api.velora.test', EDGE_SECRET: storeBinding(null) });
    assert.equal(response.status, 503);
    assert.equal((await response.json()).error.code, 'edge_not_configured');
    assert.equal(calls.length, 0);
  });
});
