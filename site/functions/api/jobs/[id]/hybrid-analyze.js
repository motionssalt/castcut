import { dispatchWorkflow } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  const body = await request.json().catch(() => ({}));
  const fps = String(body.sample_fps || 1.0);
  await dispatchWorkflow(data.pat, "hybrid-analyze.yml", { job_id: params.id, sample_fps: fps });
  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } });
}
