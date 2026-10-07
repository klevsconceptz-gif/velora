/** Creator studio: overview, posts, tiers, members and the payout address. */

import { ApiFailure, api, appState, currentUser, isSignedIn, uploadMedia } from './api.js';
import * as ui from './ui.js';

const { el, clear, notice, chip, badge, money } = ui;

function requireStudio(context, page) {
  if (!isSignedIn()) {
    context.navigate(`/login?next=${encodeURIComponent(context.route.path)}`);
    return false;
  }
  if (!page) {
    context.navigate('/apply');
    return false;
  }
  return true;
}

function studioTabs(active) {
  const tabs = [
    ['Overview', '/studio', 'overview'],
    ['Posts', '/studio/posts', 'posts'],
    ['Tiers', '/studio/tiers', 'tiers'],
    ['Members', '/studio/members', 'members'],
    ['Payout address', '/studio/payout', 'payout'],
  ];
  return el('nav', { class: 'tabs', 'aria-label': 'Studio sections' },
    tabs.map(([label, href, key]) => el('a', {
      href: `#${href}`,
      text: label,
      'aria-current': key === active ? 'page' : null,
    })),
  );
}

function verificationGate(user, container) {
  if (user && !user.email_verified) {
    container.append(notice('warning', 'Confirm your email first',
      'Publishing, tiers and payouts need a confirmed email address. Use “Resend confirmation email” on your account page.'));
    return false;
  }
  return true;
}

// ---------------------------------------------------------------- overview

