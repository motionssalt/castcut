import { readFile, getRelease, readJson, reconcileStatus, cancelActiveRuns, deleteReleaseAndTag, deleteJobFiles, putFile } from "../../../_lib/gh.js";

export async function onRequest({ request, params, data }) {
  const pat = data.pat; const id = params.id;

  if (request.method === "DELETE") {
    // Delete a job completely: cancel any live run, drop the release + tag,
    // and remove every committed file under jobs/<id>/ so nothing is left.
    const cancelled = await cancelActiveRuns(pat, id).catch(() => 0);
    await deleteReleaseAndTag(pat, `castcut-${id}`);
    const files = await deleteJobFiles(pat, id);
    return json({ ok: true, cancelled_runs: cancelled, deleted_files: files });
  }

  const [statusTxt, ingest, clips, filesJson, selection, sceneAgent, release] = await Promise.all([
    readFile(pat, `jobs/${id}/status.json`),
    readFile(pat, `jobs/${id}/ingest.json`),
    readFile(pat, `jobs/${id}/clips/manifest.json`),
    readFile(pat, `jobs/${id}/files.json`),
    readFile(pat, `jobs/${id}/selection.json`),
    readFile(pat, `jobs/${id}/scene/agent_prompt.txt`),
    getRelease(pat, `castcut-${id}`),
  ]);
  let status = statusTxt ? safeJson(statusTxt) : null;
  if (status) status = await reconcileStatus(pat, id, status);
  return json({
    job_id: id,
    status,
    ingest: ingest ? safeJson(ingest) : null,
    clipsManifest: clips ? safeJson(clips) : null,
    files: filesJson ? safeJson(filesJson) : null,
    selection: selection ? safeJson(selection) : null,
    sceneAgent: sceneAgent || null,
    release: release ? { assets: (release.assets || []).map(a => ({ name: a.name, size: a.size, browser_download_url: a.browser_download_url })) } : null,
  });
}
function safeJson(s) { try { return JSON.parse(s); } catch { return null; } }
function json(obj, status = 200) { return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } }); }
