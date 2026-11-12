import assert from 'node:assert/strict';

class MemoryStorage {
  #items = new Map();
  getItem(key) { return this.#items.get(key) || null; }
  setItem(key, value) { this.#items.set(key, String(value)); }
  removeItem(key) { this.#items.delete(key); }
}

const root = { innerHTML: '' };
const listeners = {};
globalThis.localStorage = new MemoryStorage();
globalThis.sessionStorage = new MemoryStorage();
globalThis.document = {
  querySelector: (selector) => selector === '#app' ? root : null,
  addEventListener: (name, listener) => { (listeners[name] ||= []).push(listener); },
};

await import('../src/app.js?landing-smoke');
assert.match(root.innerHTML, /Plan with purpose/);
assert.match(root.innerHTML, /SIGN IN/);

const user = {
  id: 'smoke-user', username: 'alice', name: 'Alice Example', email: 'alice@example.com', role: 'user',
  createdAt: new Date().toISOString(),
  wallet: { available: 2500, invested: 12000, earnings: 320, deposits: 5000, withdrawals: 0, transfersIn: 0, transfersOut: 0 },
  goals: [{ id: 'goal-1', name: 'First home', target: 25000, current: 5400, monthly: 400, targetDate: '2028-01-01', risk: 'Balanced', allocation: 'Balanced portfolio' }],
  holdings: [{ id: 'holding-1', assetClass: 'US Equity', ticker: 'GVUS', name: 'US Equity allocation', value: 8000, costBasis: 7600, ytd: 8, expenseRatio: 0.04 }],
  activity: [], requests: [], cart: [], orders: [], savedProperties: [], watchlist: ['TSLA', 'BTC'],
  settings: { plan: 'Builder', risk: 'Balanced', autoInvest: true, roundUps: false, emailAlerts: true, pushAlerts: true },
};
localStorage.setItem('goal-system.data.v1', JSON.stringify({ version: 1, users: [user], announcements: [], paymentMethods: [], messages: [], activity: [], requests: [], settings: {} }));
sessionStorage.setItem('goal-system.session.v1', JSON.stringify({ role: 'user', userId: user.id }));
await import('../src/app.js?member-smoke');
assert.match(root.innerHTML, /Available balance/);

const memberClick = listeners.click.at(-2);
for (const [route, expected] of [
  ['Goals', 'Your goals'], ['Portfolio', 'Asset allocation'], ['Stock Market', 'Market watch'],
  ['Crypto Trading', 'Top digital assets'], ['Properties', 'PROPERTY COLLECTION'], ['Shop', 'Electric mobility'],
  ['Activity', 'Transaction ledger'], ['Simulator', 'Contribution simulator'], ['Settings', 'Profile details'],
]) {
  const target = { closest: (selector) => selector === '[data-route]' ? { dataset: { route }, preventDefault() {} } : null };
  memberClick({ target, preventDefault() {} });
  assert.ok(root.innerHTML.includes(expected), `Member route ${route} should render ${expected}.`);
}

localStorage.setItem('goal-system.data.v1', JSON.stringify({ version: 1, users: [], announcements: [], paymentMethods: [], messages: [], activity: [], requests: [], settings: {} }));
sessionStorage.setItem('goal-system.session.v1', JSON.stringify({ role: 'admin' }));
await import('../src/app.js?admin-smoke');
assert.match(root.innerHTML, /Admin Console/);
console.log('Landing, member routes and Admin Console render smoke passed.');
