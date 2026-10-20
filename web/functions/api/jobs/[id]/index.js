import { checkAnalysisAssets } from "../../../_lib/project.js";
import { readFile, readJson, getProjectReleases, reconcileStatus, cancelActiveRuns, deleteProjectReleasesAndTags, deleteJobFiles, listJobRuns, gh, REPO } from "../../../_lib/gh.js";

export async function onRequest({ request, params, data }) {
  const pat = data.pat; const id = params.id;
  if (request.method === "DELETE") {
    // Delete a project completely: cancel any live run, drop all releases + tags,
    // and remove every committed file under jobs/<id>/ so nothing is left.
    const cancelled = await cancelActiveRuns(pat, id).catch(() => 0);
    await deleteProjectReleasesAndTags(pat, id).catch(() => {});
    const files = await deleteJobFiles(pat, id);
    return json({ ok: true, cancelled_runs: cancelled, deleted_files: files });
  }

  const [statusTxt, ingest, clips, filesJson, selection, sceneAgent, relData, exportItems, runs, audioChoiceTxt, project, analysisManifest] = await Promise.all([
    readFile(pat, `jobs/${id}/status.json`),
    readFile(pat, `jobs/${id}/ingest.json`),
    readFile(pat, `jobs/${id}/clips/manifest.json`),
    readFile(pat, `jobs/${id}/files.json`),
    readFile(pat, `jobs/${id}/selection.json`),
    readFile(pat, `jobs/${id}/scene/agent_prompt.txt`),
    getProjectReleases(pat, id).catch(() => ({ releases: [], assets: [] })),
    listProjectExports(pat, id),
    listJobRuns(pat, id).catch(() => []),
    readFile(pat, `jobs/${id}/audio_choice.json`).catch(() => null),
    readJson(pat, `jobs/${id}/ingest-request.json`),
    readJson(pat, `jobs/${id}/analysis/manifest.json`),
  ]);

  let status = statusTxt ? safeJson(statusTxt) : null;
  if (status) status = await reconcileStatus(pat, id, status);
  const assets = relData.assets || [];
  status = status || {state:'incomplete',message:'Invalid status record; repair analysis required.'};
  status.content_type = project?.content_type || null;
  const missing = checkAnalysisAssets(analysisManifest, assets);
  if (missing.length && (status.release_verified || ['ready','hybrid_ready'].includes(status.state))) {
    status.state='incomplete'; status.release_verified=false;
    status.missing_assets=missing; status.message='Analysis incomplete; missing assets: '+missing.join(', ');
  }
  const exportsList = Array.isArray(exportItems) ? exportItems : [];
  const audioChoice = audioChoiceTxt ? safeJson(audioChoiceTxt) : null;

  return json({
    job_id: id,
    project_id: id,
    status,
    ingest: ingest ? safeJson(ingest) : null,
    clipsManifest: clips ? safeJson(clips) : null,
    files: filesJson ? safeJson(filesJson) : null,
    selection: selection ? safeJson(selection) : null,
    sceneAgent: sceneAgent || null,
    audioChoice: audioChoice,
    exports: exportsList,
    releases: relData.releases || [],
    release: { assets },
    assets,
    runs: runs || [],
  });
}

async function listProjectExports(pat, jobId) {
  try {
    const items = await gh(pat, `/repos/${REPO}/contents/jobs/${jobId}/exports`);
    if (!Array.isArray(items)) return [];
    const jsonFiles = items.filter(x => x.type === "file" && x.name.endsWith(".json"));
    const records = await Promise.all(jsonFiles.map(async f => {
      const doc = await readJson(pat, f.path);
      return doc || null;
    }));
    return records.filter(Boolean).sort((a, b) => (b.created_at_epoch || 0) - (a.created_at_epoch || 0));
  } catch (e) {
    return [];
  }
}

function safeJson(s) { try { return JSON.parse(s); } catch { return null; } }
function json(obj, status = 200) { return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } }); }
