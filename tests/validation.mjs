import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { resolve } from 'node:path';
import { STOCKS, CRYPTOS, PROPERTIES, PRODUCTS, TIERS, quote, dailySeed } from '../src/data.js';
import { onRequest } from '../functions/api/[[path]].js';

const root = resolve(new URL('..', import.meta.url).pathname);
const file = (path) => readFile(resolve(root, path), 'utf8');
const exists = async (path) => access(resolve(root, path)).then(() => true, () => false);

const html = await file('index.html');
const app = await file('src/app.js');
const css = await file('styles.css');
const functions = await file('functions/api/[[path]].js');
const headers = await file('_headers');
const robots = await file('robots.txt');
const sitemap = await file('sitemap.xml');
const redirects = await file('_redirects');
const wrangler = await file('wrangler.toml');

assert.match(html, /<title>Goal system/);
for (const tag of ['name="description"', 'name="keywords"', 'name="robots"', 'name="author"', 'name="theme-color"', 'name="color-scheme"', 'property="og:title"', 'property="og:image"', 'name="twitter:card"', 'rel="canonical"', 'rel="manifest"']) assert.ok(html.includes(tag), `Missing SEO field: ${tag}`);
assert.match(html, /src="\.\/src\/app\.js"/);
assert.match(html, /href="\.\/styles\.css"/);
assert.ok(html.includes('og:image:type" content="image/jpeg"'));
assert.ok(await exists('assets/images/cards/market-desk.jpg'), 'Social preview imagery must be local.');
assert.ok(!/<script[^>]+src=["']https?:/i.test(html), 'Runtime scripts must be local.');
assert.ok(!/<link[^>]+href=["']https?:[^"']+\.css/i.test(html), 'Runtime stylesheets must be local.');
assert.match(robots, /Sitemap:/);
for (const anchor of ['#platform', '#advice', '#markets', '#properties', '#shop', '#plans']) assert.ok(sitemap.includes(anchor), `Sitemap entry missing ${anchor}`);

assert.ok(app.includes("'Stock Market'"));
assert.ok(app.includes("'Crypto Trading'"));
assert.ok(app.includes('SIGN IN') && app.includes('SIGN UP'));
assert.ok(app.includes('Admin Console'));
assert.ok(app.includes('data-action="transfer"'));
assert.ok(app.includes('checkout-form'));
assert.ok(app.includes('password-toggle'));
assert.ok(app.includes('admin-tab'));
assert.ok(!/https?:\/\//.test(app), 'The app bundle must not call third-party URLs.');
assert.ok(!/localhost|127\.0\.0\.1/.test(app), 'Browser code must not use localhost network URLs.');

for (const symbol of ['TSLA','NVDA','AMD','AAPL','MSFT','GOOGL','META','PLTR','AVGO','TSM','ASML','ARM','ORCL','QQQ','RKLB','ASTS','JOBY']) assert.ok(STOCKS.some(item => item.symbol === symbol), `Missing stock ${symbol}`);
for (const symbol of ['BTC','ETH','BNB','SOL','XRP','USDT','USDC','DOGE','ADA','TRX','AVAX','LINK','TON','DOT','MATIC','ARB','OP','UNI','AAVE','PEPE']) assert.ok(CRYPTOS.some(item => item.symbol === symbol), `Missing crypto ${symbol}`);
for (const name of ['Model S Plaid','Model 3 Long Range','Model X Plaid','Model Y Long Range','Cybertruck','Roadster','Tesla Semi','Electric Delivery Van','Performance Electric Coupe','Optimus','Optimus Pro','Optimus Mini','Kinetic Watch One','Kinetic Watch Ultra','Kinetic Watch SE','Logo Tee','Hoodie','Cap','Jacket','Home Battery','Solar Roof Panel','Survey Drone','DC Fast EV Charger','Home Wall Connector','Portable Solar Generator','Solar Inverter Converter','Inference Cluster']) assert.ok(PRODUCTS.some(item => item.name === name), `Missing product ${name}`);
assert.ok(PRODUCTS.some(item => item.category === 'Home Energy'), 'The shop should provide a dedicated Home Energy collection.');
for (const tier of ['Starter','Builder','Growth','Premium','Wealth','Private']) assert.ok(TIERS.some(item => item.name === tier));
assert.equal(TIERS[0].min, 100);
assert.equal(TIERS.at(-1).max, 10_000_000);
assert.equal(quote(100, 'stable-test', .02), quote(100, 'stable-test', .02), 'Pricing must be deterministic within the current pricing window.');
assert.equal(dailySeed().length, 10);

for (const directory of ['properties','vehicles','energy','wearables','apparel','cards','thumbs']) assert.ok(await exists(`assets/images/${directory}`), `Missing image folder ${directory}`);
for (const property of PROPERTIES) assert.ok(await exists(property.image.replace(/^\.\//,'')), `Missing property image ${property.image}`);
for (const product of PRODUCTS) assert.ok(await exists(product.image.replace(/^\.\//,'')), `Missing product image ${product.image}`);
const dataSource = await file('src/data.js');
const runtimeAssets = new Set([...`${app}\n${dataSource}\n${html}`.matchAll(/\.\/assets\/images\/[\w./-]+?\.(?:png|jpe?g|webp|svg)/g)].map(match => match[0].replace(/^\.\//,'')));
for (const asset of runtimeAssets) assert.ok(await exists(asset), `Missing referenced local runtime asset ${asset}`);
assert.equal(new Set(PROPERTIES.map(item => item.image)).size, PROPERTIES.length, 'Properties need distinct local photographs.');

for (const header of ['X-Frame-Options: DENY','X-Content-Type-Options: nosniff','Referrer-Policy: strict-origin-when-cross-origin','Permissions-Policy:']) assert.ok(headers.includes(header), `Missing deployment header ${header}`);
assert.ok(await exists('deploy/cloudflare-pages/README.md'));
assert.equal(wrangler.match(/pages_build_output_dir\s*=\s*"([^"]+)"/)?.[1], '.', 'Pages must publish the complete repository root, not the docs-only deploy folder.');
assert.ok(wrangler.includes('binding = "DB"'), 'Pages Functions must bind the D1 database as DB.');
assert.ok(!redirects.includes('/* /index.html 200'), 'Avoid Pages self-rewrite loop for index.html.');
assert.ok(await exists('migrations/0001_initial.sql'));
assert.ok(functions.includes("DEFAULT_ADMIN_PASSWORD = 'Admin@goal.system'"));
for (const endpoint of ["path[1] === 'sign-in'","path[1] === 'sign-up'","path[1] === 'reset'","path[1] === 'deposit'","path[1] === 'withdraw'","path[1] === 'transfer'","path[1] === 'users'","path[1] === 'requests'","path[1] === 'wallet-adjustment'","path[1] === 'announcements'","path[1] === 'payment-methods'"]) assert.ok(functions.includes(endpoint), `Missing backend route ${endpoint}`);
assert.match(css, /prefers-reduced-motion/);
assert.match(css, /@media\s*\(max-width:\s*640px\)/);
assert.match(css, /\.mobile-nav/);
assert.match(css, /overflow-x:\s*auto/);
assert.ok(!/@import\s+url\(['"]?https?:/i.test(css), 'Stylesheets must not import external resources.');

const sessions = new Map();
const mockDB = {
  prepare(sql) {
    const query = {
      values: [],
      bind(...values) { this.values = values; return this; },
      async first() {
        if (sql.startsWith('SELECT id FROM users LIMIT 1')) return { id: 'health-check' };
        if (sql.includes('FROM sessions s LEFT JOIN users')) {
          const record = sessions.get(this.values[0]);
          return record ? { session_id: this.values[0], session_role: record.role, expires_at: record.expiresAt, user_id: null } : null;
        }
        return null;
      },
      async run() {
        if (sql.startsWith('INSERT INTO sessions')) {
          const [id, , role, expiresAt] = this.values;
          sessions.set(id, { role, expiresAt });
          return { meta: { changes: 1 } };
        }
        if (sql.startsWith('DELETE FROM sessions')) sessions.delete(this.values[0]);
        return { meta: { changes: 1 } };
      },
      async all() { return { results: [] }; },
    };
    return query;
  },
  async batch() { return { success: true }; },
};
const login = await onRequest({
  request: new Request('https://goal-system.example/api/auth/sign-in', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'Admin', password: 'Admin@goal.system' }) }),
  env: { DB: mockDB },
});
assert.equal(login.status, 200, 'Default Admin credentials sign in through the standard server endpoint.');
assert.match(login.headers.get('set-cookie') || '', /HttpOnly/);
const cookie = (login.headers.get('set-cookie') || '').split(';')[0];
const session = await onRequest({ request: new Request('https://goal-system.example/api/auth/session', { headers: { cookie } }), env: { DB: mockDB } });
assert.equal((await session.json()).user?.role, 'admin');
const inbox = await onRequest({ request: new Request('https://goal-system.example/api/messages/inbox', { headers: { cookie } }), env: { DB: mockDB } });
assert.equal(inbox.status, 200, 'Admin inbox must not be intercepted by the general messages route.');
assert.deepEqual((await inbox.json()).conversations, []);

console.log('Goal system validation passed.');
console.log(`Stocks: ${STOCKS.length} · Crypto assets: ${CRYPTOS.length} · Products: ${PRODUCTS.length} · Properties: ${PROPERTIES.length} · Pages API smoke: passed`);
