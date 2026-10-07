/** Administrator console: moderation, users, creators, payments and the ledger. */

import { api, currentUser, isAdmin, isSignedIn } from './api.js';
import * as ui from './ui.js';

const { el, clear, notice, chip, badge, money } = ui;

function requireAdmin(context) {
  if (!isSignedIn()) {
    context.navigate(`/login?next=${encodeURIComponent(context.route.path)}`);
    return false;
  }
  if (!isAdmin()) {
    context.navigate('/account');
    return false;
  }
  return true;
}

function adminTabs(active) {
  const tabs = [
    ['Overview', '/admin', 'overview'],
    ['Users', '/admin/users', 'users'],
    ['Creators', '/admin/creators', 'creators'],
    ['Applications', '/admin/applications', 'applications'],
    ['Posts', '/admin/posts', 'posts'],
    ['Tiers', '/admin/tiers', 'tiers'],
    ['Reports', '/admin/reports', 'reports'],
    ['Payments', '/admin/payments', 'payments'],
    ['Ledger', '/admin/ledger', 'ledger'],
    ['Audit', '/admin/audit', 'audit'],
    ['Invitations', '/admin/invitations', 'invitations'],
  ];
  return el('nav', { class: 'tabs', 'aria-label': 'Admin sections' },
    tabs.map(([label, href, key]) => el('a', { href: `#${href}`, text: label, 'aria-current': key === active ? 'page' : null })),
  );
}

function shell(title, lede, active, children) {
  return el('div', { class: 'stack-lg' },
    el('header', {}, el('h1', { text: title }), lede ? el('p', { class: 'lede', text: lede }) : null),
    adminTabs(active),
    ...children,
  );
}

async function reloadAfter(context, message, kind = 'success') {
  ui.toast(message, kind);
  context.reload();
}

function notePrompt({ title, body, label = 'Note', confirmLabel = 'Save' }) {
  return new Promise((resolve) => {
    const textarea = el('textarea', { id: 'note-body', rows: 4, maxlength: 2000 });
    const dialog = el('dialog', { 'aria-labelledby': 'note-title' },
      el('h3', { id: 'note-title', text: title }),
      body ? el('p', { class: 'muted', text: body }) : null,
      el('div', { class: 'field' }, el('label', { for: 'note-body', text: label }), textarea),
      el('div', { class: 'button-row' },
        el('button', { class: 'button', type: 'button', text: 'Cancel', onclick: () => { dialog.close(); resolve(null); } }),
        el('button', {
          class: 'button button--primary', type: 'button', text: confirmLabel,
          onclick: () => {
            const value = textarea.value.trim();
            dialog.close();
            resolve(value);
          },
        }),
      ),
    );
    document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
  });
}

// ---------------------------------------------------------------- overview

export async function overviewPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api('/api/admin/overview');
  const counts = payload.counts;
  return shell('Administrator console', 'Everything here is server-authorised and audited. Financial history stays append-only.', 'overview', [
    el('section', {}, el('div', { class: 'hero__stats' },
      ui.statBlock(String(counts.users), 'Accounts'),
      ui.statBlock(String(counts.creators), 'Creator pages'),
      ui.statBlock(String(counts.active_memberships), 'Active memberships'),
      ui.statBlock(String(counts.open_reports), 'Open reports'),
      ui.statBlock(String(counts.held_payments), 'Held payments'),
      ui.statBlock(money(counts.settled_cents), 'Settled gross'),
    )),
    el('section', { class: 'grid grid--two' },
      el('div', { class: 'card' },
        el('h2', { text: 'Environment' }),
        el('dl', { class: 'kv' },
          el('dt', { text: 'Environment' }), el('dd', { text: payload.environment }),
          el('dt', { text: 'Crypto checkout' }), el('dd', { text: payload.payment_configured ? 'Configured (on-chain crypto and Tether)' : 'Not configured — checkout disabled' }),
          el('dt', { text: 'Email delivery' }), el('dd', { text: payload.email_configured ? 'Configured' : 'Not configured — verification email cannot be sent' }),
          el('dt', { text: 'Ledger balanced' }), el('dd', { text: payload.ledger_balanced ? 'Yes' : 'No — investigate' }),
          el('dt', { text: 'Administrators' }), el('dd', { text: String(counts.admins) }),
          el('dt', { text: 'Pending applications' }), el('dd', { text: String(counts.pending_applications) }),
        ),
      ),
      el('div', { class: 'card' },
        el('h2', { text: 'Guarantees the console enforces' }),
        el('ul', {}, payload.invariants.map((item) => el('li', { text: item }))),
      ),
    ),
    el('section', { class: 'card' },
      el('div', { class: 'panel-head' }, el('h2', { text: 'Recent activity' }),
        el('a', { class: 'button button--small', href: '#/admin/audit', text: 'Full audit log' })),
      el('ul', { class: 'list' }, payload.recent_audit.map((entry) => el('li', { class: 'list-item' },
        el('div', { class: 'between' },
          el('strong', { text: entry.action }),
          el('span', { class: 'subtle', text: ui.relative(entry.created_at) }),
        ),
        el('p', { class: 'subtle', text: `${entry.actor_name || 'system'} · ${entry.target_type || '—'} #${entry.target_id || '—'}` }),
      ))),
    ),
  ]);
}

// ---------------------------------------------------------------- users

