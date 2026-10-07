/** Account views: auth, account settings, privacy, memberships, messages, reports, checkout. */

import {
  ApiFailure, api, appState, canAct, currentUser, describeFailure, isSignedIn,
} from './api.js';
import * as ui from './ui.js';
import { postCard } from './views-public.js';

const { el, clear, notice, chip, badge, money } = ui;

function requireSignedIn(context) {
  if (!isSignedIn()) {
    context.navigate(`/login?next=${encodeURIComponent(context.route.path)}`);
    return false;
  }
  return true;
}

function statusNotice(user) {
  if (!user) return null;
  if (!user.email_verified) {
    return notice('warning', 'Confirm your email address',
      'Velora sends a single-use link to your address. Until it is confirmed you can browse, but buying memberships, messaging, publishing and reporting need a confirmed address.',
      el('button', {
        class: 'button button--small', type: 'button', text: 'Resend confirmation email',
        onclick: async (event) => {
          const button = event.currentTarget;
          ui.setBusy(button, true, 'Sending…');
          try {
            const result = await api('/api/auth/resend-verification', { method: 'POST', body: { email: user.email } });
            if (result.sent) {
              ui.toast('Confirmation email sent.', 'success');
            } else if (result.reason === 'unavailable') {
              ui.toast('Email delivery is not configured on this instance.', 'error');
            } else if (result.already_verified) {
              ui.toast('That address is already confirmed.', 'info');
            } else {
              ui.toast('If that address needs confirming, an email is on its way.', 'info');
            }
          } catch (error) {
            ui.toast(error.message, 'error');
          } finally {
            ui.setBusy(button, false);
          }
        },
      }),
    );
  }
  return null;
}

// ---------------------------------------------------------------- signup

export async function signupPage(params, query, context) {
  if (isSignedIn()) {
    context.navigate('/account');
    return el('div');
  }
  const container = el('div', { class: 'stack-lg' });
  const form = el('form', { class: 'form', novalidate: true });
  const formError = el('div', { role: 'alert' });

  const name = ui.field({ id: 'signup-name', label: 'Display name', autocomplete: 'name', required: true, hint: 'Shown on your profile and creator page.' });
  const email = ui.field({ id: 'signup-email', label: 'Email address', type: 'email', autocomplete: 'email', required: true, hint: 'Used for sign-in and verification. Never shown to creators.' });
  const password = ui.field({ id: 'signup-password', label: 'Password', type: 'password', autocomplete: 'new-password', required: true, hint: 'At least 8 characters.' });
  const confirm = ui.field({ id: 'signup-confirm', label: 'Confirm password', type: 'password', autocomplete: 'new-password', required: true });
  const adult = el('input', { type: 'checkbox', id: 'signup-adult' });

  const submit = ui.submitButton('Create account');
  const result = el('div', { role: 'status', 'aria-live': 'polite' });

  form.append(
    formError,
    name.wrapper, email.wrapper, password.wrapper, confirm.wrapper,
    el('div', { class: 'checkbox' },
      adult,
      el('div', {},
        el('label', { for: 'signup-adult', text: 'I confirm that I am 18 or older.' }),
        el('p', { class: 'subtle', text: 'This is a self-attestation. Velora does not verify your age or identity, and does not ask for a phone number, billing address or ID.' }),
      ),
    ),
    el('div', { class: 'button-row' }, submit),
    el('p', { class: 'subtle' },
      'Already have an account? ',
      el('a', { href: '#/login', text: 'Sign in' }),
    ),
  );

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(formError);
    clear(result);
    if (!adult.checked) {
      formError.append(notice('warning', 'Confirmation needed', 'Tick the 18+ self-attestation to create an account.'));
      adult.focus();
      return;
    }
    ui.setBusy(submit, true, 'Creating…');
    try {
      const created = await api('/api/auth/signup', {
        method: 'POST',
        body: {
          display_name: name.control.value.trim(),
          email: email.control.value.trim(),
          password: password.control.value,
          password_confirm: confirm.control.value,
          adult_attestation: true,
        },
      });
      clear(result).append(
        notice(created.verification.sent ? 'success' : 'warning',
          created.verification.sent ? 'Account created' : 'Account created — email unavailable',
          created.next_step),
        el('div', { class: 'button-row' },
          el('a', { class: 'button button--primary', href: '#/login', text: 'Go to sign in' }),
        ),
      );
      form.querySelectorAll('input').forEach((input) => { input.value = ''; });
      ui.toast('Account created.', 'success');
    } catch (error) {
      const field = error instanceof ApiFailure ? error.field : null;
      const map = { display_name: name, email: email, password: password, password_confirm: confirm };
      const target = field ? map[field] : null;
      if (target) {
        const existing = target.wrapper.querySelector('.error');
        if (existing) existing.remove();
        target.wrapper.append(el('div', { class: 'error', role: 'alert', text: error.message }));
        target.control.setAttribute('aria-invalid', 'true');
        target.control.focus();
      }
      formError.append(notice('danger', 'Could not create the account',
        field === 'adult_attestation'
          ? 'Tick the 18+ self-attestation: it records that you confirmed you are an adult.'
          : error.message));
      ui.toast(error.message, 'error');
    } finally {
      ui.setBusy(submit, false);
    }
  });

  container.append(
    el('header', {},
      el('h1', { text: 'Create your Velora account' }),
      el('p', { class: 'lede', text: 'Four details, nothing more: a display name, an email address, a password and an 18+ self-attestation.' }),
    ),
    el('div', { class: 'grid grid--two' },
      el('section', { class: 'card' }, form, result),
      el('aside', { class: 'card card--quiet' },
        el('h2', { text: 'What you get' }),
        el('ul', {},
          el('li', { text: 'A member feed of the creators you support.' }),
          el('li', { text: 'Direct messages with creators you support.' }),
          el('li', { text: 'Clear payment history: every settled invoice, in one place.' }),
        ),
        el('h2', { text: 'What Velora will not do' }),
        el('ul', {},
          el('li', { text: 'Ask for your phone number, address, ID or seed phrase.' }),
          el('li', { text: 'Charge you automatically — renewal is always manual.' }),
          el('li', { text: 'Share your email address with creators.' }),
        ),
      ),
    ),
  );
  return container;
}

// ---------------------------------------------------------------- login

