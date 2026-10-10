# Draft spec: moving Velora's data and media to Cloudflare D1, R2 and Pipelines

> **Status: draft for review. Nothing here is implemented.** It records the current
> inventory, the options, the decisions still needed, and the gates each phase must
> pass. It supersedes the "not what this repository does" note in
> [CLOUDFLARE.md](CLOUDFLARE.md) only once the decisions below are made.

## 1. Current state (measured in this repository)

| Area | What exists today | Size |
| --- | --- | --- |
| Database | SQLite via `server/db.py`, schema from 12 SQL migrations in `server/migrations/` | 26 tables |
| HTTP layer | `server/app.py` (WSGI), `server/routes/` | 1,210 lines in routes |
| Business logic | `server/services/` (accounts, admin, creators, payments, posts, social, context) | 4,833 lines |
| Media | `server/media.py` writes files under `VELORA_MEDIA_ROOT` (local disk, `/data`) | 120 lines |
| Email | `server/emaillib.py`: SMTP, or a file outbox in development | — |
| Payments | `server/btcpay.py`, `server/wallets.py`; ledger in `ledger_entries`, `payment_intents`, `webhook_events` | — |
| Config | ~45 `VELORA_*` variables in `server/config.py` | — |
| Tests | Python `unittest` suites run by `tests/run_tests.py` (454 tests), plus the Worker suite (20) | ~6,400 lines of Python tests |

Two facts shape everything below:

1. **Python cannot bind to D1 or R2 directly.** A Python process reaches them only
   through Cloudflare's REST APIs, or the code must run inside a Worker.
2. **Payments and integrity depend on SQLite transactions** (`ledger_entries`,
   `financial_integrity` migration, webhook idempotency in `webhook_events`). Any port
   has to show these properties hold, not assume them.

## 2. Target options (pick one)

| Option | What it means | Main risk |
| --- | --- | --- |
| **A. Python on Workers** | Run the existing Python code as a Python Worker, with D1 and R2 bindings. | Python Workers have a limited standard library; `sqlite3`, `smtplib` and the WSGI layer would all need replacing. Needs a spike to confirm what runs. |
| **B. Rewrite in TypeScript** | New Worker in TypeScript; port each route, service and migration. Python code is retired after parity. | Largest effort (~4,800 lines of services plus routes, and the test suites must be re-expressed). |
| **C. Hybrid** | Keep the Python API on a VPS, and move only media to R2 (uploads through the Worker or signed URLs). Data stays in SQLite. | Does not give D1 at all; it is the smallest change and delivers only the media part of the request. |

I recommend a **spike first** for option A (one route plus one migration, measured
against the existing tests) before committing to B. The spike answers whether the
Python code can run unchanged on Workers at all.

## 3. Component plan

### 3.1 D1 (database)

* Translate the 12 migrations into D1 migrations, keeping their order and history
  table so a migrated database can be verified against `python3 -m server.cli status`.
* **Transactions:** D1 does not offer `BEGIN IMMEDIATE`-style interactive transactions
  as SQLite does; multi-statement atomicity is done with `batch()`. (To verify in the
  spike.) Every ledger write and webhook idempotency check must be re-expressed and
  tested for atomicity, including concurrent deliveries of the same webhook.
* Storage limit: a single D1 database has a size cap (check the current plan limits
  before sizing; not yet verified for this account).
* Rate limiting (`rate_limits` table) and sessions (`sessions`) move into D1 or to
  Durable Objects. Rate limiting per IP is best done at the edge; decide which.

### 3.2 R2 (media)

* Replace `MediaStore.save/read/delete` with R2 `put/get/delete`. The validation
  (`sniff_image_type`, size limits, the path-traversal rules) stays where it is and is
  tested independently of the backend.
* Existing files in `/data/media` are copied to R2 with checksums compared before
  cutover. Stored keys stay the same so `post_media` rows do not change.

### 3.3 Pipelines (analytics / event stream)

Cloudflare Pipelines is **not used anywhere in this repository today**, so its purpose
has to be decided. Candidates: exporting `audit_log` and `webhook_events` to R2 as
an immutable event log, or feeding analytics. **Do not add it until the purpose is
written down**, because it creates a second copy of personal data that the privacy
retention rules (`privacy_retention` migration) must also cover.

### 3.4 Email and BTCPay

* Email: SMTP can continue from a Worker (`cloudflare:sockets`) or move to an email
  API. Decide with the spike.
* BTCPay webhooks keep the HMAC check and the independent BTCPay verification that
  `docs/DEPLOY.md` §5–6 require. The port must not weaken either.

## 4. Phases and gates

Each phase has a gate. A phase is not "done" until its gate passes.

| Phase | Work | Gate |
| --- | --- | --- |
| 0. Decisions | Choose option A/B/C, Pipelines purpose, data-retention rules for any event copies. | Written decision in this file. |
| 1. Spike | One route, one migration, one media upload, on a Worker with D1 and R2 bindings. Measure what runs. | Same request/response as the Python server for those routes; spike results recorded. |
| 2. Schema | D1 migrations 0001–0012 applied to an empty database. | `status` output matches SQLite; schema diff is empty. |
| 3. Services | Port services with their unit tests re-expressed. | All ported tests green; ledger and webhook idempotency tests pass including concurrency. |
| 4. Routes | Port routes; keep the security headers, CSRF and CORS rules. | Existing `server/tests/test_api.py` cases pass against the Worker. |
| 5. Media | Move media to R2; verify checksums. | Byte-for-byte equality for every migrated object. |
| 6. Data copy and cutover | Copy SQLite data to D1 with a freeze window; keep the old database read-only for a rollback period. | Row counts and checksums match per table; rollback rehearsed. |
| 7. Retire | Remove the Python origin, the Docker image and the `/data` volume after the rollback window. | Only after two clean weeks in production (a proposed threshold). |

## 5. Decisions needed from you

1. **Option A, B or C?** (Section 2.) I recommend the spike for A before choosing B.
2. **What is Pipelines for?** Or drop it from the scope.
3. **Is there a freeze window** acceptable for the cutover, and how long?
4. **Who owns the Cloudflare account**, and do you want me to create resources once
   you have run `wrangler login` in the sandbox? (Not authenticated at the time of
   writing; I will not ask for tokens in chat.)

## 6. Risks I have not yet verified

* Whether the Python code runs on Python Workers at all (the spike answers this).
* D1 size and throughput limits for this data volume.
* Whether D1 `batch()` gives the atomicity the ledger needs under concurrent webhooks.
* R2 consistency and cost for media reads at the expected traffic.
* Cloudflare plan limits and billing for each product.
