/**
 * Velora single-page app: router, header and shell wiring.
 *
 * Hash-based routing keeps direct hits working from any static path and avoids
 * server configuration. Each view is an async function that returns a DOM node;
 * the router shows a loading state, then a focus-managed, announced result.
 */

import { ApiFailure, appState, describeFailure, isAdmin, isSignedIn, loadBootstrap, loadSession } from './api.js';
import * as ui from './ui.js';
import * as publicViews from './views-public.js';
import * as accountViews from './views-account.js';
import * as studioViews from './views-studio.js';
import * as adminViews from './views-admin.js';

const ROUTES = [
  { name: 'home', pattern: '/', view: publicViews.home },
  { name: 'discover', pattern: '/discover', view: publicViews.discover },
  { name: 'creator', pattern: '/c/:handle', view: publicViews.creatorPage },
  { name: 'post', pattern: '/c/:handle/post/:id', view: publicViews.postPage },
  { name: 'privacy', pattern: '/privacy', view: publicViews.privacyPage },
  { name: 'safety', pattern: '/safety', view: publicViews.safetyPage },
  { name: 'signup', pattern: '/signup', view: accountViews.signupPage },
  { name: 'login', pattern: '/login', view: accountViews.loginPage },
  { name: 'verify', pattern: '/verify', view: accountViews.verifyPage },
  { name: 'invite', pattern: '/invite', view: accountViews.invitePage },
  { name: 'apply', pattern: '/apply', view: accountViews.applyPage },
  { name: 'account', pattern: '/account', view: accountViews.accountPage },
  { name: 'orientations', pattern: '/account/privacy', view: accountViews.orientationPage },
  { name: 'memberships', pattern: '/memberships', view: accountViews.membershipsPage },
  { name: 'feed', pattern: '/feed', view: accountViews.feedPage },
  { name: 'messages', pattern: '/messages', view: accountViews.messagesPage },
  { name: 'thread', pattern: '/messages/:id', view: accountViews.messagesPage },
  { name: 'reports', pattern: '/reports', view: accountViews.reportsPage },
  { name: 'checkout', pattern: '/checkout/:order', view: accountViews.checkoutPage },
  { name: 'studio', pattern: '/studio', view: studioViews.studioPage },
  { name: 'studioPosts', pattern: '/studio/posts', view: studioViews.postsPage },
  { name: 'studioPost', pattern: '/studio/posts/:id', view: studioViews.postEditorPage },
  { name: 'studioNewPost', pattern: '/studio/posts/new', view: studioViews.postEditorPage },
  { name: 'studioTiers', pattern: '/studio/tiers', view: studioViews.tiersPage },
  { name: 'studioMembers', pattern: '/studio/members', view: studioViews.membersPage },
  { name: 'studioPayout', pattern: '/studio/payout', view: studioViews.payoutPage },
  { name: 'admin', pattern: '/admin', view: adminViews.overviewPage },
  { name: 'adminUsers', pattern: '/admin/users', view: adminViews.usersPage },
  { name: 'adminUser', pattern: '/admin/users/:id', view: adminViews.userDetailPage },
  { name: 'adminCreators', pattern: '/admin/creators', view: adminViews.creatorsPage },
  { name: 'adminApplications', pattern: '/admin/applications', view: adminViews.applicationsPage },
  { name: 'adminPosts', pattern: '/admin/posts', view: adminViews.postsPage },
  { name: 'adminTiers', pattern: '/admin/tiers', view: adminViews.tiersPage },
  { name: 'adminReports', pattern: '/admin/reports', view: adminViews.reportsPage },
  { name: 'adminPayments', pattern: '/admin/payments', view: adminViews.paymentsPage },
  { name: 'adminLedger', pattern: '/admin/ledger', view: adminViews.ledgerPage },
  { name: 'adminAudit', pattern: '/admin/audit', view: adminViews.auditPage },
  { name: 'adminInvitations', pattern: '/admin/invitations', view: adminViews.invitationsPage },
];

