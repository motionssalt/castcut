// Short-lived, asset-bound encrypted cookie for native browser downloads.
// The PAT never enters a URL, a response body, or a JS-readable cookie.
const encoder = new TextEncoder();
export const COOKIE = '__Secure-castcut-download';
export const TTL = 3600;

function b64(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function unb64(text) {
  return Uint8Array.from(atob(text.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
}
async function key(env) {
  if (!/^[a-f0-9]{64}$/.test(env?.DOWNLOAD_TOKEN_KEY || '')) throw new Error('Download service is not configured');
  const bytes = Uint8Array.from(env.DOWNLOAD_TOKEN_KEY.match(/../g), h => parseInt(h, 16));
  return crypto.subtle.importKey('raw', bytes, 'AES-GCM', false, ['encrypt', 'decrypt']);
}
export async function issueTicket(env, pat, assetId, now = Date.now()) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const payload = encoder.encode(JSON.stringify({ pat, assetId: String(assetId), exp: now + TTL * 1000 }));
  const encrypted = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: encoder.encode('castcut-download-v1') }, await key(env), payload);
  return b64(iv) + '.' + b64(new Uint8Array(encrypted));
}
export async function readTicket(env, request, assetId, now = Date.now()) {
  try {
    const raw = (request.headers.get('cookie') || '').split(';').map(s => s.trim()).find(s => s.startsWith(COOKIE + '='))?.slice(COOKIE.length + 1);
    if (!raw || raw.length > 4096) return null;
    const [iv, encrypted, extra] = raw.split('.');
    if (extra || !iv || !encrypted) return null;
    const bytes = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: unb64(iv), additionalData: encoder.encode('castcut-download-v1') }, await key(env), unb64(encrypted));
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    if (payload.assetId !== String(assetId) || payload.exp <= now || payload.exp > now + TTL * 1000 || typeof payload.pat !== 'string') return null;
    return payload.pat;
  } catch { return null; }
}
export function ticketCookie(ticket, assetId) {
  return `${COOKIE}=${ticket}; Path=/api/release-assets/${assetId}; Max-Age=${TTL}; HttpOnly; Secure; SameSite=Strict`;
}
