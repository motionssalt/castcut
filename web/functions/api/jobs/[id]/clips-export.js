import { putFile, dispatchWorkflow } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  const sel = await request.json();
  const exportId = sel.export_id || `exp-${params.id}-clips-${Date.now().toString(36)}`;
  const audioTrack = (sel.audio_track !== undefined && sel.audio_track !== null) ? String(sel.audio_track) : "";
  const resolution = (sel.resolution === "900p" || sel.resolution === "900") ? "900p" : "1080p";
  const payload = {
    version: 1,
    export_id: exportId,
    order: sel.order || [],
    ignored: sel.ignored || [],
    merge_groups: sel.merge_groups || [],
    gap_threshold: Number(sel.gap_threshold ?? 2),
    padding: Number(sel.padding ?? 0.4),
    audio_track: sel.audio_track !== undefined ? sel.audio_track : null,
    resolution: resolution,
  };
  await putFile(data.pat, `jobs/${params.id}/clips/selection.json`, JSON.stringify(payload, null, 2), `castcut: ${params.id} selection (${exportId})`);
  if (sel.audio_track !== undefined && sel.audio_track !== null) {
    await putFile(data.pat, `jobs/${params.id}/audio_choice.json`, JSON.stringify({ audio_track: Number(sel.audio_track), updated_at_epoch: Math.floor(Date.now() / 1000) }, null, 2), `castcut: ${params.id} audio choice`).catch(() => {});
  }
  await dispatchWorkflow(data.pat, "clips-export.yml", {
    job_id: params.id,
    export_id: exportId,
    audio_track: audioTrack,
    resolution: resolution,
  });
  return new Response(JSON.stringify({ ok: true, export_id: exportId }), { headers: { "content-type": "application/json" } });
}
