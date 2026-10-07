# Velora

A creator membership platform built around privacy, clarity and crypto-native payments.

Velora is intentionally small and boring where it counts: one Python process
(standard library only) serves the JSON API, the vanilla-JavaScript single-page app
and the uploaded media from the same origin. No bundler, no CDN, no third-party
runtime dependency, no telemetry.

```
python3 -m server.cli migrate     # creates an empty database (no demo data)
python3 web/serve.py              # http://localhost:8000
```

Then create the first administrator with `python3 -m server.cli create-admin` and
sign in at `/#/login`. Full runbook, environment reference and payment setup:
[docs/DEPLOY.md](docs/DEPLOY.md).

---

## What it does

**For visitors** — an original landing page, category browsing, creator cards and
public creator pages that show only what the creator chose to make public: page
name, tagline, about text, categories, tiers and safe teasers for members-only
posts. Locked posts return a teaser and metadata; the body and media never reach an
unauthorised client, so there is nothing hidden in the page source.

**For members** — email signup with an optional 18+ self-attestation (not age
verification), email confirmation when the instance can send mail, a member feed
built from entitled posts, membership management with visible end dates, and
messaging with creators you currently support. Cancelling stops a future renewal
and never revokes time already paid for. Nothing renews automatically.

**For creators** — apply, then manage a page, tiers (integer USD cents, 30-day
periods), image posts with public teasers, a members view without member email
addresses, studio analytics from settled payments only, and private receiving
wallet addresses (Bitcoin, Ethereum, Tether and more — one per coin they accept). Every creator action is authorised on the server.

**For administrators** — a role-gated console for users, creators, applications,
posts, tiers, reports, payments, the frozen ledger, the audit log and one-time
administrator invitations. Sensitivity is deliberate: the console shows that an
optional orientation field is set, never its value, and administrators can add
notes but cannot mark an invoice paid, edit an amount or delete settled history.

## Privacy posture

* Sexual orientation is optional, private by default, never used for discovery,
  search or analytics, and never displayed publicly unless the member explicitly
  opts in to an eligible preset. "Prefer not to say" and free-text descriptions are
  never shown publicly.
* Velora never asks for a phone number, billing address, government ID, card number
  or wallet seed phrase, and it does not collect location data or gate access by
  geography.
* Member email addresses are never exposed to creators.
* Client addresses are hashed before storage; raw addresses are never written by
  the application.
* Deactivation hides a page and signs out sessions; deletion anonymises personal
  details while keeping settled financial history append-only, because payment
  history must stay verifiable.

## Payments

On-chain cryptocurrency and Tether, through the operator's own hosted BTCPay
Server. Creators record a receiving address for each coin or token they accept and
members pay in one of those. Supported: BTC, LTC, BCH, DOGE, XMR, ETH, BNB, TRX,
SOL, XRP, USDT on Tron / Ethereum / BNB Smart Chain / Solana, and USDC on Ethereum
(`server/wallets.py` is the catalog). There are no cards, no Lightning and no
simulated checkout: when BTCPay is not configured the API reports checkout as
unavailable and says why. A coin is offered only if the creator has a wallet for
it, the operator allows it, **and** the BTCPay store has the matching payment
method enabled.

A 30-day membership is a single invoice. Access is granted only after a webhook
whose HMAC signature verifies **and** an independent read of the same invoice
confirms a settled on-chain payment, in the coin the member chose, matching the
opaque order reference. A pending
invoice is not a paid membership and a browser redirect is not proof. Every settled
invoice, ledger entry, webhook event and audit row is append-only in the schema
itself. Velora does not automate payouts.

## Cloudflare

The frontend and an `/api` proxy deploy to Cloudflare Workers (`wrangler.jsonc`,
`cloudflare/worker.js`); the Python/SQLite API stays on a host with a persistent
disk. Full steps, secrets, troubleshooting and what was verified:
[`docs/CLOUDFLARE.md`](docs/CLOUDFLARE.md).

## Tests

```bash
python3 tests/run_tests.py      # 454 tests across 19 suites, with a summary table
```

`python3 tests/run_tests.py --list` shows the suites. Each test builds its own
temporary database; fake BTCPay and a local SMTP stub are confined to the test
process, and the production code paths fail closed when configuration is missing.
The frontend has three layers of checks: asset hygiene and API-contract tests that
read the shipped JavaScript, and a Node render pass that draws every route against
captured real API responses (`web/tests/render.mjs`).

## Layout

```
server/            WSGI app: routing, security, migrations, services, routes, CLI
  migrations/      12 additive SQL migrations (append-only triggers included)
  services/        accounts, creators, posts, payments, social, admin
  routes/          one module per area; every route declares its auth policy
  tests/           server test suites + harness (fake BTCPay, outbox, helpers)
web/               index.html, assets/ (SPA), serve.py (dev entry point), tests/
docs/DEPLOY.md     operator runbook: hosting, proxy, BTCPay, backups
tests/run_tests.py single entry point that runs every suite
```

Answers to questions this README deliberately does not answer — where to host it,
what to charge, how to pay creators out, and what your local law requires of you —
belong to whoever operates the instance.
