# Deploying Velora with Cloudflare Workers

**Read this first — why a plain "deploy to Workers" fails.** Velora's server is a
Python standard-library WSGI app that keeps a **SQLite file** and **uploaded media on
disk** (and the settled-payment ledger lives in that file). A Cloudflare Worker is a
JavaScript/Wasm isolate: it has no long-running process, no writable local disk and
no SQLite file. A Worker build that points at this repository without a Worker
config fails (nothing to deploy), and a Worker that *did* run the Python code would
have nowhere to keep the database.

Cloudflare Containers can run the Docker image, but their disk is **ephemeral** — a
container that sleeps or restarts comes back with a fresh disk
([Containers FAQ](https://developers.cloudflare.com/containers/faq/)). That would
silently erase accounts, memberships and the payment ledger, so **do not host the
Velora API on Containers** unless you first move storage elsewhere.

So Velora deploys to Cloudflare as two pieces:

```
                       ┌───────────────────────── Cloudflare ─────────────────────────┐
 browser ── HTTPS ───► │  velora.example.com  (Worker + Static Assets)                 │
                       │    /, /assets/*, /c/<handle>, /verify …  → static frontend    │
                       │    /api/*  → Worker (cloudflare/worker.js) ──────────────┐    │
                       └───────────────────────────────────────────────────────────│────┘
                                                                                   ▼
                              velora-api.example.com  (Velora API in Docker, with a persistent
                              volume for /data: database, media, secret) — on a VPS, Fly.io,
                              Render, Railway, a home server behind a Cloudflare Tunnel, …
```

* **Frontend** (`web/`) is uploaded as Workers Static Assets. Static files are served
  by Cloudflare without running Worker code, with the same security headers the API
  sends (`web/_headers`).
* **`/api/*`** goes through `cloudflare/worker.js`, a small fail-closed reverse proxy. It
  forwards the request body untouched (so BTCPay webhook signatures still verify),
  adds a shared secret and the real client address, and never logs bodies or cookies.
* **The API** (`python3 -m server.cli serve`, the repo's `Dockerfile`) stays on a host
  with a persistent disk. When `VELORA_EDGE_SECRET` is set it refuses `/api/*` calls
  that did not come through the Worker (except `/api/health`).

Everything is same-origin from the browser's point of view, so cookies, CSRF and the
`connect-src 'self'` Content-Security-Policy keep working unchanged.

> A fully Cloudflare-native port (D1 + R2 + Durable Objects, rewriting the server in
> JavaScript) is possible but is a rewrite of the database layer, the media store and
> every service. It is not what this repository does.

---

## What you need

| Item | Notes |
| --- | --- |
| Cloudflare account | Free plan works to start. Check [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/): `/api/*` calls are Worker invocations and count toward the plan's request quota; static-asset requests are free and unlimited. |
| A domain on Cloudflare | Recommended: `velora.example.com` for the site and a **different** hostname such as `velora-api.example.com` for the API. `*.workers.dev` also works for the site. |
| A host for the API | Anything that runs a Docker image with a **persistent volume** and gives you **HTTPS** (the Worker refuses plain `http://` origins). See [Step 1](#step-1--deploy-the-api-origin). |
| SMTP account | Needed in production so verification emails can be sent (`VELORA_EMAIL_TRANSPORT=smtp`). |
| BTCPay Server | Optional until you take payments; see [DEPLOY.md §5](DEPLOY.md#5-crypto-payments-btcpay-server). |
| Node 18+ and npm | Only on the machine that runs `wrangler`. The app itself has no JavaScript dependencies. |

---

## Step 0 — generate the secrets

Run these locally (nothing is stored):

```bash
python3 -m server.cli generate-secret   # → VELORA_SECRET_KEY   (signs sessions/tokens)
python3 -m server.cli generate-secret   # → VELORA_EDGE_SECRET  (shared with the Worker, ≥ 32 chars)
```

The same `VELORA_EDGE_SECRET` value goes to the API (as `VELORA_EDGE_SECRET`) and to the
Worker (as the `EDGE_SECRET` secret). The BTCPay webhook secret comes from BTCPay.
**Never** put any of these in `wrangler.jsonc`, the repository or frontend code.

---

## Step 1 — deploy the API origin

Build and run the repository's `Dockerfile` on a host with a persistent volume
mounted at `/data` (database, media and the secret file live there). The container
runs as a non-root user (`10001`), so the volume must be writable by that user.

Required environment for the API (production):

| Variable | Value |
| --- | --- |
| `VELORA_ENV` | `production` |
| `VELORA_SECRET_KEY` | from Step 0 |
| `VELORA_EDGE_SECRET` | from Step 0 (**required for this setup**, see below) |
| `VELORA_PUBLIC_BASE_URL` | the **site** URL, e.g. `https://velora.example.com` — *not* the API host. Used in verification/invitation emails and BTCPay redirects. |
| `VELORA_SECURE_COOKIES` | `always` |
| `VELORA_EMAIL_TRANSPORT` | `smtp`, plus `VELORA_SMTP_HOST`, `VELORA_SMTP_PORT`, `VELORA_SMTP_USERNAME`, `VELORA_SMTP_PASSWORD`, `VELORA_EMAIL_FROM` |
| `VELORA_BTCPAY_URL`, `_STORE_ID`, `_API_KEY`, `_WEBHOOK_SECRET` | when you are ready for payments (optional now) |

Why `VELORA_EDGE_SECRET` is required here: without it the API only sees the Worker's
address for every visitor, so the per-address rate limits on signup/login/checkout
would be shared by **everyone**. With it, the API takes the visitor's address only
from the Worker's `x-velora-client-ip` header, ignores any forwarded header a client
sends, and refuses direct calls that skip the Worker. (A raw address is never
stored — only a keyed hash used for rate limiting.)

### Option A — a VPS with Docker, published through a Cloudflare Tunnel

No open inbound port and automatic HTTPS. These are the standard `cloudflared`
commands; they were not run in this repository's CI.

```bash
# on the VPS
docker build -t velora .
docker volume create velora-data
docker run -d --name velora --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v velora-data:/data \
  -e VELORA_ENV=production -e VELORA_SECRET_KEY=… -e VELORA_EDGE_SECRET=… \
  -e VELORA_PUBLIC_BASE_URL=https://velora.example.com -e VELORA_SECURE_COOKIES=always \
  -e VELORA_EMAIL_TRANSPORT=smtp -e VELORA_SMTP_HOST=… -e VELORA_SMTP_USERNAME=… \
  -e VELORA_SMTP_PASSWORD=… -e VELORA_EMAIL_FROM='Velora <no-reply@example.com>' \
  velora

cloudflared tunnel login
cloudflared tunnel create velora-api
cloudflared tunnel route dns velora-api velora-api.example.com
# ~/.cloudflared/config.yml:
#   tunnel: velora-api
#   credentials-file: /root/.cloudflared/<id>.json
#   ingress:
#     - hostname: velora-api.example.com
#       service: http://localhost:8000
#     - service: http_status:404
cloudflared tunnel run velora-api       # or install it as a service
```

Your API origin is then `https://velora-api.example.com`.

### Option B — any container host with a volume (Fly.io, Render, Railway, …)

Create a service from the `Dockerfile`, attach a persistent disk at `/data`, set the
environment above, and note the HTTPS URL it gives you. Use a **hostname**, not a
bare IP address (Cloudflare Workers cannot `fetch()` an IP, error 1003).

### Check the origin before involving Cloudflare

```bash
curl -fsS https://velora-api.example.com/api/health
# {"ok":true,"database":"ready","migrations_applied":12,…}
curl -s -o /dev/null -w '%{http_code}\n' https://velora-api.example.com/api/bootstrap
# 403  ← correct: edge_required (the secret is missing)
```

Then create the first administrator on the origin (once; see DEPLOY.md):

```bash
docker exec -it velora python3 -m server.cli create-admin
```

---

## Step 2 — configure the Worker

1. Edit **`wrangler.jsonc`** and set the API origin (a URL, not a secret; origin only,
   no path, `https://`):

   ```jsonc
   "vars": { "ORIGIN_URL": "https://velora-api.example.com" }
   ```

2. Add the secret (never in a file). Locally:

   ```bash
   npm ci
   npx wrangler login
   npx wrangler secret put EDGE_SECRET        # paste the Step 0 value
   ```

3. Validate and deploy:

   ```bash
   npm run check        # wrangler deploy --dry-run: validates config, bundles, lists assets
   npm test             # Worker unit tests
   npm run deploy       # wrangler deploy
   ```

Until `ORIGIN_URL` and `EDGE_SECRET` are both set, `/api/*` answers
`503 edge_not_configured` with a plain explanation (the Worker fails closed rather
than guessing); the static site still loads.

### Deploying from Git (Workers Builds) instead of your laptop

In the Cloudflare dashboard: **Workers & Pages → Create → Workers → Import a
repository** (choose *Workers*, **not Pages** — the `/api` proxy needs a Worker),
pick this repository and branch, then:

| Setting | Value |
| --- | --- |
| Project / Worker name | **`velora`** — must match `name` in `wrangler.jsonc`, or the build fails with a name-mismatch error |
| Root directory | `/` (the folder that contains `wrangler.jsonc`) |
| Build command | *(leave empty)* — there is no build step. (If the dashboard has `npm run build` from a template, that works too: `package.json` defines a no-op `build`.) |
| Deploy command | `npx wrangler deploy` |
| Version command | `npx wrangler versions upload` (default) |

After the first deploy open the Worker → **Settings → Variables and Secrets → Add →
Secret** named `EDGE_SECRET`. (Plain variables in `wrangler.jsonc` such as `ORIGIN_URL`
are re-applied on every deploy; secrets persist.) The *Build* section's variables are
for the build container and are **not** where `EDGE_SECRET` goes.

### Attach your domain

Worker → **Settings → Domains & Routes → Add → Custom Domain** →
`velora.example.com` (the zone must be on Cloudflare; DNS and certificate are created
for you). Do **not** route the API hostname to the Worker — it must reach the origin
directly. Alternatively use the `*.workers.dev` URL (then set
`VELORA_PUBLIC_BASE_URL` to that URL).

---

## Step 3 — connect BTCPay (when you take payments)

Point the BTCPay webhook at the **site** URL so it passes through the Worker, whose
body streaming keeps the HMAC signature valid:

```
https://velora.example.com/api/payments/btcpay/webhook
```

If you use Cloudflare's Bot Fight Mode, WAF managed challenges or rate-limiting
rules, add a *skip* rule for that path; a challenged server-to-server POST looks like
a failed delivery. Everything else about payments is unchanged: access is still
granted only after a signed webhook **and** independent BTCPay verification (see
DEPLOY.md §5 and §6, including the review you owe before taking customer funds).

---

## Step 4 — verify the deployment

```bash
SITE=https://velora.example.com
curl -fsS $SITE/api/health                                  # {"ok":true,…} via the Worker
curl -sI $SITE/ | grep -i -E 'content-security|x-frame|strict-transport|cache-control'
curl -s -o /dev/null -w '%{http_code}\n' "$SITE/verify?token=x"    # 200 (SPA route)
curl -s $SITE/serve.py | head -c 60                         # app shell, never source code
curl -s -o /dev/null -w '%{http_code}\n' https://velora-api.example.com/api/bootstrap   # 403 direct
```

Then in a browser: sign up → the email arrives with a link on **your site's** host →
verify → log in. Checkout stays disabled with an explanation until BTCPay is
configured.

---

## Configuration reference

| Where | Name | Kind | Purpose |
| --- | --- | --- | --- |
| Worker | `ORIGIN_URL` | var in `wrangler.jsonc` | `https://` origin of the API (no path). Plain `http://` is accepted only for `localhost` during `wrangler dev`. |
| Worker | `EDGE_SECRET` | **secret** | Sent as `x-velora-edge-secret`. Must equal the API's `VELORA_EDGE_SECRET`; ≥ 32 characters. |
| API | `VELORA_EDGE_SECRET` | secret env | Enables edge mode: refuse `/api/*` without the secret (except `/api/health`), take the client address only from `x-velora-client-ip`. |
| API | `VELORA_PUBLIC_BASE_URL` | env | The public **site** URL used in emails and redirects. |

Headers the Worker adds to every proxied request: `x-velora-edge-secret`,
`x-velora-client-ip` (from `cf-connecting-ip`), `x-forwarded-host`, `x-forwarded-proto`.
Headers it strips from the browser's request first: `x-forwarded-*`, `forwarded`,
`x-real-ip`, `true-client-ip`, `cf-*`, and any client-supplied `x-velora-*` edge
headers, so a visitor cannot impersonate another address or the Worker.

Limits worth knowing: the API's own body limits still apply (256 KB JSON, 8 MB image
by default); Cloudflare's request-body cap on Free/Pro is 100 MB, well above them.

---

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| Build: *"Missing entry-point"*, *"Could not detect…"*, or nothing to deploy | No Worker config was found. `wrangler.jsonc` must be at the repository root of the build (Root directory `/`). Use **Workers**, not Pages. |
| Build: *"The name in your Wrangler configuration file … must match the name of your Worker"* | The dashboard project name must equal `"name": "velora"` in `wrangler.jsonc` (or change both) — [documented requirement](https://developers.cloudflare.com/workers/ci-cd/builds/troubleshoot/). If it happens **only on non-production branches** with the name correct, it is a [known Workers Builds issue](https://github.com/cloudflare/workers-sdk/issues/15682); set that branch's deploy command to `env -u WRANGLER_CI_MATCH_TAG npx wrangler versions upload`, or build from your production branch. |
| Build tries to run Python / `pip install` | Clear the Build command. Velora needs no build; Python runs on the API origin only. |
| Deploy succeeds but the site shows API errors | `/api/*` returns `503 edge_not_configured` → set `ORIGIN_URL` in `wrangler.jsonc` and the `EDGE_SECRET` secret, redeploy. |
| `403 edge_required` from every API call | `EDGE_SECRET` (Worker) ≠ `VELORA_EDGE_SECRET` (API). Re-set one so they are identical. |
| `502 origin_unreachable` / Cloudflare 521–523 | The API host or tunnel is down, or not HTTPS. Re-run the origin checks in Step 1. |
| Cloudflare error 1003 / 1000 | `ORIGIN_URL` uses an IP address or a hostname that resolves to Cloudflare itself (loop). Use a separate API hostname. |
| `403 origin_rejected` on login/signup | The API must see the site host. The Worker sends `x-forwarded-host`; make sure nothing between the Worker and the API strips it, or set `VELORA_ALLOWED_ORIGINS=https://velora.example.com` on the API. |
| `429 rate_limited` for everyone | Edge mode is not active on the API (`VELORA_EDGE_SECRET` unset), so all visitors share one address. Set it. |
| Verification email links point at the API host or `localhost` | Set `VELORA_PUBLIC_BASE_URL` to the site URL. |
| Login works but you are logged out on reload | `VELORA_SECURE_COOKIES=always` requires HTTPS end-to-end for the site; check you are not browsing an `http://` URL. |
| BTCPay shows webhook delivery failures | Webhook URL must be the **site** URL; add a WAF/Bot-Fight skip for `/api/payments/btcpay/webhook`; confirm `VELORA_BTCPAY_WEBHOOK_SECRET`. |
| Data disappeared after a restart | The API's `/data` is not on a persistent volume (for example it ran on Cloudflare Containers or a host with ephemeral disk). Restore from backup and move the origin to persistent storage. |

---

## What was verified, and what was not

Verified in this repository (see `server/tests/test_cloudflare.py`,
`server/tests/test_edge.py`, `cloudflare/tests/worker.test.mjs`, and the
`cloudflare` CI job):

* `wrangler deploy --dry-run` accepts `wrangler.jsonc` (wrangler 4.148.0) and uploads
  only the public site — `tests/`, `serve.py` and `_headers` are excluded.
* The Worker ran in Cloudflare's local `workerd` runtime (`wrangler dev`) in front of a
  real Velora API: static shell and security headers, SPA deep links, signup →
  email → verify → login through the proxy, session cookie flags, CSRF and
  foreign-origin refusal, webhook route body passthrough, upload-limit response, direct
  calls to the origin refused without the secret, spoofed client headers ignored.
* Unit tests cover header stripping, streaming bodies, fail-closed configuration and
  the 502 path.

**Not** verified (needs your Cloudflare account and hosts): a real remote deploy,
Workers Builds settings, custom domain, a Cloudflare Tunnel, behaviour under
Cloudflare's WAF/Bot rules, and BTCPay against a real server.
