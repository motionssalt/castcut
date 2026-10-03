import { readJson, putFile, dispatchWorkflow } from "../../../_lib/gh.js";

// PUT    /api/jobs/<id>/selection  — confirm picked torrent files, dispatch ingest
// DELETE /api/jobs/<id>/selection  — clear the pick (back to awaiting_selection)
export async function onRequest({ request, params, data }) {
  const pat = data.pat; const id = params.id;
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const status = await readJson(pat, `jobs/${id}/status.json`);
  if (!status) return j({ error: "job not found" }, 404);

  if (request.method === "DELETE") {
    await deleteFileSafe(pat, `jobs/${id}/selection.json`, `castcut: ${id} clear selection`);
    await putFile(pat, `jobs/${id}/status.json`, JSON.stringify({
      ...status, state: "awaiting_selection",
      message: "Selection cleared — pick which files to download.",
      updated_at: now, updated_at_epoch: Math.floor(Date.now() / 1000),
    }, null, 2), `castcut: ${id} awaiting_selection`);
    return j({ ok: true });
  }

  if (request.method !== "PUT") return j({ error: "method not allowed" }, 405);

  const body = await request.json().catch(() => ({}));
  const indices = Array.isArray(body.selected_indices) ? body.selected_indices.map(Number).filter(n => Number.isInteger(n) && n >= 1) : [];
  if (!indices.length) return j({ error: "selected_indices must be a non-empty array of 1-based file indices" }, 400);

  const filesDoc = await readJson(pat, `jobs/${id}/files.json`);
  if (!filesDoc || !Array.isArray(filesDoc.files)) return j({ error: "no file list yet — wait for metadata" }, 400);
  const byIndex = new Map(filesDoc.files.map(f => [f.index, f]));
  const picked = indices.map(i => byIndex.get(i));
  if (picked.some(p => !p)) return j({ error: "selected index not in the torrent's file list" }, 400);
  const nonVideo = picked.filter(p => !p.is_video);
  if (nonVideo.length) return j({ error: `not a video file: ${nonVideo.map(p => p.path).join(", ")}` }, 400);

  const payload = {
    version: 1,
    job_id: id,
    selected_indices: indices,
    paths: picked.map(p => p.path),
    total_bytes: picked.reduce((a, p) => a + (p.length || 0), 0),
    confirmed_at: now,
  };
  await putFile(pat, `jobs/${id}/selection.json`, JSON.stringify(payload, null, 2), `castcut: ${id} file selection (${indices.length})`);
  await putFile(pat, `jobs/${id}/status.json`, JSON.stringify({
    ...status, state: "queued_for_ingest",
    message: `Downloading ${indices.length} selected file(s)…`,
    selected_files: picked.map(p => ({ index: p.index, path: p.path, length: p.length })),
    updated_at: now, updated_at_epoch: Math.floor(Date.now() / 1000),
  }, null, 2), `castcut: ${id} queued_for_ingest`);
  await dispatchWorkflow(pat, "ingest.yml", { job_id: id });
  return j({ ok: true, selected: indices.length });
}

async function deleteFileSafe(pat, path, message) {
  try {
    const { gh } = await import("../../../_lib/gh.js");
    const cur = await gh(pat, `/repos/motionssalt/castcut/contents/${encodeURIComponent(path).replace(/%2F/g, "/")}`);
    const { deleteFile } = await import("../../../_lib/gh.js");
    await deleteFile(pat, path, cur.sha, message);
  } catch { /* absent is fine */ }
}
function j(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } }); }