export const router = {
  navigate(path, { replace = false } = {}) {
    const target = `#${path.startsWith('/') ? path : `/${path}`}`;
    if (replace) {
      window.location.replace(target);
    } else {
      window.location.hash = target;
    }
  },
  current() {
    return parseHash(window.location.hash);
  },
};

export function parseHash(hash) {
  const raw = (hash || '').replace(/^#/, '') || '/';
  const [pathPart, queryPart] = raw.split('?');
  const segments = pathPart.split('/').filter(Boolean);
  const query = Object.fromEntries(new URLSearchParams(queryPart || ''));

  for (const route of ROUTES) {
    const patternSegments = route.pattern.split('/').filter(Boolean);
    if (patternSegments.length !== segments.length) continue;
    const params = {};
    let matched = true;
    for (let index = 0; index < patternSegments.length; index += 1) {
      const expected = patternSegments[index];
      const actual = decodeURIComponent(segments[index]);
      if (expected.startsWith(':')) {
        params[expected.slice(1)] = actual;
      } else if (expected !== actual) {
        matched = false;
        break;
      }
    }
    if (matched) {
      return { name: route.name, view: route.view, params, query, path: pathPart };
    }
  }
  return { name: 'notFound', view: publicViews.notFoundPage, params: {}, query, path: pathPart };
}

// ---------------------------------------------------------------- header

function navLink(label, href, options = {}) {
  const active = window.location.hash.startsWith(`#${href}`);
  return ui.el('a', {
    href: `#${href}`,
    text: label,
    'aria-current': active ? 'page' : null,
    ...options,
  });
}

function renderHeader() {
  const header = document.getElementById('site-header-inner');
  ui.clear(header);

  const signedIn = isSignedIn();
  const user = signedIn ? appState.session.user : null;
  const capabilities = signedIn ? appState.session.capabilities : null;

  header.append(
    ui.el('a', { class: 'brand', href: '#/' },
      ui.el('span', { class: 'brand__mark', 'aria-hidden': 'true', text: 'V' }),
      ui.el('span', {},
        ui.el('span', { text: 'Velora' }),
        ui.el('span', { class: 'brand__tag', text: 'creator memberships' }),
      ),
    ),
    ui.el('button', {
      class: 'nav-toggle',
      type: 'button',
      'aria-expanded': 'false',
      'aria-controls': 'site-nav',
      onclick: (event) => {
        const nav = document.getElementById('site-nav');
        const open = nav.dataset.open === 'true';
        nav.dataset.open = open ? 'false' : 'true';
        event.currentTarget.setAttribute('aria-expanded', open ? 'false' : 'true');
      },
      text: 'Menu',
    }),
  );

  const nav = ui.el('nav', { class: 'site-nav', id: 'site-nav', 'aria-label': 'Main' });
  nav.append(navLink('Discover', '/discover'), navLink('Privacy', '/privacy'));

  if (!signedIn) {
    nav.append(
      navLink('Sign in', '/login'),
      ui.el('a', { class: 'button button--primary button--small', href: '#/signup', text: 'Create account' }),
    );
  } else {
    nav.append(
      navLink('Feed', '/feed'),
      navLink('Messages', '/messages'),
      navLink('Memberships', '/memberships'),
    );
    if (capabilities && (capabilities.creator || appState.creatorPage)) {
      nav.append(navLink('Studio', '/studio'));
    }
    if (capabilities && capabilities.admin) {
      nav.append(navLink('Admin', '/admin'));
    }
    nav.append(
      ui.el('a', { class: 'button button--ghost button--small', href: '#/account', text: user ? user.display_name : 'Account' }),
      ui.el('button', {
        type: 'button',
        class: 'button button--small',
        text: 'Sign out',
        onclick: async () => {
          try {
            await import('./api.js').then(({ api }) => api('/api/auth/logout', { method: 'POST' }));
          } catch (error) {
            // Signing out locally is still correct even if the call failed.
          }
          appState.session = { authenticated: false };
          ui.toast('Signed out.', 'info');
          router.navigate('/');
          await boot({ skipBootstrap: true });
        },
      }),
    );
  }
  header.append(nav);
}

function renderFooter() {
  const footer = document.getElementById('site-footer');
  ui.clear(footer);
  footer.append(
    ui.el('div', { class: 'shell' },
      ui.el('div', { class: 'site-footer__grid' },
        ui.el('div', {},
          ui.el('strong', { text: 'Velora' }),
          ui.el('p', { class: 'subtle', text: 'A creator membership platform built around privacy, clarity and Bitcoin.' }),
        ),
        ui.el('div', {},
          ui.el('strong', { text: 'For visitors' }),
          ui.el('ul', {},
            ui.el('li', {}, ui.el('a', { href: '#/discover', text: 'Discover creators' })),
            ui.el('li', {}, ui.el('a', { href: '#/signup', text: 'Create an account' })),
            ui.el('li', {}, ui.el('a', { href: '#/feed', text: 'Member feed' })),
          ),
        ),
        ui.el('div', {},
          ui.el('strong', { text: 'Trust' }),
          ui.el('ul', {},
            ui.el('li', {}, ui.el('a', { href: '#/privacy', text: 'Privacy and data' })),
            ui.el('li', {}, ui.el('a', { href: '#/safety', text: 'Safety and reporting' })),
            ui.el('li', {}, ui.el('a', { href: '#/apply', text: 'Apply to create' })),
          ),
        ),
        ui.el('div', {},
          ui.el('strong', { text: 'Payments' }),
          ui.el('ul', {},
            ui.el('li', { text: 'On-chain BTC only, via hosted BTCPay checkout' }),
            ui.el('li', { text: 'No automatic renewal, ever' }),
            ui.el('li', { text: 'Settled invoices are append-only' }),
          ),
        ),
      ),
      ui.el('p', { class: 'subtle', text: 'Velora does not verify age or identity. The 18+ checkbox is a self-attestation.' }),
    ),
  );
}

// ---------------------------------------------------------------- error routing

function handleFailure(error) {
  if (error instanceof ApiFailure && error.isAuth) {
    ui.toast('Your session ended. Sign in to continue.', 'info');
    router.navigate(`/login?next=${encodeURIComponent(window.location.hash.slice(1) || '/')}`);
    return true;
  }
  return false;
}

// ---------------------------------------------------------------- render loop

let renderToken = 0;

async function render() {
  const token = ++renderToken;
  const route = parseHash(window.location.hash);
  const container = document.getElementById('app');
  ui.clear(container);
  container.append(ui.loadingBlock('Loading Velora…'));

  const context = {
    route,
    navigate: (path, options) => router.navigate(path, options),
    reload: () => render(),
    refreshShell: () => {
      renderHeader();
      renderFooter();
    },
  };

  try {
    const node = await route.view(route.params, route.query, context);
    if (token !== renderToken) return;
    ui.clear(container);
    container.append(node);
    const label = container.querySelector('h1');
    ui.announce(label ? `${label.textContent} — page loaded` : 'Page loaded');
    ui.focusHeading(container);
  } catch (error) {
    if (token !== renderToken) return;
    if (handleFailure(error)) return;
    ui.clear(container);
    container.append(ui.errorState(error, () => render()));
    ui.toast(describeFailure(error), 'error');
  }
  renderHeader();
}

async function boot({ skipBootstrap = false } = {}) {
  try {
    if (!skipBootstrap) {
      await loadBootstrap();
    } else {
      await loadSession().catch(() => {});
    }
  } catch (error) {
    document.getElementById('app').replaceChildren(
      ui.errorState(error, () => boot()),
    );
    return;
  }
  renderHeader();
  renderFooter();
  await render();
}

window.addEventListener('hashchange', () => { render(); });
document.addEventListener('DOMContentLoaded', () => { boot(); });

export { boot, render };
