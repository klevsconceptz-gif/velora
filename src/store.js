const DB_KEY = 'goal-system.data.v1';
const SESSION_KEY = 'goal-system.session.v1';

export function newId(prefix = 'gv') {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export function initialDatabase() {
  return {
    version: 1,
    users: [],
    announcements: [{
      id: 'welcome-note',
      title: 'A clearer way to build toward what matters',
      body: 'Welcome to Goal system. Set a goal, choose a pace that feels sustainable and check in whenever you need a fresh perspective.',
      audience: 'all',
      level: 'Update',
      author: 'Admin',
      createdAt: new Date().toISOString(),
    }],
    paymentMethods: [
      { id: 'method-ach', name: 'ACH transfer', detail: 'Account details are shared after you create a deposit request.', time: '1–3 business days', fee: 'No platform fee' },
      { id: 'method-wire', name: 'Wire transfer', detail: 'Request wire instructions from the payments desk.', time: 'Same business day', fee: 'Bank fees may apply' },
      { id: 'method-card', name: 'Debit card', detail: 'Card deposits are reviewed before your wallet is updated.', time: 'Usually within 1 business day', fee: 'Processing may apply' },
    ],
    messages: [],
    activity: [],
    requests: [],
    settings: { maintenanceMode: false },
  };
}

function mergeDatabase(saved) {
  const base = initialDatabase();
  if (!saved || typeof saved !== 'object') return base;
  return {
    ...base,
    ...saved,
    users: Array.isArray(saved.users) ? saved.users : [],
    announcements: Array.isArray(saved.announcements) ? saved.announcements : base.announcements,
    paymentMethods: Array.isArray(saved.paymentMethods) ? saved.paymentMethods : base.paymentMethods,
    messages: Array.isArray(saved.messages) ? saved.messages : [],
    requests: Array.isArray(saved.requests) ? saved.requests : [],
  };
}

export function loadDatabase() {
  try {
    const saved = localStorage.getItem(DB_KEY);
    const db = saved ? mergeDatabase(JSON.parse(saved)) : initialDatabase();
    if (!saved) saveDatabase(db);
    return db;
  } catch {
    return initialDatabase();
  }
}

export function saveDatabase(db) {
  try { localStorage.setItem(DB_KEY, JSON.stringify(db)); } catch (error) { console.error('Unable to save local workspace data.', error); }
}

export function loadSession() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null'); } catch { return null; }
}
export function saveSession(session) {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch { /* Private browsing can disable sessionStorage; the active view still works. */ }
}
export function findUser(db, id) { return db.users.find((user) => user.id === id) || null; }
export function userByUsername(db, username) {
  const normalized = String(username || '').trim().toLowerCase();
  return db.users.find((user) => user.username.toLowerCase() === normalized || user.email.toLowerCase() === normalized) || null;
}
export function addActivity(db, user, type, title, amount, meta = {}) {
  const row = { id: newId('act'), userId: user.id, type, title, amount: Number(amount || 0), date: new Date().toISOString(), ...meta };
  user.activity ||= [];
  user.activity.unshift(row);
  db.activity.unshift(row);
  return row;
}
export function createUserRecord({ username, name, email, passwordHash }) {
  return {
    id: newId('user'),
    username: String(username).trim(),
    name: String(name).trim(),
    email: String(email).trim().toLowerCase(),
    role: 'user',
    passwordHash,
    createdAt: new Date().toISOString(),
    wallet: { available: 0, invested: 0, earnings: 0, deposits: 0, withdrawals: 0, transfersIn: 0, transfersOut: 0 },
    goals: [],
    holdings: [],
    activity: [],
    requests: [],
    cart: [],
    orders: [],
    savedProperties: [],
    watchlist: ['TSLA', 'NVDA', 'AAPL', 'BTC', 'ETH'],
    settings: { plan: 'Starter', risk: 'Balanced', autoInvest: false, roundUps: false, emailAlerts: true, pushAlerts: true },
  };
}
export function visibleAnnouncements(db, user) {
  return db.announcements.filter((item) => item.audience === 'all' || item.audience === user.id || String(item.audience).toLowerCase() === user.username.toLowerCase()).sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
}
export function messagesFor(db, userId) {
  return db.messages.filter((message) => message.userId === userId).sort((a, b) => new Date(a.createdAt) - new Date(b.createdAt));
}
export function saveAll(db) { saveDatabase(db); }
