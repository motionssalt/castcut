import { putFile, dispatchWorkflow } from "../../../_lib/gh.js";
import { projectType } from "../../../_lib/project.js";
export async function onRequest({ params, data, request }) {
  const body = await request.json().catch(() => ({}));
  const content_type = await projectType(data.pat, params.id, body.content_type);
  const req = { version: 1, request: String(body.request || "").slice(0, 2000), created_at_epoch: Math.floor(Date.now() / 1000) };
  await putFile(data.pat, `jobs/${params.id}/scene/scene-request.json`, JSON.stringify(req, null, 2), `castcut: ${params.id} scene request`);
  await dispatchWorkflow(data.pat, "scene-analyze.yml", { job_id: params.id, content_type });
  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } });
}
