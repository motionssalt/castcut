import { dispatchWorkflow, readJson } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  const body = await request.json().catch(() => ({}));
  const fps = String(body.sample_fps || 1.0);
  const reqDoc = await readJson(data.pat, `jobs/${params.id}/ingest-request.json`).catch(() => ({}));
  const contentType = body.content_type || reqDoc?.content_type || "anime";
  await dispatchWorkflow(data.pat, "hybrid-analyze.yml", { job_id: params.id, sample_fps: fps, content_type: contentType });
  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } });
}