export async function loginPage(params, query, context) {
  if (isSignedIn()) {
    context.navigate('/account');
    return el('div');
  }
  const next = query.next && query.next.startsWith('/') ? query.next : '/account';
  const form = el('form', { class: 'form', novalidate: true });
  const email = ui.field({ id: 'login-email', label: 'Email address', type: 'email', autocomplete: 'email', required: true });
  const password = ui.field({ id: 'login-password', label: 'Password', type: 'password', autocomplete: 'current-password', required: true });
  const error = el('div', { role: 'alert' });
  const submit = ui.submitButton('Sign in');

  form.append(error, email.wrapper, password.wrapper, el('div', { class: 'button-row' }, submit),
    el('p', { class: 'subtle' },
      'No account yet? ',
      el('a', { href: '#/signup', text: 'Create one' }),
    ));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(error);
    ui.setBusy(submit, true, 'Signing in…');
    try {
      const payload = await api('/api/auth/login', {
        method: 'POST',
        body: { email: email.control.value.trim(), password: password.control.value },
      });
      await import('./api.js').then(({ loadSession }) => loadSession());
      context.refreshShell();
      ui.toast(`Welcome back${payload.user ? `, ${payload.user.display_name}` : ''}.`, 'success');
      if (payload.verification_required) {
        clear(error).append(notice('warning', 'Confirm your email address', payload.notice));
        return;
      }
      context.navigate(next, { replace: true });
      await context.reload();
    } catch (failure) {
      error.append(notice('danger', 'Sign-in failed', failure.message));
      ui.toast(failure.message, 'error');
    } finally {
      ui.setBusy(submit, false);
    }
  });

  return el('div', { class: 'stack-lg' },
    el('header', {}, el('h1', { text: 'Sign in to Velora' })),
    el('div', { class: 'grid grid--two' },
      el('section', { class: 'card' }, form),
      el('aside', { class: 'card card--quiet' },
        el('h2', { text: 'Session safety' }),
        el('ul', {},
          el('li', { text: 'Sessions use an HttpOnly cookie, so scripts cannot read your token.' }),
          el('li', { text: 'Every change you make carries an anti-forgery token.' }),
          el('li', { text: 'You can sign out other devices from your account page.' }),
        ),
        el('p', { class: 'subtle', text: 'Velora stores only a keyed hash of the network address a session came from — never the raw address, and never a password.' }),
      ),
    ),
  );
}

// ---------------------------------------------------------------- verify / invite

export async function verifyPage(params, query, context) {
  const token = query.token;
  const container = el('div', { class: 'stack-lg' }, el('h1', { text: 'Confirm your email' }));
  if (!token) {
    container.append(notice('warning', 'No token in this link', 'Open the confirmation link from your email exactly as sent, or request a new one from your account page.'));
    return container;
  }
  try {
    const result = await api('/api/auth/verify-email', { method: 'POST', body: { token } });
    container.append(
      notice('success', 'Email confirmed', `Thanks, ${result.display_name}. Your address ${result.email} is confirmed.`),
      el('div', { class: 'button-row' }, el('a', { class: 'button button--primary', href: `#/login?next=/account`, text: 'Sign in' })),
    );
  } catch (error) {
    container.append(
      notice('danger', 'Confirmation failed', error.message),
      el('div', { class: 'button-row' }, el('a', { class: 'button button--ghost', href: '#/login', text: 'Sign in to resend' })),
    );
  }
  return container;
}

export async function invitePage(params, query, context) {
  const token = query.token;
  const container = el('div', { class: 'stack-lg' });
  if (!token) {
    container.append(el('h1', { text: 'Invitation' }), notice('warning', 'Incomplete link', 'This invitation link is missing its token.'));
    return container;
  }
  let preview;
  try {
    preview = await api(`/api/invitations/${encodeURIComponent(token)}`);
  } catch (error) {
    container.append(el('h1', { text: 'Invitation' }), notice('danger', 'Invitation not usable', error.message));
    return container;
  }

  container.append(
    el('h1', { text: 'Velora invitation' }),
    notice('info', `Invitation for ${preview.role === 'admin' ? 'administrator' : 'creator'} access`,
      preview.note ? `Message from the inviter: ${preview.note}` : 'Review the details below before accepting.'),
  );

  if (isSignedIn() && currentUser()) {
    const user = currentUser();
    const accept = el('button', { class: 'button button--primary', type: 'button', text: 'Accept invitation' });
    const status = el('div', { role: 'status' });
    accept.addEventListener('click', async () => {
      ui.setBusy(accept, true, 'Accepting…');
      try {
        const result = await api('/api/invitations/accept', { method: 'POST', body: { token } });
        clear(status).append(notice('success', 'Invitation accepted', `Your account now has ${result.role} access.`));
        await import('./api.js').then(({ loadSession }) => loadSession());
        context.refreshShell();
      } catch (error) {
        clear(status).append(notice('danger', 'Could not accept', error.message));
      } finally {
        ui.setBusy(accept, false);
      }
    });
    container.append(
      el('section', { class: 'card' },
        el('p', { class: 'muted', text: `Signed in as ${user.display_name}${preview.masked_email ? ` · invitation bound to ${preview.masked_email}` : ''}.` }),
        accept,
        status,
      ),
    );
    return container;
  }

  if (!preview.email_bound) {
    container.append(notice('warning', 'Create an account first',
      'This invitation is not bound to an email address. Create an account or sign in, then open the invitation link again.'));
    container.append(el('div', { class: 'button-row' },
      el('a', { class: 'button button--primary', href: '#/signup', text: 'Create an account' }),
      el('a', { class: 'button button--ghost', href: '#/login', text: 'Sign in' }),
    ));
    return container;
  }

  const name = ui.field({ id: 'invite-name', label: 'Display name', required: true });
  const password = ui.field({ id: 'invite-password', label: 'Password', type: 'password', autocomplete: 'new-password', required: true, hint: 'At least 8 characters.' });
  const confirm = ui.field({ id: 'invite-confirm', label: 'Confirm password', type: 'password', autocomplete: 'new-password', required: true });
  const adult = el('input', { type: 'checkbox', id: 'invite-adult' });
  const error = el('div', { role: 'alert' });
  const submit = ui.submitButton('Accept and create account');
  const form = el('form', { class: 'form', novalidate: true },
    error, name.wrapper, password.wrapper, confirm.wrapper,
    el('div', { class: 'checkbox' }, adult,
      el('div', {},
        el('label', { for: 'invite-adult', text: 'I confirm that I am 18 or older.' }),
        el('p', { class: 'subtle', text: 'A self-attestation, not age verification.' }),
      ),
    ),
    el('div', { class: 'button-row' }, submit),
  );

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(error);
    ui.setBusy(submit, true, 'Accepting…');
    try {
      const result = await api('/api/invitations/accept', {
        method: 'POST',
        body: {
          token,
          display_name: name.control.value.trim(),
          password: password.control.value,
          password_confirm: confirm.control.value,
          adult_attestation: adult.checked,
        },
      });
      clear(error).append(notice('success', 'Invitation accepted',
        result.verification && !result.verification.sent
          ? 'Your account exists, but this instance could not send the confirmation email. An operator must configure email delivery.'
          : 'Check your email for the confirmation link, then sign in.'));
    } catch (failure) {
      error.append(notice('danger', 'Could not accept the invitation', failure.message));
    } finally {
      ui.setBusy(submit, false);
    }
  });

  container.append(el('section', { class: 'card' }, el('h2', { text: 'Create your account' }), form));
  return container;
}

// ---------------------------------------------------------------- apply to create

