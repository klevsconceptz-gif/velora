/** Public views: home, discovery, creator pages, posts, privacy and safety. */

import { ApiFailure, api, appState, isSignedIn } from './api.js';
import * as ui from './ui.js';

const { el, clear, notice, chip, badge, money } = ui;

export function creatorCard(creator) {
  const tiers = creator.tiers || [];
  const lowest = creator.lowest_price_cents;
  return el('article', { class: 'card creator-card' },
    el('div', { class: 'creator-card__top' },
      el('span', { class: 'monogram', 'aria-hidden': 'true', text: creator.monogram || 'V' }),
      el('div', {},
        el('div', { class: 'creator-card__name' }, el('a', { href: `#/c/${creator.handle}`, text: creator.page_name })),
        el('div', { class: 'creator-card__handle', text: `@${creator.handle}` }),
      ),
    ),
    el('p', { class: 'creator-card__tagline', text: creator.tagline || 'No tagline yet.' }),
    el('div', { class: 'tier-chips' },
      el('span', { class: 'chip', text: categoryLabel(creator.category) }),
      creator.stats ? el('span', { class: 'chip', text: `${creator.stats.active_members} member${creator.stats.active_members === 1 ? '' : 's'}` }) : null,
      lowest !== null && lowest !== undefined
        ? el('span', { class: 'chip chip--price', text: `from ${money(lowest)} / 30 days` })
        : el('span', { class: 'chip chip--warn', text: 'No tiers yet' }),
    ),
    tiers.length ? el('ul', { class: 'tag-list' },
      tiers.map((tier) => el('li', {}, chip(`${tier.name} · ${money(tier.price_cents)}`))),
    ) : null,
    creator.viewer_membership && creator.viewer_membership.active
      ? notice('success', 'You are a member', `Access until ${ui.formatDate(creator.viewer_membership.ends_at)}.`)
      : null,
    el('div', { class: 'button-row' },
      el('a', {
        class: 'button button--primary button--small',
        href: `#/c/${creator.handle}`,
        text: 'View page',
      }),
    ),
  );
}

export function categoryLabel(key) {
  const found = (appState.categories || []).find((entry) => entry.key === key);
  return found ? found.label : key;
}

// ---------------------------------------------------------------- home

