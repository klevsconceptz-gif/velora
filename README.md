# Goal system

A responsive goal-based financial workspace for goals, portfolios, stocks, crypto, property discovery, wallet operations, marketplace orders and platform administration. The frontend is a dependency-free single-page app. Cloudflare Pages Functions provide same-origin API routes backed by D1; browser-local persistence remains available when running the static-only client.

## Local development

```sh
npm ci
npx wrangler d1 migrations apply goal-system-db --local
npm run build
npm run dev
```

Open `http://localhost:8788`. To verify actual Pages Functions and D1 reads/writes, run `npm run integration` in another terminal while Pages dev is running. The integration suite creates unique test users and exercises storage through the HTTP API.

## Sign in

- Admin username: `Admin`
- Admin password: `Admin@goal.system`
- Members can create accounts with **SIGN UP**.

Set the `ADMIN_PASSWORD` Cloudflare Pages secret to rotate the initial administrator password before production use.

## Cloudflare Pages and D1

- `wrangler.toml` configures Pages with `deploy/cloudflare-pages/site` as the publish directory and D1 binding `DB`.
- `npm run build` checks source, runs UI/API smoke tests, and copies only the app shell, modules and local assets into that publish directory.
- Pages Functions remain in root `functions/api/`; the deployment bundle automatically compiles them.
- Apply `migrations/0001_initial.sql` to create users, sessions, wallet ledger, payment requests, transfers, messages, announcements and payment methods.
- The database ID in `wrangler.toml` is a placeholder. Create a D1 database and replace it before a remote migration or deploy.
- Set the Pages build command to `npm run build`; the Wrangler config supplies the output directory.
- Detailed provisioning and deployment steps are in `deploy/cloudflare-pages/README.md`.

## Checks

```sh
npm test
npm run check
npm run build
npm run integration # with Pages dev and local D1 running
```

The automated checks cover data catalogs, local asset references, SEO/security files, frontend route rendering, existing edge-worker tests and real Pages+D1 authentication/state/wallet/chat/admin read-write flows. A manual workflow checklist is in `TESTING.md`.
