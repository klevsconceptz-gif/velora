# Goal system

A responsive goal-based financial workspace for goals, portfolios, stocks, crypto, property discovery, wallet operations, marketplace orders and platform administration. The front end is a dependency-free single-page app. It can be served as static files and includes Cloudflare Pages Functions backed by D1 for server-side authentication, wallet operations and administration.

## Run locally

Static client preview (localStorage persistence and all in-app flows):

```sh
python3 -m http.server 4173 --bind 0.0.0.0
```

Open `http://localhost:4173`.

For the D1-backed full-stack runtime, install Wrangler locally, create a D1 database, set its ID in `wrangler.toml`, apply the migration, then start Pages:

```sh
npx wrangler d1 migrations apply goal-system-db --local
npx wrangler pages dev . --ip 0.0.0.0 --port 8788
```

In another terminal, run the API + D1 read/write integration suite with `npm run integration`. The Pages Functions are same-origin under `/api/*`. The browser uses those relative endpoints when the D1 binding is ready and retains browser-local persistence for the static-only runtime.

## Sign in

- Admin username: `Admin`
- Admin password: `Admin@goal.system`
- Member accounts can be created with **SIGN UP**.

Admin authentication is handled by the Pages Function when D1 is enabled. Set `ADMIN_PASSWORD` as a Cloudflare Pages secret to rotate the server-side default.

## D1 and Cloudflare Pages

- Apply `migrations/0001_initial.sql` to provision user accounts, sessions, wallet ledger, payment requests, transfers, messages, announcements and payment methods.
- Pages publish directory: repository root (`.`); `npm run build` runs syntax and UI/API smoke checks without bundling or moving the frontend files.
- Security response headers, CSP and SPA fallback: root `_headers` and `_redirects`.
- Deployment notes and publish-folder copies: `deploy/cloudflare-pages/`.
- SEO metadata, robots rules, sitemap, web manifest and local favicon/social card are in the root and `assets/images/`.

## Project layout

```text
assets/images/       Local property, vehicle, energy, wearable, apparel and market imagery
functions/api/       Cloudflare Pages API and D1-backed auth/data operations
migrations/          Initial D1 schema and initial payment methods/announcement
src/                 Data, pricing engine, local persistence, API client and application UI
deploy/cloudflare-pages/ Pages deployment instructions and headers/redirects
tests/               UI/API smoke checks and real Pages + D1 integration suite
```

## Checks

```sh
npm test
npm run check
```

`npm test` verifies app structure, data catalogs, local asset references, security/deployment files, SEO metadata and the absence of third-party runtime resources; it smoke-renders the landing page, member routes and Admin Console, and exercises Pages API sign-in/session/inbox paths with a mock D1 binding. `npm run integration` tests real D1 reads and writes through Pages Functions. The workflow checklist is in `TESTING.md`.
