# Cloudflare Pages deployment

Goal system uses Cloudflare Pages with D1-backed Functions. `npm run build` runs tests and creates the static site in `deploy/cloudflare-pages/site`; the Pages Function source stays in the repository-root `functions/api/` directory and binds to D1 as `DB`.

## Provision and deploy

1. Create a D1 database: `npx wrangler d1 create goal-system-db`.
2. Replace the placeholder `database_id` in the root `wrangler.toml` with the returned ID.
3. Apply the schema remotely: `npx wrangler d1 migrations apply goal-system-db --remote`.
4. In the Cloudflare Pages project, set the build command to `npm run build`. Wrangler reads the output directory `deploy/cloudflare-pages/site` from `wrangler.toml`.
5. Set `ADMIN_PASSWORD` as a Cloudflare Pages secret: `npx wrangler pages secret put ADMIN_PASSWORD --project-name goal-system`.
6. Deploy with `npm run deploy` or `npx wrangler pages deploy deploy/cloudflare-pages/site --project-name goal-system`.

The generated Pages output contains only the app shell, local modules, local images, SEO files and response headers. API requests are same-origin under `/api/*`. The D1 schema is in `migrations/0001_initial.sql`.

## Local full-stack check

```sh
npx wrangler d1 migrations apply goal-system-db --local
npm run build
npm run dev
```

In another terminal, run `npm run integration`. That suite signs up isolated test members, verifies state reads and writes, exercises wallet/admin/chat flows, then signs out and back in to confirm data persisted.
