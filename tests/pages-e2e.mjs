import assert from 'node:assert/strict';

const base = (process.env.GOAL_SYSTEM_BASE_URL || 'http://127.0.0.1:8788').replace(/\/$/, '');
const api = `${base}/api`;

const home = await fetch(`${base}/`);
assert.equal(home.status, 200, 'Pages should serve the application shell.');
assert.match(await home.text(), /<title>Goal system/);
const clientRoute = await fetch(`${base}/dashboard`);
assert.equal(clientRoute.status, 200, 'Client-side routes should fall back to the application shell.');
assert.match(await clientRoute.text(), /<title>Goal system/);
const clientBundle = await fetch(`${base}/src/app.js`);
assert.equal(clientBundle.status, 200, 'Pages should serve the SPA module.');

async function request(path, { method = 'GET', body, cookie, expected = 200 } = {}) {
  const headers = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (cookie) headers.cookie = cookie;
  const response = await fetch(`${api}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* Report status below. */ }
  assert.equal(response.status, expected, `${method} ${path}: ${payload.error || response.statusText}`);
  return { payload, cookie: response.headers.get('set-cookie')?.split(';', 1)[0] || cookie };
}

const health = await request('/health');
assert.equal(health.payload.ready, true, 'D1 health route must be ready before integration checks.');
await request('/state', { expected: 401 });

const suffix = Date.now().toString(36).slice(-7);
const aliceLogin = await request('/auth/sign-up', {
  method: 'POST', expected: 201,
  body: { name: 'Alice Integration', username: `alice${suffix}`, email: `alice-${suffix}@example.test`, password: 'integration password one' },
});
const aliceId = aliceLogin.payload.user.id;
const aliceCookie = aliceLogin.cookie;
assert.match(aliceCookie || '', /^goal_session=/, 'Sign-up must set a server session cookie.');

let state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.id, aliceId);
assert.ok(state.payload.state.paymentMethods.length > 0, 'User state should read payment methods from D1.');

await request('/state', {
  method: 'PUT', cookie: aliceCookie,
  body: { state: {
    goals: [{ id: `goal-${suffix}`, name: 'First home', target: 25000, current: 500, monthly: 300 }],
    holdings: [{ id: `holding-${suffix}`, assetClass: 'US Equity', ticker: 'GVUS', value: 100, costBasis: 100 }],
    cart: [{ productId: 'logo-tee', variant: 'M', quantity: 1 }],
    orders: [{ id: `order-${suffix}`, status: 'Received' }],
    watchlist: ['TSLA', 'BTC'], settings: { plan: 'Builder', risk: 'Balanced' },
    profile: { name: 'Alice Integration Updated', email: `alice-${suffix}@example.test` },
  } },
});
state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.name, 'Alice Integration Updated');
assert.equal(state.payload.state.goals[0].id, `goal-${suffix}`);
assert.equal(state.payload.state.holdings[0].ticker, 'GVUS');
assert.equal(state.payload.state.cart[0].productId, 'logo-tee');
assert.equal(state.payload.state.orders[0].id, `order-${suffix}`);

const adminLogin = await request('/auth/sign-in', {
  method: 'POST', body: { username: 'Admin', password: process.env.ADMIN_PASSWORD || 'Admin@goal.system' },
});
const adminCookie = adminLogin.cookie;
assert.equal(adminLogin.payload.user.role, 'admin');
await request('/admin/users', { cookie: aliceCookie, expected: 403 });

const deposit = (await request('/wallet/deposit', {
  method: 'POST', cookie: aliceCookie, expected: 201,
  body: { amount: 50, method: state.payload.state.paymentMethods[0].id, reference: `deposit-${suffix}` },
})).payload;
assert.equal(deposit.status, 'Pending');
const pending = (await request('/admin/requests', { cookie: adminCookie })).payload.requests.find(item => item.id === deposit.id);
assert.equal(pending.username, `alice${suffix}`);
await request(`/admin/requests/${deposit.id}`, { method: 'POST', cookie: adminCookie, body: { status: 'Approved' } });
state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.wallet.available, 50);
assert.equal(state.payload.user.wallet.deposits, 50);

const withdrawal = (await request('/wallet/withdraw', {
  method: 'POST', cookie: aliceCookie, expected: 201,
  body: { amount: 20, holder: 'Alice Integration', details: 'Bank ending 0001' },
})).payload;
await request('/wallet/withdraw', { method: 'POST', cookie: aliceCookie, body: { amount: 31, details: 'Should exceed available after reserve' }, expected: 409 });
await request(`/admin/requests/${withdrawal.id}`, { method: 'POST', cookie: adminCookie, body: { status: 'Rejected' } });

const bobLogin = await request('/auth/sign-up', {
  method: 'POST', expected: 201,
  body: { name: 'Bob Integration', username: `bob${suffix}`, email: `bob-${suffix}@example.test`, password: 'integration password two' },
});
const bobCookie = bobLogin.cookie;
await request('/wallet/transfer', { method: 'POST', cookie: aliceCookie, expected: 201, body: { username: `bob${suffix}`, amount: 10, note: 'Integration transfer' } });
state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.wallet.available, 40, 'Transfer should persist the sender debit.');
const bobState = await request('/state', { cookie: bobCookie });
assert.equal(bobState.payload.user.wallet.available, 10, 'Transfer should persist the recipient credit.');

await request('/wallet/invest', { method: 'POST', cookie: aliceCookie, body: { amount: 10, assetClass: 'US Equity', goalId: `goal-${suffix}` } });
state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.wallet.available, 30);
assert.equal(state.payload.user.wallet.invested, 10);
assert.ok(state.payload.state.activity.some(item => item.type === 'Investment'));

await request('/messages', { method: 'POST', cookie: aliceCookie, expected: 201, body: { text: 'Integration help request' } });
let thread = (await request(`/messages?userId=${encodeURIComponent(aliceId)}`, { cookie: adminCookie })).payload.messages;
assert.equal(thread[0].text, 'Integration help request');
await request('/messages', { method: 'POST', cookie: adminCookie, expected: 201, body: { userId: aliceId, text: 'Integration admin reply' } });
thread = (await request('/messages', { cookie: aliceCookie })).payload.messages;
assert.equal(thread.at(-1).text, 'Integration admin reply');
const inbox = (await request('/messages/inbox', { cookie: adminCookie })).payload.conversations;
assert.ok(inbox.some(item => item.id === aliceId), 'The admin inbox must return the member conversation.');

const announcement = (await request('/admin/announcements', {
  method: 'POST', cookie: adminCookie, expected: 201,
  body: { title: `Integration ${suffix}`, body: 'D1 announcement read/write check', audience: 'all', level: 'Update' },
})).payload.announcement;
state = await request('/state', { cookie: aliceCookie });
assert.ok(state.payload.state.announcements.some(item => item.id === announcement.id));
await request('/admin/wallet-adjustment', {
  method: 'POST', cookie: adminCookie,
  body: { userId: aliceId, field: 'earnings', operation: 'add', amount: 5, note: 'Integration check' },
});
state = await request('/state', { cookie: aliceCookie });
assert.equal(state.payload.user.wallet.earnings, 5);

await request('/auth/sign-out', { method: 'POST', cookie: aliceCookie });
await request('/state', { cookie: aliceCookie, expected: 401 });
const relogin = await request('/auth/sign-in', {
  method: 'POST', body: { username: `alice${suffix}`, password: 'integration password one' },
});
state = await request('/state', { cookie: relogin.cookie });
assert.equal(state.payload.user.id, aliceId, 'D1 data should remain available after sign-out and sign-in.');
assert.equal(state.payload.user.wallet.earnings, 5);

console.log('Cloudflare Pages + D1 end-to-end read/write checks passed.');
