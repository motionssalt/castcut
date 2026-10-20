import { putFile } from "../../../_lib/gh.js";

export async function onRequest({ params, data, request }) {
  if (request.method !== "POST") return new Response("Method not allowed", { status: 405 });
  const body = await request.json().catch(() => ({}));
  if (body.audio_track === undefined || body.audio_track === null) {
    return new Response(JSON.stringify({ error: "missing audio_track" }), { status: 400, headers: { "content-type": "application/json" } });
  }
  const payload = {
    audio_track: Number(body.audio_track),
    updated_at_epoch: Math.floor(Date.now() / 1000),
  };
  await putFile(data.pat, `jobs/${params.id}/audio_choice.json`, JSON.stringify(payload, null, 2), `castcut: ${params.id} audio choice`);
  return new Response(JSON.stringify({ ok: true, audio_track: payload.audio_track }), { headers: { "content-type": "application/json" } });
}