export async function home() {
  const container = el('div', { class: 'stack-lg' });
  const hero = el('section', { class: 'hero', 'aria-labelledby': 'hero-title' },
    el('div', {},
      el('span', { class: 'hero__eyebrow', text: 'Privacy-conscious creator memberships' }),
      el('h1', { id: 'hero-title', text: 'A calm home for the work you charge for.' }),
      el('p', { class: 'lede', text: 'Velora gives creators a public page, paid 30-day membership tiers and a direct line to members — with on-chain Bitcoin payments, no automatic renewal, and no dark patterns.' }),
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--primary', href: '#/discover', text: 'Discover creators' }),
        isSignedIn()
          ? el('a', { class: 'button button--ghost', href: '#/apply', text: 'Apply to create' })
          : el('a', { class: 'button button--ghost', href: '#/signup', text: 'Create an account' }),
      ),
      el('p', { class: 'subtle', text: 'Signing up takes a display name, an email address, a password and an 18+ self-attestation. Nothing else.' }),
    ),
    el('div', { class: 'hero__panel' },
      el('h2', { text: 'How a membership works' }),
      el('ol', { class: 'timeline' },
        el('li', { dataset: { state: 'done' } },
          el('span', { class: 'timeline__step', text: '1' }),
          el('div', {}, el('strong', { text: 'Pick a tier' }), el('p', { class: 'muted', text: 'Prices are set in US dollars by the creator.' }))),
        el('li', { dataset: { state: 'done' } },
          el('span', { class: 'timeline__step', text: '2' }),
          el('div', {}, el('strong', { text: 'Pay in on-chain BTC' }), el('p', { class: 'muted', text: 'BTCPay quotes the BTC amount in its hosted checkout.' }))),
        el('li', { dataset: { state: 'current' } },
          el('span', { class: 'timeline__step', text: '3' }),
          el('div', {}, el('strong', { text: 'Velora verifies the settlement' }), el('p', { class: 'muted', text: 'A signed webhook plus an independent BTCPay check — a redirect alone never unlocks anything.' }))),
        el('li', {},
          el('span', { class: 'timeline__step', text: '4' }),
          el('div', {}, el('strong', { text: '30 days of access' }), el('p', { class: 'muted', text: 'Then it simply ends. Renewing is always your choice.' }))),
      ),
    ),
  );
  container.append(hero);

  const trust = el('section', { 'aria-labelledby': 'trust-title' },
    el('div', { class: 'panel-head' }, el('h2', { id: 'trust-title', text: 'Built to be trusted' })),
    el('div', { class: 'grid grid--two' },
      trustCard('No automatic charges', 'Every 30-day period is a separate payment. Velora never schedules a recurring crypto charge — not as an option, not as a default.'),
      trustCard('Data minimisation', 'No phone number, billing address, government ID or seed phrase is ever requested. Location is never collected and there is no IP geolocation gate.'),
      trustCard('Optional means optional', 'Sexual orientation is private by default, never used to filter discovery, and only appears on a creator page when that person opts in.'),
      trustCard('Append-only money records', 'Settled invoices, the frozen 10% platform fee and the ledger are written once. Not even an administrator can rewrite them.'),
    ),
  );
  container.append(trust);

  const featured = el('section', { 'aria-labelledby': 'featured-title' },
    el('div', { class: 'panel-head' },
      el('h2', { id: 'featured-title', text: 'Creators on Velora' }),
      el('a', { href: '#/discover', text: 'See all' }),
    ),
  );
  const grid = el('div', { class: 'grid' }, ui.skeleton(4));
  featured.append(grid);
  container.append(featured);

  try {
    const payload = await api('/api/creators?per_page=6');
    clear(grid);
    if (!payload.items.length) {
      grid.append(ui.emptyState(
        'No public pages yet',
        'This instance starts with an empty database — no demo creators. Once someone applies and is approved, their page appears here.',
        el('a', { class: 'button button--primary', href: '#/signup', text: 'Create an account' }),
      ));
    } else {
      payload.items.forEach((creator) => grid.append(creatorCard(creator)));
    }
  } catch (error) {
    clear(grid).append(ui.errorState(error));
  }
  return container;
}

function trustCard(title, body) {
  return el('div', { class: 'card card--quiet' }, el('h3', { text: title }), el('p', { class: 'muted', text: body }));
}

// ---------------------------------------------------------------- discovery

