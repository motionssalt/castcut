import { dispatchWorkflow } from "../../../_lib/gh.js";

// Legacy hybrid endpoint: dispatches both parallel pipelines (faces and scene analysis)
export async function onRequest({ params, data, request }) {
  const body = await request.json().catch(() => ({}));
  const fps = String(body.sample_fps || 1.0);
  const contentType = body.content_type === "irl" ? "irl" : "anime";

  await Promise.all([
    dispatchWorkflow(data.pat, "faces.yml", { job_id: params.id, sample_fps: fps, content_type: contentType }),
    dispatchWorkflow(data.pat, "scene-analyze.yml", { job_id: params.id }),
  ]);

  return new Response(JSON.stringify({ ok: true, message: "Parallel face and scene analysis triggered." }), {
    headers: { "content-type": "application/json" },
  });
}
