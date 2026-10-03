import { gh, putFile, dispatchWorkflow, listJobs, readFile, REPO } from "../../_lib/gh.js";

export async function onRequest({ request, data }) {
  const pat = data.pat;
  if (request.method === "GET") {
    const ids = await listJobs(pat);
    const jobs = [];
    for (const id of ids.slice().reverse().slice(0, 50)) {
      const s = await readFile(pat, `jobs/${id}/status.json`);
      if (!s) { jobs.push({ job_id: id }); continue; }
      try { const j = JSON.parse(s); jobs.push({ job_id: id, state: j.state, updated_at: j.updated_at, title: j.title }); }
      catch { jobs.push({ job_id: id }); }
    }
    return json({ jobs });
  }
  if (request.method === "POST") {
    const body = await request.json();
    const kind = body?.source?.kind;
    if (!["direct_url", "gdrive", "magnet", "torrent_file"].includes(kind)) return json({ error: "invalid source.kind" }, 400);
    const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    // Write request + initial status; if torrent_file, also commit the raw .torrent
    const req = {
      version: 1, job_id: jobId, title: body.title || "",
      source: { kind, url: body?.source?.url, magnet: body?.source?.magnet },
      created_at_epoch: Math.floor(Date.now() / 1000),
    };
    await putFile(pat, `jobs/${jobId}/ingest-request.json`, JSON.stringify(req, null, 2), `castcut: create job ${jobId}`);
    if (kind === "torrent_file") {
      const b64 = body?.source?.torrent_b64 || "";
      if (!b64) return json({ error: "missing torrent file" }, 400);
      const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      await putFile(pat, `jobs/${jobId}/source.torrent`, bin, `castcut: ${jobId} torrent`);
    }
    const now = new Date().toISOString().replace(/\.\d+/, "").replace("Z", "Z");
    const ttl = 86400;
    const status = {
      job_id: jobId, state: "created", message: "Queued for ingest",
      title: body.title || "",
      created_at_epoch: Math.floor(Date.now() / 1000),
      created_at: now, updated_at: now,
      expires_at_epoch: Math.floor(Date.now() / 1000) + ttl,
    };
    await putFile(pat, `jobs/${jobId}/status.json`, JSON.stringify(status, null, 2), `castcut: status ${jobId}`);
    await dispatchWorkflow(pat, "ingest.yml", { job_id: jobId });
    return json({ job_id: jobId });
  }
  return json({ error: "method not allowed" }, 405);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
}