export async function discover(params, query) {
  const state = {
    q: query.q || '',
    category: query.category || '',
    sort: query.sort || 'recent',
    page: Number(query.page || 1) || 1,
  };

  const results = el('div', { class: 'grid' });
  const summary = el('p', { class: 'subtle', role: 'status', 'aria-live': 'polite' });
  const pagination = el('div', { class: 'button-row' });

  const searchInput = el('input', {
    type: 'search',
    id: 'discover-search',
    value: state.q,
    placeholder: 'Search by name, handle or tagline',
    'aria-label': 'Search creators',
  });
  const categorySelect = el('select', { id: 'discover-category', 'aria-label': 'Filter by category' },
    el('option', { value: '', text: 'All categories' }),
    (appState.categories || []).map((category) => el('option', {
      value: category.key,
      text: category.label,
      selected: category.key === state.category,
    })),
  );
  const sortSelect = el('select', { id: 'discover-sort', 'aria-label': 'Sort results' },
    el('option', { value: 'recent', text: 'Newest pages', selected: state.sort === 'recent' }),
    el('option', { value: 'members', text: 'Most members', selected: state.sort === 'members' }),
    el('option', { value: 'price', text: 'Lowest price', selected: state.sort === 'price' }),
    el('option', { value: 'name', text: 'Name (A–Z)', selected: state.sort === 'name' }),
  );

  async function load() {
    clear(results).append(ui.skeleton(6));
    clear(pagination);
    try {
      const search = new URLSearchParams();
      if (state.q) search.set('q', state.q);
      if (state.category) search.set('category', state.category);
      search.set('sort', state.sort);
      search.set('page', String(state.page));
      search.set('per_page', '12');
      const payload = await api(`/api/creators?${search.toString()}`);
      clear(results);
      summary.textContent = payload.total
        ? `${payload.total} public page${payload.total === 1 ? '' : 's'} · page ${payload.page} of ${payload.total_pages}`
        : 'No pages match those filters.';
      if (!payload.items.length) {
        results.append(ui.emptyState(
          state.q || state.category ? 'No creators match' : 'No creator pages yet',
          state.q || state.category
            ? 'Try a different search or category, or clear the filters.'
            : 'Pages appear here as soon as a creator is approved and publishes.',
          el('button', {
            class: 'button', type: 'button', text: 'Clear filters',
            onclick: () => {
              state.q = ''; state.category = ''; state.page = 1;
              searchInput.value = ''; categorySelect.value = '';
              load();
            },
          }),
        ));
      } else {
        payload.items.forEach((creator) => results.append(creatorCard(creator)));
      }
      if (payload.page > 1) {
        pagination.append(el('button', {
          class: 'button', type: 'button', text: 'Previous page',
          onclick: () => { state.page -= 1; load(); },
        }));
      }
      if (payload.has_more) {
        pagination.append(el('button', {
          class: 'button', type: 'button', text: 'Next page',
          onclick: () => { state.page += 1; load(); },
        }));
      }
    } catch (error) {
      clear(results).append(ui.errorState(error, load));
    }
  }

  let debounce = null;
  searchInput.addEventListener('input', () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      state.q = searchInput.value.trim();
      state.page = 1;
      load();
    }, 320);
  });
  categorySelect.addEventListener('change', () => {
    state.category = categorySelect.value;
    state.page = 1;
    load();
  });
  sortSelect.addEventListener('change', () => {
    state.sort = sortSelect.value;
    state.page = 1;
    load();
  });

  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Discover creators' }),
      el('p', { class: 'lede', text: 'Browse public pages by category. Discovery never filters on private profile fields — sexual orientation in particular is never part of search.' }),
    ),
    el('section', { class: 'card', 'aria-label': 'Filters' },
      el('div', { class: 'grid grid--two' },
        el('div', { class: 'field' }, el('label', { for: 'discover-search', text: 'Search' }), searchInput),
        el('div', { class: 'field' }, el('label', { for: 'discover-category', text: 'Category' }), categorySelect),
        el('div', { class: 'field' }, el('label', { for: 'discover-sort', text: 'Sort' }), sortSelect),
      ),
    ),
    summary,
    el('section', { 'aria-label': 'Creator results' }, results),
    pagination,
  );
  load();
  return container;
}

// ---------------------------------------------------------------- creator page

