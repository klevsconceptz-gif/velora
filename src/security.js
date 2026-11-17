const encoder = new TextEncoder();
const ADMIN_PASSWORD_SHA256 = 'c5f582a9354586b9576130648ae6bcaf1dfb8c98827432a4bc99307172a1a044';

function bytesToHex(bytes) { return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join(''); }
function randomSalt() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return bytesToHex(bytes);
}
async function digest(text) {
  const result = await crypto.subtle.digest('SHA-256', encoder.encode(text));
  return bytesToHex(new Uint8Array(result));
}
async function derive(password, salt) {
  const key = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits({ name: 'PBKDF2', salt: Uint8Array.from(salt.match(/.{2}/g).map((chunk) => parseInt(chunk, 16))), iterations: 120000, hash: 'SHA-256' }, key, 256);
  return bytesToHex(new Uint8Array(bits));
}
export async function makePasswordHash(password) {
  const salt = randomSalt();
  return { algorithm: 'PBKDF2-SHA256', iterations: 120000, salt, hash: await derive(password, salt) };
}
export async function verifyPassword(password, stored) {
  try {
    if (typeof stored === 'string') return (await digest(password)) === stored;
    if (!stored?.salt || !stored?.hash) return false;
    return (await derive(password, stored.salt)) === stored.hash;
  } catch { return false; }
}
export async function isAdminPassword(password) { return (await digest(password)) === ADMIN_PASSWORD_SHA256; }
export function isStrongEnough(password) { return String(password || '').length >= 8; }