export async function applyPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const user = currentUser();
  const container = el('div', { class: 'stack-lg' }, el('h1', { text: 'Apply to create on Velora' }));
  container.append(statusNotice(user));

  let application = appState.application;
  try {
    const current = await api('/api/creators/applications/mine');
    application = current.application;
  } catch (error) {
    application = appState.application;
  }

  if (appState.creatorPage) {
    container.append(notice('success', 'You already have a creator page',
      `Your page is at /c/${appState.creatorPage.handle}.`,
      el('div', { class: 'button-row' }, el('a', { class: 'button button--primary button--small', href: '#/studio', text: 'Open studio' }))));
    return container;
  }

  if (application) {
    const variant = application.status === 'approved' ? 'success' : application.status === 'rejected' ? 'danger' : 'info';
    container.append(notice(variant, `Application ${application.status}`,
      application.decision_note || 'Your application is in the review queue. Nothing else is required from you right now.'));
    if (application.status !== 'pending') {
      container.append(el('p', { class: 'subtle', text: 'You can submit a new application if your plans changed.' }));
    } else {
      return container;
    }
  }

  const category = ui.field({ id: 'apply-category', label: 'Category', type: 'select', required: true });
  const select = category.control;
  select.append(el('option', { value: '', text: 'Choose a category…' }));
  (appState.categories || []).forEach((entry) => select.append(el('option', { value: entry.key, text: entry.label })));
  const handle = ui.field({ id: 'apply-handle', label: 'Preferred handle (optional)', hint: 'Lowercase letters, numbers, hyphens. 3–30 characters.' });
  const pitch = ui.field({ id: 'apply-pitch', label: 'What will members get?', type: 'textarea', required: true, hint: 'At least 40 characters. This goes to the operator reviewing your application.' });
  const error = el('div', { role: 'alert' });
  const submit = ui.submitButton('Submit application');
  const walletInputs = new Map();
  const walletSection = el('fieldset', { class: 'stack' },
    el('legend', { text: 'Crypto payment wallets (optional now, needed before members can pay you)' }),
    el('p', { class: 'subtle', text: 'Add a receiving address for each coin or token you want to accept. Use the exact network shown. You can add or change these any time in the studio. Never enter a seed phrase or private key.' }),
  );
  (appState.paymentAssets || []).forEach((asset) => {
    const input = ui.field({
      id: `apply-wallet-${asset.key}`, label: `${asset.label} address`,
      hint: [`Example: ${asset.example}`, asset.warning].filter(Boolean).join(' '),
    });
    input.control.setAttribute('autocomplete', 'off');
    input.control.setAttribute('spellcheck', 'false');
    walletInputs.set(asset.key, input.control);
    walletSection.append(input.wrapper);
  });
  const form = el('form', { class: 'form', novalidate: true }, error, category.wrapper, handle.wrapper, pitch.wrapper, walletSection, el('div', { class: 'button-row' }, submit));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    clear(error);
    ui.setBusy(submit, true, 'Submitting…');
    try {
      const created = await api('/api/creators/apply', {
        method: 'POST',
        body: {
          category: select.value,
          desired_handle: handle.control.value.trim() || null,
          pitch: pitch.control.value.trim(),
          wallets: Object.fromEntries([...walletInputs.entries()]
            .map(([key, control]) => [key, control.value.trim()])
            .filter(([, value]) => value)),
        },
      });
      clear(error).append(notice('success', `Application ${created.status}`,
        'A human reviews applications. Once approved, your page and tiers can be created from the studio.'));
      ui.toast('Application submitted.', 'success');
    } catch (failure) {
      error.append(notice('danger', 'Application not submitted', failure.message));
    } finally {
      ui.setBusy(submit, false);
    }
  });

  container.append(
    el('p', { class: 'lede', text: 'Creator pages are reviewed before going live. Tell the operator what you plan to publish; you can edit everything later in the studio.' }),
    el('section', { class: 'card' }, form),
    el('aside', { class: 'card card--quiet' },
      el('h2', { text: 'How payouts work' }),
      el('ul', {},
        el('li', { text: 'Members pay on-chain in a coin or Tether token you accept, through BTCPay Server, into the operator’s store.' }),
        el('li', { text: 'You record a receiving address for each coin you accept; the operator reviews payouts manually.' }),
        el('li', { text: 'Velora never sends funds automatically and never holds your keys.' }),
      ),
    ),
  );
  return container;
}

// ---------------------------------------------------------------- account

