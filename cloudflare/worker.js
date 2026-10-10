/**
 * Velora edge Worker.
 *
 * Cloudflare Workers cannot run Velora's Python + SQLite server, so Velora is
 * split in two on Cloudflare:
 *
 *   browser ──► Cloudflare ──► static frontend (web/, served by Workers Static
 *                  │              Assets; no Worker code runs for these files)
 *                  └─ /api/* ──► this Worker ──► the Velora API (ORIGIN_URL),
 *                                               which keeps the database and media
 *
 * The Worker is a thin, fail-closed reverse proxy. It never reads or logs
 * bodies, cookies or credentials. See docs/CLOUDFLARE.md.
 *
 * Configuration
 *   ORIGIN_URL   (var, wrangler.jsonc)  https origin of the Velora API, no path.
 *   EDGE_SECRET  (Secrets Store binding, wrangler.jsonc)  must equal
 *                                        VELORA_EDGE_SECRET on the API. A plain
 *                                        string is also accepted (tests, dev).
 */

const SECURITY_HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
  'Cache-Control': 'no-store',
};

/** Request headers that a client must never be able to smuggle to the origin. */
const STRIP_REQUEST_HEADERS = [
  'host', 'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'forwarded', 'x-forwarded-for',
  'x-forwarded-host', 'x-forwarded-proto', 'x-forwarded-port', 'x-real-ip',
  'true-client-ip', 'x-velora-edge-secret', 'x-velora-client-ip',
];

const EDGE_SECRET_MIN_LENGTH = 32;

/**
 * Read the edge secret. `env.EDGE_SECRET` is a Secrets Store binding (an object
 * with an async get()); a plain string is accepted too. Returns the secret, or
 * null when it is missing, unreadable, not a string, or shorter than 32
 * characters. Callers treat null as "not configured" and fail closed.
 */
export async function readEdgeSecret(env) {
  const binding = env ? env.EDGE_SECRET : undefined;
  let value;
  try {
    if (typeof binding === 'string') {
      value = binding;
    } else if (binding && typeof binding.get === 'function') {
      value = await binding.get();
    } else {
      return null;
    }
  } catch {
    return null; // the store could not be read; never fall back to anything else
  }
  if (typeof value !== 'string' || value.length < EDGE_SECRET_MIN_LENGTH) return null;
  return value;
}

const METHODS_WITH_BODY = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const ALLOWED_METHODS = new Set(['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE']);

function failure(status, code, message) {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status,
    headers: { ...SECURITY_HEADERS, 'Content-Type': 'application/json; charset=utf-8' },
  });
}

/** Validate ORIGIN_URL once per request; returns a URL or a Response describing the problem. */
export function parseOrigin(env, requestUrl) {
  const raw = typeof env.ORIGIN_URL === 'string' ? env.ORIGIN_URL.trim() : '';
  if (!raw) {
    return failure(503, 'edge_not_configured',
      'Velora is not connected to its API yet: the ORIGIN_URL variable is not set on this Worker.');
  }
  let origin;
  try {
    origin = new URL(raw);
  } catch {
    return failure(503, 'edge_not_configured', 'ORIGIN_URL on this Worker is not a valid URL.');
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname);
  if (origin.protocol !== 'https:' && !(local && origin.protocol === 'http:')) {
    return failure(503, 'edge_not_configured',
      'ORIGIN_URL must use https:// (plain http is only accepted for localhost during development).');
  }
  if (origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) {
    return failure(503, 'edge_not_configured',
      'ORIGIN_URL must be an origin only, for example https://api.example.com (no path, query or credentials).');
  }
  if (origin.host === requestUrl.host) {
    return failure(503, 'edge_not_configured',
      'ORIGIN_URL points at this same hostname, which would loop. Use a separate hostname for the API.');
  }
  return origin;
}

export async function proxyApi(request, env) {
  if (!ALLOWED_METHODS.has(request.method)) {
    return failure(405, 'method_not_allowed', 'That method is not allowed here.');
  }
  const url = new URL(request.url);
  const origin = parseOrigin(env, url);
  if (origin instanceof Response) return origin;
  const edgeSecret = await readEdgeSecret(env);
  if (edgeSecret === null) {
    return failure(503, 'edge_not_configured',
      'The EDGE_SECRET secret is missing, unreadable or shorter than 32 characters on this Worker.');
  }

  const headers = new Headers(request.headers);
  for (const name of STRIP_REQUEST_HEADERS) headers.delete(name);
  // cf-* headers are Cloudflare's to set; never relay what a client sent.
  for (const name of [...headers.keys()]) {
    if (name.startsWith('cf-') || name.startsWith('x-envoy-')) headers.delete(name);
  }
  headers.set('x-velora-edge-secret', edgeSecret);
  const clientIp = request.headers.get('cf-connecting-ip');
  if (clientIp) headers.set('x-velora-client-ip', clientIp);
  headers.set('x-forwarded-host', url.host);
  headers.set('x-forwarded-proto', 'https');

  const target = new URL(url.pathname + url.search, origin);
  const init = { method: request.method, headers, redirect: 'manual' };
  if (METHODS_WITH_BODY.has(request.method) && request.body !== null) {
    init.body = request.body; // streamed untouched, so webhook signatures stay valid
  }

  let upstream;
  try {
    upstream = await fetch(target.toString(), init);
  } catch {
    return failure(502, 'origin_unreachable',
      'Velora could not reach its API. Nothing was changed. Please try again shortly.');
  }

  const response = new Response(upstream.body, upstream);
  response.headers.delete('x-powered-by');
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    if (!response.headers.has(name)) response.headers.set(name, value);
  }
  return response;
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === '/api' || pathname.startsWith('/api/')) return proxyApi(request, env);
    // Everything else is served by Workers Static Assets before the Worker runs;
    // reaching here means the assets binding is the right fallback.
    if (env.ASSETS && typeof env.ASSETS.fetch === 'function') return env.ASSETS.fetch(request);
    return failure(404, 'not_found', 'Not found.');
  },
};
