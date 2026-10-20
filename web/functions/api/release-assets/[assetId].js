// Byte-transparent native download: no browser Blob, no Worker buffering.
// Authorize POST with PAT, then GET/HEAD with an encrypted asset-bound cookie.
import { REPO } from '../../_lib/gh.js';
import { issueTicket, ticketCookie } from '../../_lib/download-ticket.js';
const noStore = { 'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer' };

export async function onRequest({ request, params, data, env }) {
  if (!['GET', 'HEAD', 'POST'].includes(request.method)) return new Response('Method not allowed', { status: 405 });
  if (!/^\d+$/.test(String(params.assetId))) return new Response('Invalid asset ID', { status: 400 });
  if (!data.pat) return new Response('Unauthorized', { status: 401, headers: noStore });
  if (request.method === 'POST') {
    const origin = request.headers.get('Origin');
    if (origin && origin !== new URL(request.url).origin) return new Response('Invalid origin', { status: 403 });
  }
  const endpoint = `https://api.github.com/repos/${REPO}/releases/assets/${params.assetId}`;
  const headers = { Authorization: `Bearer ${data.pat}`, 'User-Agent': 'castcut-pages', Accept: 'application/vnd.github+json' };
  const metadata = await fetch(endpoint, { headers });
  if (!metadata.ok) return new Response('Release asset unavailable', { status: metadata.status, headers: noStore });
  const asset = await metadata.json();
  if (asset.state !== 'uploaded' || !asset.name?.endsWith('.mp4') || !/^sha256:[a-f0-9]{64}$/.test(asset.digest || '') || !(asset.size > 12)) {
    return new Response('No verified MP4 asset', { status: 409, headers: noStore });
  }
  if (request.method === 'POST') {
    try {
      const ticket = await issueTicket(env, data.pat, params.assetId);
      return Response.json({ url: `/api/release-assets/${params.assetId}?download=1`, name: `CastCut-${params.assetId}.mp4`, size: asset.size }, {
        headers: { ...noStore, 'Set-Cookie': ticketCookie(ticket, params.assetId) }
      });
    } catch { return new Response('Download service unavailable', { status: 503, headers: noStore }); }
  }
  const range = request.headers.get('Range');
  if (range && !/^bytes=\d*-\d*$/.test(range)) return new Response('Invalid byte range', { status: 416, headers: { ...noStore, 'Content-Range': `bytes */${asset.size}` } });
  const rangeHeaders = range ? { Range: range } : {};
  let response = await fetch(endpoint, { headers: { ...headers, ...rangeHeaders, Accept: 'application/octet-stream' }, redirect: 'manual' });
  for (let i = 0; response.status >= 300 && response.status < 400 && i < 3; i++) {
    const location = response.headers.get('location');
    if (!location) return new Response('Missing asset redirect', { status: 502 });
    const next = new URL(location);
    if (next.protocol !== 'https:' || next.hostname !== 'release-assets.githubusercontent.com') return new Response('Unexpected asset redirect', { status: 502 });
    // Never forward Authorization to GitHub's signed CDN.
    response = await fetch(next.toString(), { headers: rangeHeaders, redirect: 'manual' });
  }
  if (response.status === 416) return new Response(null, { status: 416, headers: { ...noStore, 'Content-Range': `bytes */${asset.size}` } });
  if (!response.ok || !response.body || ![200, 206].includes(response.status)) return new Response('Asset download failed', { status: 502, headers: noStore });
  if (/json|text\/html/.test(response.headers.get('content-type') || '')) return new Response('Unexpected asset body', { status: 502 });
  const length = response.headers.get('content-length');
  const partial = response.status === 206;
  const contentRange = response.headers.get('content-range');
  let expected = Number(asset.size);
  if (partial) {
    const m = contentRange?.match(/^bytes (\d+)-(\d+)\/(\d+)$/);
    if (!m || Number(m[3]) !== expected || Number(m[2]) < Number(m[1]) || Number(m[2]) >= expected) return new Response('Invalid partial asset', { status: 502 });
    expected = Number(m[2]) - Number(m[1]) + 1;
  }
  if (length && Number(length) !== expected) return new Response('Asset size mismatch', { status: 502 });
  const outputHeaders = {
    ...noStore, 'Content-Type': 'video/mp4', 'Content-Length': String(expected),
    'Content-Disposition': `attachment; filename="CastCut-${params.assetId}.mp4"`,
    'Accept-Ranges': 'bytes', 'X-Content-Type-Options': 'nosniff',
    'X-Asset-SHA256': asset.digest.slice(7), 'ETag': `"${asset.digest}"`,
  };
  if (partial) outputHeaders['Content-Range'] = contentRange;
  if (request.method === 'HEAD') await response.body.cancel();
  return new Response(request.method === 'HEAD' ? null : response.body, { status: response.status, headers: outputHeaders });
}