export async function accountPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const session = await api('/api/auth/session');
  const user = session.user;
  const container = el('div', { class: 'stack-lg' });
  const status = el('div', { role: 'status', 'aria-live': 'polite' });

  container.append(
    el('header', {},
      el('h1', { text: 'Your account' }),
      el('p', { class: 'lede', text: 'Profile, security and privacy controls. Email addresses are never shown to creators or other members.' }),
    ),
    statusNotice(user),
    status,
  );

  // Profile
  const nameField = ui.field({ id: 'profile-name', label: 'Display name', value: user.display_name, required: true });
  const bioField = ui.field({ id: 'profile-bio', label: 'Short bio (optional)', type: 'textarea', value: user.bio || '', hint: 'Shown on your creator page when you have one.' });
  const profileForm = el('form', { class: 'form', novalidate: true }, nameField.wrapper, bioField.wrapper,
    el('div', { class: 'button-row' }, ui.submitButton('Save profile')));
  profileForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = profileForm.querySelector('button[type="submit"]');
    ui.setBusy(button, true, 'Saving…');
    try {
      const updated = await api('/api/account/profile', {
        method: 'PATCH',
        body: { display_name: nameField.control.value.trim(), bio: bioField.control.value.trim() },
      });
      clear(status).append(notice('success', 'Profile saved', `Display name is now “${updated.user.display_name}”.`));
      await import('./api.js').then(({ loadSession }) => loadSession());
      context.refreshShell();
    } catch (error) {
      clear(status).append(notice('danger', 'Profile not saved', error.message));
    } finally {
      ui.setBusy(button, false);
    }
  });

  container.append(el('section', { class: 'card' }, el('h2', { text: 'Profile' }), profileForm));

  // Security
  const sessionsCard = el('section', { class: 'card' }, el('h2', { text: 'Sessions and security' }));
  const sessionList = el('div', { class: 'stack' }, ui.skeleton(2));
  sessionsCard.append(sessionList);
  sessionsCard.append(el('div', { class: 'button-row' },
    el('button', {
      class: 'button', type: 'button', text: 'Sign out other devices',
      onclick: async (event) => {
        ui.setBusy(event.currentTarget, true, 'Signing out…');
        try {
          const result = await api('/api/account/sessions/revoke-others', { method: 'POST' });
          ui.toast(`${result.revoked} other session(s) signed out.`, 'success');
          refreshSessions();
        } catch (error) {
          ui.toast(error.message, 'error');
        } finally {
          ui.setBusy(event.currentTarget, false);
        }
      },
    }),
  ));
  async function refreshSessions() {
    try {
      const payload = await api('/api/account/sessions');
      clear(sessionList);
      sessionList.append(el('ul', { class: 'list' }, payload.items.map((session) => el('li', { class: `list-item ${session.current ? 'list-item--active' : ''}` },
        el('div', { class: 'between' },
          el('strong', { text: session.current ? 'This device' : 'Another device' }),
          el('span', { class: 'subtle', text: `Last seen ${ui.relative(session.last_seen_at)}` }),
        ),
        el('p', { class: 'subtle', text: session.user_agent || 'Unknown client' }),
        el('p', { class: 'subtle', text: `Expires ${ui.formatDateTime(session.expires_at)}` }),
      ))));
      sessionList.append(el('p', { class: 'subtle', text: payload.notice }));
    } catch (error) {
      clear(sessionList).append(notice('warning', 'Sessions unavailable', error.message));
    }
  }
  refreshSessions();
  container.append(sessionsCard);

  // Privacy
  container.append(el('section', { class: 'card' },
    el('h2', { text: 'Privacy and data' }),
    el('p', { class: 'muted', text: 'Orientation is optional and private by default. You can also export or remove your data from here.' }),
    el('div', { class: 'button-row' },
      el('a', { class: 'button button--ghost', href: '#/account/privacy', text: 'Privacy settings' }),
      el('a', { class: 'button button--ghost', href: '#/reports', text: 'My reports' }),
      el('button', {
        class: 'button', type: 'button', text: 'Download my data',
        onclick: async (event) => {
          ui.setBusy(event.currentTarget, true, 'Preparing…');
          try {
            const data = await api('/api/account/export');
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const link = el('a', { href: url, download: 'velora-account-export.json' });
            document.body.append(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            ui.toast('Export downloaded.', 'success');
          } catch (error) {
            ui.toast(error.message, 'error');
          } finally {
            ui.setBusy(event.currentTarget, false);
          }
        },
      }),
    ),
  ));

  // Deactivate / delete
  const deactivate = el('section', { class: 'card' },
    el('h2', { text: 'Deactivate or delete' }),
    el('p', { class: 'muted', text: 'Deactivating hides your page and signs out every device. Deleting replaces your personal details with anonymous placeholders.' }),
  );
  const passwordField = ui.field({ id: 'account-password', label: 'Your password', type: 'password', autocomplete: 'current-password' });
  deactivate.append(passwordField.wrapper);
  deactivate.append(el('div', { class: 'button-row' },
    el('button', {
      class: 'button button--danger', type: 'button', text: 'Deactivate my account',
      onclick: async (event) => {
        if (!passwordField.control.value) {
          ui.toast('Enter your password first.', 'error');
          passwordField.control.focus();
          return;
        }
        const confirmed = await ui.confirmDialog({
          title: 'Deactivate your account?',
          body: 'Your page is hidden, every session is signed out, and you cannot sign in until an administrator restores the account. Paid-for membership time is not refunded or removed.',
          confirmLabel: 'Deactivate',
          danger: true,
        });
        if (!confirmed) return;
        ui.setBusy(event.currentTarget, true, 'Deactivating…');
        try {
          await api('/api/account/deactivate', { method: 'POST', body: { password: passwordField.control.value } });
          ui.toast('Account deactivated.', 'info');
          await import('./api.js').then(({ loadSession }) => loadSession());
          context.navigate('/', { replace: true });
          context.refreshShell();
          await context.reload();
        } catch (error) {
          ui.toast(error.message, 'error');
        } finally {
          ui.setBusy(event.currentTarget, false);
        }
      },
    }),
  ));
  const confirmInput = el('input', { type: 'text', id: 'delete-confirm', placeholder: 'delete my account' });
  deactivate.append(el('div', { class: 'field' },
    el('label', { for: 'delete-confirm', text: 'Type “delete my account” to confirm deletion' }),
    confirmInput,
    el('p', { class: 'hint', text: 'Settled payment records are kept in anonymised form because financial history is append-only.' }),
  ));
  deactivate.append(el('div', { class: 'button-row' },
    el('button', {
      class: 'button button--danger', type: 'button', text: 'Delete my personal data',
      onclick: async (event) => {
        const confirmed = await ui.confirmDialog({
          title: 'Delete your personal data?',
          body: 'This cannot be undone. Your email, display name, bio and optional fields are replaced with anonymous placeholders, and messages you wrote are cleared.',
          confirmLabel: 'Delete my data',
          danger: true,
        });
        if (!confirmed) return;
        ui.setBusy(event.currentTarget, true, 'Deleting…');
        try {
          const result = await api('/api/account/delete', {
            method: 'POST',
            body: { password: passwordField.control.value, confirm: confirmInput.value.trim() },
          });
          ui.toast(result.message, 'success', 9000);
          await import('./api.js').then(({ loadSession }) => loadSession());
          context.navigate('/', { replace: true });
          context.refreshShell();
          await context.reload();
        } catch (error) {
          ui.toast(error.message, 'error');
        } finally {
          ui.setBusy(event.currentTarget, false);
        }
      },
    }),
  ));
  container.append(deactivate);
  container.append(el('p', { class: 'subtle', text: `Account created ${ui.formatDate(user.created_at)} · role ${user.role} · email ${user.email}` }));
  return container;
}

// ---------------------------------------------------------------- orientation / privacy