export async function studioPage(params, query, context) {
  const session = await api('/api/auth/session').catch(() => appState.session);
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const overview = await api('/api/studio/overview');
  const stats = overview.stats;
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: `Studio · ${overview.page.page_name}` }),
      el('p', { class: 'lede', text: `Public page at /c/${overview.page.handle}. Only intentionally public details appear there.` }),
    ),
    studioTabs('overview'),
  );
  verificationGate(session.user, container);

  container.append(el('section', { 'aria-label': 'Key numbers' },
    el('div', { class: 'hero__stats' },
      ui.statBlock(String(stats.active_members), 'Active members'),
      ui.statBlock(String(stats.published_posts), 'Published posts'),
      ui.statBlock(money(stats.net_last_30_days_cents), 'Your share, 30 days'),
      ui.statBlock(money(stats.net_cents), 'Your share, all time'),
    ),
  ));

  container.append(el('section', { class: 'grid grid--two' },
    el('div', { class: 'card' },
      el('h2', { text: 'Money at a glance' }),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Settled gross' }), el('dd', { text: money(stats.gross_cents) }),
        el('dt', { text: 'Platform fee (10%)' }), el('dd', { text: money(stats.platform_fee_cents) }),
        el('dt', { text: 'Your share' }), el('dd', { text: money(stats.net_cents) }),
        el('dt', { text: 'Settled invoices' }), el('dd', { text: String(stats.settled_invoice_count) }),
        el('dt', { text: 'Open payment attempts' }), el('dd', { text: String(stats.open_payment_attempts) }),
      ),
      el('p', { class: 'subtle', text: 'Settled records are append-only. Held or pending payments never appear here as income.' }),
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--ghost button--small', href: '#/studio/payout', text: 'Payout address' }),
        el('a', { class: 'button button--ghost button--small', href: `#/c/${overview.page.handle}`, text: 'View public page' }),
      ),
    ),
    el('div', { class: 'card' },
      el('h2', { text: 'Tiers' }),
      overview.tiers.length
        ? el('ul', { class: 'list' }, overview.tiers.map((tier) => el('li', { class: 'list-item' },
          el('div', { class: 'between' },
            el('strong', { text: tier.name }),
            badge(tier.is_active ? 'On sale' : 'Paused', tier.is_active ? 'active' : 'expired'),
          ),
          el('p', { class: 'muted', text: `${money(tier.price_cents)} / 30 days · ${tier.active_members} active member(s)` }),
        )))
        : el('p', { class: 'muted', text: 'No tiers yet. Create one so members can support you.' }),
      el('div', { class: 'button-row' }, el('a', { class: 'button button--primary button--small', href: '#/studio/tiers', text: 'Manage tiers' })),
    ),
  ));

  container.append(el('section', { class: 'card' },
    el('div', { class: 'panel-head' },
      el('h2', { text: 'Recent posts' }),
      el('a', { class: 'button button--primary button--small', href: '#/studio/posts/new', text: 'New post' }),
    ),
    overview.recent_posts.length
      ? el('ul', { class: 'list' }, overview.recent_posts.map((post) => el('li', { class: 'list-item' },
        el('div', { class: 'between' },
          el('strong', {}, el('a', { href: `#/studio/posts/${post.id}`, text: post.title })),
          badge(post.status, post.status === 'published' ? 'active' : post.status),
        ),
        el('p', { class: 'subtle', text: `${post.visibility === 'public' ? 'Public' : 'Members-only'} · updated ${ui.relative(post.updated_at)}` }),
      )))
      : el('p', { class: 'muted', text: 'Nothing published yet.' }),
  ));

  const pageName = ui.field({ id: 'page-name', label: 'Page name', value: overview.page.page_name, required: true });
  const pageTagline = ui.field({
    id: 'page-tagline', label: 'Tagline', value: overview.page.tagline || '',
    hint: 'One line shown in discovery results.',
  });
  const pageAbout = ui.field({ id: 'page-about', label: 'About your page', type: 'textarea', rows: 6, value: overview.page.about || '' });
  const pageCategory = ui.field({ id: 'page-category', label: 'Category', type: 'select', required: true });
  (appState.categories || []).forEach((entry) => pageCategory.control.append(el('option', {
    value: entry.key, text: entry.label, selected: overview.page.category === entry.key,
  })));
  const pageStatus = ui.field({ id: 'page-status', label: 'Page visibility', type: 'select' });
  pageStatus.control.append(
    el('option', { value: 'active', text: 'Active — shown in discovery and open to new members', selected: overview.page.status === 'active' }),
    el('option', { value: 'paused', text: 'Paused — hidden from discovery, tiers closed', selected: overview.page.status !== 'active' }),
  );
  const editorStatus = el('div', { role: 'status', 'aria-live': 'polite' });
  const editorError = el('div', { role: 'alert' });
  const editorForm = el('form', { class: 'form', novalidate: true },
    editorError,
    pageName.wrapper, pageTagline.wrapper, pageCategory.wrapper, pageAbout.wrapper, pageStatus.wrapper,
    el('div', { class: 'button-row' }, ui.submitButton('Save page details')),
    editorStatus,
  );
  editorForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(editorError);
    const button = editorForm.querySelector('button[type="submit"]');
    ui.setBusy(button, true, 'Saving…');
    try {
      await api('/api/studio/page', {
        method: 'PATCH',
        body: {
          page_name: pageName.control.value.trim(),
          tagline: pageTagline.control.value.trim(),
          about: pageAbout.control.value,
          category: pageCategory.control.value,
          status: pageStatus.control.value,
        },
      });
      ui.toast('Page details saved.', 'success');
      context.reload();
    } catch (failure) {
      editorError.append(notice('danger', 'Page not saved', failure.message));
    } finally {
      ui.setBusy(button, false);
    }
  });

  container.append(el('section', { class: 'card' },
    el('div', { class: 'panel-head' },
      el('h2', { text: 'Page details' }),
      el('a', { class: 'button button--small button--ghost', href: `#/c/${overview.page.handle}`, text: 'View public page' }),
    ),
    el('p', { class: 'muted', text: 'Only these details, your tiers and the posts you publish appear on your public page. Optional orientation is controlled separately under account privacy.' }),
    editorForm,
  ));

  container.append(el('section', { class: 'card card--quiet' },
    el('h2', { text: 'What the studio can and cannot do' }),
    el('ul', {},
      el('li', { text: 'Publishing, editing, archiving posts and setting tiers are all server-authorised to your account.' }),
      el('li', { text: 'Member email addresses are never shown here — only display names, tier and dates.' }),
      el('li', { text: 'Velora does not send payouts or split funds. The operator reviews and settles payouts out of band.' }),
    ),
  ));
  return container;
}

