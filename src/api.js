const API_BASE = '/api';
export async function apiRequest(path, { method = 'GET', body = null } = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    credentials: 'same-origin',
    headers: body === null ? { 'Accept': 'application/json' } : { 'Accept': 'application/json', 'Content-Type': 'application/json' },
    body: body === null ? undefined : JSON.stringify(body),
  });
  let payload = {};
  try { payload = await response.json(); } catch { payload = {}; }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}
export async function apiHealth() {
  try { const payload = await apiRequest('/health'); return Boolean(payload.ready); } catch { return false; }
}