export async function creatorPage(params) {
  const handle = params.handle;
  const payload = await api(`/api/creators/${encodeURIComponent(handle)}`);
  const container = el('div', { class: 'stack-lg' });
  const tierSection = el('section', { 'aria-labelledby': 'tiers-title' });
  const postSection = el('section', { 'aria-labelledby': 'posts-title' });

  container.append(
    el('header', { class: 'card' },
      el('div', { class: 'creator-card__top' },
        el('span', { class: 'monogram', 'aria-hidden': 'true', text: payload.monogram || 'V' }),
        el('div', {},
          el('h1', { text: payload.page_name }),
          el('p', { class: 'muted', text: `@${payload.handle} · ${categoryLabel(payload.category)}` }),
        ),
      ),
      payload.tagline ? el('p', { class: 'lede', text: payload.tagline }) : null,
      el('div', { class: 'tier-chips' },
        chip(`${payload.stats.active_members} members`, 'info'),
        chip(`${payload.stats.published_posts} posts`),
        payload.stats.page_views !== undefined ? chip(`${payload.stats.page_views} page views`) : null,
        payload.owner_orientation ? chip(`Shares: ${payload.owner_orientation.label}`) : null,
      ),
      !payload.available ? notice('warning', 'This page is paused', payload.unavailable_notice) : null,
      payload.viewer_membership && payload.viewer_membership.active
        ? notice('success', 'You are a member',
          `Your access runs until ${ui.formatDateTime(payload.viewer_membership.ends_at)}. ` +
          (payload.viewer_membership.cancel_requested_at
            ? 'Renewal is stopped; the time you paid for continues.'
            : 'Nothing renews automatically.'))
        : null,
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--ghost button--small', href: '#/discover', text: 'Back to discover' }),
        el('button', {
          class: 'button button--small', type: 'button', text: 'Report this page',
          onclick: () => reportDialog('creator', payload.id, payload.page_name),
        }),
      ),
      el('p', { class: 'subtle', text: payload.disclosure }),
    ),
  );

  if (payload.about) {
    container.append(el('section', { class: 'card' }, el('h2', { text: 'About' }), el('p', { class: 'post__body', text: payload.about })));
  }

  tierSection.append(el('div', { class: 'panel-head' }, el('h2', { id: 'tiers-title', text: 'Membership tiers' })));
  if (!payload.available) {
    tierSection.append(ui.emptyState('Tiers are hidden', 'This creator paused their page, so tiers are not shown.'));
  } else if (!payload.tiers.length) {
    tierSection.append(ui.emptyState('No tiers yet', 'This creator has not published a membership tier yet.'));
  } else {
    const grid = el('div', { class: 'grid' });
    payload.tiers.forEach((tier) => {
      grid.append(el('article', { class: 'card' },
        el('h3', { text: tier.name }),
        el('p', { class: 'muted', text: tier.description || 'No description provided.' }),
        el('p', {}, el('strong', { text: `${money(tier.price_cents)} / 30 days` })),
        el('div', { class: 'button-row' },
          el('button', {
            class: 'button button--primary button--small',
            type: 'button',
            text: isSignedIn() ? 'Choose this tier' : 'Sign in to join',
            onclick: () => {
              if (!isSignedIn()) {
                window.location.hash = `#/login?next=${encodeURIComponent(`/c/${payload.handle}`)}`;
                return;
              }
              window.location.hash = `#/checkout/tier-${tier.id}`;
            },
          }),
        ),
        el('p', { class: 'subtle', text: 'One payment covers 30 days of access. No automatic renewal.' }),
      ));
    });
    tierSection.append(grid);
  }
  container.append(tierSection);

  postSection.append(
    el('div', { class: 'panel-head' },
      el('h2', { id: 'posts-title', text: 'Posts' }),
      el('span', { class: 'subtle', text: 'Public teasers only for locked posts' }),
    ),
  );
  if (!payload.available) {
    postSection.append(ui.emptyState('Posts are hidden', 'This creator paused their page.'));
  } else if (!payload.posts.length) {
    postSection.append(ui.emptyState('Nothing published yet', 'When this creator publishes a post, it appears here.'));
  } else {
    const list = el('div', { class: 'stack' });
    payload.posts.forEach((post) => list.append(postCard(post, payload)));
    postSection.append(list);
  }
  container.append(postSection);
  return container;
}

