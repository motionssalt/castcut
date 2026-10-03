import { readJson, putFile, dispatchWorkflow } from "../../../_lib/gh.js";

// POST /api/jobs/<id>/retry — re-dispatch the step that failed/was cancelled.
// Inference order uses the durable artifacts each step leaves behind, so a
// retry resumes from the failed step instead of starting over.
export async function onRequest({ request, params, data }) {
  if (request.method !== "POST") return j({ error: "method not allowed" }, 405);
  const pat = data.pat; const id = params.id;
  const status = await readJson(pat, `jobs/${id}/status.json`);
  if (!status) return j({ error: "job not found" }, 404);
  const s = String(status.state || "");
  if (!/failed|cancelled/.test(s)) return j({ error: `job is ${s}; retry only applies to failed/cancelled jobs` }, 400);

  const from = String(status.failed_from || status.cancelled_from || s);
  const req = await readJson(pat, `jobs/${id}/ingest-request.json`);
  const kind = req?.source?.kind || "";
  const isTorrent = kind === "magnet" || kind === "torrent_file";
  const [filesJson, selection, production, sceneReq, clipsSel] = await Promise.all([
    readJson(pat, `jobs/${id}/files.json`),
    readJson(pat, `jobs/${id}/selection.json`),
    readJson(pat, `jobs/${id}/scene/production.json`),
    readJson(pat, `jobs/${id}/scene/scene-request.json`),
    readJson(pat, `jobs/${id}/clips/selection.json`),
  ]);

  let workflow = null;
  let inputs = { job_id: id };
  if (/select/.test(from)) workflow = "select.yml";
  else if (/clips_export/.test(from) || (clipsSel && /clips/.test(from))) workflow = "clips-export.yml";
  else if (/scene_export/.test(from) || (production && /scene/.test(from))) workflow = "scene-export.yml";
  else if (/scene/.test(from) || sceneReq) workflow = production ? "scene-export.yml" : "scene-analyze.yml";
  else if (/faces/.test(from)) workflow = "faces.yml";
  else if (/ingest|created|queued/.test(from)) {
    if (isTorrent && !filesJson) workflow = "select.yml";
    else if (isTorrent && !selection) return j({ error: "no file selection yet — pick the files first" }, 400);
    else workflow = "ingest.yml";
  }
  if (!workflow) {
    // cancelled with no usable history: fall back to artifact inference
    if (production) workflow = "scene-export.yml";
    else if (clipsSel) workflow = "clips-export.yml";
    else if (sceneReq) workflow = "scene-analyze.yml";
    else if (isTorrent && !selection) return j({ error: "pick the torrent files first (no selection saved)" }, 400);
    else workflow = "ingest.yml";
  }

  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const next = {
    ...status,
    state: "queued",
    message: `Retrying — ${workflow.replace(".yml", "")} dispatched.`,
    updated_at: now,
    updated_at_epoch: Math.floor(Date.now() / 1000),
  };
  delete next.failed_step; delete next.run_url;
  await putFile(pat, `jobs/${id}/status.json`, JSON.stringify(next, null, 2), `castcut: retry ${id} (${workflow})`);
  await dispatchWorkflow(pat, workflow, inputs);
  return j({ ok: true, workflow });
}
function j(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } }); }