export async function orientationPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const payload = await api('/api/account/privacy');
  const user = payload.user;
  const options = appState.orientationOptions || {
    presets: [], public_eligible: [],
    prefer_not_to_say: { value: 'prefer_not_to_say', label: 'Prefer not to say' },
    sensitivity: 'Sexual orientation is optional, private by default and never used to filter discovery.',
  };
  const eligible = new Set(options.public_eligible || []);
  const current = user.orientation || { value: null, self_described: null, visibility: 'private' };
  const container = el('div', { class: 'stack-lg' });
  const status = el('div', { role: 'status', 'aria-live': 'polite' });

  const presetSelect = el('select', { id: 'orientation-value' },
    el('option', { value: '', text: 'No answer', selected: !current.value && !current.self_described }),
    (options.presets || []).map((option) => el('option', {
      value: option.value,
      text: eligible.has(option.value) ? option.label : `${option.label} (private only)`,
      selected: current.value === option.value,
    })),
    el('option', {
      value: '__self__', text: 'Describe it in your own words (private only)',
      selected: Boolean(current.self_described),
    }),
    el('option', {
      value: options.prefer_not_to_say.value, text: options.prefer_not_to_say.label,
      selected: current.value === options.prefer_not_to_say.value,
    }),
  );
  const selfText = el('input', {
    type: 'text', id: 'orientation-self', maxlength: 300,
    value: current.self_described || '',
    placeholder: 'Describe it in your own words',
    disabled: current.self_described ? null : 'disabled',
  });
  const visibility = el('select', { id: 'orientation-visibility' },
    el('option', { value: 'private', text: 'Keep private (default)', selected: current.visibility !== 'public' }),
    el('option', { value: 'public', text: 'Show on my creator page', selected: current.visibility === 'public' }),
  );

  const visibilityNote = el('p', { class: 'hint', text: 'Choose a listed option to make “show on my creator page” possible. Your own words and “prefer not to say” always stay private.' });
  function syncFields() {
    const value = presetSelect.value;
    selfText.disabled = value !== '__self__';
    if (value !== '__self__') selfText.value = current.self_described || '';
    const shareable = eligible.has(value) || (value === '' ? false : false);
    visibility.disabled = !shareable && value !== options.prefer_not_to_say.value ? !shareable : false;
    clear(visibilityNote);
    visibilityNote.append(document.createTextNode(
      shareable
        ? 'This option may appear on your own creator page only, and you can turn it off at any time.'
        : 'This choice cannot be shown publicly. It is stored privately and never appears on any page.',
    ));
  }
  presetSelect.addEventListener('change', syncFields);
  syncFields();

  const form = el('form', { class: 'form', novalidate: true },
    el('div', { class: 'field' },
      el('label', { for: 'orientation-value', text: 'Sexual orientation (optional)' }),
      presetSelect),
    el('div', { class: 'field' },
      el('label', { for: 'orientation-self', text: 'Or your own words (optional)' }),
      selfText),
    el('div', { class: 'field' },
      el('label', { for: 'orientation-visibility', text: 'Visibility' }),
      visibility,
      visibilityNote),
    el('div', { class: 'button-row' }, ui.submitButton('Save privacy settings')),
  );

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const value = presetSelect.value;
    const body = {
      orientation: value === '__self__'
        ? { self_described: selfText.value.trim() || null }
        : { value: value || null },
      visibility: visibility.disabled && visibility.value === 'public' ? 'private' : visibility.value,
    };
    ui.setBusy(button, true, 'Saving…');
    try {
      const result = await api('/api/account/orientation', { method: 'PUT', body });
      clear(status);
      (result.notes || []).forEach((note) => status.append(notice('info', 'Kept private', note)));
      status.append(notice('success', 'Privacy settings saved',
        result.user.orientation.visibility === 'public'
          ? 'This value now appears on your own creator page only.'
          : 'This value stays private and never appears on any public page.'));
      await import('./api.js').then(({ loadSession }) => loadSession());
    } catch (error) {
      clear(status).append(notice('danger', 'Not saved', error.message));
    } finally {
      ui.setBusy(button, false);
    }
  });

  container.append(
    el('header', {}, el('h1', { text: 'Privacy settings' })),
    el('div', { class: 'callout-privacy', text: options.sensitivity }),
    el('div', { class: 'grid grid--two' },
      el('section', { class: 'card' }, form, status),
      el('aside', { class: 'card card--quiet' },
        el('h2', { text: 'Rules Velora enforces' }),
        el('ul', {},
          el('li', { text: '“Prefer not to say” is never displayed publicly.' }),
          el('li', { text: 'Your own words stay private and never reach a public page.' }),
          el('li', { text: 'Orientation is never used in discovery, search, analytics or logs.' }),
          el('li', { text: 'Every change is recorded so you can see when something became visible.' }),
        ),
        el('h2', { text: 'Your disclosure history' }),
        payload.orientation_disclosures.length
          ? el('ul', { class: 'list' }, payload.orientation_disclosures.map((entry) => el('li', { class: 'list-item' },
            el('div', { class: 'between' },
              el('strong', { text: entry.visibility === 'public' ? 'Shared on your creator page' : 'Kept private' }),
              el('span', { class: 'subtle', text: ui.formatDateTime(entry.created_at) }),
            ),
            el('p', { class: 'subtle', text: `Value type: ${String(entry.value_kind).replace(/_/g, ' ')} · changed by you` }),
          )))
          : el('p', { class: 'subtle', text: 'No changes recorded yet.' }),
      ),
    ),
    el('section', { class: 'card' },
      el('h2', { text: 'What Velora holds about you' }),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Email' }), el('dd', { text: user.email }),
        el('dt', { text: '18+ self-attestation' }), el('dd', { text: `${ui.formatDate(user.adult_attested_at)} (self-attestation, not verification)` }),
        el('dt', { text: 'Email confirmed' }), el('dd', { text: user.email_verified ? `Yes, ${ui.formatDate(user.email_verified_at)}` : 'Not yet' }),
        el('dt', { text: 'Orientation' }), el('dd', { text: current.value ? String(current.value).replace(/_/g, ' ') : (current.self_described ? 'Self-described (private)' : 'Not provided') }),
        el('dt', { text: 'Visibility' }), el('dd', { text: current.visibility }),
      ),
      el('p', { class: 'subtle', text: payload.explainers.retention }),
      el('p', { class: 'subtle', text: payload.explainers.data_minimisation }),
      payload.account_actions.length
        ? el('div', {},
          el('h3', { text: 'Account history' }),
          el('ul', { class: 'list' }, payload.account_actions.map((entry) => el('li', { class: 'list-item' },
            el('div', { class: 'between' },
              el('strong', { text: String(entry.action).replace(/\./g, ' · ') }),
              el('span', { class: 'subtle', text: ui.formatDateTime(entry.created_at) }),
            ),
            el('p', { class: 'subtle', text: `By ${entry.actor_kind}${entry.reason ? ` · ${entry.reason}` : ''}` }),
          ))))
        : null,
    ),
  );
  return container;
}

// ---------------------------------------------------------------- memberships & payments

