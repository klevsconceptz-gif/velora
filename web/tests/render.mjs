/**
 * Frontend render check.
 *
 * Runs the *shipped* view modules in Node against a small DOM stub and one real
 * captured payload set (see capture_fixtures.py). Every route in app.js is
 * rendered for the role that is allowed to see it, so a renamed field, a missing
 * export or an endpoint that no longer exists fails here instead of in a
 * browser.
 *
 * Run:  node web/tests/render.mjs        (from the repository root)
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(here, '..');
const fixturePath = process.env.VELORA_FIXTURES
  ? path.resolve(process.env.VELORA_FIXTURES)
  : path.join(here, 'fixtures', 'api.json');

if (!fs.existsSync(fixturePath)) {
  console.error('Missing fixtures. Run: python3 web/tests/capture_fixtures.py');
  process.exit(2);
}
const captured = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const fixtures = captured.fixtures;
const byRole = captured.by_role || {};

// --------------------------------------------------------------------------- DOM stub

class DomNode {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.nodeName = this.tagName;
    this.children = [];
    this.childNodes = this.children;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.className = '';
    this.value = '';
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.id = '';
    this.parentNode = null;
    this._text = '';
    this._listeners = new Map();
    const self = this;
    this.classList = {
      add(...names) {
        const current = new Set(String(self.className).split(/\s+/).filter(Boolean));
        names.forEach((name) => current.add(name));
        self.className = [...current].join(' ');
      },
      remove(...names) {
        const current = new Set(String(self.className).split(/\s+/).filter(Boolean));
        names.forEach((name) => current.delete(name));
        self.className = [...current].join(' ');
      },
      contains(name) {
        return String(self.className).split(/\s+/).includes(name);
      },
      toggle(name) {
        this.contains(name) ? this.remove(name) : this.add(name);
      },
    };
  }

  get firstChild() {
    return this.children[0] || null;
  }

  get lastChild() {
    return this.children[this.children.length - 1] || null;
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join('');
  }

  set textContent(value) {
    this.children = [];
    this._text = value === null || value === undefined ? '' : String(value);
  }

  get innerText() {
    return this.textContent;
  }

  append(...nodes) {
    for (const node of nodes.flat(8)) {
      if (node === null || node === undefined || typeof node === 'boolean') continue;
      const child = node instanceof DomNode
        ? node
        : Object.assign(new DomNode('#text'), { _text: String(node) });
      child.parentNode = this;
      this.children.push(child);
    }
    return this;
  }

  appendChild(node) {
    return this.append(node);
  }

  prepend(...nodes) {
    const added = [];
    for (const node of nodes.flat(8)) {
      if (node === null || node === undefined) continue;
      added.push(node instanceof DomNode ? node : Object.assign(new DomNode('#text'), { _text: String(node) }));
    }
    this.children = [...added, ...this.children];
    return this;
  }

  removeChild(node) {
    const index = this.children.indexOf(node);
    if (index >= 0) this.children.splice(index, 1);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    this._text = '';
    return this.append(...nodes);
  }

  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }

  setAttribute(name, value) {
    const text = String(value);
    this.attributes[name] = text;
    if (name === 'id') this.id = text;
    if (name === 'class') this.className = text;
    if (name === 'value') this.value = text;
    if (name === 'disabled') this.disabled = true;
    if (name === 'checked') this.checked = true;
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  hasAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name);
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === 'disabled') this.disabled = false;
  }

  addEventListener(type, handler) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const list = this._listeners.get(type) || [];
    const index = list.indexOf(handler);
    if (index >= 0) list.splice(index, 1);
  }

  dispatch(type) {
    const event = { type, currentTarget: this, target: this, preventDefault() {}, stopPropagation() {} };
    for (const handler of this._listeners.get(type) || []) handler(event);
    return event;
  }

  click() {
    return this.dispatch('click');
  }

  focus() {
    return undefined;
  }

  blur() {
    return undefined;
  }

  scrollIntoView() {
    return undefined;
  }

  closest() {
    return null;
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (child.tagName !== '#TEXT' && matchesSelector(child, selector)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function matchesSelector(node, selector) {
  const match = String(selector).trim().match(/^([a-zA-Z0-9#.\-*]*)((?:\[[^\]]*\])*)$/);
  if (!match) return false;
  let [, head, attrs] = match;
  if (head && head !== '*') {
    const parts = head.split(/(?=[.#])/);
    for (const part of parts) {
      if (part.startsWith('#')) {
        if (node.id !== part.slice(1)) return false;
      } else if (part.startsWith('.')) {
        if (!String(node.className).split(/\s+/).includes(part.slice(1))) return false;
      } else if (node.tagName !== part.toUpperCase()) {
        return false;
      }
    }
  }
  const attributePattern = /\[([^\]=]+)(?:="([^"]*)")?\]/g;
  let attribute;
  while ((attribute = attributePattern.exec(attrs))) {
    const name = attribute[1];
    const expected = attribute[2];
    const actual = node.getAttribute(name);
    if (actual === null) return false;
    if (expected !== undefined && actual !== expected) return false;
  }
  return true;
}

const documentStub = {
  body: new DomNode('body'),
  documentElement: new DomNode('html'),
  title: 'Velora',
  hidden: false,
  createElement: (tag) => new DomNode(tag),
  createTextNode: (text) => Object.assign(new DomNode('#text'), { _text: String(text) }),
  createDocumentFragment: () => new DomNode('#fragment'),
  getElementById(id) {
    const found = documentStub.body.querySelectorAll(`#${id}`);
    return found[0] || null;
  },
  querySelector: (selector) => documentStub.body.querySelector(selector),
  querySelectorAll: (selector) => documentStub.body.querySelectorAll(selector),
  addEventListener: () => undefined,
};

const windowStub = {
  location: { hash: '#/discover', href: 'http://velora.test/#/discover', origin: 'http://velora.test', pathname: '/' },
  document: documentStub,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  open: () => null,
  confirm: () => false,
  alert: () => undefined,
  scrollTo: () => undefined,
  matchMedia: () => ({ matches: false, addEventListener: () => undefined }),
  navigator: { language: 'en', userAgent: 'node' },
};

globalThis.Node = DomNode;
globalThis.Element = DomNode;
globalThis.HTMLElement = DomNode;
globalThis.document = documentStub;
globalThis.window = windowStub;
// Node >= 21 defines `navigator` as a getter-only global, so define it explicitly.
Object.defineProperty(globalThis, 'navigator', {
  value: windowStub.navigator, configurable: true, writable: true,
});
Object.defineProperty(globalThis, 'location', {
  value: windowStub.location, configurable: true, writable: true,
});
globalThis.requestAnimationFrame = (callback) => setTimeout(callback, 0);
globalThis.cancelAnimationFrame = (handle) => clearTimeout(handle);
globalThis.scrollTo = () => undefined;

// --------------------------------------------------------------------------- fetch stub

let currentRole = 'anonymous';
const unmatchedCalls = [];
const networkCalls = [];

function normalized(url) {
  return String(url).replace(/^https?:\/\/[^/]+/, '');
}

function pickFixture(method, url, role) {
  const request = normalized(url);
  const [pathOnly] = request.split('?');
  const scoped = byRole[role] || {};
  const exact = scoped[`${method} ${request}`];
  if (exact) return exact;
  for (const [key, entry] of Object.entries(scoped)) {
    const [keyMethod, keyUrl] = [key.slice(0, key.indexOf(' ')), key.slice(key.indexOf(' ') + 1)];
    if (keyMethod !== method) continue;
    if (keyUrl === request || keyUrl.split('?')[0] === pathOnly) return entry;
  }
  const anyExact = fixtures[`${method} ${request}`];
  if (anyExact) return anyExact;
  for (const [key, entry] of Object.entries(fixtures)) {
    const [keyMethod, keyUrl] = [key.slice(0, key.indexOf(' ')), key.slice(key.indexOf(' ') + 1)];
    if (keyMethod !== method) continue;
    if (keyUrl === request || keyUrl.split('?')[0] === pathOnly) return entry;
  }
  return null;
}

function makeResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (String(name).toLowerCase() === 'content-type' ? 'application/json' : null) },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

globalThis.fetch = async (url, options = {}) => {
  const method = (options.method || 'GET').toUpperCase();
  const body = options.body ? JSON.parse(options.body) : undefined;
  networkCalls.push({ method, url: normalized(url), body, role: currentRole });
  const entry = pickFixture(method, url, currentRole);
  if (!entry) {
    unmatchedCalls.push(`${method} ${normalized(url)} (role ${currentRole})`);
    return makeResponse(404, { error: { code: 'not_found', message: `not captured: ${method} ${url}` } });
  }
  return makeResponse(entry.status, entry.json);
};

globalThis.XMLHttpRequest = class {
  open() {
    throw new Error('XMLHttpRequest is not available in the render check');
  }
};

// --------------------------------------------------------------------------- modules

const { appState, loadBootstrap } = await import(path.join(webRoot, 'assets/api.js'));
const publicViews = await import(path.join(webRoot, 'assets/views-public.js'));
const accountViews = await import(path.join(webRoot, 'assets/views-account.js'));
const studioViews = await import(path.join(webRoot, 'assets/views-studio.js'));
const adminViews = await import(path.join(webRoot, 'assets/views-admin.js'));

const namespaces = { publicViews, accountViews, studioViews, adminViews };

const appSource = fs.readFileSync(path.join(webRoot, 'assets/app.js'), 'utf8');
const ROUTE_RE = /\{\s*name:\s*'([^']+)',\s*pattern:\s*'([^']+)',\s*view:\s*(\w+)\.(\w+)/g;
const routes = [];
let routeMatch;
while ((routeMatch = ROUTE_RE.exec(appSource))) {
  routes.push({ name: routeMatch[1], pattern: routeMatch[2], ns: routeMatch[3], fn: routeMatch[4] });
}

function scoped(role, key) {
  return (byRole[role] || {})[key] || (role === 'anonymous' ? fixtures[key] : null) || null;
}

function bootstrapFor(role) {
  const entry = scoped(role, 'GET /api/bootstrap') || fixtures['GET /api/bootstrap'];
  return entry ? entry.json : {};
}

async function applyState(role) {
  currentRole = role;
  const bootstrap = bootstrapFor(role) || {};
  appState.features = bootstrap.features || {};
  appState.emailStatus = bootstrap.email_status || null;
  appState.checkout = bootstrap.checkout || { available: false };
  appState.categories = bootstrap.categories || [];
  appState.paymentAssets = bootstrap.payment_assets || [];
  appState.orientationOptions = bootstrap.orientation_options || null;
  appState.reportReasons = bootstrap.report_reasons || [];
  appState.session = bootstrap.session || { authenticated: false };
  appState.creatorPage = bootstrap.creator_page || null;
  appState.application = bootstrap.application || null;
  if (role === 'anonymous') {
    appState.session = { authenticated: false };
    return;
  }
  const session = scoped(role, 'GET /api/auth/session');
  if (session && session.json && session.json.authenticated) {
    // Re-use the captured session so capabilities match the role being rendered.
    appState.session = session.json;
  }
}

function roleForRoute(name) {
  if (name.startsWith('admin')) return 'admin';
  if (name.toLowerCase().startsWith('studio')) return 'creator';
  if (['account', 'orientations', 'memberships', 'feed', 'messages', 'thread', 'reports',
       'checkout', 'apply'].includes(name)) {
    return 'member';
  }
  return 'anonymous';
}

function exampleFor(pattern) {
  const orderRef = Object.keys(fixtures)
    .find((key) => key.startsWith('GET /api/payments/orders/'))
    ?.split('/').pop();
  const values = { handle: 'sample-creator', id: '1', order: orderRef || 'VLR-TEST' };
  const params = {};
  const path = pattern.replace(/:([a-z]+)/gi, (_, name) => {
    const value = values[name] || '1';
    params[name] = value;
    return value;
  });
  const query = {};
  if (pattern === '/invite') {
    const invitationKey = Object.keys(fixtures).find((key) => key.startsWith('GET /api/invitations/'));
    if (invitationKey) query.token = invitationKey.split('/').pop();
  }
  return { path, params, query };
}

// --------------------------------------------------------------------------- run

const results = [];
const problems = [];
const renderedText = new Map();

for (const route of routes) {
  const role = roleForRoute(route.name);
  const view = namespaces[route.ns]?.[route.fn];
  if (typeof view !== 'function') {
    problems.push(`${route.name}: ${route.ns}.${route.fn} is not an exported function`);
    continue;
  }
  await applyState(role);
  const { path: examplePath, params, query } = exampleFor(route.pattern);
  windowStub.location.hash = `#${examplePath}`;
  const kept = [];
  const context = {
    route: { name: route.name, path: examplePath, pattern: route.pattern },
    navigate: (target) => kept.push(`navigate:${target}`),
    reload: () => kept.push('reload'),
    refreshShell: () => kept.push('refreshShell'),
  };
  let node = null;
  try {
    node = await view(params, query, context);
  } catch (error) {
    problems.push(`${route.name} (${route.pattern}): threw ${error && error.stack ? error.stack.split('\n')[0] : error}`);
    continue;
  }
  if (!node || typeof node !== 'object' || !('textContent' in node)) {
    problems.push(`${route.name} (${route.pattern}): view did not return a DOM node`);
    continue;
  }
  const text = node.textContent;
  if (!text || !text.trim()) {
    problems.push(`${route.name} (${route.pattern}): rendered nothing`);
  }
  if (text.includes('[object Object]')) {
    problems.push(`${route.name} (${route.pattern}): rendered "[object Object]"`);
  }
  renderedText.set(route.name, text);
  const heading = node.querySelector('h1');
  results.push({
    route: route.name,
    role,
    heading: heading ? heading.textContent : null,
    length: text.length,
  });
}

// Privacy check: a locked post must never render the members-only body.
const membersBody = 'Detailed members-only notes.';
await applyState('anonymous');
const lockedPost = await publicViews.postPage({ id: '2' }, {}, { route: { path: '/c/sample-creator/post/2' }, navigate() {}, reload() {}, refreshShell() {} });
if (lockedPost.textContent.includes(membersBody)) {
  problems.push('anonymous post page leaked members-only body text');
}
const publicCreator = await publicViews.creatorPage({ handle: 'sample-creator' }, {}, { route: { path: '/c/sample-creator' }, navigate() {}, reload() {}, refreshShell() {} });
if (publicCreator.textContent.includes(membersBody)) {
  problems.push('anonymous creator page leaked members-only body text');
}

// Multi-asset payments: wallets, the application form and the coin picker.
function expectText(routeName, needles) {
  const text = renderedText.get(routeName) || '';
  for (const needle of needles) {
    if (!text.includes(needle)) problems.push(`${routeName} did not render ${JSON.stringify(needle)}`);
  }
}
expectText('studioPayout', ['Crypto payment wallets', 'Tether and stablecoins', 'USDT · Tron (TRC-20)', 'USDT · Ethereum (ERC-20)',
  'ETH', 'Remove', 'Never paste a seed phrase']);
expectText('apply', ['USDT · Tron (TRC-20)', 'Crypto payment wallets (optional now']);
{
  const quoteKey = Object.keys(fixtures).find((key) => key.startsWith('GET /api/payments/quote?tier_id='));
  if (!quoteKey) {
    problems.push('no captured checkout quote');
  } else {
    await applyState('member');
    const tier = quoteKey.split('=').pop();
    const checkout = await accountViews.checkoutPage({ order: `tier-${tier}` }, {},
      { route: { path: `/checkout/tier-${tier}` }, navigate() {}, reload() {}, refreshShell() {} });
    const radios = checkout.querySelectorAll('input[type="radio"]');
    if (radios.length < 2) problems.push(`checkout picker rendered ${radios.length} coin choices, expected several`);
    const text = checkout.textContent;
    for (const needle of ['Pay with', 'USDT · Tron (TRC-20)', 'ETH', 'BTC']) {
      if (!text.includes(needle)) problems.push(`checkout picker did not render ${JSON.stringify(needle)}`);
    }
    if (/Bitcoin on-chain only/i.test(text)) problems.push('checkout still claims Bitcoin-only payment');
  }
}

for (const call of unmatchedCalls) {
  problems.push(`no captured fixture: ${call}`);
}

const missingHeadings = results.filter((entry) => !entry.heading);
if (missingHeadings.length) {
  problems.push(`routes without an h1: ${missingHeadings.map((entry) => `${entry.route}(${entry.role})`).join(', ')}`);
}

console.log(`rendered ${results.length}/${routes.length} routes across ` +
            `${new Set(results.map((entry) => entry.role)).size} roles using ${Object.keys(fixtures).length} fixtures`);
console.log(`network calls: ${networkCalls.length}`);
for (const entry of results) {
  console.log(`  ${entry.role.padEnd(9)} ${entry.route.padEnd(20)} h1=${entry.heading ? `"${entry.heading.slice(0, 48)}"` : '—'}`);
}

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const problem of problems) console.error('  -', problem);
  process.exit(1);
}
console.log('\nfrontend render check passed');