export function postCard(post, creator) {
  const card = el('article', { class: 'card post' });
  card.append(
    el('div', { class: 'post__meta' },
      badge(post.visibility === 'public' ? 'Public' : 'Members', post.visibility === 'public' ? 'active' : 'processing'),
      el('span', { text: ui.formatDateTime(post.published_at || post.created_at) }),
      post.locked ? chip('Locked', 'warn') : null,
    ),
    el('h3', {}, el('a', { href: creator ? `#/c/${creator.handle}/post/${post.id}` : '#/discover', text: post.title })),
  );
  if (post.locked) {
    card.append(
      el('p', { class: 'post__teaser', text: post.teaser || 'No public teaser was provided for this post.' }),
      el('div', { class: 'locked-panel' },
        el('strong', { text: 'Members-only content' }),
        el('p', { class: 'muted', text: 'The full post is available to members with an active 30-day membership. Velora checks this on the server, so locked content is never sent to your browser.' }),
        creator ? el('a', { class: 'button button--primary button--small', href: `#/c/${creator.handle}`, text: 'See tiers' }) : null,
      ),
    );
  } else {
    card.append(el('p', { class: 'post__body', text: ui.truncate(post.body || '', 420) }));
    if (post.media && post.media.length) {
      card.append(el('div', { class: 'post__media' },
        post.media.map((media) => el('img', {
          src: media.url,
          alt: media.original_name ? `Attachment: ${media.original_name}` : 'Post attachment',
          loading: 'lazy',
        })),
      ));
    }
    card.append(el('div', { class: 'button-row' },
      creator ? el('a', { class: 'button button--small', href: `#/c/${creator.handle}/post/${post.id}`, text: 'Open post' }) : null,
    ));
  }
  if (creator) {
    card.append(el('div', { class: 'button-row' },
      el('button', {
        class: 'button button--small', type: 'button', text: 'Report',
        onclick: () => reportDialog('post', post.id, post.title),
      }),
    ));
  }
  return card;
}

// ---------------------------------------------------------------- single post

export async function postPage(params) {
  const post = await api(`/api/posts/${encodeURIComponent(params.id)}`);
  const container = el('div', { class: 'stack' });
  container.append(
    el('div', { class: 'button-row' },
      el('a', { class: 'button button--ghost button--small', href: `#/c/${params.handle}`, text: '← Back to page' }),
    ),
    el('article', { class: 'card post' },
      el('div', { class: 'post__meta' },
        badge(post.visibility === 'public' ? 'Public post' : 'Members-only post', post.visibility === 'public' ? 'active' : 'processing'),
        el('span', { text: ui.formatDateTime(post.published_at || post.created_at) }),
        post.access === 'admin' ? badge('Admin view', 'reviewing') : null,
        post.access === 'owner' ? badge('Your post', 'reviewing') : null,
      ),
      el('h1', { text: post.title }),
      post.locked
        ? el('div', { class: 'locked-panel' },
          el('p', { class: 'post__teaser', text: post.teaser }),
          el('p', { class: 'muted', text: post.teaser_notice }),
          el('a', { class: 'button button--primary', href: `#/c/${params.handle}`, text: 'See membership tiers' }),
        )
        : el('div', { class: 'stack' },
          el('p', { class: 'post__body', text: post.body }),
          post.media && post.media.length
            ? el('div', { class: 'post__media' },
              post.media.map((media) => el('figure', {},
                el('img', {
                  src: media.url,
                  alt: media.original_name ? `Attachment: ${media.original_name}` : 'Post attachment',
                  loading: 'lazy',
                }),
                media.original_name ? el('figcaption', { class: 'subtle', text: media.original_name }) : null,
              )))
            : null,
        ),
    ),
    post.gate_notice ? el('p', { class: 'subtle', text: post.gate_notice }) : null,
    el('div', { class: 'button-row' },
      el('button', {
        class: 'button button--small', type: 'button', text: 'Report this post',
        onclick: () => reportDialog('post', post.id, post.title),
      }),
    ),
  );
  return container;
}

// ---------------------------------------------------------------- reporting