export async function membershipsPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const [overview, payments] = await Promise.all([
    api('/api/memberships'),
    api('/api/payments/status').catch(() => ({ intents: [], invoices: [], unavailable_notice: null })),
  ]);
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Your memberships' }),
      el('p', { class: 'lede', text: 'Each membership covers 30 days from the moment its payment settles. Nothing renews automatically — cancelling keeps the time you already paid for.' }),
    ),
  );

  const list = el('div', { class: 'stack' });
  if (!overview.items.length) {
    list.append(ui.emptyState('No memberships yet',
      overview.empty_state ? overview.empty_state.body : 'Support a creator to unlock their members-only posts.',
      el('a', { class: 'button button--primary', href: '#/discover', text: 'Discover creators' })));
  }
  overview.items.forEach((item) => {
    const membership = item.membership;
    const card = el('article', { class: 'card' },
      el('div', { class: 'between' },
        el('h3', {}, el('a', { href: `#/c/${membership.creator.handle}`, text: membership.creator.page_name })),
        badge(item.access.active ? (item.access.cancel_requested ? 'Cancelling' : 'Active') : membership.status,
          item.access.active ? (item.access.cancel_requested ? 'pending' : 'active') : membership.status),
      ),
      el('dl', { class: 'kv' },
        el('dt', { text: 'Tier' }), el('dd', { text: membership.tier.name }),
        el('dt', { text: 'Access until' }), el('dd', { text: ui.formatDateTime(membership.ends_at) }),
        el('dt', { text: 'Automatic renewal' }), el('dd', { text: 'Never — renewal is always manual' }),
      ),
      el('div', { class: 'button-row' },
        item.access.active ? el('a', { class: 'button button--small', href: `#/c/${membership.creator.handle}`, text: 'Continue on page' }) : el('a', { class: 'button button--primary button--small', href: `#/c/${membership.creator.handle}`, text: 'Renew access' }),
        item.access.cancel_requested && item.access.active ? el('button', {
          class: 'button button--small', type: 'button', text: 'Keep it running',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Updating…');
            try {
              await api(`/api/memberships/${membership.id}/resume`, { method: 'POST' });
              ui.toast('Cancellation withdrawn.', 'success');
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }) : null,
        item.access.active ? el('button', {
          class: 'button button--danger button--small', type: 'button', text: 'Cancel renewal',
          onclick: async (event) => {
            const confirmed = await ui.confirmDialog({
              title: 'Cancel this membership?',
              body: `Access continues until ${ui.formatDateTime(membership.ends_at)}. Cancelling only stops a future renewal; nothing you paid for is removed.`,
              confirmLabel: 'Cancel renewal',
            });
            if (!confirmed) return;
            ui.setBusy(event.currentTarget, true, 'Cancelling…');
            try {
              const result = await api(`/api/memberships/${membership.id}/cancel`, { method: 'POST' });
              ui.toast(result.message || 'Renewal cancelled. Access continues until the period ends.', 'success', 8000);
              context.reload();
            } catch (error) {
              ui.toast(error.message, 'error');
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }) : null,
        el('button', {
          class: 'button button--small', type: 'button', text: 'Report a problem',
          onclick: () => import('./views-public.js').then(({ reportDialog }) => reportDialog('creator', membership.creator.id, membership.creator.page_name)),
        }),
      ),
    );
    list.append(card);
  });
  container.append(el('section', {}, el('h2', { text: 'Access' }), list));

  const history = el('section', { class: 'card' },
    el('h2', { text: 'Payment status' }),
    el('p', { class: 'muted', text: 'A pending invoice is not a paid membership: access only follows a verified, settled on-chain payment.' }),
  );
  if (payments.unavailable_notice) {
    history.append(notice('warning', 'Crypto checkout is unavailable', payments.unavailable_notice));
  }
  if (!payments.intents.length) {
    history.append(el('p', { class: 'subtle', text: 'No payment attempts yet.' }));
  } else {
    const table = el('table', {},
      el('caption', { text: 'Every order and its real status' }),
      el('thead', {}, el('tr', {},
        el('th', { scope: 'col', text: 'Order' }), el('th', { scope: 'col', text: 'Creator' }),
        el('th', { scope: 'col', text: 'Amount' }), el('th', { scope: 'col', text: 'Pay with' }),
        el('th', { scope: 'col', text: 'Status' }),
        el('th', { scope: 'col', text: 'Created' }), el('th', { scope: 'col', text: '' }),
      )),
      el('tbody', {}, payments.intents.map((intent) => el('tr', {},
        el('td', { class: 'mono', text: intent.order_ref }),
        el('td', { text: intent.creator.page_name }),
        el('td', { text: money(intent.amount_cents) }),
        el('td', { text: ui.assetName(intent) }),
        el('td', {}, badge(intent.status_label, intent.status)),
        el('td', { text: ui.formatDate(intent.created_at) }),
        el('td', {}, el('a', { href: `#/checkout/${intent.order_ref}`, text: 'Open' })),
      ))),
    );
    history.append(el('div', { class: 'table-wrap' }, table));
  }
  container.append(history);

  if (payments.invoices.length) {
    container.append(el('section', { class: 'card' },
      el('h2', { text: 'Settled invoices' }),
      el('p', { class: 'subtle', text: 'Settled records are append-only: amounts and fees never change after settlement.' }),
      el('div', { class: 'table-wrap' }, el('table', {},
        el('thead', {}, el('tr', {},
          el('th', { scope: 'col', text: 'Order' }), el('th', { scope: 'col', text: 'Amount' }),
          el('th', { scope: 'col', text: 'Paid in' }), el('th', { scope: 'col', text: 'Settled' }),
        )),
        el('tbody', {}, payments.invoices.map((invoice) => el('tr', {},
          el('td', { class: 'mono', text: invoice.order_ref }),
          el('td', { text: money(invoice.amount_cents) }),
          el('td', { text: `${ui.assetAmount(invoice)} · ${ui.assetName(invoice)}` }),
          el('td', { text: ui.formatDateTime(invoice.settled_at) }),
        ))),
      )),
    ));
  }
  return container;
}

// ---------------------------------------------------------------- feed

export async function feedPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const payload = await api('/api/feed');
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Member feed' }),
      el('p', { class: 'lede', text: 'Posts from creators you currently support. Locked posts stay locked until a payment settles.' }),
    ),
  );
  if (!payload.items.length) {
    container.append(ui.emptyState(
      payload.empty_state ? payload.empty_state.title : 'Nothing here yet',
      payload.empty_state ? payload.empty_state.body : 'Posts from creators you support appear here.',
      el('a', { class: 'button button--primary', href: '#/discover', text: 'Discover creators' }),
    ));
    return container;
  }
  container.append(el('div', { class: 'stack' }, payload.items.map((post) => postCard(post, { handle: post.creator.handle, id: post.creator.id }))));
  if (payload.memberships.length) {
    container.append(el('section', { class: 'card' },
      el('h2', { text: 'Active access' }),
      el('ul', { class: 'tag-list' }, payload.memberships.map((membership) => el('li', {},
        chip(`${membership.page_name} until ${ui.formatDate(membership.ends_at)}`, membership.cancel_requested ? 'warn' : 'ok'))),
      ),
    ));
  }
  return container;
}

// ---------------------------------------------------------------- messages

