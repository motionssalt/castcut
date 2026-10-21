/** Start a real HTTP attachment download; never fetch video bytes into JS.
 * Only a small authorization response is read by the site. The browser/OS
 * owns transfer progress, disk writes, cancellation and byte-range resume.
 */
import { loadPat } from './core.js';
// Old exports must not stay downloadable just because their record says done.
// Bind the actual asset to a successful decode AND post-upload SHA256 receipt.
export const REQUIRED_REBUILD = 'decoded-frames-pcm-v1';
export function verifiedExportAsset(exp, assets) {
  // A previous successful decode is not evidence of the new frame/PCM rebuild.
  // Keep obsolete downloads distinct so a gallery cannot reopen the old file.
  if (exp?.checks?.rebuild_version !== REQUIRED_REBUILD || !exp.checks.exact_frame_cadence) return null;
  if (exp?.status !== 'done' || !exp.ok || !exp.checks?.ok || !exp.checks.full_decode_passed || !exp.delivery?.runner_release_download_identical) return null;
  if (!/^[a-f0-9]{64}$/.test(exp.sha256 || '') || exp.sha256 !== exp.checks.sha256 || exp.sha256 !== exp.delivery.sha256) return null;
  const name = exp.asset_name || `castcut-export-${exp.export_id || exp.id}.mp4`;
  return assets.find(a => a.name === name && a.state === 'uploaded' && a.name.endsWith('.mp4') &&
    Number(a.id) === Number(exp.delivery.asset_id) && Number(a.size) === Number(exp.checks.size_bytes) &&
    a.digest === `sha256:${exp.sha256}`) || null;
}

export async function startNativeDownload(asset) {
  const id = Number(asset?.id);
  if (!Number.isSafeInteger(id) || id < 1 || !String(asset?.name).endsWith('.mp4') || asset.state !== 'uploaded') {
    throw new Error('No completed MP4 release asset available.');
  }
  const endpoint = `/api/release-assets/${id}`;
  const response = await fetch(endpoint, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'x-castcut-pat': loadPat() },
  });
  if (!response.ok) throw new Error(`Cannot start download (${response.status}). Refresh and try again.`);
  const handoff = await response.json();
  // Never navigate to a supplied third-party URL, or put a PAT in the URL.
  if (handoff.url !== `${endpoint}?download=1`) throw new Error('Invalid download handoff');
  window.location.assign(handoff.url);
  return handoff;
}
