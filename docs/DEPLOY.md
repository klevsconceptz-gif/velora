# Deploying Velora

Velora is one process: a Python standard-library WSGI app that serves the JSON API,
the single-page frontend and the uploaded media from the same origin. There is no
build step, no bundler, no CDN and no JavaScript dependency to install.

Two things must be true before the app is *useful* to real people — and one thing
must be true before it should take anyone's money.

| Capability | Configured by | If missing |
| --- | --- | --- |
| Email (confirm address, invitations) | `VELORA_EMAIL_TRANSPORT=smtp` + SMTP settings | Signup still works; the API says delivery is unavailable and tells the operator what to do. Protected actions report `email_verification_required`. |
| Crypto checkout | `VELORA_BTCPAY_*` | Checkout is disabled everywhere with a plain explanation (`checkout_unavailable`). Nothing is simulated. |
| Taking customer funds | Your own review | See [Before you take money](#before-you-take-money). |

---

## 1. Run it locally

```bash
git clone <your-fork> velora && cd velora
python3 -m server.cli status          # shows paths, migration state, feature flags
python3 -m server.cli migrate         # creates var/velora.db — empty, no demo data
python3 web/serve.py                  # http://0.0.0.0:8000
# equivalent: python3 -m server.cli serve --port 8000
```

Open <http://localhost:8000>. The database is empty by design: there is no seed
command, no demo admin and no sample creator. `/api/health` returns `503` until the
migrations have been applied.

To create the **first administrator** (once):

```bash
python3 -m server.cli create-admin          # interactive, asks for typed confirmation
python3 -m server.cli admin-list            # lists administrators
```

The command refuses to run when an active, verified administrator already exists;
later administrators come from a one-time invitation in the console or an audited
promotion of a verified account. There is no default password and no public
self-registration for admin.

Development conveniences, and why they are refused in production:

* `VELORA_EMAIL_TRANSPORT=file` writes `.eml` files to `VELORA_EMAIL_OUTBOX` so you
  can click verification links without a mail provider. `VELORA_ENV=production`
  refuses to start with this transport.
* With no `VELORA_SECRET_KEY`, a development instance keeps a private key file at
  `var/dev_secret.key`. Production requires an explicit key.

---

## 2. Run it in a container

```bash
docker build -t velora .
docker run -d --name velora -p 8000:8000 \
  -v velora-data:/data \
  -e VELORA_ENV=production \
  -e VELORA_SECRET_KEY="$(python3 -m server.cli generate-secret)" \
  -e VELORA_PUBLIC_BASE_URL="https://velora.example" \
  -e VELORA_EMAIL_TRANSPORT=smtp \
  -e VELORA_EMAIL_FROM="Velora <no-reply@velora.example>" \
  -e VELORA_SMTP_HOST=smtp.example.net \
  -e VELORA_SMTP_USERNAME=apikey \
  -e VELORA_SMTP_PASSWORD=… \
  -e VELORA_BTCPAY_URL="https://btcpay.example.net" \
  -e VELORA_BTCPAY_STORE_ID=… \
  -e VELORA_BTCPAY_API_KEY=… \
  -e VELORA_BTCPAY_WEBHOOK_SECRET=… \
  velora
```

The image runs as UID `10001`, keeps all mutable state under `/data` (database,
media, secret file) and applies pending migrations on start. Back up that volume;
nothing else needs to persist.

Then create the first administrator inside the container:

```bash
docker exec -it velora python3 -m server.cli create-admin
```

### Any other host

* **Plain VPS / systemd** — run `python3 -m server.cli serve --host 127.0.0.1 --port 8000`
  behind nginx or Caddy, which terminates TLS. Point the proxy at the process and
  forward `/` (API and SPA share one origin).
* **PaaS (Render, Railway, Fly.io, …)** — the start command is
  `python3 -m server.cli serve --host 0.0.0.0 --port $PORT`. Mount a persistent
  volume at `/data` and set `VELORA_DB_PATH`/`VELORA_MEDIA_ROOT` inside it, or the
  database is lost on every redeploy. SQLite is a single file: one instance, no
  horizontal scaling.

---

## 3. Behind a proxy

Set `VELORA_TRUST_PROXY=1` **only** when a proxy you control sits in front of the
app. With it enabled, `CF-Connecting-IP`, `True-Client-IP`, `X-Real-IP` and
`X-Forwarded-For` are read (in that order), each value is validated as an IP
address, and the result is hashed before storage — Velora never writes a raw
address to disk. With it disabled, forwarded headers are ignored entirely, because
a client can always invent them.

Other headers that matter:

| Header | Use |
| --- | --- |
| `Host` / `X-Forwarded-Host` | Same-origin check for state-changing requests |
| `X-Forwarded-Proto` | `Secure` flag on the session cookie and HSTS (`VELORA_SECURE_COOKIES=auto`) |
| `Origin` | Every unsafe request is refused unless it matches the instance's own origin, `VELORA_ALLOWED_ORIGINS` or `VELORA_ALLOWED_ORIGIN_SUFFIXES` |

If the app is served from more than one hostname, list the extras:

```bash
VELORA_ALLOWED_ORIGINS="https://velora.example,https://www.velora.example"
```

Framing is refused in production (`frame-ancestors 'none'` plus
`X-Frame-Options: DENY`). A development instance permits framing so it can live in
an editor or preview pane; set `VELORA_FRAME_ANCESTORS` explicitly to change this
either way.

---

## 4. Environment reference

| Variable | Default | Notes |
| --- | --- | --- |
| `VELORA_ENV` | `development` | `development`, `test` or `production`. Production tightens several rules. |
| `VELORA_DB_PATH` | `var/velora.db` | SQLite file. Put it on persistent storage. |
| `VELORA_MEDIA_ROOT` | `var/media` | Uploaded images. Back this up with the database. |
| `VELORA_SECRET_KEY` | dev key file | **Required in production.** `python3 -m server.cli generate-secret`. Rotating it invalidates sessions and rate-limit buckets. |
| `VELORA_PUBLIC_BASE_URL` | derived from the request | Absolute base for verification and invitation links. Set it in production so links are correct behind a proxy. |
| `VELORA_ALLOWED_ORIGINS` / `…_SUFFIXES` | empty | Extra origins allowed to make state-changing requests. |
| `VELORA_TRUST_PROXY` | `0` | Read forwarded client-IP headers (see above). |
| `VELORA_SECURE_COOKIES` | `auto` | `auto` = Secure when the request is HTTPS, `always`, `never` (local development only). |
| `VELORA_SESSION_TTL_SECONDS` | 14 days | Session lifetime. |
| `VELORA_VERIFICATION_TTL_SECONDS` | 24 h | Email-confirmation link lifetime. |
| `VELORA_INVITATION_TTL_SECONDS` | 72 h | Administrator-invitation lifetime. |
| `VELORA_PBKDF2_ITERATIONS` | 240 000 | Password hashing cost. Lower only in tests. |
| `VELORA_MAX_BODY_BYTES` | 256 KiB | JSON body limit. |
| `VELORA_MAX_UPLOAD_BYTES` | 8 MiB | Per-image upload limit. |
| `VELORA_ALLOWED_IMAGE_TYPES` | png, jpeg, webp, gif | Content is sniffed; the declared type is ignored. |
| `VELORA_EMAIL_TRANSPORT` | `none` | `smtp`, `file` (development only), `none`. |
| `VELORA_EMAIL_FROM` | `Velora <no-reply@localhost>` | Envelope/From address. |
| `VELORA_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_STARTTLS` | — | 587 + STARTTLS by default; 465 uses implicit TLS. |
| `VELORA_EMAIL_OUTBOX` | — | Directory for the development `file` transport. |
| `VELORA_BTCPAY_URL` | — | Base URL of the operator's hosted BTCPay Server. |
| `VELORA_BTCPAY_STORE_ID` | — | Store that receives memberships. |
| `VELORA_BTCPAY_API_KEY` | — | Key with invoice-create/read permission. **Never** in frontend code or git. |
| `VELORA_BTCPAY_WEBHOOK_SECRET` | — | Shared secret for the webhook HMAC. Without it every delivery is refused and recorded. |
| `VELORA_BTCPAY_INVOICE_TTL_MINUTES` | 60 | Hosted-invoice expiry. |
| `VELORA_PAYMENT_METHODS` | all | Comma-separated catalog keys offered at checkout. |
| `VELORA_PAYMENT_METHOD_IDS` | — | `key=BTCPAY-METHOD-ID` overrides for the default payment-method ids. |
| `VELORA_BTCPAY_ALLOW_LOCALHOST` | `0` | Escape hatch for a BTCPay instance on your own machine. Production code otherwise refuses localhost/private URLs. |
| `VELORA_FRAME_ANCESTORS` | dev `*`, prod `'none'` | CSP `frame-ancestors` value. |

Secrets belong in the process environment or your platform's secret store — never
in the repository, the frontend bundle, a log line or a test fixture.

---

## 5. Crypto payments (BTCPay Server)

1. In your BTCPay Server, create (or choose) a store that will receive 30-day
   memberships. Note its **store id**.
2. Create an API key with invoice **create** and **read** permission for that store.
3. Create a webhook pointing at `https://your-velora/api/payments/btcpay/webhook`
   for the `InvoiceSettled` family of events, and copy its **secret**.
4. Set `VELORA_BTCPAY_URL`, `VELORA_BTCPAY_STORE_ID`, `VELORA_BTCPAY_API_KEY` and
   `VELORA_BTCPAY_WEBHOOK_SECRET`, then restart.
5. Enable the coins and tokens you want to accept as **on-chain payment methods**
   on that store (Bitcoin-family coins such as BTC, LTC, BCH and DOGE are built in; Ethereum,
   Tron, Solana, XRP, Monero and Tether tokens depend on the BTCPay plugins you
   install). Give the API key
   permission to read store settings so Velora can see which methods are enabled.

Which coins members can pay with is the intersection of three things, checked each
time a quote is built (the store's list is cached for 60 seconds):

1. the creator has recorded a wallet address for the coin,
2. the operator offers it — `VELORA_PAYMENT_METHODS` (comma-separated catalog keys,
   default: the whole catalog), and
3. the BTCPay store has the matching payment method enabled.

If BTCPay cannot be asked, no coin is offered (fail closed) and no order is
created. Velora requests each invoice restricted to the single chosen method;
Lightning is never requested.

BTCPay payment-method ids differ by plugin and version. Velora's defaults
(`BTC-CHAIN`, `LTC-CHAIN`, `ETH-CHAIN`, `USDT_TRC20-CHAIN`, …) are in
`server/wallets.py`. **Check the ids your store reports** under
`GET /api/v1/stores/<id>/payment-methods` and override any that differ with
`VELORA_PAYMENT_METHOD_IDS`, e.g. `usdt_trc20=USDT-TRON,eth=ETH-CHAIN`.
Catalog keys: `btc ltc bch doge xmr eth bnb trx sol xrp usdt_trc20 usdt_erc20
usdt_bep20 usdt_sol usdc_erc20`.

How the money path behaves, by design:

* Prices are integer USD cents; BTCPay quotes the amount in the member's chosen coin for the 30-day period.
  Each period is a separate invoice — nothing is scheduled, nothing auto-charges.
* The 10% platform fee is computed when the invoice is created and frozen into the
  settled record.
* A **pending** invoice is never a paid membership, and returning from BTCPay's
  hosted checkout proves nothing. Access is granted only after a webhook whose HMAC
  signature verifies **and** an independent API read of the same invoice confirms a
  settled on-chain payment for the same opaque order reference.
* Partial, overpaid, unconfirmed, off-chain, mismatched, late or manually-marked
  payments are held for review instead of unlocking access.
* Settled invoices, ledger entries, webhook events and audit rows are append-only
  at the database level. Administrators can add notes; they cannot mark an invoice
  paid, edit an amount or delete history.
* Creators record one receiving address per coin or token they accept (also
  possible while applying). Only they and administrators can read them. Addresses
  are checked for format and checksum on the right network (an Ethereum address is
  refused for Tron, and so on) and seed phrases / private keys are refused. Saving
  one records a destination — Velora does not split funds, automate payouts or
  verify wallet ownership. Payouts are a manual operator process. The wallet
  that applied at settlement is snapshotted on the settled invoice.
* A payment made in a different coin than the one chosen, or only partly in it, is
  held for review like any other mismatch.

Until each of those is configured and tested, `/api/payments/availability` reports
checkout as unavailable and the studio shows the creator why.

---

## 6. Before you take money

Deploying the software is not the same as being ready to accept customer funds.
Before you enable checkout for real payments, work through your own
payout/legal/tax/compliance process for the jurisdictions you actually serve —
including how creator earnings are paid out and recorded. Velora deliberately does
not automate payouts and does not claim to enforce any jurisdictional rule.

Velora also stores as little as possible: a display name, an email address, a
password hash, an 18+ self-attestation timestamp, optional private-by-default
orientation, creator content and the payment records above. It never asks for a
phone number, billing address, government ID, card number or wallet seed phrase,
and it does not geolocate visitors. Read `/#/privacy` (rendered from
`/api/privacy/summary`) and make sure the operator-facing promises match what you
actually do.

---

## 7. Operating it

```bash
python3 -m server.cli status              # config, migration state, row counts
python3 -m server.cli migrate             # apply pending migrations (idempotent)
python3 -m server.cli maintenance         # expire memberships, prune rate limits (cron daily)
python3 -m server.cli user verify-email --email someone@example.com
python3 -m server.cli admin-list
```

* **Backups** — stop writes (or use `sqlite3 velora.db ".backup backup.db"`) and
  copy the database *and* the media root together; they reference one another.
* **Health** — `GET /api/health` returns `200` when migrations are current and
  `503` otherwise. Point your platform's health check at it.
* **Logs** — the app writes no access log of its own and never logs secrets,
  tokens or raw addresses. If you add a reverse-proxy log, remember it will contain
  client IPs, which the application does not.
* **Upgrades** — pull, rebuild, restart. Migrations are additive and run
  automatically; a failed migration rolls back rather than leaving a half-applied
  schema (the runner refuses to start on a checksum mismatch, which means a
  released migration file was edited after being applied).

---

## 8. Tests

```bash
python3 tests/run_tests.py            # every suite, with a per-module summary
python3 tests/run_tests.py --list     # just the suite names
node web/tests/render.mjs             # optional: SPA render check on captured payloads
```

The suites use fake BTCPay and a local SMTP stub, and they never touch your
`var/` directory — each test builds its own database in a temporary folder.