export function reportDialog(targetType, targetId, label) {
  const reasonSelect = el('select', { id: 'report-reason' },
    el('option', { value: '', text: 'Choose a reason…' }),
    (appState.reportReasons || []).map((reason) => el('option', { value: reason.value, text: reason.label })),
  );
  const details = el('textarea', { id: 'report-details', rows: 4, maxlength: 1000 });
  const status = el('div', { role: 'status', 'aria-live': 'polite' });

  const dialog = el('dialog', { 'aria-labelledby': 'report-title' },
    el('h3', { id: 'report-title', text: 'Report to moderators' }),
    el('p', { class: 'muted', text: `Reporting: ${label || `${targetType} #${targetId}`}` }),
    el('div', { class: 'field' }, el('label', { for: 'report-reason', text: 'Reason' }), reasonSelect),
    el('div', { class: 'field' },
      el('label', { for: 'report-details', text: 'Anything else we should know? (optional)' }),
      details,
    ),
    status,
    el('div', { class: 'button-row' },
      el('button', {
        class: 'button', type: 'button', text: 'Cancel',
        onclick: () => dialog.close(),
      }),
      el('button', {
        class: 'button button--primary', type: 'button', text: 'Send report',
        onclick: async (event) => {
          const button = event.currentTarget;
          clear(status);
          if (!reasonSelect.value) {
            status.append(notice('warning', 'Choose a reason', 'Moderators need a reason to act on a report.'));
            return;
          }
          if (!isSignedIn()) {
            window.location.hash = `#/login?next=${encodeURIComponent(window.location.hash.slice(1))}`;
            return;
          }
          ui.setBusy(button, true, 'Sending…');
          try {
            await api('/api/reports', {
              method: 'POST',
              body: {
                target_type: targetType,
                target_id: Number(targetId),
                reason_code: reasonSelect.value,
                details: details.value.trim() || null,
              },
            });
            clear(status).append(notice('success', 'Report sent', 'Track its status under Reports in your account.'));
            ui.toast('Report sent to moderators.', 'success');
            setTimeout(() => dialog.close(), 900);
          } catch (error) {
            clear(status).append(notice('danger', 'Report not sent', error.message));
          } finally {
            ui.setBusy(button, false);
          }
        },
      }),
    ),
  );
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove());
  dialog.showModal();
  return dialog;
}

// ---------------------------------------------------------------- static pages

export async function privacyPage() {
  let summary = null;
  try {
    summary = await api('/api/privacy/summary');
  } catch (error) {
    summary = null;
  }
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Privacy and your data' }),
      el('p', { class: 'lede', text: 'Velora collects the minimum needed to run a membership platform, and it tells you exactly what that means.' }),
    ),
  );

  if (!summary) {
    container.append(notice('warning', 'Privacy summary unavailable', 'Velora could not load its detailed privacy summary. The policy below still applies.'));
  }

  const collected = summary ? summary.collected : [
    'Display name, email address and a password hash.',
    'An 18+ self-attestation timestamp (not identity or age verification).',
    'Optional, private-by-default sexual orientation — never used for discovery filters.',
  ];
  const notCollected = summary ? summary.not_collected : [
    'No phone number, billing address or government ID.',
    'No wallet seed phrases, private keys or card details.',
    'No location data and no IP-geolocation gate.',
  ];

  container.append(
    el('section', { class: 'grid grid--two' },
      el('div', { class: 'card' },
        el('h2', { text: 'What Velora stores' }),
        el('ul', { class: 'tag-list' }, collected.map((item) => el('li', {}, chip(item)))),
      ),
      el('div', { class: 'card' },
        el('h2', { text: 'What Velora never asks for' }),
        el('ul', { class: 'tag-list' }, notCollected.map((item) => el('li', {}, chip(item, 'ok')))),
      ),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'Sexual orientation' }),
      el('p', { class: 'muted', text: 'Orientation is optional and private by default. It is never used to filter discovery, search, analytics or public listings, and it is excluded from every public serializer. If you choose to share an eligible option on your own creator page, that choice is audited so you can see when it became visible. "Prefer not to say" and your own words are never displayed publicly.' }),
      el('p', { class: 'muted', text: 'You can change or clear this at any time from Account → Privacy. Clearing it removes the value from your record.' }),
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--ghost button--small', href: '#/account/privacy', text: 'Manage my privacy settings' }),
      ),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'Membership payments' }),
      el('p', { class: 'muted', text: summary ? summary.payments : 'Memberships are paid in on-chain BTC through a hosted BTCPay Server checkout. Velora stores the settled invoice, the frozen platform fee and an append-only ledger.' }),
      el('p', { class: 'muted', text: 'A pending invoice is not a paid membership, and a browser redirect is not proof of payment. Only a verified, settled, matching on-chain invoice grants 30 days of access.' }),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'Your controls' }),
      el('ul', {}, (summary ? summary.rights : [
        'See and export your account data at any time.',
        'Turn optional orientation sharing off.',
        'Deactivate your account so it can no longer sign in.',
        'Delete your personal details; financial history is retained in anonymised form.',
      ]).map((item) => el('li', { text: item }))),
      el('p', { class: 'subtle', text: summary ? summary.demo_data : 'This instance ships with an empty database.' }),
    ),
    el('section', { class: 'card callout-privacy' },
      el('h2', { text: 'Retention and deletion' }),
      el('p', { class: 'muted', text: 'Deactivating hides your page and signs out every session. Deleting replaces your personal details with anonymous placeholders, clears optional and sensitive fields, and removes message bodies you wrote. Membership, invoice and ledger records are kept in anonymised form because settled financial history must stay verifiable and append-only.' }),
    ),
  );
  return container;
}

