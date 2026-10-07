/**
 * DOM helpers.
 *
 * Everything is built with DOM APIs and `textContent`. There is no `innerHTML`
 * anywhere in this frontend, so user-authored text can never become markup.
 */

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') {
      node.className = value;
    } else if (key === 'dataset') {
      Object.assign(node.dataset, value);
    } else if (key === 'for') {
      node.htmlFor = value;
    } else if (key === 'text') {
      node.textContent = value;
    } else if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'value') {
      node.value = value;
    } else if (value === true) {
      node.setAttribute(key, '');
    } else {
      node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

export function append(parent, children) {
  for (const child of children.flat(4)) {
    if (child === null || child === undefined || child === false || child === true) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function frag(...children) {
  const fragment = document.createDocumentFragment();
  append(fragment, children);
  return fragment;
}

// ---------------------------------------------------------------- formatting

export function money(cents) {
  if (cents === null || cents === undefined) return '—';
  const value = Number(cents) / 100;
  return value.toLocaleString(undefined, { style: 'currency', currency: 'USD' });
}

export function sats(value) {
  if (!value) return '—';
  return `${Number(value).toLocaleString()} sats`;
}

export function formatDate(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export function formatDateTime(iso) {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

export function relative(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const diff = Date.now() - date.getTime();
  const minutes = Math.round(diff / 60000);
  if (Math.abs(minutes) < 1) return 'just now';
  if (Math.abs(minutes) < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (Math.abs(days) < 30) return `${days} d ago`;
  return formatDate(iso);
}

export function daysUntil(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return Math.ceil((date.getTime() - Date.now()) / 86400000);
}

export function truncate(value, length = 140) {
  const text = String(value || '');
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

// ---------------------------------------------------------------- blocks

export function notice(kind, title, body, extra) {
  const icons = { info: 'ℹ', success: '✓', warning: '!', danger: '⚠' };
  return el('div', { class: `notice notice--${kind}`, role: kind === 'danger' ? 'alert' : 'status' },
    el('span', { class: 'notice__icon', 'aria-hidden': 'true', text: icons[kind] || 'ℹ' }),
    el('div', {},
      title ? el('strong', { text: title }) : null,
      body ? (typeof body === 'string' ? el('p', { text: body }) : body) : null,
      extra || null,
    ),
  );
}

export function badge(text, variant = '') {
  return el('span', { class: `badge ${variant ? `badge--${variant}` : ''}`, text });
}

export function chip(text, variant = '') {
  return el('span', { class: `chip ${variant ? `chip--${variant}` : ''}`, text });
}

export function statBlock(value, label) {
  return el('div', { class: 'stat' },
    el('div', { class: 'stat__value', text: value }),
    el('div', { class: 'stat__label', text: label }),
  );
}

export function skeleton(lines = 3) {
  const block = el('div', { class: 'skeleton', 'aria-hidden': 'true' },
    el('div', { class: 'skeleton__line skeleton__line--title' }),
  );
  for (let index = 0; index < lines; index += 1) {
    block.append(el('div', {
      class: `skeleton__line ${index % 3 === 2 ? 'skeleton__line--short' : ''}`,
    }));
  }
  return block;
}

export function loadingBlock(label = 'Loading…') {
  return el('div', { class: 'card card--quiet', role: 'status', 'aria-live': 'polite' },
    el('div', { class: 'cluster' },
      el('span', { class: 'spinner', 'aria-hidden': 'true' }),
      el('span', { text: label }),
    ),
    el('div', { class: 'skeleton', 'aria-hidden': 'true' },
      el('div', { class: 'skeleton__line skeleton__line--title' }),
      el('div', { class: 'skeleton__line' }),
      el('div', { class: 'skeleton__line skeleton__line--short' }),
    ),
  );
}

export function emptyState(title, body, action) {
  return el('div', { class: 'empty-state' },
    el('h3', { text: title }),
    body ? el('p', { text: body }) : null,
    action || null,
  );
}

export function errorState(error, onRetry) {
  const message = error && error.message ? error.message : 'Something went wrong.';
  return el('div', { class: 'card' },
    notice('danger', 'That did not work', message),
    onRetry ? el('div', { class: 'button-row' },
      el('button', { class: 'button', type: 'button', onclick: onRetry, text: 'Try again' }),
    ) : null,
  );
}

export function linkButton(label, href, variant = 'button') {
  return el('a', { class: variant, href, text: label });
}

/** A labelled form field with an error slot wired through aria-describedby. */
export function field({ id, label, type = 'text', value = '', hint, error, required = false,
  autocomplete, placeholder, min, max, rows, name }) {
  const controlId = id || `field-${Math.random().toString(36).slice(2, 9)}`;
  const hintId = hint ? `${controlId}-hint` : null;
  const errorId = error ? `${controlId}-error` : null;
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || null;

  const attributes = {
    id: controlId,
    name: name || controlId,
    required: required || null,
    autocomplete: autocomplete || null,
    placeholder: placeholder || null,
    'aria-describedby': describedBy,
    'aria-invalid': error ? 'true' : null,
  };
  let control;
  if (type === 'textarea') {
    control = el('textarea', { ...attributes, rows: rows || 6 }, value);
  } else if (type === 'select') {
    control = el('select', attributes);
  } else {
    control = el('input', { ...attributes, type, value, min: min ?? null, max: max ?? null });
  }

  const wrapper = el('div', { class: 'field' },
    el('label', { for: controlId, text: label }),
    hint ? el('div', { class: 'hint', id: hintId, text: hint }) : null,
    control,
    errorId ? el('div', { class: 'error', id: errorId, role: 'alert', text: error }) : null,
  );
  return { wrapper, control, id: controlId };
}

export function submitButton(label, { variant = 'button button--primary' } = {}) {
  return el('button', { class: variant, type: 'submit', text: label });
}

// ---------------------------------------------------------------- toasts

function toastRegion() {
  let region = document.getElementById('toast-region');
  if (!region) {
    region = el('div', { id: 'toast-region', role: 'status', 'aria-live': 'polite' });
    document.body.append(region);
  }
  return region;
}

export function toast(message, kind = 'info', timeout = 6000) {
  const node = el('div', { class: `toast toast--${kind}` }, el('p', { text: message }));
  toastRegion().append(node);
  const remove = () => node.remove();
  node.addEventListener('click', remove);
  if (timeout) setTimeout(remove, timeout);
  return node;
}

// ---------------------------------------------------------------- dialogs

export function confirmDialog({ title, body, confirmLabel = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const dialog = el('dialog', { 'aria-labelledby': 'dialog-title' },
      el('h3', { id: 'dialog-title', text: title }),
      typeof body === 'string' ? el('p', { class: 'muted', text: body }) : body,
      el('div', { class: 'button-row' },
        el('button', {
          class: 'button', type: 'button',
          onclick: () => {
            dialog.close();
            resolve(false);
          },
          text: 'Cancel',
        }),
        el('button', {
          class: `button ${danger ? 'button--berry' : 'button--primary'}`,
          type: 'button',
          onclick: () => {
            dialog.close();
            resolve(true);
          },
          text: confirmLabel,
        }),
      ),
    );
    document.body.append(dialog);
    dialog.addEventListener('close', () => dialog.remove());
    dialog.showModal();
  });
}

// ---------------------------------------------------------------- a11y

export function announce(message) {
  let live = document.getElementById('route-announcer');
  if (!live) {
    live = el('div', { id: 'route-announcer', class: 'visually-hidden', 'aria-live': 'polite' });
    document.body.append(live);
  }
  live.textContent = message;
}

export function focusHeading(root) {
  const heading = root.querySelector('h1');
  if (heading) {
    heading.setAttribute('tabindex', '-1');
    heading.focus({ preventScroll: true });
  }
}

export function setBusy(button, busy, busyLabel = 'Working…') {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
  } else {
    if (button.dataset.label) button.textContent = button.dataset.label;
    button.disabled = false;
    button.removeAttribute('aria-busy');
  }
}