export async function usersPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const search = query.q || '';
  const role = query.role || '';
  const status = query.status || '';
  const page = Number(query.page || 1) || 1;
  const qs = new URLSearchParams();
  if (search) qs.set('q', search);
  if (role) qs.set('role', role);
  if (status) qs.set('status', status);
  qs.set('page', String(page));
  const payload = await api(`/api/admin/users?${qs.toString()}`);

  const searchInput = el('input', { type: 'search', id: 'user-search', value: search, 'aria-label': 'Search accounts' });
  const roleSelect = el('select', { id: 'user-role', 'aria-label': 'Filter by role' },
    ['', 'member', 'creator', 'admin'].map((value) => el('option', { value, text: value || 'All roles', selected: role === value })));
  const statusSelect = el('select', { id: 'user-status', 'aria-label': 'Filter by status' },
    ['', 'active', 'suspended', 'archived', 'anonymized'].map((value) => el('option', { value, text: value || 'All statuses', selected: status === value })));
  const filters = el('form', {
    class: 'card',
    onsubmit: (event) => {
      event.preventDefault();
      const next = new URLSearchParams();
      if (searchInput.value.trim()) next.set('q', searchInput.value.trim());
      if (roleSelect.value) next.set('role', roleSelect.value);
      if (statusSelect.value) next.set('status', statusSelect.value);
      context.navigate(`/admin/users?${next.toString()}`);
    },
  },
    el('div', { class: 'grid grid--two' },
      el('div', { class: 'field' }, el('label', { for: 'user-search', text: 'Search email or display name' }), searchInput),
      el('div', { class: 'field' }, el('label', { for: 'user-role', text: 'Role' }), roleSelect),
      el('div', { class: 'field' }, el('label', { for: 'user-status', text: 'Status' }), statusSelect),
    ),
    el('div', { class: 'button-row' }, el('button', { class: 'button button--primary', type: 'submit', text: 'Apply filters' })),
  );

  const table = el('table', {},
    el('caption', { text: `${payload.total} account(s) · page ${payload.page} of ${payload.total_pages}` }),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: 'Account' }), el('th', { scope: 'col', text: 'Email' }),
      el('th', { scope: 'col', text: 'Role' }), el('th', { scope: 'col', text: 'Status' }),
      el('th', { scope: 'col', text: 'Verified' }), el('th', { scope: 'col', text: 'Memberships' }),
      el('th', { scope: 'col', text: 'Settled' }), el('th', { scope: 'col', text: '' }),
    )),
    el('tbody', {}, payload.items.map((user) => el('tr', {},
      el('td', { text: user.display_name }),
      el('td', { text: user.email }),
      el('td', {}, badge(user.role, user.role === 'admin' ? 'reviewing' : '')),
      el('td', {}, badge(user.status, user.status === 'active' ? 'active' : user.status)),
      el('td', { text: user.email_verified ? 'Yes' : 'No' }),
      el('td', { text: String(user.active_memberships || 0) }),
      el('td', { text: money(user.settled_cents || 0) }),
      el('td', {}, el('a', { href: `#/admin/users/${user.id}`, text: 'Open' })),
    ))),
  );

  const pagination = el('div', { class: 'button-row' });
  if (payload.page > 1) {
    pagination.append(el('button', {
      class: 'button', type: 'button', text: 'Previous page',
      onclick: () => { qs.set('page', String(payload.page - 1)); context.navigate(`/admin/users?${qs.toString()}`); },
    }));
  }
  if (payload.page < payload.total_pages) {
    pagination.append(el('button', {
      class: 'button', type: 'button', text: 'Next page',
      onclick: () => { qs.set('page', String(payload.page + 1)); context.navigate(`/admin/users?${qs.toString()}`); },
    }));
  }

  return shell('Users', 'Emails are visible here because this console is administrator-only.', 'users', [
    notice('info', 'Privacy note', 'Optional orientation values are never shown in this console — only whether one is set. Accounts cannot be hardened into a deletion of settled financial history.'),
    filters,
    el('div', { class: 'table-wrap' }, table),
    pagination,
  ]);
}

