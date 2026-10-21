import { putFile, dispatchWorkflow } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  const body = await request.json();
  const prod = body.production;
  if (!prod || typeof prod !== "object") return j({ error: "missing production object" }, 400);
  if (!Array.isArray(prod.segments) || prod.segments.length === 0) return j({ error: 'production.segments must be a non-empty array' }, 400);
  const exportId = body.export_id || `exp-${params.id}-scene-${Date.now().toString(36)}`;
  const audioTrack = (body.audio_track !== undefined && body.audio_track !== null) ? String(body.audio_track) : (prod.audio_track !== undefined && prod.audio_track !== null ? String(prod.audio_track) : "");
  const resolution = (body.resolution === "900p" || body.resolution === "900" || prod.resolution === "900p") ? "900p" : "1080p";
  if (body.audio_track !== undefined && body.audio_track !== null) {
    prod.audio_track = Number(body.audio_track);
  }
  prod.resolution = resolution;
  await putFile(data.pat, `jobs/${params.id}/scene/production.json`, JSON.stringify(prod, null, 2), `castcut: ${params.id} production json (${exportId})`);
  if (audioTrack !== "") {
    await putFile(data.pat, `jobs/${params.id}/audio_choice.json`, JSON.stringify({ audio_track: Number(audioTrack), updated_at_epoch: Math.floor(Date.now() / 1000) }, null, 2), `castcut: ${params.id} audio choice`).catch(() => {});
  }
  await dispatchWorkflow(data.pat, "scene-export.yml", {
    job_id: params.id,
    export_id: exportId,
    audio_track: audioTrack,
    resolution: resolution,
  });
  return j({ ok: true, export_id: exportId });
}

function j(o, s = 200) { return new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } }); }
