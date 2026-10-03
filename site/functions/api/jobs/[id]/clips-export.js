import { putFile, dispatchWorkflow } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  const sel = await request.json();
  const exportId = sel.export_id || `exp-${params.id}-clips-${Date.now().toString(36)}`;
  const payload = {
    version: 1,
    export_id: exportId,
    order: sel.order || [],
    ignored: sel.ignored || [],
    merge_groups: sel.merge_groups || [],
    gap_threshold: Number(sel.gap_threshold ?? 2),
    padding: Number(sel.padding ?? 0.4),
  };
  await putFile(data.pat, `jobs/${params.id}/clips/selection.json`, JSON.stringify(payload, null, 2), `castcut: ${params.id} selection (${exportId})`);
  await dispatchWorkflow(data.pat, "clips-export.yml", { job_id: params.id, export_id: exportId });
  return new Response(JSON.stringify({ ok: true, export_id: exportId }), { headers: { "content-type": "application/json" } });
}