export async function userDetailPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api(`/api/admin/users/${encodeURIComponent(params.id)}`);
  const user = payload.user;
  const me = currentUser();
  const isSelf = me && me.id === user.id;

  const action = async (path, body, message, options = {}) => {
    if (options.confirm) {
      const confirmed = await ui.confirmDialog({ title: options.confirm, body: options.body || '', confirmLabel: options.confirmLabel || 'Continue', danger: options.danger });
      if (!confirmed) return;
    }
    try {
      await api(path, { method: 'POST', body: body || {} });
      await reloadAfter(context, message);
    } catch (error) {
      ui.toast(error.message, 'error');
    }
  };

  const container = shell(`Account: ${user.display_name}`, null, 'users', [
    el('section', { class: 'card' },
      el('div', { class: 'between' },
        el('h2', { text: 'Account' }),
        el('div', { class: 'cluster' }, badge(user.role, user.role === 'admin' ? 'reviewing' : ''), badge(user.status, user.status === 'active' ? 'active' : user.status)),
      ),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Email' }), el('dd', { text: user.email }),
        el('dt', { text: 'Email confirmed' }), el('dd', { text: user.email_verified ? ui.formatDateTime(user.email_verified_at) : 'No' }),
        el('dt', { text: '18+ self-attestation' }), el('dd', { text: `${ui.formatDate(user.adult_attested_at)} (self-attestation only)` }),
        el('dt', { text: 'Orientation set' }), el('dd', { text: user.orientation_set ? 'Yes — value hidden by policy' : 'Not provided' }),
        el('dt', { text: 'Created' }), el('dd', { text: ui.formatDateTime(user.created_at) }),
        el('dt', { text: 'Creator page' }), el('dd', { text: user.handle ? `@${user.handle} (${user.page_status})` : 'None' }),
        el('dt', { text: 'Bio' }), el('dd', { text: user.bio || '—' }),
      ),
      el('p', { class: 'subtle', text: payload.privacy_notice }),
      el('div', { class: 'button-row' },
        isSelf ? el('p', { class: 'subtle', text: 'Administrators cannot change their own role or status.' }) : null,
        !isSelf && user.role !== 'admin' ? el('button', {
          class: 'button button--small button--primary', type: 'button', text: 'Promote to administrator',
          onclick: () => action(`/api/admin/users/${user.id}/promote`, { role: 'admin', reason: 'audited promotion' }, 'Account promoted to administrator.'),
        }) : null,
        !isSelf && user.role !== 'creator' ? el('button', {
          class: 'button button--small', type: 'button', text: 'Grant creator role',
          onclick: () => action(`/api/admin/users/${user.id}/promote`, { role: 'creator', reason: 'creator role granted' }, 'Creator role granted.'),
        }) : null,
        !isSelf && user.role !== 'member' ? el('button', {
          class: 'button button--small', type: 'button', text: 'Demote to member',
          onclick: () => action(`/api/admin/users/${user.id}/demote`, { role: 'member', reason: 'role reduced' }, 'Account demoted.'),
        }) : null,
        !isSelf && user.status === 'active' ? el('button', {
          class: 'button button--small button--danger', type: 'button', text: 'Suspend account',
          onclick: () => action(`/api/admin/users/${user.id}/status`, { status: 'suspended', reason: 'safety review' }, 'Account suspended.'),
        }) : null,
        !isSelf && user.status !== 'active' && user.status !== 'anonymized' ? el('button', {
          class: 'button button--small', type: 'button', text: 'Restore account',
          onclick: () => action(`/api/admin/users/${user.id}/status`, { status: 'active', reason: 'restored' }, 'Account restored.'),
        }) : null,
        !isSelf && user.status !== 'archived' && user.status !== 'anonymized' ? el('button', {
          class: 'button button--small', type: 'button', text: 'Archive (deactivate)',
          onclick: () => action(`/api/admin/users/${user.id}/status`, { status: 'archived', reason: 'archived by admin' }, 'Account archived.', { confirm: 'Archive this account?', body: 'The account is signed out, its page is paused, and it cannot sign in until restored. Payment history is untouched.' }),
        }) : null,
      ),
    ),
    el('section', { class: 'grid grid--two' },
      el('div', { class: 'card' },
        el('h2', { text: 'Memberships' }),
        payload.memberships.length
          ? el('ul', { class: 'list' }, payload.memberships.map((membership) => el('li', { class: 'list-item' },
            el('div', { class: 'between' },
              el('strong', { text: membership.handle || `creator #${membership.creator_id}` }),
              badge(membership.status),
            ),
            el('p', { class: 'subtle', text: `${membership.tier_name || `tier #${membership.tier_id}`} · until ${ui.formatDateTime(membership.ends_at)}` }),
          )))
          : el('p', { class: 'subtle', text: 'No memberships.' }),
      ),
      el('div', { class: 'card' },
        el('h2', { text: 'Payments' }),
        payload.payment_intents.length
          ? el('ul', { class: 'list' }, payload.payment_intents.map((intent) => el('li', { class: 'list-item' },
            el('div', { class: 'between' },
              el('span', { class: 'mono', text: intent.order_ref }),
              badge(intent.status_label, intent.status),
            ),
            el('p', { class: 'subtle', text: `${money(intent.amount_cents)} · ${ui.formatDateTime(intent.created_at)}` }),
            intent.hold_reason ? el('p', { class: 'subtle', text: `Hold reason: ${intent.hold_reason}` }) : null,
          )))
          : el('p', { class: 'subtle', text: 'No payment attempts.' }),
        el('p', { class: 'subtle', text: `${payload.invoices.length} settled invoice(s) on file.` }),
      ),
    ),
  ]);

  const notesCard = el('section', { class: 'card' },
    el('div', { class: 'panel-head' }, el('h2', { text: 'Internal notes' }),
      el('button', {
        class: 'button button--small', type: 'button', text: 'Add note',
        onclick: async () => {
          const body = await notePrompt({ title: 'Add an internal note', body: 'Notes are internal only. They never change payment or ledger history.' });
          if (!body) return;
          await action('/api/admin/notes', { target_type: 'user', target_id: user.id, body }, 'Note added.');
        },
      })),
    payload.notes.length
      ? el('ul', { class: 'list' }, payload.notes.map((note) => el('li', { class: 'list-item' },
        el('p', { text: note.body }),
        el('p', { class: 'subtle', text: `${note.admin_name || 'admin'} · ${ui.formatDateTime(note.created_at)}` }),
      )))
      : el('p', { class: 'subtle', text: 'No notes yet.' }),
  );
  container.append(notesCard);

  container.append(el('section', { class: 'card' },
    el('h2', { text: 'Audit trail for this account' }),
    payload.audit.length
      ? el('ul', { class: 'list' }, payload.audit.map((entry) => el('li', { class: 'list-item' },
        el('div', { class: 'between' }, el('strong', { text: entry.action }), el('span', { class: 'subtle', text: ui.formatDateTime(entry.created_at) })),
        el('p', { class: 'subtle', text: `${entry.actor_name || 'system'} · ${entry.meta || ''}` }),
      )))
      : el('p', { class: 'subtle', text: 'No recorded actions.' }),
    el('h3', { text: 'Anonymize (irreversible)' }),
    el('p', { class: 'muted', text: 'Replaces personal details with anonymous placeholders. Settled invoices, ledger entries and settlement records stay, pointing at the anonymised account.' }),
    el('button', {
      class: 'button button--danger button--small', type: 'button', text: 'Anonymize account',
      disabled: isSelf ? true : null,
      onclick: () => action(`/api/admin/users/${user.id}/anonymize`, { reason: 'data minimisation request' }, 'Account anonymised.',
        { confirm: 'Anonymize this account?', body: 'This cannot be undone. Financial history is retained in anonymised form.', confirmLabel: 'Anonymize', danger: true }),
    }),
  ));

  if (payload.reports.length) {
    container.append(el('section', { class: 'card' },
      el('h2', { text: 'Reports involving this account' }),
      el('ul', { class: 'list' }, payload.reports.map((report) => el('li', { class: 'list-item' },
        el('div', { class: 'between' }, el('strong', { text: report.target_label || report.target_type }), badge(report.status, report.status)),
        el('p', { class: 'subtle', text: `${report.reason_code.replace(/_/g, ' ')} · ${ui.formatDateTime(report.created_at)}` }),
      ))),
    ));
  }
  return container;
}

