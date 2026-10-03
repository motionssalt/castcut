import { readFile, getRelease } from "../../../_lib/gh.js";

export async function onRequest({ params, data }) {
  const pat = data.pat; const id = params.id;
  const [status, ingest, clips, sceneAgent, release] = await Promise.all([
    readFile(pat, `jobs/${id}/status.json`),
    readFile(pat, `jobs/${id}/ingest.json`),
    readFile(pat, `jobs/${id}/clips/manifest.json`),
    readFile(pat, `jobs/${id}/scene/agent_prompt.txt`),
    getRelease(pat, `castcut-${id}`),
  ]);
  return json({
    job_id: id,
    status: status ? safeJson(status) : null,
    ingest: ingest ? safeJson(ingest) : null,
    clipsManifest: clips ? safeJson(clips) : null,
    sceneAgent: sceneAgent || null,
    release: release ? { assets: (release.assets || []).map(a => ({ name: a.name, size: a.size, browser_download_url: a.browser_download_url })) } : null,
  });
}
function safeJson(s) { try { return JSON.parse(s); } catch { return null; } }
function json(obj, status = 200) { return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } }); }
