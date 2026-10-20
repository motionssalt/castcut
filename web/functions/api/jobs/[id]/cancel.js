import { readJson, putFile, cancelActiveRuns } from "../../../_lib/gh.js";

// POST /api/jobs/<id>/cancel — cancel the live workflow run(s) and mark the
// job cancelled immediately (the workflow's own cancelled() step confirms).
export async function onRequest({ request, params, data }) {
  if (request.method !== "POST") return j({ error: "method not allowed" }, 405);
  const pat = data.pat; const id = params.id;
  const status = await readJson(pat, `jobs/${id}/status.json`);
  if (!status) return j({ error: "job not found" }, 404);
  const cancelled = await cancelActiveRuns(pat, id);
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const next = {
    ...status,
    state: "cancelled",
    cancelled_from: status.state,
    message: cancelled ? "Cancel requested — the run is stopping." : "Cancelled (no active run found).",
    updated_at: now,
    updated_at_epoch: Math.floor(Date.now() / 1000),
  };
  await putFile(pat, `jobs/${id}/status.json`, JSON.stringify(next, null, 2), `castcut: cancel ${id}`);
  return j({ ok: true, cancelled_runs: cancelled });
}
function j(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } }); }
