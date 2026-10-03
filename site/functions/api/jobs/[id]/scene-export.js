import { putFile, dispatchWorkflow } from "../../../_lib/gh.js";
export async function onRequest({ params, data, request }) {
  const body = await request.json();
  const prod = body.production;
  if (!prod || typeof prod !== "object") return j({ error: "missing production object" }, 400);
  if (!Array.isArray(prod.segments) || prod.segments.length === 0) return j({ error: 'production.segments must be a non-empty array' }, 400);
  await putFile(data.pat, `jobs/${params.id}/scene/production.json`, JSON.stringify(prod, null, 2), `castcut: ${params.id} production json`);
  await dispatchWorkflow(data.pat, "scene-export.yml", { job_id: params.id });
  return j({ ok: true });
}
function j(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } }); }
