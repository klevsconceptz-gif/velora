# Goal system validation checklist

Run automated structure/UI checks with `npm test` and syntax checks with `npm run check`. For real D1 read/write integration, apply the migration, start `npx wrangler pages dev . --ip 0.0.0.0 --port 8788`, then run `npm run integration` in another terminal. For browser workflow validation, serve the root with a static HTTP server or use Pages dev.

## Authentication & account separation
- [ ] SIGN UP validates required fields, username format, duplicate username/email and an eight-character minimum password.
- [ ] SIGN IN opens the signed-in member’s own dashboard; password show/hide works.
- [ ] Forgot password validates account identity and accepts a new password.
- [ ] The Admin username and password sign in through the shared SIGN IN form and open Admin Console.
- [ ] A normal member does not see the Admin Console navigation or Admin-only routes.
- [ ] Sign out clears the active session without deleting persisted member data.

## Dashboard, goals & portfolio
- [ ] Dashboard shows invested value, portfolio value, goal progress, wallet balance, earnings, deposits, withdrawals, transfers, recent activity, notices, announcements, chat and requests.
- [ ] Wallet add-funds and withdrawal requests remain Pending until Admin review.
- [ ] A transfer validates registered usernames, debits the sender, credits the recipient and records both sides.
- [ ] Goals can be created, edited and deleted with target amounts between $100 and $10,000,000.
- [ ] Every goal shows tier, risk, allocation, target, contribution, progress, projection and funding detail.
- [ ] Investing updates the wallet, goal (when selected), holdings and activity ledger.
- [ ] Portfolio table sorts holdings and displays asset class, value, cost basis, gain/loss, YTD return and expense ratio.

## Admin Console
- [ ] Admin can list, create, edit, inspect/open and delete user accounts.
- [ ] Admin can send/delete announcements for all members or an individual audience.
- [ ] Member and Admin chat messages persist and replies appear in the user dashboard.
- [ ] Admin can add/remove payment methods.
- [ ] Deposit/withdrawal requests can be approved or rejected; approved deposits/withdrawals update wallet totals.
- [ ] Admin can add, subtract and set available balance, investment value and earnings; changes appear in the member dashboard.
- [ ] Admin can sign out from Admin Console.

## Markets, properties & simulator
- [ ] Stock Market includes a heat map, market widgets, search, exchange/sector/focus/watchlist filters, stock fundamentals and sparklines.
- [ ] Crypto Trading includes asset pricing, heat map, categories, sorting, market cap, volume, dominance, TVL and sparklines.
- [ ] Pricing remains deterministic for a given day and does not call a third-party quote API.
- [ ] Property cards use multiple local images; search/status/type/beds/max-price/saved-only filters work.
- [ ] Property detail shows address, city/state, size, price, mortgage/carrying cost, rent/night estimates, REIT yield, down payment and goal match where applicable.
- [ ] Contribution and return sliders update simulator projections and the selected goal comparison.

## Shop, cart & orders
- [ ] Shop filters every category and supports product detail, variants, rating/reviews and specs.
- [ ] EV and humanoid-robot reservations calculate a deposit and remaining balance.
- [ ] Goods calculate quantity, shipping, tax and due-today total.
- [ ] Cart quantity, remove and clear actions work.
- [ ] Checkout captures contact, delivery, address, payment and reference details; creates an order ID and clears the cart.
- [ ] Recent orders appear when the cart is empty.

## UI, SEO, security & deployment
- [ ] Landing page contains hero, calls to action, linked platform areas, daily financial/lifestyle advice, plans and footer.
- [ ] Responsive layouts work at 320px, tablet, desktop and large desktop widths; tables scroll within their panels and no page-level horizontal overflow appears.
- [ ] Mobile bottom navigation, desktop sidebar, sticky topbar, touch-friendly controls and mobile-fit modals work.
- [ ] Reduced-motion preference is respected.
- [ ] Meta title/description/keywords/robots/author/theme, Open Graph, Twitter, canonical, favicon and web manifest are present.
- [ ] robots.txt, sitemap.xml, local Open Graph art, `_headers`, `_redirects`, D1 migration and Pages Functions are present.
- [ ] Runtime scripts, styles and images are local; app API calls are same-origin only.
- [ ] Cloudflare Pages serves the static front end without a build step and exposes `/api/*` when D1 is configured.