export async function messagesPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const container = el('div', { class: 'stack-lg' }, el('header', {}, el('h1', { text: 'Messages' })));
  const payload = await api('/api/threads');
  container.append(el('p', { class: 'lede', text: payload.rules }));

  const layout = el('div', { class: 'thread-layout' });
  const listPane = el('section', { class: 'card', 'aria-label': 'Conversations' }, el('h2', { text: 'Conversations' }));
  const detailPane = el('section', { class: 'card', 'aria-label': 'Conversation' });
  layout.append(listPane, detailPane);
  container.append(layout);

  if (!payload.items.length) {
    listPane.append(ui.emptyState('No conversations yet', payload.empty_state.body));
    detailPane.append(el('h2', { text: 'Start a conversation' }),
      el('p', { class: 'muted', text: 'Messages open with creators you currently support. Support a tier, then send the first message from their page.' }));
    const memberships = await api('/api/memberships').catch(() => ({ items: [] }));
    const active = memberships.items.filter((item) => item.access.active);
    if (active.length) {
      const select = el('select', { id: 'thread-creator' }, active.map((item) => el('option', {
        value: String(item.membership.creator.id), text: item.membership.creator.page_name,
      })));
      const body = el('textarea', { id: 'thread-body', rows: 4, maxlength: 4000 });
      detailPane.append(el('div', { class: 'field' }, el('label', { for: 'thread-creator', text: 'Creator' }), select));
      detailPane.append(el('div', { class: 'field' }, el('label', { for: 'thread-body', text: 'Message' }), body));
      detailPane.append(el('button', {
        class: 'button button--primary', type: 'button', text: 'Send message',
        onclick: async (event) => {
          ui.setBusy(event.currentTarget, true, 'Sending…');
          try {
            const created = await api('/api/threads', {
              method: 'POST',
              body: { creator_id: Number(select.value), body: body.value.trim() },
            });
            ui.toast('Message sent.', 'success');
            context.navigate(`/messages/${created.thread.id}`);
          } catch (error) {
            ui.toast(error.message, 'error');
          } finally {
            ui.setBusy(event.currentTarget, false);
          }
        },
      }));
    }
    return container;
  }

  listPane.append(el('ul', { class: 'list' }, payload.items.map((thread) => el('li', {
    class: `list-item ${String(thread.id) === String(params.id) ? 'list-item--active' : ''}`,
  },
    el('div', { class: 'between' },
      el('strong', { text: thread.counterpart.display_name }),
      thread.unread_count ? badge(`${thread.unread_count} new`, 'pending') : null,
    ),
    el('p', { class: 'subtle', text: ui.truncate(thread.preview || 'No messages yet', 80) }),
    el('div', { class: 'between' },
      el('span', { class: 'subtle', text: ui.relative(thread.last_message_at) }),
      el('a', { href: `#/messages/${thread.id}`, text: 'Open' }),
    ),
  ))));

  const threadId = params.id ? Number(params.id) : payload.items[0].id;
  const thread = await api(`/api/threads/${threadId}`);
  detailPane.append(
    el('div', { class: 'between' },
      el('h2', { text: thread.thread.counterpart.display_name }),
      thread.thread.membership_state === 'ended' ? badge('Read-only', 'expired') : badge('Active', 'active'),
    ),
  );
  if (thread.thread.counterpart.kind === 'member') {
    detailPane.append(el('p', { class: 'subtle', text: thread.thread.counterpart.note }));
  }
  const messageList = el('ul', { class: 'message-list' }, thread.messages.map((message) => {
    const mine = currentUser() && message.sender_id === currentUser().id;
    return el('li', { class: `message ${mine ? 'message--mine' : ''}` },
      el('div', { class: 'message__meta', text: `${mine ? 'You' : thread.thread.counterpart.display_name} · ${ui.relative(message.created_at)}` }),
      el('div', { class: 'message__body', text: message.removed ? '[removed]' : message.body }),
    );
  }));
  detailPane.append(messageList);

  if (thread.can_send) {
    const body = el('textarea', { id: 'reply-body', rows: 3, maxlength: 4000, 'aria-label': 'Write a reply' });
    detailPane.append(el('form', {
      class: 'form',
      onsubmit: async (event) => {
        event.preventDefault();
        const button = event.currentTarget.querySelector('button[type="submit"]');
        if (!body.value.trim()) return;
        ui.setBusy(button, true, 'Sending…');
        try {
          await api(`/api/threads/${threadId}/messages`, { method: 'POST', body: { body: body.value.trim() } });
          body.value = '';
          context.reload();
        } catch (error) {
          ui.toast(error.message, 'error');
        } finally {
          ui.setBusy(button, false);
        }
      },
    }, el('div', { class: 'field' }, el('label', { for: 'reply-body', text: 'Reply' }), body),
      el('div', { class: 'button-row' }, ui.submitButton('Send reply'))));
  } else {
    detailPane.append(notice('info', 'Read-only conversation', thread.read_only_reason));
  }
  return container;
}

// ---------------------------------------------------------------- reports

export async function reportsPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const payload = await api('/api/reports/mine');
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'My reports' }),
      el('p', { class: 'lede', text: 'Reports you send go to the moderation queue with a status you can follow.' }),
    ),
  );
  if (!payload.items.length) {
    container.append(ui.emptyState(payload.empty_state.title, payload.empty_state.body,
      el('a', { class: 'button button--ghost', href: '#/discover', text: 'Back to discover' })));
    return container;
  }
  container.append(el('div', { class: 'stack' }, payload.items.map((report) => el('article', { class: 'card' },
    el('div', { class: 'between' },
      el('h3', { text: report.target_label || `${report.target_type} #${report.target_id}` }),
      badge(report.status, report.status),
    ),
    el('p', { class: 'muted', text: payload.statuses[report.status] || report.status }),
    el('dl', { class: 'kv' },
      el('dt', { text: 'Reason' }), el('dd', { text: report.reason_code.replace(/_/g, ' ') }),
      el('dt', { text: 'Filed' }), el('dd', { text: ui.formatDateTime(report.created_at) }),
      report.resolution_note ? el('dt', { text: 'Moderator note' }) : null,
      report.resolution_note ? el('dd', { text: report.resolution_note }) : null,
    ),
    report.details ? el('p', { class: 'subtle', text: `Your note: ${report.details}` }) : null,
  ))));
  return container;
}

// ---------------------------------------------------------------- checkout

export async function checkoutPage(params, query, context) {
  if (!requireSignedIn(context)) return el('div');
  const target = params.order || '';
  if (target.startsWith('tier-')) {
    return startCheckoutPage(Number(target.slice(5)), context);
  }
  return orderStatusPage(target, context);
}

