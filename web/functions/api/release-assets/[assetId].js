// Byte-transparent same-origin bridge for GitHub asset redirects whose CDN
// omits CORS headers. _middleware verifies the owner's PAT on every request.
// Never buffer media in a Worker, log credentials, or expose signed URLs.
import { REPO } from '../../_lib/gh.js';

export async function onRequest({ request, params, data }) {
  if (request.method !== 'GET') return new Response('Method not allowed', { status: 405 });
  if (!/^\d+$/.test(String(params.assetId))) return new Response('Invalid asset ID', { status: 400 });
  const endpoint = `https://api.github.com/repos/${REPO}/releases/assets/${params.assetId}`;
  const headers = { Authorization: `Bearer ${data.pat}`, 'User-Agent': 'castcut-pages', Accept: 'application/vnd.github+json' };
  const metadata = await fetch(endpoint, { headers });
  if (!metadata.ok) return new Response('Release asset unavailable', { status: metadata.status });
  const asset = await metadata.json();
  if (asset.state !== 'uploaded' || !asset.name?.endsWith('.mp4') || !asset.digest?.startsWith('sha256:')) {
    return new Response('No verified MP4 asset', { status: 409 });
  }
  let response = await fetch(endpoint, { headers: { ...headers, Accept: 'application/octet-stream' }, redirect: 'manual' });
  for (let i = 0; response.status >= 300 && response.status < 400 && i < 3; i++) {
    const next = new URL(response.headers.get('location'));
    if (next.protocol !== 'https:' || next.hostname !== 'release-assets.githubusercontent.com') {
      return new Response('Unexpected asset redirect', { status: 502 });
    }
    // Crucially do NOT forward Authorization to the signed-URL host.
    response = await fetch(next.toString(), { redirect: 'manual' });
  }
  if (!response.ok || !response.body) return new Response('Asset download failed', { status: 502 });
  if (/json|text\/html/.test(response.headers.get('content-type') || '')) {
    return new Response('Unexpected asset body', { status: 502 });
  }
  const length = response.headers.get('content-length');
  if (length && Number(length) !== Number(asset.size)) return new Response('Asset size mismatch', { status: 502 });
  const name = asset.name.replace(/[^a-zA-Z0-9._-]/g, '_');
  return new Response(response.body, { headers: {
    'Content-Type': 'video/mp4',
    'Content-Length': String(asset.size),
    'Content-Disposition': `attachment; filename="${name}"`,
    'Cache-Control': 'private, no-store',
    'X-Content-Type-Options': 'nosniff',
    'X-Asset-SHA256': asset.digest.slice(7),
  } });
}
