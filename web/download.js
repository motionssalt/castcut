/** Start a real HTTP attachment download; never fetch video bytes into JS.
 * Only a small authorization response is read by the site. The browser/OS
 * owns transfer progress, disk writes, cancellation and byte-range resume.
 */
import { loadPat } from './core.js';
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
