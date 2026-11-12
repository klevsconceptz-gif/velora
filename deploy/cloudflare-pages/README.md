# Cloudflare Pages deployment

Goal system has no frontend bundling step. The **repository root** is the complete Pages output directory; keep `pages_build_output_dir = "."` in the root `wrangler.toml`. Set the Pages build command to `npm run build` to run syntax/UI/API smoke checks; it leaves the static files in place. Pages Functions live in `functions/api/` and bind to D1 as `DB`.

## Provision and deploy

1. Create a D1 database: `npx wrangler d1 create goal-system-db`.
2. Replace the placeholder `database_id` in the root `wrangler.toml` with the returned ID.
3. Apply the schema remotely: `npx wrangler d1 migrations apply goal-system-db --remote`.
4. Set the Pages build command to `npm run build`, output directory to the repository root (`.`), and confirm the `DB` D1 binding (declared in the root Wrangler configuration).
5. Set `ADMIN_PASSWORD` as a Cloudflare Pages secret to rotate the required initial administrator password: `npx wrangler pages secret put ADMIN_PASSWORD --project-name goal-system`.
6. Deploy the repository root: `npx wrangler pages deploy . --project-name goal-system`.

The client files are served as-is after the build checks. Its `/api/*` requests are same-origin and are served by `functions/api/[[path]].js`. The D1 schema is in `migrations/0001_initial.sql`.

## Local full-stack check

```sh
npx wrangler d1 migrations apply goal-system-db --local
npx wrangler pages dev . --ip 0.0.0.0 --port 8788
```

In another terminal, run `npm run integration`. That suite signs up isolated test members, verifies state reads and writes, exercises wallet/admin/chat flows, then signs out and back in to confirm data persisted.

This folder holds deployment notes and copies of `_headers`/`_redirects` for reference. It is **not** a standalone Pages output directory; deploying this folder would omit the application and Functions.
