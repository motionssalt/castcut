import { putFile, dispatchWorkflow, listJobs, readJson, reconcileStatus } from "../../_lib/gh.js";

const KINDS = ["direct_url", "gdrive", "magnet", "torrent_file"];
const ACTIVE = new Set(["created", "queued", "queued_for_ingest", "selecting", "ingest_running", "faces_running", "scene_analyzing", "clips_exporting", "scene_exporting"]);

export async function onRequest({ request, data }) {
  const pat = data.pat;

  if (request.method === "GET") {
    const ids = await listJobs(pat);
    const jobs = [];
    const recent = ids.slice().reverse().slice(0, 50);
    for (const id of recent) {
      const j = await readJson(pat, `jobs/${id}/status.json`);
      const project = await readJson(pat, `jobs/${id}/ingest-request.json`);
      if (!j) {
        jobs.push({
          job_id: id,
          content_type: project?.content_type || null,
          state: "incomplete",
          message: "Invalid status record; repair analysis required.",
        });
        continue;
      }
      let title = j.title || j.torrent_name || null;
      if (!title) {
        const reqDoc = await readJson(pat, `jobs/${id}/ingest-request.json`);
        title = reqDoc?.title || reqDoc?.torrent_name || null;
        if (!title) {
          const filesDoc = await readJson(pat, `jobs/${id}/files.json`);
          title = filesDoc?.torrent_name || null;
        }
      }
      jobs.push({
        job_id: id,
        state: j.state,
        message: j.message,
        title: title,
        content_type: project?.content_type || null,
        torrent_name: j.torrent_name || null,
        updated_at: j.updated_at,
        workflow_run_url: j.workflow_run_url || j.run_url || null,
      });
    }

    // Reconcile the active ones against GitHub Actions
    await Promise.all(
      jobs.filter(j => ACTIVE.has(j.state)).slice(0, 8).map(async (j) => {
        try {
          const fixed = await reconcileStatus(pat, j.job_id, {
            state: j.state,
            message: j.message,
            workflow_run_url: j.workflow_run_url,
          });
          j.state = fixed.state;
          j.message = fixed.message || j.message;
        } catch { /* best effort */ }
      })
    );

    for (const j of jobs) delete j.workflow_run_url;
    return json({ jobs });
  }

  if (request.method === "POST") {
    try {
      const body = await request.json().catch(() => ({}));
      const kind = body?.source?.kind;
      if (!KINDS.includes(kind)) return json({ error: "invalid source.kind" }, 400);

      const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      const epoch = Math.floor(Date.now() / 1000);
      const userTitle = String(body.title || "").trim().slice(0, 200);
      const torrentName = String(body.torrent_name || "").trim().slice(0, 255);
      const resolvedTitle = userTitle || torrentName || "";

      if (!["irl", "anime"].includes(body?.content_type)) {
        return json({ error: "content_type must be explicitly anime or irl" }, 400);
      }
      const contentType = body.content_type;

      const req = {
        version: 1,
        job_id: jobId,
        title: userTitle,
        torrent_name: torrentName,
        content_type: contentType,
        source: { kind, url: body?.source?.url, magnet: body?.source?.magnet },
        created_at_epoch: epoch,
      };

      if (kind === "magnet" && !String(req.source.magnet || "").startsWith("magnet:?")) {
        return json({ error: "source.magnet must be a magnet:? URI" }, 400);
      }

      await putFile(pat, `jobs/${jobId}/ingest-request.json`, JSON.stringify(req, null, 2), `castcut: create job ${jobId}`);

      const status = {
        job_id: jobId,
        title: resolvedTitle,
        torrent_name: torrentName,
        content_type: contentType,
        created_at_epoch: epoch,
        created_at: now,
        updated_at: now,
        updated_at_epoch: epoch,
        expires_at_epoch: epoch + 86400,
      };

      const writeStatus = (extra) => putFile(pat, `jobs/${jobId}/status.json`, JSON.stringify({ ...status, ...extra }, null, 2), `castcut: status ${jobId}`);

      const dispatchSafely = async (wf, inp, fallbackState = "failed") => {
        try {
          await dispatchWorkflow(pat, wf, inp);
        } catch (err) {
          const msg = `Workflow dispatch error (${wf}): ${err.message || String(err)}`;
          await writeStatus({ state: fallbackState, message: msg }).catch(() => {});
          throw new Error(msg);
        }
      };

      if (kind === "torrent_file") {
        const b64 = body?.source?.torrent_b64 || "";
        if (!b64) return json({ error: "missing torrent file" }, 400);
        const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        if (!bin.length || bin.length > 1024 * 1024) return json({ error: "torrent file must be 1 byte – 1 MB" }, 400);
        await putFile(pat, `jobs/${jobId}/source.torrent`, bin, `castcut: ${jobId} torrent`);

        const files = Array.isArray(body?.files) ? body.files : null;
        if (files && files.length) {
          const doc = {
            version: 1,
            job_id: jobId,
            source_kind: "torrent_file",
            torrent_name: String(body.torrent_name || "").slice(0, 255),
            file_count: files.length,
            files: files.map(f => ({ index: f.index, path: String(f.path).slice(0, 1024), length: Number(f.length) || 0, is_video: !!f.is_video })),
            video_indices: files.filter(f => f.is_video).map(f => f.index),
            single_video: files.filter(f => f.is_video).length === 1,
            fetched_at_epoch: epoch,
          };
          doc.total_bytes = doc.files.reduce((a, f) => a + f.length, 0);
          await putFile(pat, `jobs/${jobId}/files.json`, JSON.stringify(doc, null, 2), `castcut: ${jobId} files.json`);

          const sel = Array.isArray(body.selected_indices) ? body.selected_indices.map(Number).filter(n => doc.video_indices.includes(n)) : [];
          if (sel.length) {
            const picked = doc.files.filter(f => sel.includes(f.index));
            await putFile(pat, `jobs/${jobId}/selection.json`, JSON.stringify({
              version: 1,
              job_id: jobId,
              selected_indices: sel,
              paths: picked.map(p => p.path),
              total_bytes: picked.reduce((a, p) => a + p.length, 0),
              confirmed_at: now,
            }, null, 2), `castcut: ${jobId} file selection (${sel.length})`);
            await writeStatus({
              state: "queued_for_ingest",
              message: `Downloading ${sel.length} selected file(s)…`,
              selected_files: picked.map(p => ({ index: p.index, path: p.path, length: p.length })),
            });
            await dispatchSafely("ingest.yml", { job_id: jobId }, "ingest_failed");
            return json({ job_id: jobId, state: "queued_for_ingest" });
          }
          await writeStatus({ state: "awaiting_selection", message: "File list ready — pick which files to download." });
          return json({ job_id: jobId, state: "awaiting_selection" });
        }

        await writeStatus({ state: "awaiting_selection", message: "Open the job to list the torrent's files." });
        await dispatchSafely("select.yml", { job_id: jobId }, "select_failed");
        return json({ job_id: jobId, state: "awaiting_selection" });
      }

      if (kind === "magnet") {
        await writeStatus({ state: "selecting", message: "Fetching torrent metadata (listing files — no content download)" });
        await dispatchSafely("select.yml", { job_id: jobId }, "select_failed");
        return json({ job_id: jobId, state: "selecting" });
      }

      // direct_url / gdrive
      await writeStatus({ state: "queued", message: "Queued for ingest" });
      await dispatchSafely("ingest.yml", { job_id: jobId }, "ingest_failed");
      return json({ job_id: jobId, state: "queued" });
    } catch (err) {
      return json({ error: err.message || "Failed to create project" }, 500);
    }
  }

  return json({ error: "method not allowed" }, 405);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });
}