// ---------------------------------------------------------------- creators and applications

export async function creatorsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api('/api/admin/creators');
  const cards = el('div', { class: 'stack' }, payload.items.map((creator) => el('article', { class: 'card' },
    el('div', { class: 'between' },
      el('h3', {}, el('a', { href: `#/c/${creator.handle}`, text: creator.page_name })),
      el('div', { class: 'cluster' },
        badge(creator.status, creator.status === 'active' ? 'active' : creator.status),
        creator.payout_address_on_file ? chip('Wallet on file', 'ok') : chip('No wallet address', 'warn'),
      ),
    ),
    el('p', { class: 'muted', text: creator.tagline || 'No tagline' }),
    el('dl', { class: 'kv' },
      el('dt', { text: 'Owner' }), el('dd', { text: `${creator.owner.display_name} · ${creator.owner_email} (${creator.owner_status})` }),
      el('dt', { text: 'Tiers' }), el('dd', { text: `${creator.active_tiers} active` }),
      el('dt', { text: 'Posts' }), el('dd', { text: `${creator.published_posts} published` }),
      el('dt', { text: 'Members' }), el('dd', { text: String(creator.active_members) }),
      el('dt', { text: 'Settled gross' }), el('dd', { text: money(creator.gross_cents) }),
    ),
    el('div', { class: 'button-row' },
      creator.status !== 'paused' ? el('button', {
        class: 'button button--small', type: 'button', text: 'Pause page',
        onclick: async () => {
          try {
            await api(`/api/admin/creators/${creator.id}/status`, { method: 'POST', body: { status: 'paused', reason: 'operator pause' } });
            await reloadAfter(context, 'Creator page paused.');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }) : el('button', {
        class: 'button button--small', type: 'button', text: 'Resume page',
        onclick: async () => {
          try {
            await api(`/api/admin/creators/${creator.id}/status`, { method: 'POST', body: { status: 'active', reason: 'resumed' } });
            await reloadAfter(context, 'Creator page resumed.');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }),
      creator.status !== 'archived' ? el('button', {
        class: 'button button--danger button--small', type: 'button', text: 'Archive page',
        onclick: async () => {
          const confirmed = await ui.confirmDialog({ title: 'Archive this creator page?', body: 'The page stops being served. Tiers and posts are hidden; payment history is untouched.', confirmLabel: 'Archive page', danger: true });
          if (!confirmed) return;
          try {
            await api(`/api/admin/creators/${creator.id}/status`, { method: 'POST', body: { status: 'archived', reason: 'archived by admin' } });
            await reloadAfter(context, 'Creator page archived.');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }) : null,
      el('button', {
        class: 'button button--small', type: 'button', text: 'View wallet addresses',
        onclick: async (event) => {
          ui.setBusy(event.currentTarget, true, 'Loading…');
          try {
            const result = await api(`/api/admin/creators/${creator.id}/payout`);
            const payout = result.payout;
            const dialog = el('dialog', { 'aria-labelledby': 'payout-title' },
              el('h3', { id: 'payout-title', text: `Wallet addresses · ${creator.page_name}` }),
              payout.wallets.length
                ? el('dl', { class: 'kv' }, payout.wallets.flatMap((wallet) => [
                  el('dt', { text: wallet.asset.label }),
                  el('dd', { class: 'mono', text: wallet.address }),
                ]))
                : el('p', { class: 'muted', text: 'No wallet addresses recorded.' }),
              el('p', { class: 'subtle', text: payout.notice }),
              el('div', { class: 'button-row' }, el('button', { class: 'button', type: 'button', text: 'Close', onclick: () => dialog.close() })),
            );
            document.body.append(dialog);
            dialog.addEventListener('close', () => dialog.remove());
            dialog.showModal();
          } catch (error) {
            ui.toast(error.message, 'error');
          } finally {
            ui.setBusy(event.currentTarget, false);
          }
        },
      }),
      el('button', {
        class: 'button button--small', type: 'button', text: 'Add note',
        onclick: async () => {
          const body = await notePrompt({ title: 'Note on this creator' });
          if (!body) return;
          try {
            await api('/api/admin/notes', { method: 'POST', body: { target_type: 'creator_page', target_id: creator.id, body } });
            ui.toast('Note added.', 'success');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }),
    ),
  )));
  return shell('Creator pages', 'Wallet addresses are visible only to the creator and authorized administrators.', 'creators', [cards]);
}

export async function applicationsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const status = query.status || 'pending';
  const payload = await api(`/api/admin/applications?status=${encodeURIComponent(status)}`);
  const items = el('div', { class: 'stack' });
  if (!payload.items.length) {
    items.append(ui.emptyState('Nothing in this queue', 'Applications appear here as members apply to create.'));
  }
  payload.items.forEach((application) => {
    const handleInput = el('input', { type: 'text', value: application.desired_handle || '', 'aria-label': 'Handle to assign' });
    const noteInput = el('textarea', { rows: 3, 'aria-label': 'Note to the applicant' });
    items.append(el('article', { class: 'card' },
      el('div', { class: 'between' },
        el('h3', { text: `Application #${application.id}` }),
        el('div', { class: 'cluster' }, badge(application.status), chip(application.category || '—')),
      ),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Applicant' }), el('dd', { text: `${application.display_name} · ${application.applicant_email}` }),
        el('dt', { text: 'Account status' }), el('dd', { text: application.applicant_status }),
        el('dt', { text: 'Preferred handle' }), el('dd', { text: application.desired_handle || 'not specified' }),
        el('dt', { text: 'Submitted' }), el('dd', { text: ui.formatDateTime(application.created_at) }),
      ),
      el('blockquote', { class: 'post__teaser', text: application.pitch }),
      el('div', { class: 'field' }, el('label', { text: 'Handle to assign on approval' }), handleInput),
      el('div', { class: 'field' }, el('label', { text: 'Note to the applicant (visible to them on rejection)' }), noteInput),
      el('div', { class: 'button-row' },
        el('button', {
          class: 'button button--primary button--small', type: 'button', text: 'Approve and create page',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Approving…');
            try {
              const result = await api(`/api/admin/applications/${application.id}/review`, {
                method: 'POST',
                body: { decision: 'approve', handle: handleInput.value.trim() || null, note: noteInput.value.trim() || null },
              });
              await reloadAfter(context, `Approved. Page handle: ${result.handle}`);
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
        el('button', {
          class: 'button button--danger button--small', type: 'button', text: 'Reject',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Rejecting…');
            try {
              await api(`/api/admin/applications/${application.id}/review`, {
                method: 'POST',
                body: { decision: 'reject', note: noteInput.value.trim() || null },
              });
              await reloadAfter(context, 'Application rejected.', 'info');
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
      ),
    ));
  });
  return shell('Creator applications', 'Approving creates the creator page and the creator role, and is audited.', 'applications', [
    el('div', { class: 'button-row' },
      ['pending', 'approved', 'rejected', 'all'].map((value) => el('a', {
        class: `button button--small ${value === status ? 'button--primary' : ''}`,
        href: `#/admin/applications?status=${value}`,
        text: value,
      })),
    ),
    items,
  ]);
}

// ---------------------------------------------------------------- content moderation

export async function postsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const search = query.q || '';
  const payload = await api(`/api/admin/posts${search ? `?q=${encodeURIComponent(search)}` : ''}`);
  const searchInput = el('input', { type: 'search', id: 'post-search', value: search, 'aria-label': 'Search posts by title', placeholder: 'Search titles' });
  const table = el('table', {},
    el('caption', { text: `${payload.items.length} post(s) (most recent first)` }),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: 'Post' }), el('th', { scope: 'col', text: 'Creator' }),
      el('th', { scope: 'col', text: 'Visibility' }), el('th', { scope: 'col', text: 'Status' }),
      el('th', { scope: 'col', text: 'Media' }), el('th', { scope: 'col', text: 'Moderation' }), el('th', { scope: 'col', text: '' }),
    )),
    el('tbody', {}, payload.items.map((post) => el('tr', {},
      el('td', {}, el('a', { href: `#/c/${post.handle}/post/${post.id}`, text: post.title })),
      el('td', { text: `@${post.handle}` }),
      el('td', { text: post.visibility === 'public' ? 'Public' : 'Members-only' }),
      el('td', {}, badge(post.status, post.status === 'published' ? 'active' : post.status)),
      el('td', { text: String(post.media_count) }),
      el('td', { text: post.admin_archived ? 'Archived by moderator' : '—' }),
      el('td', {}, el('div', { class: 'button-row' },
        post.admin_archived ? el('button', {
          class: 'button button--small', type: 'button', text: 'Restore',
          onclick: async () => {
            try {
              await api(`/api/admin/posts/${post.id}/restore`, { method: 'POST' });
              await reloadAfter(context, 'Post restored.');
            } catch (error) { ui.toast(error.message, 'error'); }
          },
        }) : el('button', {
          class: 'button button--danger button--small', type: 'button', text: 'Archive',
          onclick: async () => {
            const reason = await notePrompt({ title: 'Archive this post', body: 'The post stops being served to everyone. Nothing is deleted.', label: 'Reason (stored with the post)', confirmLabel: 'Archive' });
            if (reason === null) return;
            try {
              await api(`/api/admin/posts/${post.id}/archive`, { method: 'POST', body: { note: reason || null } });
              await reloadAfter(context, 'Post archived.', 'info');
            } catch (error) { ui.toast(error.message, 'error'); }
          },
        }),
      )),
    ))),
  );
  return shell('Posts', 'Moderation archives content. Nothing is deleted, and the action is audited.', 'posts', [
    el('form', {
      class: 'card',
      onsubmit: (event) => {
        event.preventDefault();
        context.navigate(`/admin/posts?q=${encodeURIComponent(searchInput.value.trim())}`);
      },
    },
      el('div', { class: 'field' }, el('label', { for: 'post-search', text: 'Search by title' }), searchInput),
      el('div', { class: 'button-row' }, el('button', { class: 'button button--primary', type: 'submit', text: 'Search' })),
    ),
    el('div', { class: 'table-wrap' }, table),
  ]);
}

export async function tiersPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api('/api/admin/tiers');
  const table = el('table', {},
    el('caption', { text: `${payload.items.length} tier(s)` }),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: 'Tier' }), el('th', { scope: 'col', text: 'Creator' }),
      el('th', { scope: 'col', text: 'Price' }), el('th', { scope: 'col', text: 'State' }),
      el('th', { scope: 'col', text: 'Active members' }), el('th', { scope: 'col', text: '' }),
    )),
    el('tbody', {}, payload.items.map((tier) => el('tr', {},
      el('td', { text: tier.name }),
      el('td', { text: `@${tier.handle}` }),
      el('td', { text: `${money(tier.price_cents)} / 30 days` }),
      el('td', {}, badge(tier.is_active ? 'on sale' : 'archived', tier.is_active ? 'active' : 'archived')),
      el('td', { text: String(tier.active_members) }),
      el('td', {}, tier.is_active ? el('button', {
        class: 'button button--danger button--small', type: 'button', text: 'Archive tier',
        onclick: async () => {
          const confirmed = await ui.confirmDialog({
            title: 'Archive this tier?',
            body: 'New purchases stop. Existing members keep the access they already paid for.',
            confirmLabel: 'Archive tier',
          });
          if (!confirmed) return;
          try {
            const result = await api(`/api/admin/tiers/${tier.id}/archive`, { method: 'POST', body: { reason: 'operator archive' } });
            await reloadAfter(context, result.notice || 'Tier archived.', 'info');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }) : null),
    ))),
  );
  return shell('Tiers', null, 'tiers', [el('div', { class: 'table-wrap' }, table)]);
}

// ---------------------------------------------------------------- reports

export async function reportsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const status = query.status || '';
  const payload = await api(`/api/admin/reports${status ? `?status=${encodeURIComponent(status)}` : ''}`);
  const items = el('div', { class: 'stack' });
  if (!payload.items.length) items.append(ui.emptyState('No reports here', 'Reports from members appear in this queue.'));

  payload.items.forEach((report) => {
    const statusSelect = el('select', { 'aria-label': `Status for report ${report.id}` },
      ['open', 'reviewing', 'resolved', 'dismissed'].map((value) => el('option', { value, text: value, selected: report.status === value })));
    const noteInput = el('textarea', { rows: 3, 'aria-label': `Resolution note for report ${report.id}`, maxlength: 1000 });
    items.append(el('article', { class: 'card' },
      el('div', { class: 'between' },
        el('h3', { text: report.target_label || `${report.target_type} #${report.target_id}` }),
        el('div', { class: 'cluster' }, badge(report.status, report.status), chip(report.reason_code.replace(/_/g, ' '))),
      ),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Filed by' }), el('dd', { text: report.reporter_name || 'unknown' }),
        el('dt', { text: 'Filed' }), el('dd', { text: ui.formatDateTime(report.created_at) }),
        el('dt', { text: 'Handled by' }), el('dd', { text: report.handler_name || '—' }),
      ),
      report.details ? el('blockquote', { class: 'post__teaser', text: report.details }) : null,
      report.target_url ? el('p', {}, el('a', { href: `#${report.target_url}`, text: 'Open the reported content' })) : null,
      el('div', { class: 'grid grid--two' },
        el('div', { class: 'field' }, el('label', { text: 'Status' }), statusSelect),
        el('div', { class: 'field' }, el('label', { text: 'Resolution note' }), noteInput),
      ),
      el('div', { class: 'button-row' },
        el('button', {
          class: 'button button--primary button--small', type: 'button', text: 'Save decision',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Saving…');
            try {
              await api(`/api/admin/reports/${report.id}`, {
                method: 'POST',
                body: { status: statusSelect.value, resolution_note: noteInput.value.trim() || null },
              });
              await reloadAfter(context, 'Report updated.');
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
        report.target_url ? el('a', { class: 'button button--small', href: `#${report.target_url}`, text: 'Inspect content' }) : null,
      ),
    ));
  });

  return shell('Reports', 'Reports are the supported moderation channel. Decisions and notes are audited.', 'reports', [
    el('div', { class: 'button-row' },
      ['', 'open', 'reviewing', 'resolved', 'dismissed'].map((value) => el('a', {
        class: `button button--small ${value === status ? 'button--primary' : ''}`,
        href: value ? `#/admin/reports?status=${value}` : '#/admin/reports',
        text: value || 'all',
      })),
      el('span', { class: 'subtle', text: Object.entries(payload.counts).map(([key, count]) => `${key}: ${count}`).join(' · ') }),
    ),
    items,
  ]);
}

// ---------------------------------------------------------------- payments and ledger

export async function paymentsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const status = query.status || '';
  const payload = await api(`/api/admin/payments${status ? `?status=${encodeURIComponent(status)}` : ''}`);
  const summaries = payload.summaries;

  const rows = payload.items.map((intent) => el('tr', {},
    el('td', { class: 'mono', text: intent.order_ref }),
    el('td', { text: intent.member_name }),
    el('td', { text: intent.creator.page_name }),
    el('td', { text: `${money(intent.amount_cents)} · ${intent.tier_name}` }),
    el('td', {}, badge(intent.status_label, intent.status)),
    el('td', { text: intent.asset_amount ? `${ui.assetAmount(intent)} · ${ui.assetName(intent)}` : ui.assetName(intent) }),
    el('td', { text: ui.formatDateTime(intent.created_at) }),
    el('td', {}, el('div', { class: 'button-row' },
      intent.status !== 'settled' ? el('button', {
        class: 'button button--small', type: 'button', text: 'Archive attempt',
        onclick: async () => {
          const reason = await notePrompt({ title: 'Archive this unresolved attempt', body: 'Only attempts without a settled invoice can be archived. Money history is never altered.', label: 'Reason' });
          if (reason === null) return;
          try {
            await api(`/api/admin/payments/${intent.id}/archive`, { method: 'POST', body: { reason: reason || null } });
            await reloadAfter(context, 'Attempt archived.', 'info');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }) : el('span', { class: 'subtle', text: 'Append-only' }),
      el('button', {
        class: 'button button--small', type: 'button', text: 'Note',
        onclick: async () => {
          const body = await notePrompt({ title: `Note on ${intent.order_ref}` });
          if (!body) return;
          try {
            await api('/api/admin/notes', { method: 'POST', body: { target_type: 'payment_intent', target_id: intent.id, body } });
            ui.toast('Note added.', 'success');
          } catch (error) { ui.toast(error.message, 'error'); }
        },
      }),
    )),
  ));

  return shell('Payments', 'Velora cannot mark an invoice paid or fabricate a settlement. Held payments stay locked until a human reviews them.', 'payments', [
    notice('info', 'Immutability', payload.immutability_notice),
    el('section', { class: 'hero__stats' },
      ui.statBlock(money(summaries.settled_cents), 'Settled gross'),
      ui.statBlock(money(summaries.fee_cents), 'Platform fees'),
      ui.statBlock(money(summaries.creator_net_cents), 'Creator share'),
      ui.statBlock(String(summaries.held_count), 'Held for review'),
      ui.statBlock(String(summaries.pending_count), 'Pending'),
    ),
    el('div', { class: 'button-row' },
      ['', 'pending', 'processing', 'settled', 'held', 'expired', 'cancelled', 'archived'].map((value) => el('a', {
        class: `button button--small ${value === status ? 'button--primary' : ''}`,
        href: value ? `#/admin/payments?status=${value}` : '#/admin/payments',
        text: value || 'all',
      })),
    ),
    el('div', { class: 'table-wrap' }, el('table', {},
      el('caption', { text: `${payload.items.length} payment attempt(s)` }),
      el('thead', {}, el('tr', {},
        el('th', { scope: 'col', text: 'Order' }), el('th', { scope: 'col', text: 'Member' }),
        el('th', { scope: 'col', text: 'Creator' }), el('th', { scope: 'col', text: 'Amount' }),
        el('th', { scope: 'col', text: 'Status' }), el('th', { scope: 'col', text: 'Crypto amount' }),
        el('th', { scope: 'col', text: 'Created' }), el('th', { scope: 'col', text: 'Actions' }),
      )),
      el('tbody', {}, rows),
    )),
    el('section', { class: 'card' },
      el('h2', { text: 'Webhook deliveries' }),
      el('p', { class: 'muted', text: 'Every delivery is recorded with its signature result. Invalid signatures never change state, and duplicate deliveries are ignored.' }),
      payload.webhooks.length
        ? el('div', { class: 'table-wrap' }, el('table', {},
          el('thead', {}, el('tr', {},
            el('th', { scope: 'col', text: 'Delivery' }), el('th', { scope: 'col', text: 'Event' }),
            el('th', { scope: 'col', text: 'Invoice' }), el('th', { scope: 'col', text: 'Signature' }),
            el('th', { scope: 'col', text: 'Outcome' }), el('th', { scope: 'col', text: 'Received' }),
          )),
          el('tbody', {}, payload.webhooks.map((event) => el('tr', {},
            el('td', { class: 'mono', text: event.delivery_id }),
            el('td', { text: event.event_type || '—' }),
            el('td', { class: 'mono', text: event.invoice_id || '—' }),
            el('td', {}, badge(event.signature_valid ? 'valid' : 'invalid', event.signature_valid ? 'active' : 'held')),
            el('td', { text: event.outcome }),
            el('td', { text: ui.formatDateTime(event.received_at) }),
          ))),
        ))
        : el('p', { class: 'subtle', text: 'No deliveries recorded yet.' }),
    ),
  ]);
}

export async function ledgerPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api('/api/admin/ledger');
  const table = el('table', {},
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: '#' }), el('th', { scope: 'col', text: 'Order' }),
      el('th', { scope: 'col', text: 'Type' }), el('th', { scope: 'col', text: 'Account' }),
      el('th', { scope: 'col', text: 'Direction' }), el('th', { scope: 'col', text: 'Amount' }),
      el('th', { scope: 'col', text: 'Recorded' }),
    )),
    el('tbody', {}, payload.items.map((entry) => el('tr', {},
      el('td', { text: String(entry.id) }),
      el('td', { class: 'mono', text: entry.order_ref || '—' }),
      el('td', { text: entry.entry_type.replace(/_/g, ' ') }),
      el('td', { text: entry.account }),
      el('td', {}, badge(entry.direction, entry.direction === 'credit' ? 'active' : 'reviewing')),
      el('td', { text: money(entry.amount_cents) }),
      el('td', { text: ui.formatDateTime(entry.created_at) }),
    ))),
  );
  return shell('Ledger', 'Append-only. Administrators can read it but never edit or delete an entry.', 'ledger', [
    notice(payload.balanced ? 'success' : 'danger',
      payload.balanced ? 'Debits equal credits' : 'Ledger is not balanced — investigate',
      `Debits ${money(payload.debit_cents)} · credits ${money(payload.credit_cents)}. ${payload.notice}`),
    el('section', { class: 'grid grid--two' }, payload.totals.map((total) => el('div', { class: 'card' },
      el('h3', { text: total.account }),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Direction' }), el('dd', { text: total.direction }),
        el('dt', { text: 'Total' }), el('dd', { text: money(total.total) }),
        el('dt', { text: 'Entries' }), el('dd', { text: String(total.entries) }),
      ),
    ))),
    el('div', { class: 'table-wrap' }, table),
  ]);
}

