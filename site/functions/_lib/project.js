import { readJson } from './gh.js';

export async function projectType(pat, id, proposed) {
  const record = await readJson(pat, `jobs/${id}/ingest-request.json`);
  const value = record?.content_type;
  if (!['irl','anime'].includes(value)) throw new Error(`Project ${id}: missing immutable content_type in creation record; never default to anime.`);
  if (proposed && proposed !== value) throw new Error(`Project ${id}: content_type mismatch: requested ${proposed}, stored ${value}.`);
  return value;
}

export function checkAnalysisAssets(manifest, assets) {
  if (!manifest?.assets?.length) return ['manifest.json (verified analysis manifest unavailable)'];
  return manifest.assets.filter(ref => !assets.some(a => a.name === ref.name && a.release_tag === ref.release_tag && (ref.size == null || ref.size === a.size))).map(ref => `${ref.release_tag}/${ref.name}`);
}
