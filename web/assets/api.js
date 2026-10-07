/**
 * API client.
 *
 * All reads and writes go to Velora's own origin (the server serves this app),
 * so no host names or credentials exist in this file. State-changing calls
 * include the anti-forgery token that was issued with the session cookie.
 */

export const appState = {
  session: { authenticated: false },
  features: null,
  emailStatus: null,
  checkout: { available: false },
  categories: [],
  orientationOptions: null,
  reportReasons: [],
  creatorPage: null,
  application: null,
};

export class ApiFailure extends Error {
  constructor(status, payload) {
    const error = (payload && payload.error) || {};
    super(error.message || 'Something went wrong.');
    this.status = status;
    this.code = error.code || 'error';
    this.field = error.field || null;
    this.details = error.details || null;
  }

  get isAuth() {
    return this.status === 401;
  }

  get needsVerification() {
    return this.code === 'email_verification_required';
  }
}

function csrfToken() {
  return appState.session && appState.session.csrf_token ? appState.session.csrf_token : null;
}

async function raw(path, { method = 'GET', body, signal, headers = {} } = {}) {
  const init = {
    method,
    credentials: 'same-origin',
    headers: { Accept: 'application/json', ...headers },
    signal,
  };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const token = csrfToken();
  if (token && method !== 'GET') {
    init.headers['X-Velora-CSRF'] = token;
  }
  const response = await fetch(path, init);
  const contentType = response.headers.get('content-type') || '';
  let payload = null;
  if (contentType.includes('application/json')) {
    payload = await response.json().catch(() => null);
  }
  if (!response.ok) {
    throw new ApiFailure(response.status, payload);
  }
  return payload;
}

/** Call the API, retrying once if the session's CSRF token went stale. */
export async function api(path, options = {}) {
  try {
    return await raw(path, options);
  } catch (error) {
    if (error instanceof ApiFailure && (error.code === 'csrf_invalid' || error.code === 'csrf_required')) {
      await loadSession().catch(() => {});
      return raw(path, options);
    }
    throw error;
  }
}

export async function loadBootstrap() {
  const payload = await api('/api/bootstrap');
  appState.features = payload.features;
  appState.emailStatus = payload.email_status;
  appState.checkout = payload.checkout;
  appState.categories = payload.categories || [];
  appState.orientationOptions = payload.orientation_options || null;
  appState.reportReasons = payload.report_reasons || [];
  appState.session = payload.session || { authenticated: false };
  appState.creatorPage = payload.creator_page;
  appState.application = payload.application;
  return payload;
}

export async function loadSession() {
  const payload = await api('/api/auth/session');
  appState.session = payload.authenticated ? payload : { authenticated: false };
  return appState.session;
}

export function isSignedIn() {
  return Boolean(appState.session && appState.session.authenticated);
}

export function currentUser() {
  return appState.session && appState.session.user ? appState.session.user : null;
}

export function canAct() {
  return Boolean(appState.session && appState.session.can_act);
}

export function isAdmin() {
  const capabilities = appState.session && appState.session.capabilities;
  return Boolean(capabilities && capabilities.admin);
}

export function isCreator() {
  const capabilities = appState.session && appState.session.capabilities;
  return Boolean(capabilities && capabilities.creator);
}

export function uploadMedia(postId, file, onProgress) {
  // Uploads send the raw image bytes; the server sniffs the real content type and
  // ignores anything a caller claims. Filenames are metadata only.
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open('POST', `/api/studio/posts/${postId}/media?filename=${encodeURIComponent(file.name || 'image')}`);
    request.withCredentials = true;
    request.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    const token = csrfToken();
    if (token) {
      request.setRequestHeader('X-Velora-CSRF', token);
    }
    request.upload.addEventListener('progress', (event) => {
      if (onProgress && event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    });
    request.addEventListener('load', () => {
      let payload = null;
      try {
        payload = JSON.parse(request.responseText);
      } catch (error) {
        payload = null;
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload);
      } else {
        reject(new ApiFailure(request.status, payload));
      }
    });
    request.addEventListener('error', () => reject(new ApiFailure(0, null)));
    request.send(file);
  });
}

export function describeFailure(error) {
  if (error instanceof ApiFailure) {
    if (error.code === 'checkout_unavailable') {
      return error.message;
    }
    return error.message;
  }
  return 'Velora could not reach the server. Check your connection and try again.';
}