export async function safetyPage() {
  return el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Safety and reporting' }),
      el('p', { class: 'lede', text: 'Every account, page, post and message can be reported. Reports go to a moderation queue with a visible status.' }),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'How reporting works' }),
      el('ol', { class: 'timeline' },
        el('li', { dataset: { state: 'done' } }, el('span', { class: 'timeline__step', text: '1' }), el('div', {}, el('strong', { text: 'You report' }), el('p', { class: 'muted', text: 'Pick a reason and add context if you want. Reports are rate limited to prevent abuse.' }))),
        el('li', { dataset: { state: 'done' } }, el('span', { class: 'timeline__step', text: '2' }), el('div', {}, el('strong', { text: 'Moderators see the queue' }), el('p', { class: 'muted', text: 'Administrators review reports in the console, where notes and decisions are audited.' }))),
        el('li', {}, el('span', { class: 'timeline__step', text: '3' }), el('div', {}, el('strong', { text: 'You see the outcome' }), el('p', { class: 'muted', text: 'The report keeps a status: open, reviewing, resolved or dismissed, with a note when one is added.' }))),
      ),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'What Velora does not claim' }),
      el('ul', {},
        el('li', { text: 'The 18+ checkbox is a self-attestation, not age verification or identity verification.' }),
        el('li', { text: 'Velora does not verify that a creator controls the BTC address they record.' }),
        el('li', { text: 'Velora does not enforce jurisdictional rules or apply location-based gating.' }),
      ),
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--ghost button--small', href: '#/reports', text: 'My reports' }),
      ),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'Talk to the operator' }),
      el('p', { class: 'muted', text: 'This instance has no built-in support inbox. Reports are the supported channel: an administrator sees them in the console and can add internal notes and a resolution.' }),
    ),
  );
}

export function notFoundPage() {
  return el('div', { class: 'stack' },
    el('h1', { text: 'That page does not exist' }),
    el('p', { class: 'muted', text: 'The link may be old, or the creator may have removed their page.' }),
    el('div', { class: 'button-row' },
      el('a', { class: 'button button--primary', href: '#/discover', text: 'Discover creators' }),
      el('a', { class: 'button button--ghost', href: '#/', text: 'Go home' }),
    ),
  );
}

export { ApiFailure };