export async function auditPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api(`/api/admin/audit?limit=${encodeURIComponent(query.limit || '150')}`);
  const table = el('table', {},
    el('caption', { text: `${payload.items.length} most recent entries` }),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: 'When' }), el('th', { scope: 'col', text: 'Actor' }),
      el('th', { scope: 'col', text: 'Role' }), el('th', { scope: 'col', text: 'Action' }),
      el('th', { scope: 'col', text: 'Target' }), el('th', { scope: 'col', text: 'Detail' }),
    )),
    el('tbody', {}, payload.items.map((entry) => el('tr', {},
      el('td', { text: ui.formatDateTime(entry.created_at) }),
      el('td', { text: entry.actor_name || 'system' }),
      el('td', { text: entry.actor_role || '—' }),
      el('td', { class: 'mono', text: entry.action }),
      el('td', { text: `${entry.target_type || '—'} #${entry.target_id || '—'}` }),
      el('td', { class: 'subtle', text: entry.meta || '' }),
    ))),
  );
  return shell('Audit log', 'Append-only record of sign-ins, creator actions and every administrative change.', 'audit', [
    el('div', { class: 'table-wrap' }, table),
  ]);
}

export async function invitationsPage(params, query, context) {
  if (!requireAdmin(context)) return el('div');
  const payload = await api('/api/admin/invitations');
  const emailInput = el('input', { type: 'email', id: 'invite-email', 'aria-label': 'Invitation email' });
  const roleSelect = el('select', { id: 'invite-role' },
    el('option', { value: 'admin', text: 'Administrator' }),
    el('option', { value: 'creator', text: 'Creator' }),
  );
  const noteInput = el('textarea', { rows: 3, maxlength: 300, 'aria-label': 'Note for the invitation' });
  const created = el('div', { role: 'status', 'aria-live': 'polite' });

  const form = el('form', {
    class: 'form',
    onsubmit: async (event) => {
      event.preventDefault();
      const button = event.currentTarget.querySelector('button[type="submit"]');
      ui.setBusy(button, true, 'Creating…');
      try {
        const result = await api('/api/admin/invitations', {
          method: 'POST',
          body: {
            email: emailInput.value.trim() || null,
            role: roleSelect.value,
            note: noteInput.value.trim() || null,
          },
        });
        clear(created);
        const delivery = result.delivery || {};
        created.append(notice('success', 'Invitation created',
          delivery.sent
            ? `An invitation email was sent to ${result.email_bound ? 'that address' : 'the recipient'}. It expires and works once.`
            : (delivery.message || 'Email delivery is unavailable here, so share the single-use link securely. It expires and works once.')));
        if (result.link) {
          created.append(
            el('p', { class: 'muted', text: 'Copy this one-time link now — Velora only stores a hash of it:' }),
            el('p', { class: 'mono', text: result.link }),
          );
        }
        ui.toast('Invitation created.', 'success');
        context.reload();
      } catch (error) {
        clear(created).append(notice('danger', 'Invitation not created', error.message));
      } finally {
        ui.setBusy(button, false);
      }
    },
  },
    el('div', { class: 'field' }, el('label', { for: 'invite-email', text: 'Email address (recommended)' }),
      emailInput, el('p', { class: 'hint', text: 'When set, only that address can accept the invitation.' })),
    el('div', { class: 'field' }, el('label', { for: 'invite-role', text: 'Role' }), roleSelect),
    el('div', { class: 'field' }, el('label', { text: 'Note (optional)' }), noteInput),
    el('div', { class: 'button-row' }, ui.submitButton('Create invitation')),
  );

  return shell('Administrator invitations', 'One-time, expiring invitations. A new administrator must still confirm their email address before acting.', 'invitations', [
    notice('info', 'Why invitations',
      'There is no default administrator and no public admin signup. The first administrator is provisioned from the CLI; every later one comes from an invitation like these or an audited promotion.'),
    el('section', { class: 'card' }, form, created),
    el('section', { class: 'card' },
      el('h2', { text: 'Recent invitations' }),
      payload.items.length
        ? el('div', { class: 'table-wrap' }, el('table', {},
          el('thead', {}, el('tr', {},
            el('th', { scope: 'col', text: 'Created' }), el('th', { scope: 'col', text: 'Role' }),
            el('th', { scope: 'col', text: 'Bound email' }), el('th', { scope: 'col', text: 'State' }),
            el('th', { scope: 'col', text: 'Invited by' }), el('th', { scope: 'col', text: 'Used by' }),
            el('th', { scope: 'col', text: '' }),
          )),
          el('tbody', {}, payload.items.map((invitation) => el('tr', {},
            el('td', { text: ui.formatDateTime(invitation.created_at) }),
            el('td', { text: invitation.role }),
            el('td', { text: invitation.masked_email || 'not bound' }),
            el('td', {}, badge(invitation.state, invitation.state === 'used' ? 'active' : invitation.state === 'usable' ? 'pending' : 'expired')),
            el('td', { text: invitation.invited_by || '—' }),
            el('td', { text: invitation.used_by || '—' }),
            el('td', {}, invitation.state === 'usable' ? el('button', {
              class: 'button button--danger button--small', type: 'button', text: 'Revoke',
              onclick: async () => {
                try {
                  await api(`/api/admin/invitations/${invitation.id}/revoke`, { method: 'POST' });
                  await reloadAfter(context, 'Invitation revoked.', 'info');
                } catch (error) { ui.toast(error.message, 'error'); }
              },
            }) : null),
          ))),
        ))
        : el('p', { class: 'subtle', text: 'No invitations yet.' }),
    ),
  ]);
}