async function startCheckoutPage(tierId, context) {
  const quote = await api(`/api/payments/quote?tier_id=${encodeURIComponent(tierId)}`);
  const container = el('div', { class: 'stack-lg' },
    el('header', {},
      el('h1', { text: 'Review your membership' }),
      el('p', { class: 'lede', text: `One on-chain crypto payment for 30 days of access to ${quote.creator.page_name}. Nothing renews automatically.` }),
    ),
  );

  container.append(el('section', { class: 'card' },
    el('h2', { text: quote.tier.name }),
    el('dl', { class: 'kv' },
      el('dt', { text: 'Creator' }), el('dd', {}, el('a', { href: `#/c/${quote.creator.handle}`, text: quote.creator.page_name })),
      el('dt', { text: 'Price' }), el('dd', { text: `${money(quote.amount_cents)} per 30 days` }),
      el('dt', { text: 'Platform fee (frozen at settlement)' }), el('dd', { text: `${money(quote.platform_fee_cents)} (${quote.fee_percent}%)` }),
      el('dt', { text: 'Creator share' }), el('dd', { text: money(quote.creator_net_cents) }),
      el('dt', { text: 'Payment method' }), el('dd', { text: 'On-chain crypto or Tether, paid through BTCPay — no cards, no Lightning' }),
    ),
    quote.existing_membership && quote.existing_membership.status === 'active'
      ? notice('info', 'You already have access', `Renewing adds 30 days from ${ui.formatDateTime(quote.existing_membership.ends_at)} instead of replacing the time you have.`)
      : null,
    el('p', { class: 'muted', text: quote.renewal_notice }),
  ));

  if (!quote.can_checkout) {
    quote.blockers.forEach((blocker) => container.append(notice('warning', 'Checkout is not available', blocker.message)));
    container.append(el('div', { class: 'button-row' },
      el('a', { class: 'button button--ghost', href: `#/c/${quote.creator.handle}`, text: 'Back to page' }),
      el('a', { class: 'button button--ghost', href: '#/memberships', text: 'My memberships' }),
    ));
    return container;
  }

  const choices = quote.payment_options.filter((option) => option.available);
  let chosen = choices.length === 1 ? choices[0].key : null;
  const picker = el('fieldset', { class: 'card stack' },
    el('legend', { text: 'Pay with' }),
    el('p', { class: 'muted', text: 'Choose a coin or token this creator accepts. The exact amount is quoted by BTCPay at checkout. Send only the asset and network shown.' }),
  );
  choices.forEach((option) => {
    const id = `pay-${option.key}`;
    const radio = el('input', { type: 'radio', name: 'pay-asset', id, value: option.key });
    if (chosen === option.key) radio.checked = true;
    radio.addEventListener('change', () => { chosen = option.key; });
    picker.append(el('div', { class: 'field field--choice' },
      radio,
      el('label', { for: id, text: option.label }),
      option.warning ? el('div', { class: 'hint', text: option.warning }) : null,
    ));
  });
  quote.payment_options.filter((option) => !option.available).forEach((option) => {
    picker.append(el('p', { class: 'subtle', text: `${option.label} is not available right now.` }));
  });
  container.append(picker);

  container.append(el('section', { class: 'card' },
    el('h2', { text: 'What happens next' }),
    el('ol', { class: 'timeline' }, quote.sequence.map((step, index) => el('li', {
      dataset: { state: index === 0 ? 'current' : '' },
    },
      el('span', { class: 'timeline__step', text: String(index + 1) }),
      el('div', {}, el('p', { class: 'muted', text: step })),
    ))),
    el('div', { class: 'button-row' },
      el('button', {
        class: 'button button--primary', type: 'button', text: 'Create pending order and open BTCPay',
        onclick: async (event) => {
          if (!chosen) {
            ui.toast('Choose a coin or token to pay with first.', 'error');
            return;
          }
          ui.setBusy(event.currentTarget, true, 'Creating order…');
          try {
            const intent = await api('/api/payments/intents', { method: 'POST', body: { tier_id: tierId, asset: chosen } });
            context.navigate(`/checkout/${intent.order_ref}`);
            if (intent.checkout_url) {
              window.open(intent.checkout_url, '_blank', 'noopener');
            }
          } catch (error) {
            ui.toast(error.message, 'error', 9000);
          } finally {
            ui.setBusy(event.currentTarget, false);
          }
        },
      }),
      el('a', { class: 'button button--ghost', href: `#/c/${quote.creator.handle}`, text: 'Cancel' }),
    ),
    el('p', { class: 'subtle', text: 'Velora stores a pending order first. Access is granted only after BTCPay reports the on-chain invoice settled and Velora verifies it independently.' }),
  ));
  return container;
}

async function orderStatusPage(orderRef, context) {
  const container = el('div', { class: 'stack-lg' });
  const statusBox = el('div', { role: 'status', 'aria-live': 'polite' });
  const detail = el('section', { class: 'card' });
  container.append(el('header', {}, el('h1', { text: 'Order status' })), statusBox, detail);

  let pollTimer = null;

  async function load({ silent = false } = {}) {
    try {
      const order = await api(`/api/payments/orders/${encodeURIComponent(orderRef)}`);
      clear(detail);
      detail.append(
        el('h2', { text: order.status_label }),
        el('dl', { class: 'kv' },
          el('dt', { text: 'Order reference' }), el('dd', { class: 'mono', text: order.order_ref }),
          el('dt', { text: 'Amount' }), el('dd', { text: `${money(order.amount_cents)} (${order.currency})` }),
          el('dt', { text: 'Paying with' }), el('dd', { text: ui.assetName(order) }),
          el('dt', { text: 'Amount quoted' }), el('dd', { text: ui.assetAmount(order) }),
          el('dt', { text: 'Created' }), el('dd', { text: ui.formatDateTime(order.created_at) }),
          el('dt', { text: 'Checkout expires' }), el('dd', { text: ui.formatDateTime(order.expires_at) }),
          order.settled_at ? el('dt', { text: 'Settled' }) : null,
          order.settled_at ? el('dd', { text: ui.formatDateTime(order.settled_at) }) : null,
        ),
        el('p', { class: 'muted', text: order.notice }),
      );
      if (order.status === 'settled') {
        clear(statusBox).append(notice('success', 'Payment verified — access granted',
          'Your 30-day membership is active. You can find it under Memberships.'));
      } else if (order.status === 'held') {
        clear(statusBox).append(notice('warning', 'Payment held for review',
          `${order.hold_reason ? `Reason: ${order.hold_reason.replace(/_/g, ' ')}. ` : ''}Velora does not unlock content automatically for a payment it cannot fully verify. A moderator will review it; nothing is deleted.`));
      } else if (order.status === 'pending' || order.status === 'processing') {
        clear(statusBox).append(notice('info', 'Waiting for a verified settlement',
          'If you have not paid yet, open the BTCPay checkout. Access unlocks only after the invoice settles and Velora re-checks it with BTCPay.'));
      } else {
        clear(statusBox).append(notice('warning', order.status_label, 'This order is not awaiting payment. You can start a new one from the creator page.'));
      }

      const actions = el('div', { class: 'button-row' },
        order.checkout_url ? el('a', {
          class: 'button button--primary', href: order.checkout_url, target: '_blank', rel: 'noopener noreferrer',
          text: 'Open crypto checkout',
        }) : null,
        el('button', {
          class: 'button', type: 'button', text: 'Check payment status now',
          onclick: async (event) => {
            ui.setBusy(event.currentTarget, true, 'Checking…');
            try {
              const refreshed = await api(`/api/payments/intents/${order.id}/refresh`, { method: 'POST' });
              ui.toast(refreshed.verification ? refreshed.verification.message : 'Status refreshed.', refreshed.status === 'settled' ? 'success' : 'info', 8000);
              await load({ silent: true });
            } catch (error) {
              ui.toast(error.message, 'error', 9000);
            } finally {
              ui.setBusy(event.currentTarget, false);
            }
          },
        }),
        el('a', { class: 'button button--ghost', href: '#/memberships', text: 'My memberships' }),
      );
      detail.append(actions);
      detail.append(el('p', { class: 'subtle', text: 'A browser redirect is not proof of payment: Velora only grants access after verifying the settled on-chain invoice with BTCPay Server itself.' }));

      if ((order.status === 'pending' || order.status === 'processing') && !pollTimer) {
        let attempts = 0;
        pollTimer = setInterval(async () => {
          attempts += 1;
          if (attempts > 20 || document.hidden) {
            clearInterval(pollTimer);
            pollTimer = null;
            return;
          }
          try {
            await api(`/api/payments/intents/${order.id}/refresh`, { method: 'POST' });
          } catch (error) {
            /* keep polling quietly */
          }
          await load({ silent: true });
        }, 15000);
      }
      if (order.status === 'settled' && pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    } catch (error) {
      if (!silent) {
        clear(detail).append(ui.errorState(error, () => load()));
      }
    }
  }

  await load();
  if (context && context.route) {
    // Stop polling when leaving the page.
    window.addEventListener('hashchange', () => {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }, { once: true });
  }
  return container;
}
