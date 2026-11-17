# Goal system deployment

For the active Pages build, D1 provisioning, secrets and release workflow, see [`deploy/cloudflare-pages/README.md`](../deploy/cloudflare-pages/README.md).

To validate the full stack locally, use `npm run build`, start `npm run dev`, then run `npm run integration` from another terminal. The integration suite verifies persisted reads and writes across sign-out/sign-in.
