const ADMIN_USERNAME = 'Admin';
const DEFAULT_ADMIN_PASSWORD = 'Admin@goal.system';
const ITERATIONS = 120000;
const SESSION_DAYS = 14;
const COOKIE_NAME = 'goal_session';
const encoder = new TextEncoder();

const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers } });
const fail = (message, status = 400) => json({ error: message }, status);
const id = (prefix = 'gv') => `${prefix}-${crypto.randomUUID()}`;
const now = () => new Date().toISOString();
const text = (value, max = 200) => String(value ?? '').trim().slice(0, max);
const usernameKey = (value) => text(value, 24).toLowerCase();
const emailKey = (value) => text(value, 140).toLowerCase();
const safeNumber = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
const parseJson = async (request) => { try { return await request.json(); } catch { return {}; } };

function toHex(bytes) { return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join(''); }
function fromHex(value) { return new Uint8Array(String(value).match(/.{1,2}/g)?.map((pair) => parseInt(pair, 16)) || []); }
function randomHex(size = 16) { const bytes = new Uint8Array(size); crypto.getRandomValues(bytes); return toHex(bytes); }
async function passwordHash(password, salt = randomHex(), iterations = ITERATIONS) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: fromHex(salt), iterations, hash: 'SHA-256' }, key, 256);
  return { salt, hash: toHex(bits), iterations };
}
async function verifyPassword(password, row) {
  if (!row?.password_salt || !row?.password_hash) return false;
  const result = await passwordHash(password, row.password_salt, Number(row.password_iterations || ITERATIONS));
  return result.hash === row.password_hash;
}
function cookieHeader(sessionId, url, maxAge = SESSION_DAYS * 86400) {
  return `${COOKIE_NAME}=${sessionId}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${maxAge}${url.protocol === 'https:' ? '; Secure' : ''}`;
}
function getCookie(request, name) {
  const cookie = request.headers.get('Cookie') || '';
  const value = cookie.split(';').map((part) => part.trim()).find((part) => part.startsWith(`${name}=`));
  return value ? decodeURIComponent(value.slice(name.length + 1)) : '';
}
function publicUser(row) {
  if (!row) return null;
  return {
    id: row.id, username: row.username, email: row.email, name: row.display_name, role: row.role,
    createdAt: row.created_at,
    wallet: {
      available: Number(row.wallet_available || 0), invested: Number(row.wallet_invested || 0), earnings: Number(row.wallet_earnings || 0),
      deposits: Number(row.wallet_deposits || 0), withdrawals: Number(row.wallet_withdrawals || 0),
      transfersIn: Number(row.wallet_transfers_in || 0), transfersOut: Number(row.wallet_transfers_out || 0),
    },
  };
}
async function resolveSession(request, env) {
  const cookie = getCookie(request, COOKIE_NAME);
  if (!cookie) return null;
  const record = await env.DB.prepare(`SELECT s.id AS session_id,s.role AS session_role,s.expires_at,u.id AS user_id,u.username,u.email,u.display_name,u.role,u.created_at,u.wallet_available,u.wallet_invested,u.wallet_earnings,u.wallet_deposits,u.wallet_withdrawals,u.wallet_transfers_in,u.wallet_transfers_out,u.wallet_reserved FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.id=? AND s.expires_at>? LIMIT 1`).bind(cookie, now()).first();
  if (!record) return null;
  return { id: record.session_id, role: record.session_role, user: record.user_id ? publicUser({ ...record, id: record.user_id }) : null };
}
async function requireSession(request, env) {
  const session = await resolveSession(request, env);
  if (!session) throw Object.assign(new Error('Sign in to continue.'), { status: 401 });
  return session;
}
function requireAdmin(session) {
  if (session.role !== 'admin') throw Object.assign(new Error('Administrator access required.'), { status: 403 });
}
function requireUser(session) {
  if (session.role !== 'user' || !session.user?.id) throw Object.assign(new Error('A member account is required.'), { status: 403 });
}
async function createSession(env, userId, role) {
  const sessionId = randomHex(32), expires = new Date(Date.now() + SESSION_DAYS * 86400000).toISOString();
  await env.DB.prepare('INSERT INTO sessions(id,user_id,role,expires_at,created_at) VALUES(?,?,?,?,?)').bind(sessionId, userId || null, role, expires, now()).run();
  return sessionId;
}
function walletFor(row) { return publicUser(row)?.wallet || {}; }
function safeState(value) {
  const source = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const result = {};
  for (const key of ['goals', 'holdings', 'cart', 'orders', 'savedProperties', 'watchlist', 'settings']) {
    if (source[key] !== undefined) result[key] = source[key];
  }
  if (source.profile && typeof source.profile === 'object') {
    const name = text(source.profile.name, 70), email = emailKey(source.profile.email);
    if (name && email.includes('@')) result.profile = { name, email };
  }
  return result;
}
async function getState(env, userId) {
  const [stateRow, userRow, ledgerRows, requestRows, announcementRows, methodRows] = await Promise.all([
    env.DB.prepare('SELECT data_json FROM user_state WHERE user_id=?').bind(userId).first(),
    env.DB.prepare('SELECT * FROM users WHERE id=?').bind(userId).first(),
    env.DB.prepare('SELECT id,type,title,amount,note,created_at AS date FROM ledger WHERE user_id=? ORDER BY created_at DESC LIMIT 300').bind(userId).all(),
    env.DB.prepare('SELECT id,user_id,type,amount,status,method,reference,holder,details,note,created_at,reviewed_at,review_note FROM requests WHERE user_id=? ORDER BY created_at DESC LIMIT 200').bind(userId).all(),
    env.DB.prepare("SELECT * FROM announcements WHERE audience='all' OR audience=? OR lower(audience)=(SELECT username_key FROM users WHERE id=?) ORDER BY created_at DESC LIMIT 100").bind(userId, userId).all(),
    env.DB.prepare('SELECT * FROM payment_methods ORDER BY created_at').all(),
  ]);
  let state = {};
  try { state = JSON.parse(stateRow?.data_json || '{}'); } catch { state = {}; }
  const activity = (ledgerRows.results || []).map((item) => ({ id: item.id, type: item.type, title: item.title, amount: Number(item.amount || 0), note: item.note, date: item.date }));
  const requests = (requestRows.results || []).map((item) => ({ id: item.id, userId: item.user_id, type: item.type, amount: Number(item.amount || 0), status: item.status, method: item.method, reference: item.reference, holder: item.holder, details: item.details, note: item.note, createdAt: item.created_at, reviewedAt: item.reviewed_at, reviewNote: item.review_note }));
  const announcements = (announcementRows.results || []).map((item) => ({ id: item.id, title: item.title, body: item.body, audience: item.audience, level: item.level, author: item.author, createdAt: item.created_at }));
  const paymentMethods = (methodRows.results || []).map((item) => ({ id: item.id, name: item.name, detail: item.detail, time: item.processing_time, fee: item.fee }));
  return { user: publicUser(userRow), state: { ...state, activity, requests, announcements, paymentMethods }, requests };
}
async function insertLedger(env, userId, type, title, amount = 0, note = '') {
  await env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), userId, type, title, amount, text(note, 400), now()).run();
}
async function getUserById(env, userId) { return env.DB.prepare('SELECT * FROM users WHERE id=?').bind(userId).first(); }
async function routeApi(request, env, url) {
  const path = url.pathname.replace(/^\/api\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  const method = request.method.toUpperCase();
  const first = path[0] || '';
  if (first === 'health' && method === 'GET') {
    try { await env.DB.prepare('SELECT id FROM users LIMIT 1').first(); return json({ ready: true, service: 'Goal system API' }); }
    catch { return json({ ready: false, service: 'Goal system API', message: 'Apply the initial D1 migration to enable server storage.' }); }
  }
  if (!env.DB) return json({ error: 'The Goal system database binding is not configured.' }, 503);

  if (first === 'auth' && path[1] === 'sign-up' && method === 'POST') {
    const body = await parseJson(request), name = text(body.name, 70), username = text(body.username, 24), email = emailKey(body.email), password = String(body.password || '');
    if (!name || !email.includes('@') || !/^[a-zA-Z0-9_.-]{3,24}$/.test(username) || username.toLowerCase() === 'admin') return fail('Enter a valid name, email and username.');
    if (password.length < 8 || password.length > 200) return fail('Password must be at least eight characters.');
    const hashed = await passwordHash(password), userId = id('user'), created = now();
    try {
      await env.DB.batch([
        env.DB.prepare('INSERT INTO users(id,username,username_key,email,email_key,display_name,role,password_salt,password_hash,password_iterations,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(userId, username, username.toLowerCase(), email, email, name, 'user', hashed.salt, hashed.hash, hashed.iterations, created),
        env.DB.prepare('INSERT INTO user_state(user_id,data_json,updated_at) VALUES(?,?,?)').bind(userId, '{}', created),
        env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), userId, 'Account', 'Goal system account created', 0, 'Your workspace is ready.', created),
      ]);
    } catch (error) { return fail('That username or email is already registered.', 409); }
    const sessionId = await createSession(env, userId, 'user'), row = await getUserById(env, userId);
    return json({ user: publicUser(row), state: { goals: [], cart: [], orders: [], savedProperties: [], watchlist: ['TSLA', 'NVDA', 'AAPL', 'BTC', 'ETH'], settings: { plan: 'Starter', risk: 'Balanced', autoInvest: false, roundUps: false, emailAlerts: true, pushAlerts: true }, activity: [], requests: [] } }, 201, { 'set-cookie': cookieHeader(sessionId, url) });
  }
  if (first === 'auth' && path[1] === 'sign-in' && method === 'POST') {
    const body = await parseJson(request), username = text(body.username, 140), password = String(body.password || '');
    if (username.toLowerCase() === ADMIN_USERNAME.toLowerCase() && password === (env.ADMIN_PASSWORD || DEFAULT_ADMIN_PASSWORD)) {
      const sessionId = await createSession(env, null, 'admin');
      return json({ user: { id: 'admin', username: ADMIN_USERNAME, name: 'Admin', email: '', role: 'admin' } }, 200, { 'set-cookie': cookieHeader(sessionId, url) });
    }
    const key = username.toLowerCase();
    const row = await env.DB.prepare('SELECT * FROM users WHERE username_key=? OR email_key=? LIMIT 1').bind(key, key).first();
    if (!row || row.role !== 'user' || !await verifyPassword(password, row)) return fail('Those sign-in details did not match an account.', 401);
    const sessionId = await createSession(env, row.id, 'user');
    return json({ user: publicUser(row) }, 200, { 'set-cookie': cookieHeader(sessionId, url) });
  }
  if (first === 'auth' && path[1] === 'reset' && method === 'POST') {
    const body = await parseJson(request), key = usernameKey(body.username), email = emailKey(body.email), password = String(body.password || '');
    if (password.length < 8 || password.length > 200) return fail('Password must be at least eight characters.');
    const user = await env.DB.prepare('SELECT id FROM users WHERE (username_key=? OR email_key=?) AND email_key=? LIMIT 1').bind(key, key, email).first();
    if (!user) return fail('We could not match that username and email to an account.', 404);
    const hashed = await passwordHash(password);
    await env.DB.prepare('UPDATE users SET password_salt=?,password_hash=?,password_iterations=? WHERE id=?').bind(hashed.salt, hashed.hash, hashed.iterations, user.id).run();
    await env.DB.prepare('DELETE FROM sessions WHERE user_id=?').bind(user.id).run();
    return json({ ok: true });
  }
  if (first === 'auth' && path[1] === 'session' && method === 'GET') {
    const session = await resolveSession(request, env);
    if (!session) return json({ user: null }, 200);
    if (session.role === 'admin') return json({ user: { id: 'admin', username: ADMIN_USERNAME, name: 'Admin', email: '', role: 'admin' } });
    return json(await getState(env, session.user.id));
  }
  if (first === 'auth' && path[1] === 'sign-out' && method === 'POST') {
    const cookie = getCookie(request, COOKIE_NAME);
    if (cookie) await env.DB.prepare('DELETE FROM sessions WHERE id=?').bind(cookie).run();
    return json({ ok: true }, 200, { 'set-cookie': cookieHeader('', url, 0) });
  }
  if (first === 'state' && method === 'GET') {
    const session = await requireSession(request, env); requireUser(session); return json(await getState(env, session.user.id));
  }
  if (first === 'state' && method === 'PUT') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), state = safeState(body.state);
    if (JSON.stringify(state).length > 250000) return fail('Workspace state is too large.');
    const prev = await env.DB.prepare('SELECT data_json FROM user_state WHERE user_id=?').bind(session.user.id).first(); let merged = {};
    try { merged = JSON.parse(prev?.data_json || '{}'); } catch { merged = {}; }
    Object.assign(merged, state);
    const writes = [env.DB.prepare('INSERT INTO user_state(user_id,data_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at').bind(session.user.id, JSON.stringify(merged), now())];
    if (state.profile) writes.push(env.DB.prepare('UPDATE users SET display_name=?,email=?,email_key=? WHERE id=?').bind(state.profile.name, state.profile.email, state.profile.email, session.user.id));
    await env.DB.batch(writes);
    return json({ ok: true });
  }

  if (first === 'wallet' && path[1] === 'deposit' && method === 'POST') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), amount = safeNumber(body.amount);
    if (amount < 10 || amount > 10_000_000) return fail('Deposit amount must be at least $10.');
    const methodRow = await env.DB.prepare('SELECT name FROM payment_methods WHERE id=?').bind(text(body.method, 100)).first();
    if (!methodRow) return fail('Choose an available payment method.');
    const requestId = id('req'), created = now(), reference = text(body.reference, 120);
    await env.DB.batch([
      env.DB.prepare('INSERT INTO requests(id,user_id,type,amount,status,method,reference,created_at) VALUES(?,?,?,?,?,?,?,?)').bind(requestId, session.user.id, 'Deposit', amount, 'Pending', methodRow.name, reference, created),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), session.user.id, 'Deposit request', 'Deposit request submitted', 0, `${amount} · Awaiting review`, created),
    ]);
    return json({ id: requestId, type: 'Deposit', amount, status: 'Pending', method: methodRow.name, reference, createdAt: created }, 201);
  }
  if (first === 'wallet' && path[1] === 'withdraw' && method === 'POST') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), amount = safeNumber(body.amount);
    if (amount < 10 || amount > 10_000_000) return fail('Withdrawal amount must be at least $10.');
    const user = await getUserById(env, session.user.id);
    if (amount > Number(user.wallet_available) - Number(user.wallet_reserved)) return fail('The amount is greater than your available balance.', 409);
    const requestId = id('req'), created = now();
    await env.DB.batch([
      env.DB.prepare('UPDATE users SET wallet_reserved=wallet_reserved+? WHERE id=?').bind(amount, user.id),
      env.DB.prepare('INSERT INTO requests(id,user_id,type,amount,status,method,reference,holder,details,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(requestId, user.id, 'Withdrawal', amount, 'Pending', 'Bank / wallet destination', text(body.details, 180), text(body.holder, 100), text(body.details, 180), text(body.note, 150), created),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), user.id, 'Withdrawal request', 'Withdrawal request submitted', 0, `${amount} · Awaiting review`, created),
    ]);
    return json({ id: requestId, type: 'Withdrawal', amount, status: 'Pending', createdAt: created }, 201);
  }
  if (first === 'wallet' && path[1] === 'transfer' && method === 'POST') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), amount = safeNumber(body.amount), targetName = usernameKey(body.username);
    if (amount <= 0 || amount > 10_000_000) return fail('Enter a valid transfer amount.');
    const [sender, recipient] = await Promise.all([
      getUserById(env, session.user.id), env.DB.prepare('SELECT * FROM users WHERE username_key=? LIMIT 1').bind(targetName).first(),
    ]);
    if (!recipient) return fail('We could not find a registered member with that username.', 404);
    if (recipient.id === sender.id) return fail('Choose another member. You cannot transfer funds to yourself.');
    if (amount > Number(sender.wallet_available)) return fail('The amount is greater than your available balance.', 409);
    const transferId = id('tr'), created = now(), note = text(body.note, 160);
    await env.DB.batch([
      env.DB.prepare('UPDATE users SET wallet_available=wallet_available-?,wallet_transfers_out=wallet_transfers_out+? WHERE id=? AND wallet_available>=?').bind(amount, amount, sender.id, amount),
      env.DB.prepare('UPDATE users SET wallet_available=wallet_available+?,wallet_transfers_in=wallet_transfers_in+? WHERE id=?').bind(amount, amount, recipient.id),
      env.DB.prepare('INSERT INTO transfers(id,sender_id,recipient_id,amount,note,created_at) VALUES(?,?,?,?,?,?)').bind(transferId, sender.id, recipient.id, amount, note, created),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), sender.id, 'Transfer', `Transfer to @${recipient.username}`, -amount, note, created),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), recipient.id, 'Transfer', `Transfer from @${sender.username}`, amount, note, created),
    ]);
    return json({ id: transferId, recipient: { username: recipient.username, name: recipient.display_name }, amount, createdAt: created }, 201);
  }
  if (first === 'wallet' && path[1] === 'invest' && method === 'POST') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), amount = safeNumber(body.amount), user = await getUserById(env, session.user.id);
    if (amount < 10 || amount > Number(user.wallet_available)) return fail('Investment amount must be at least $10 and within your available balance.', 409);
    const created = now(), note = text(body.assetClass || 'US Equity', 60);
    await env.DB.batch([
      env.DB.prepare('UPDATE users SET wallet_available=wallet_available-?,wallet_invested=wallet_invested+? WHERE id=? AND wallet_available>=?').bind(amount, amount, user.id, amount),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), user.id, 'Investment', 'Portfolio investment', -amount, note, created),
    ]);
    return json({ ok: true, amount });
  }

  if (first === 'wallet' && path[1] === 'payment' && method === 'POST') {
    const session = await requireSession(request, env); requireUser(session); const body = await parseJson(request), amount = safeNumber(body.amount), title = text(body.title || 'Marketplace order payment', 120), user = await getUserById(env, session.user.id);
    if (amount <= 0 || amount > Number(user.wallet_available)) return fail('The payment is greater than your available balance.', 409);
    const created = now(); await env.DB.batch([
      env.DB.prepare('UPDATE users SET wallet_available=wallet_available-? WHERE id=? AND wallet_available>=?').bind(amount, user.id, amount),
      env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'), user.id, 'Payment', title, -amount, text(body.note || 'Wallet checkout', 180), created),
    ]); return json({ ok: true, amount });
  }

  if (first === 'messages' && method === 'GET' && path[1] !== 'inbox') {
    const session = await requireSession(request, env), target = url.searchParams.get('userId');
    const userId = session.role === 'admin' ? text(target, 100) : session.user.id;
    if (session.role === 'admin') requireAdmin(session); else requireUser(session);
    if (!userId) return fail('Choose a member conversation.');
    if (session.role === 'user' && userId !== session.user.id) return fail('You can only access your own conversation.', 403);
    const result = await env.DB.prepare('SELECT id,user_id,sender,body,read_at,created_at FROM messages WHERE user_id=? ORDER BY created_at ASC LIMIT 500').bind(userId).all();
    if (session.role === 'admin') await env.DB.prepare('UPDATE messages SET read_at=? WHERE user_id=? AND sender=? AND read_at IS NULL').bind(now(), userId, 'user').run();
    return json({ messages: (result.results || []).map(m => ({ id:m.id,userId:m.user_id,sender:m.sender,text:m.body,read:Boolean(m.read_at),createdAt:m.created_at })) });
  }
  if (first === 'messages' && method === 'POST') {
    const session = await requireSession(request, env), body = await parseJson(request), target = text(body.userId, 100);
    const userId = session.role === 'admin' ? target : session.user.id;
    if (session.role === 'admin') requireAdmin(session); else requireUser(session);
    if (!userId || !await getUserById(env, userId)) return fail('Choose an active member conversation.');
    const message = text(body.text, 1000); if (!message) return fail('Write a message before sending.');
    const messageId = id('msg'), created = now(), sender = session.role === 'admin' ? 'admin' : 'user';
    await env.DB.prepare('INSERT INTO messages(id,user_id,sender,body,created_at) VALUES(?,?,?,?,?)').bind(messageId, userId, sender, message, created).run();
    return json({ id: messageId, userId, sender, text: message, createdAt: created }, 201);
  }

  if (first === 'admin') {
    const session = await requireSession(request, env); requireAdmin(session);
    if (path[1] === 'users' && method === 'GET') {
      const result = await env.DB.prepare('SELECT * FROM users WHERE role=? ORDER BY created_at DESC').bind('user').all();
      const users = await Promise.all(result.results.map(async row => { const state = await getState(env, row.id); return { ...publicUser(row), ...state.state }; }));
      return json({ users });
    }
    if (path[1] === 'users' && method === 'POST') {
      const body = await parseJson(request), name = text(body.name,70), username = text(body.username,24), email = emailKey(body.email), password = String(body.password||'');
      if (!name || !/^[a-zA-Z0-9_.-]{3,24}$/.test(username) || username.toLowerCase()==='admin' || !email.includes('@')) return fail('Enter a valid name, username and email.');
      if (password.length < 8) return fail('Password must be at least eight characters.');
      const hashed=await passwordHash(password), userId=id('user'), created=now();
      try { await env.DB.batch([env.DB.prepare('INSERT INTO users(id,username,username_key,email,email_key,display_name,role,password_salt,password_hash,password_iterations,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(userId,username,username.toLowerCase(),email,email,name,'user',hashed.salt,hashed.hash,hashed.iterations,created),env.DB.prepare('INSERT INTO user_state(user_id,data_json,updated_at) VALUES(?,?,?)').bind(userId,'{}',created)]); }
      catch { return fail('That username or email is already registered.',409); }
      return json({ user:publicUser(await getUserById(env,userId)) },201);
    }
    if (path[1] === 'users' && path[2] && method === 'PUT') {
      const body=await parseJson(request), userId=path[2], name=text(body.name,70),username=text(body.username,24),email=emailKey(body.email);
      if(!name||!username||!email.includes('@')||username.toLowerCase()==='admin')return fail('Enter valid user details.');
      const existing=await getUserById(env,userId);if(!existing)return fail('User not found.',404);
      try {
        if(body.password){if(String(body.password).length<8)return fail('Password must be at least eight characters.');const hashed=await passwordHash(String(body.password));await env.DB.prepare('UPDATE users SET username=?,username_key=?,email=?,email_key=?,display_name=?,password_salt=?,password_hash=?,password_iterations=? WHERE id=?').bind(username,username.toLowerCase(),email,email,name,hashed.salt,hashed.hash,hashed.iterations,userId).run();}
        else await env.DB.prepare('UPDATE users SET username=?,username_key=?,email=?,email_key=?,display_name=? WHERE id=?').bind(username,username.toLowerCase(),email,email,name,userId).run();
      }catch{return fail('That username or email is already in use.',409);}
      return json({user:publicUser(await getUserById(env,userId))});
    }
    if (path[1] === 'users' && path[2] && method === 'DELETE') {
      const result=await env.DB.prepare('DELETE FROM users WHERE id=?').bind(path[2]).run();if(!result.meta.changes)return fail('User not found.',404);return json({ok:true});
    }
    if (path[1] === 'requests' && method === 'GET') {
      const rows=await env.DB.prepare('SELECT r.*,u.username,u.display_name FROM requests r JOIN users u ON u.id=r.user_id ORDER BY r.created_at DESC LIMIT 500').all();
      return json({requests:rows.results.map(r=>({id:r.id,userId:r.user_id,username:r.username,userName:r.display_name,type:r.type,amount:Number(r.amount),status:r.status,method:r.method,reference:r.reference,details:r.details,createdAt:r.created_at,reviewedAt:r.reviewed_at,reviewNote:r.review_note}))});
    }
    if (path[1] === 'requests' && path[2] && method === 'POST') {
      const body=await parseJson(request), status=body.status==='Approved'?'Approved':'Rejected', req=await env.DB.prepare('SELECT * FROM requests WHERE id=?').bind(path[2]).first();
      if(!req)return fail('Request not found.',404);if(req.status!=='Pending')return fail('This request has already been reviewed.',409);
      const reviewed=now(),note=status==='Approved'?'Authenticated by Admin':'Declined by Admin', user=await getUserById(env,req.user_id);
      if(req.type==='Withdrawal'&&status==='Approved'&&Number(req.amount)>Number(user.wallet_available)){return fail('The member’s available balance is insufficient.',409);}
      const statements=[env.DB.prepare('UPDATE requests SET status=?,reviewed_at=?,review_note=? WHERE id=? AND status=?').bind(status,reviewed,note,req.id,'Pending')];
      if(req.type==='Withdrawal')statements.push(env.DB.prepare('UPDATE users SET wallet_reserved=MAX(0,wallet_reserved-?),wallet_available=wallet_available-?,wallet_withdrawals=wallet_withdrawals+? WHERE id=?').bind(req.amount,status==='Approved'?req.amount:0,status==='Approved'?req.amount:0,req.user_id));
      if(req.type==='Deposit'&&status==='Approved')statements.push(env.DB.prepare('UPDATE users SET wallet_available=wallet_available+?,wallet_deposits=wallet_deposits+? WHERE id=?').bind(req.amount,req.amount,req.user_id));
      statements.push(env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'),req.user_id,req.type,`${req.type} request ${status.toLowerCase()}`,status==='Approved'?(req.type==='Deposit'?req.amount:-req.amount):0,note,reviewed));
      await env.DB.batch(statements);return json({ok:true,status});
    }
    if (path[1] === 'wallet-adjustment' && method === 'POST') {
      const body=await parseJson(request),user=await getUserById(env,text(body.userId,100)),field=String(body.field||'available'),operation=String(body.operation||'add'),amount=safeNumber(body.amount);
      if(!user)return fail('User not found.',404);const columns={available:'wallet_available',invested:'wallet_invested',earnings:'wallet_earnings'};if(!columns[field]||amount<0)return fail('Choose a valid wallet field and amount.');
      const column=columns[field],before=Number(user[column]||0),after=operation==='set'?amount:operation==='subtract'?before-amount:before+amount;if(after<0)return fail('The adjustment cannot move a balance below zero.');
      await env.DB.batch([env.DB.prepare(`UPDATE users SET ${column}=? WHERE id=?`).bind(after,user.id),env.DB.prepare('INSERT INTO ledger(id,user_id,type,title,amount,note,created_at) VALUES(?,?,?,?,?,?,?)').bind(id('act'),user.id,'Admin adjustment',`${field} adjusted by Admin`,after-before,text(body.note,180),now())]);
      return json({user:publicUser(await getUserById(env,user.id))});
    }
    if (path[1] === 'announcements' && method === 'GET') { const result=await env.DB.prepare('SELECT * FROM announcements ORDER BY created_at DESC').all();return json({announcements:result.results}); }
    if (path[1] === 'announcements' && method === 'POST') { const body=await parseJson(request),item={id:id('ann'),title:text(body.title,100),body:text(body.body,1000),audience:text(body.audience||'all',100),level:text(body.level||'Update',30),author:'Admin',created_at:now()};if(!item.title||!item.body)return fail('A title and announcement body are required.');await env.DB.prepare('INSERT INTO announcements(id,title,body,audience,level,author,created_at) VALUES(?,?,?,?,?,?,?)').bind(item.id,item.title,item.body,item.audience,item.level,item.author,item.created_at).run();return json({announcement:item},201); }
    if (path[1] === 'announcements' && path[2] && method === 'DELETE') { await env.DB.prepare('DELETE FROM announcements WHERE id=?').bind(path[2]).run();return json({ok:true}); }
    if (path[1] === 'payment-methods' && method === 'GET') { const result=await env.DB.prepare('SELECT * FROM payment_methods ORDER BY created_at').all();return json({methods:result.results.map(m=>({id:m.id,name:m.name,detail:m.detail,time:m.processing_time,fee:m.fee}))}); }
    if (path[1] === 'payment-methods' && method === 'POST') { const body=await parseJson(request),item={id:id('method'),name:text(body.name,70),detail:text(body.detail,220),time:text(body.time,70),fee:text(body.fee,80)};if(!item.name||!item.detail)return fail('Method name and payment details are required.');await env.DB.prepare('INSERT INTO payment_methods(id,name,detail,processing_time,fee,created_at) VALUES(?,?,?,?,?,?)').bind(item.id,item.name,item.detail,item.time,item.fee,now()).run();return json({method:item},201); }
    if (path[1] === 'payment-methods' && path[2] && method === 'DELETE') { await env.DB.prepare('DELETE FROM payment_methods WHERE id=?').bind(path[2]).run();return json({ok:true}); }
  }

  if (first === 'messages' && method === 'GET' && path[1] === 'inbox') {
    const session=await requireSession(request,env);requireAdmin(session);
    const result=await env.DB.prepare('SELECT u.id,u.username,u.display_name,u.email,(SELECT body FROM messages m WHERE m.user_id=u.id ORDER BY m.created_at DESC LIMIT 1) AS last_message,(SELECT created_at FROM messages m WHERE m.user_id=u.id ORDER BY m.created_at DESC LIMIT 1) AS last_at,(SELECT COUNT(*) FROM messages m WHERE m.user_id=u.id AND m.sender="user" AND m.read_at IS NULL) AS unread FROM users u WHERE u.role="user" ORDER BY last_at DESC').all();return json({conversations:result.results});
  }

  return fail('API route not found.', 404);
}

export async function onRequest(context) {
  const { request, env } = context, url = new URL(request.url), origin = request.headers.get('Origin');
  if (origin && new URL(origin).host !== url.host) return fail('Cross-origin requests are not allowed.', 403);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { 'allow': 'GET, POST, PUT, DELETE, OPTIONS' } });
  try { return await routeApi(request, env, url); }
  catch (error) { return json({ error: error?.message || 'The request could not be completed.' }, error?.status || 500); }
}