// ---------------------------------------------------------------- posts

export async function postsPage(params, query, context) {
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const status = query.status || '';
  const payload = await api(`/api/studio/posts${status ? `?status=${encodeURIComponent(status)}` : ''}`);
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Posts' }),
      el('p', { class: 'lede', text: 'Draft, publish and archive. Members-only posts need a public teaser so visitors see a safe preview instead of your content.' }),
    ),
    studioTabs('posts'),
  );

  container.append(el('div', { class: 'between' },
    el('div', { class: 'button-row' },
      el('a', { class: status === '' ? 'button button--small button--primary' : 'button button--small', href: '#/studio/posts', text: 'All' }),
      el('a', { class: status === 'published' ? 'button button--small button--primary' : 'button button--small', href: '#/studio/posts?status=published', text: 'Published' }),
      el('a', { class: status === 'draft' ? 'button button--small button--primary' : 'button button--small', href: '#/studio/posts?status=draft', text: 'Drafts' }),
      el('a', { class: status === 'archived' ? 'button button--small button--primary' : 'button button--small', href: '#/studio/posts?status=archived', text: 'Archived' }),
    ),
    el('a', { class: 'button button--primary button--small', href: '#/studio/posts/new', text: 'New post' }),
  ));

  if (!payload.items.length) {
    container.append(ui.emptyState('No posts here', 'Create your first post and choose who can read it.',
      el('a', { class: 'button button--primary', href: '#/studio/posts/new', text: 'Write a post' })));
    return container;
  }

  const list = el('div', { class: 'stack' });
  payload.items.forEach((post) => {
    const card = el('article', { class: 'card' },
      el('div', { class: 'between' },
        el('h3', {}, el('a', { href: `#/studio/posts/${post.id}`, text: post.title })),
        el('div', { class: 'cluster' },
          badge(post.status, post.status === 'published' ? 'active' : post.status),
          badge(post.visibility === 'public' ? 'Public' : 'Members', post.visibility === 'public' ? 'dismissed' : 'reviewing'),
          post.admin_archived ? badge('Archived by a moderator', 'held') : null,
        ),
      ),
      el('p', { class: 'post__teaser', text: post.teaser || 'No teaser' }),
      el('p', { class: 'subtle', text: `${post.media.length} attachment(s) · updated ${ui.relative(post.updated_at)}` }),
      post.admin_archive_note ? notice('danger', 'Moderator note', post.admin_archive_note) : null,
      el('div', { class: 'button-row' },
        el('a', { class: 'button button--small', href: `#/studio/posts/${post.id}`, text: 'Edit' }),
        post.status !== 'published' ? el('button', {
          class: 'button button--small button--primary', type: 'button', text: 'Publish',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Publishing…');
            try {
              await api(`/api/studio/posts/${post.id}/publish`, { method: 'POST' });
              ui.toast('Post published.', 'success');
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }) : el('button', {
          class: 'button button--small', type: 'button', text: 'Unpublish',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Working…');
            try {
              await api(`/api/studio/posts/${post.id}/unpublish`, { method: 'POST' });
              ui.toast('Post moved back to drafts.', 'info');
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
        post.status !== 'archived' ? el('button', {
          class: 'button button--danger button--small', type: 'button', text: 'Archive',
          onclick: async (event) => {
            const confirmed = await ui.confirmDialog({
              title: 'Archive this post?',
              body: 'The post stops being served to visitors and members. Nothing is deleted; you can publish it again later.',
              confirmLabel: 'Archive post',
              danger: true,
            });
            if (!confirmed) return;
            ui.setBusy(event.currentTarget, true, 'Archiving…');
            try {
              await api(`/api/studio/posts/${post.id}/archive`, { method: 'POST' });
              ui.toast('Post archived.', 'info');
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }) : null,
      ),
    );
    list.append(card);
  });
  container.append(el('section', {}, list));
  return container;
}

export async function postEditorPage(params, query, context) {
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const isNew = !params.id || params.id === 'new' || context.route.name === 'studioNewPost';
  let post = null;
  if (!isNew) {
    post = await api(`/api/studio/posts/${encodeURIComponent(params.id)}`);
  }
  const tiers = (await api('/api/studio/tiers')).items;
  const container = el('div', { class: 'stack-lg' },
    el('header', {}, el('h1', { text: isNew ? 'New post' : `Edit: ${post.title}` })),
    studioTabs('posts'),
  );

  const title = ui.field({ id: 'post-title', label: 'Title', value: post ? post.title : '', required: true });
  const teaser = ui.field({
    id: 'post-teaser', label: 'Public teaser', type: 'textarea', rows: 3,
    value: post ? post.teaser : '',
    hint: 'Shown to everyone, including visitors. Required for members-only posts (at least a sentence). Never include private details here.',
  });
  const body = ui.field({ id: 'post-body', label: 'Post body', type: 'textarea', rows: 10, value: post ? post.body : '', required: true });
  const visibility = el('select', { id: 'post-visibility' },
    el('option', { value: 'members', text: 'Members-only (needs a public teaser)', selected: !post || post.visibility === 'members' }),
    el('option', { value: 'public', text: 'Public (anyone can read)', selected: post && post.visibility === 'public' }),
  );
  const tierBoxes = el('div', { class: 'cluster' },
    tiers.length ? tiers.map((tier) => {
      const box = el('input', {
        type: 'checkbox', id: `tier-${tier.id}`, value: String(tier.id),
        checked: post && post.tier_ids.includes(tier.id) ? 'checked' : null,
      });
      return el('label', { class: 'checkbox', for: `tier-${tier.id}` }, box, el('span', { text: `${tier.name} — ${money(tier.price_cents)}` }));
    }) : [el('p', { class: 'subtle', text: 'No tiers yet: members-only posts unlock for any active membership of your page.' })],
  );

  const error = el('div', { role: 'alert' });
  const status = el('div', { role: 'status', 'aria-live': 'polite' });

  const form = el('form', { class: 'form', novalidate: true },
    error,
    title.wrapper,
    el('div', { class: 'field' }, el('label', { for: 'post-visibility', text: 'Who can read this?' }), visibility),
    teaser.wrapper,
    body.wrapper,
    el('fieldset', {}, el('legend', { text: 'Unlock for these tiers (optional)' }), tierBoxes,
      el('p', { class: 'hint', text: 'Leave all unchecked to unlock the post for any active tier.' })),
    el('div', { class: 'button-row' },
      ui.submitButton(isNew ? 'Save draft' : 'Save changes'),
      el('button', {
        class: 'button button--primary', type: 'button', text: isNew ? 'Save and publish' : 'Save and publish',
        onclick: async (event) => {
          ui.setBusy(event.currentTarget, true, 'Publishing…');
          const saved = await save({ publish: true, silent: true, button: event.currentTarget });
          if (saved) {
            ui.toast('Post published.', 'success');
            context.navigate(`/studio/posts/${saved.id}`);
          }
          ui.setBusy(event.currentTarget, false);
        },
      }),
    ),
  );

  function collect() {
    return {
      title: title.control.value.trim(),
      teaser: teaser.control.value.trim(),
      body: body.control.value,
      visibility: visibility.value,
      tier_ids: [...form.querySelectorAll('input[type="checkbox"]:checked')]
        .map((box) => Number(box.value)).filter((value) => !Number.isNaN(value)),
    };
  }

  async function save({ publish = false, silent = false, button = null } = {}) {
    clear(error);
    const payload = collect();
    if (publish) payload.publish = true;
    try {
      if (isNew) {
        const created = await api('/api/studio/posts', { method: 'POST', body: payload });
        if (!silent) {
          clear(status).append(notice('success', 'Post saved', 'Use “Save and publish” when it is ready.'));
          context.navigate(`/studio/posts/${created.id}`);
        }
        return created;
      }
      const updated = await api(`/api/studio/posts/${post.id}`, { method: 'PATCH', body: payload });
      if (publish) {
        await api(`/api/studio/posts/${post.id}/publish`, { method: 'POST' });
      }
      if (!silent) clear(status).append(notice('success', 'Changes saved', 'Your post was updated.'));
      return updated;
    } catch (failure) {
      const field = failure instanceof ApiFailure ? failure.field : null;
      const message = failure.message;
      const target = { title: title, teaser: teaser, body: body }[field];
      if (target) {
        const existing = target.wrapper.querySelector('.error');
        if (existing) existing.remove();
        target.wrapper.append(el('div', { class: 'error', role: 'alert', text: message }));
      }
      error.append(notice('danger', 'Not saved', message));
      ui.toast(message, 'error');
      return null;
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    ui.setBusy(button, true, 'Saving…');
    await save();
    ui.setBusy(button, false);
  });

  container.append(el('section', { class: 'card' }, form, status));

  const mediaCard = el('section', { class: 'card' }, el('h2', { text: 'Image attachments' }));
  if (isNew) {
    mediaCard.append(el('p', { class: 'muted', text: 'Save the post first, then attach images. PNG, JPEG, GIF or WebP only — the file type is verified from the file itself.' }));
  } else {
    const progress = el('div', { class: 'progress' }, el('div', { class: 'progress__bar' }));
    const fileInput = el('input', { type: 'file', id: 'media-file', accept: 'image/png,image/jpeg,image/gif,image/webp' });
    const uploadError = el('div', { role: 'alert' });
    mediaCard.append(
      el('div', { class: 'grid' }, (post.media || []).map((media) => el('figure', {},
        el('img', { src: `/api/studio/media/${media.id}`, alt: media.original_name ? `Attachment: ${media.original_name}` : 'Attachment', loading: 'lazy' }),
        el('figcaption', { class: 'subtle', text: `${media.content_type} · ${Math.round(media.byte_size / 1024)} KB` }),
        el('button', {
          class: 'button button--danger button--small', type: 'button', text: 'Remove',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Removing…');
            try {
              await api(`/api/studio/posts/${post.id}/media/${media.id}`, { method: 'DELETE' });
              ui.toast('Attachment removed.', 'info');
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
      ))),
      el('div', { class: 'field' }, el('label', { for: 'media-file', text: 'Add an image' }), fileInput,
        el('p', { class: 'hint', text: 'Up to 8 MB. Velora checks the real file signature and ignores the browser’s claimed type.' })),
      progress,
      uploadError,
    );
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      clear(uploadError);
      progress.querySelector('.progress__bar').dataset.progress = '0';
      try {
        await uploadMedia(post.id, file, () => {});
        ui.toast('Image attached.', 'success');
        context.reload();
      } catch (error) {
        uploadError.append(notice('danger', 'Upload failed', error.message));
      } finally {
        fileInput.value = '';
      }
    });
  }
  container.append(mediaCard);
  return container;
}

// ---------------------------------------------------------------- tiers

export async function tiersPage(params, query, context) {
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const payload = await api('/api/studio/tiers');
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Membership tiers' }),
      el('p', { class: 'lede', text: 'Prices are stored in whole cents in US dollars; BTCPay quotes the BTC amount at checkout. Each tier buys a 30-day period.' }),
    ),
    studioTabs('tiers'),
  );

  const list = el('section', { class: 'card' }, el('h2', { text: 'Your tiers' }));
  if (!payload.items.length) {
    list.append(ui.emptyState('No tiers yet', 'Create your first tier to start accepting members.'));
  } else {
    list.append(el('ul', { class: 'list' }, payload.items.map((tier) => el('li', { class: 'list-item' },
      el('div', { class: 'between' },
        el('strong', { text: tier.name }),
        el('div', { class: 'cluster' },
          badge(tier.is_active ? 'On sale' : 'Paused', tier.is_active ? 'active' : 'expired'),
          chip(`${tier.active_members} member(s)`),
        ),
      ),
      el('p', { class: 'muted', text: tier.description || 'No description' }),
      el('p', {}, el('strong', { text: `${money(tier.price_cents)} / 30 days` })),
      el('div', { class: 'button-row' },
        el('button', {
          class: 'button button--small', type: 'button', text: 'Edit price or name',
          onclick: () => {
            const nameInput = el('input', { type: 'text', value: tier.name, id: 'tier-name' });
            const priceInput = el('input', { type: 'text', value: (tier.price_cents / 100).toFixed(2), id: 'tier-price' });
            const descriptionInput = el('textarea', { id: 'tier-desc', rows: 3 }, tier.description || '');
            const dialog = el('dialog', { 'aria-labelledby': `tier-edit-${tier.id}` },
              el('h3', { id: `tier-edit-${tier.id}`, text: `Edit ${tier.name}` }),
              el('div', { class: 'field' }, el('label', { for: 'tier-name', text: 'Name' }), nameInput),
              el('div', { class: 'field' }, el('label', { for: 'tier-price', text: 'Price in USD' }), priceInput,
                el('p', { class: 'hint', text: 'Minimum $1.00. Existing members keep the price they paid.' })),
              el('div', { class: 'field' }, el('label', { for: 'tier-desc', text: 'Description' }), descriptionInput),
              el('div', { class: 'button-row' },
                el('button', { class: 'button', type: 'button', text: 'Cancel', onclick: () => dialog.close() }),
                el('button', {
                  class: 'button button--primary', type: 'button', text: 'Save tier',
                  onclick: async (event) => {
                    ui.setBusy(event.currentTarget, true, 'Saving…');
                    try {
                      await api(`/api/studio/tiers/${tier.id}`, {
                        method: 'PATCH',
                        body: { name: nameInput.value.trim(), price: priceInput.value.trim(), description: descriptionInput.value.trim() },
                      });
                      ui.toast('Tier updated.', 'success');
                      dialog.close();
                      context.reload();
                    } catch (error) {
                      ui.toast(error.message, 'error');
                    } finally {
                      ui.setBusy(event.currentTarget, false);
                    }
                  },
                }),
              ),
            );
            document.body.append(dialog);
            dialog.addEventListener('close', () => dialog.remove());
            dialog.showModal();
          },
        }),
        el('button', {
          class: `button button--small ${tier.is_active ? 'button--danger' : 'button--primary'}`, type: 'button',
          text: tier.is_active ? 'Pause sales' : 'Put back on sale',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Updating…');
            try {
              const result = await api(`/api/studio/tiers/${tier.id}/active`, {
                method: 'POST', body: { is_active: !tier.is_active },
              });
              ui.toast(result.notice || 'Tier updated.', 'info', 7000);
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
      ),
    ))));
  }
  container.append(list);

  const createCard = el('section', { class: 'card' }, el('h2', { text: 'Add a tier' }));
  const name = ui.field({ id: 'new-tier-name', label: 'Tier name', required: true });
  const price = ui.field({ id: 'new-tier-price', label: 'Price in USD per 30 days', required: true, hint: 'Examples: 5, 9.99, 25' });
  const description = ui.field({ id: 'new-tier-desc', label: 'What does it include?', type: 'textarea', rows: 3 });
  const createError = el('div', { role: 'alert' });
  const createForm = el('form', { class: 'form', novalidate: true }, createError, name.wrapper, price.wrapper, description.wrapper,
    el('div', { class: 'button-row' }, ui.submitButton('Create tier')));
  createForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(createError);
    const button = createForm.querySelector('button[type="submit"]');
    ui.setBusy(button, true, 'Creating…');
    try {
      await api('/api/studio/tiers', {
        method: 'POST',
        body: { name: name.control.value.trim(), price: price.control.value.trim(), description: description.control.value.trim() },
      });
      ui.toast('Tier created.', 'success');
      context.reload();
    } catch (error) {
      createError.append(notice('danger', 'Tier not created', error.message));
    } finally {
      ui.setBusy(button, false);
    }
  });
  createCard.append(createForm);
  container.append(createCard);
  return container;
}

// ---------------------------------------------------------------- members

export async function membersPage(params, query, context) {
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const payload = await api('/api/studio/members');
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Members' }),
      el('p', { class: 'lede', text: 'Who currently supports your page, and who has lapsed. Email addresses are never shared with creators.' }),
    ),
    studioTabs('members'),
    notice('info', 'Privacy by design', payload.privacy_note),
  );
  if (!payload.items.length) {
    container.append(ui.emptyState('No members yet', 'When someone pays for a tier, they appear here with their membership window.'));
    return container;
  }
  const table = el('table', {},
    el('caption', { text: `${payload.items.length} membership record(s)` }),
    el('thead', {}, el('tr', {},
      el('th', { scope: 'col', text: 'Member' }),
      el('th', { scope: 'col', text: 'Tier' }),
      el('th', { scope: 'col', text: 'Status' }),
      el('th', { scope: 'col', text: 'Started' }),
      el('th', { scope: 'col', text: 'Access until' }),
      el('th', { scope: 'col', text: 'Renewal' }),
    )),
    el('tbody', {}, payload.items.map((item) => el('tr', {},
      el('td', { text: item.member_name }),
      el('td', { text: item.tier_name }),
      el('td', {}, badge(item.active ? 'active' : item.status, item.active ? 'active' : item.status)),
      el('td', { text: ui.formatDate(item.started_at) }),
      el('td', { text: ui.formatDateTime(item.ends_at) }),
      el('td', { text: item.cancel_requested_at ? 'Cancelled by member' : 'Manual' }),
    ))),
  );
  container.append(el('div', { class: 'table-wrap' }, table));
  return container;
}

// ---------------------------------------------------------------- payout

export async function payoutPage(params, query, context) {
  if (!requireStudio(context, appState.creatorPage)) return el('div');
  const payload = await api('/api/studio/payout');
  const payout = payload.payout;
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'BTC receiving address' }),
      el('p', { class: 'lede', text: 'Where you want the operator to send your share. Saving it records a destination and nothing more.' }),
    ),
    studioTabs('payout'),
    notice('warning', 'Read this before saving',
      'Velora does not verify that you control this address, does not split funds and does not send transfers. The operator reviews payouts manually. Never paste a seed phrase or a private key here — Velora will never ask for one.'),
  );

  if (payout.btc_address) {
    container.append(el('section', { class: 'card' },
      el('h2', { text: 'Address on file' }),
      el('p', { class: 'mono', text: payout.btc_address }),
      el('p', { class: 'subtle', text: `Type detected: ${payout.address_kind} · saved ${ui.formatDateTime(payout.saved_at)}` }),
      el('p', { class: 'muted', text: 'Visible only to you and authorized administrators.' }),
    ));
  } else {
    container.append(el('section', { class: 'card' }, el('h2', { text: 'No address yet' }), el('p', { class: 'muted', text: payout.notice })));
  }

  const address = ui.field({
    id: 'payout-address', label: 'On-chain BTC receiving address', value: payout.btc_address || '',
    hint: 'Mainnet addresses beginning with 1, 3 or bc1. Velora checks the format only.',
  });
  const error = el('div', { role: 'alert' });
  const form = el('form', { class: 'form', novalidate: true }, error, address.wrapper,
    el('div', { class: 'button-row' }, ui.submitButton('Save address')));
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(error);
    const button = form.querySelector('button[type="submit"]');
    ui.setBusy(button, true, 'Saving…');
    try {
      await api('/api/studio/payout', { method: 'PUT', body: { btc_address: address.control.value.trim() } });
      ui.toast('Receiving address saved.', 'success');
      context.reload();
    } catch (failure) {
      error.append(notice('danger', 'Address not saved', failure.message));
    } finally {
      ui.setBusy(button, false);
    }
  });
  container.append(el('section', { class: 'card' }, form));
  return container;
}
