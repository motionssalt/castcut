/** ClipForge authenticated byte downloader, pinned a05846de7c76.
 * Reuses its streaming assembly and byte-save flow; verifies length/SHA256
 * before any save. No token in URLs, DOM, logs or downloaded content.
 */
import { loadPat } from './core.js';
export async function downloadReleaseAsset(credentials, asset, onProgress) {
  const endpoint = String(asset.url || asset.browserUrl);
  if (!/^https:\/\/api\.github\.com\/repos\/motionssalt\/castcut\/releases\/assets\/\d+$/.test(endpoint)) {
    throw new Error('Untrusted asset URL');
  }
  let response;
  try {
    response = await fetch(endpoint, {
      headers: {
        'Accept': 'application/octet-stream',
        'Authorization': `Bearer ${credentials.githubPat}`
      }
    });
  } catch (error) {
    // GitHub's signed release CDN can omit CORS headers. Same bytes, streamed
    // server-side through the PAT-gated site; no remux/conversion or token URL.
    if (!(error instanceof TypeError)) throw error;
    response = await fetch(`/api/release-assets/${Number(asset.id)}`, {
      headers: { 'x-castcut-pat': credentials.githubPat }
    });
  }
  if (!response.ok) throw new Error(`Release download failed (${response.status}); no file saved.`);
  const total = Number(response.headers.get('content-length') || asset.size || 0);
  if (!response.body) {
    const buf = new Uint8Array(await response.arrayBuffer());
    if (onProgress) onProgress(buf.length, total || buf.length);
    return buf;
  }
  const reader = response.body.getReader();
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (onProgress) onProgress(received, total);
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.length; }
  return out;
}


export async function verifiedDownload(asset, onProgress) {
  const id = Number(asset.id);
  if (!Number.isSafeInteger(id) || id < 1 || !String(asset.name).endsWith('.mp4')) {
    throw new Error('No valid MP4 release asset; refusing to save an HTML/error response.');
  }
  const url = `https://api.github.com/repos/motionssalt/castcut/releases/assets/${id}`;
  const bytes = await downloadReleaseAsset({ githubPat: loadPat() }, { ...asset, url }, onProgress);
  if (bytes.length !== Number(asset.size)) throw new Error('Download incomplete: byte count differs from release.');
  if (bytes.length < 12 || String.fromCharCode(...bytes.slice(4,8)) !== 'ftyp') {
    throw new Error('Download is not MP4; no file was saved.');
  }
  if (!/^sha256:[a-f0-9]{64}$/.test(asset.digest || '')) throw new Error('Missing release SHA256; refusing unverified save.');
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const digest = [...hash].map(b => b.toString(16).padStart(2, '0')).join('');
  if (`sha256:${digest}` !== asset.digest) throw new Error('Download SHA256 mismatch; no file was saved.');
  return bytes;
}

export async function saveMp4(bytes, suggestedName) {
  // Same ClipForge saveBytes flow; explicitly declare the MP4 MIME type.
  if (typeof window.showSaveFilePicker === 'function') {
    let handle;
    try { handle = await window.showSaveFilePicker({ suggestedName }); }
    catch (error) { if (error?.name === 'AbortError') return 'cancelled'; }
    if (handle) {
      const writable = await handle.createWritable();
      await writable.write(bytes);
      await writable.close();
      return 'picked';
    }
  }
  const blob = new Blob([bytes], { type: 'video/mp4' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = suggestedName;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return 'download';
}
