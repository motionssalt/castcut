// Shared GitHub helpers for Pages Functions. The owner's PAT (verified by
// _middleware) is forwarded for all content/dispatch calls.
export const REPO_OWNER = "motionssalt";
export const REPO_NAME = "castcut";
export const REPO = `${REPO_OWNER}/${REPO_NAME}`;

const UA = "castcut-pages/1.0";
const JOB_WORKFLOWS = ["ingest.yml", "select.yml", "faces.yml", "scene-analyze.yml", "hybrid-analyze.yml", "clips-export.yml", "scene-export.yml"];
const ACTIVE_RUN_STATES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
// job states that mean "a workflow run should be active right now"
const BUSY_JOB_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "faces_running", "scene_analyzing", "hybrid_analyzing", "clips_exporting", "scene_exporting",
]);

export async function gh(pat, path, { method = "GET", body = null, raw = false, acceptRaw = false } = {}) {
  const headers = {
    Authorization: `Bearer ${pat}`,
    Accept: acceptRaw ? "application/vnd.github.raw" : "application/vnd.github+json",
    "User-Agent": UA,
  };
  if (body) headers["content-type"] = "application/json";
  const r = await fetch(`https://api.github.com${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`github ${method} ${path} -> ${r.status}: ${t.slice(0, 200)}`);
  }
  if (raw) return r;
  if (acceptRaw) return r.text();
  if (r.status === 204) return null;
  return r.json();
}

const esc = (p) => encodeURIComponent(p).replace(/%2F/g, "/");

export async function readFile(pat, path) {
  try {
    return await gh(pat, `/repos/${REPO}/contents/${esc(path)}`, { acceptRaw: true });
  } catch (e) {
    if (String(e.message).includes("-> 404")) return null;
    throw e;
  }
}

export async function readJson(pat, path) {
  const txt = await readFile(pat, path);
  if (txt == null) return null;
  try { return JSON.parse(txt); } catch { return null; }
}

export function bytesToBase64(bytes) {
  if (typeof Buffer !== "undefined") {
    return Buffer.from(bytes).toString("base64");
  }
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const CHUNK_SIZE = 8192;
  let binary = "";
  for (let i = 0; i < u8.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

export async function putFile(pat, path, content, message) {
  let sha = null;
  try {
    const cur = await gh(pat, `/repos/${REPO}/contents/${esc(path)}`);
    sha = cur.sha;
  } catch (e) {
    if (!String(e.message).includes("-> 404")) throw e;
  }
  const b64 = typeof content === "string"
    ? (typeof Buffer !== "undefined" ? Buffer.from(content, "utf8").toString("base64") : btoa(unescape(encodeURIComponent(content))))
    : bytesToBase64(content);
  return gh(pat, `/repos/${REPO}/contents/${esc(path)}`, {
    method: "PUT",
    body: { message, content: b64, sha: sha || undefined },
  });
}

export async function deleteFile(pat, path, sha, message) {
  return gh(pat, `/repos/${REPO}/contents/${esc(path)}`, {
    method: "DELETE",
    body: { message, sha, branch: "main" },
  });
}

export async function dispatchWorkflow(pat, filename, inputs) {
  return gh(pat, `/repos/${REPO}/actions/workflows/${filename}/dispatches`, {
    method: "POST",
    body: { ref: "main", inputs },
  });
}

export async function getRelease(pat, tag) {
  try {
    return await gh(pat, `/repos/${REPO}/releases/tags/${encodeURIComponent(tag)}`);
  } catch (e) {
    if (String(e.message).includes("-> 404")) return null;
    throw e;
  }
}

export async function listAllReleases(pat) {
  try {
    const list = await gh(pat, `/repos/${REPO}/releases?per_page=100`);
    return Array.isArray(list) ? list : [];
  } catch (e) {
    return [];
  }
}

export async function getProjectReleases(pat, jobId) {
  const all = await listAllReleases(pat);
  const matching = all.filter(r => {
    const tag = r.tag_name || "";
    return tag === `castcut-${jobId}` ||
           tag === `castcut-job-${jobId}` ||
           tag.startsWith(`castcut-${jobId}-`) ||
           tag.startsWith(`castcut-job-${jobId}-`);
  });
  const assets = [];
  for (const rel of matching) {
    for (const a of (rel.assets || [])) {
      assets.push({
        id: a.id,
        name: a.name,
        size: a.size,
        browser_download_url: a.browser_download_url,
        release_tag: rel.tag_name,
        created_at: a.created_at,
      });
    }
  }
  return { releases: matching, assets };
}

export async function listJobs(pat) {
  try {
    const items = await gh(pat, `/repos/${REPO}/contents/jobs`);
    if (!Array.isArray(items)) return [];
    return items.filter(x => x.type === "dir").map(x => x.name);
  } catch (e) {
    if (String(e.message).includes("-> 404")) return [];
    throw e;
  }
}

// ---------------------------------------------------------------------------
// Workflow-run inspection / control (status reconcile, cancel, retry, delete)
// ---------------------------------------------------------------------------

// Runs for one job, newest first. Job workflows set run-name to include the
// job id, so a per-workflow scan + title filter finds them reliably.
export async function listJobRuns(pat, jobId) {
  const out = [];
  await Promise.all(JOB_WORKFLOWS.map(async (wf) => {
    try {
      const d = await gh(pat, `/repos/${REPO}/actions/workflows/${wf}/runs?per_page=10`);
      for (const r of d.workflow_runs || []) {
        if ((r.display_title || "").includes(jobId)) {
          out.push({
            id: r.id, name: r.display_title || r.name, workflow: wf,
            status: r.status, conclusion: r.conclusion,
            html_url: r.html_url, created_at: r.created_at,
          });
        }
      }
    } catch { /* a missing workflow file must not break reconcile */ }
  }));
  out.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  return out;
}

export async function cancelRun(pat, runId) {
  try {
    await gh(pat, `/repos/${REPO}/actions/runs/${runId}/cancel`, { method: "POST", body: {} });
    return true;
  } catch { return false; }
}

export async function cancelActiveRuns(pat, jobId) {
  const runs = await listJobRuns(pat, jobId);
  const active = runs.filter(r => ACTIVE_RUN_STATES.has(r.status));
  for (const r of active) await cancelRun(pat, r.id);
  return active.length;
}

// First failed step of a run -> short human reason.
export async function failedStepInfo(pat, runId) {
  try {
    const d = await gh(pat, `/repos/${REPO}/actions/runs/${runId}/jobs?per_page=50`);
    const jobs = d.jobs || [];
    const failedJob = jobs.find(j => j.conclusion === "failure" || j.conclusion === "timed_out") || jobs[0];
    if (!failedJob) return null;
    const failedStep = (failedJob.steps || []).find(s => s.conclusion === "failure");
    return {
      job: failedJob.name || "",
      step: failedStep ? failedStep.name : "",
      conclusion: failedJob.conclusion || "failure",
    };
  } catch { return null; }
}

// If status.json claims a run is busy but Actions says its run died while the
// user was away, fold the truth into the status object (read-only — the
// workflow's own status write is authoritative when it happens).
export async function reconcileStatus(pat, jobId, status) {
  if (!status || !status.state || !BUSY_JOB_STATES.has(status.state)) return status;
  const runs = await listJobRuns(pat, jobId);
  if (!runs.length) return status;
  if (runs.some(r => ACTIVE_RUN_STATES.has(r.status))) return status; // genuinely running
  const latest = runs[0];
  if (status.workflow_run_url && latest.html_url !== status.workflow_run_url) return status; // a newer run is coming
  if (latest.status !== "completed") return status;
  const out = { ...status, run_url: latest.html_url };
  if (latest.conclusion === "cancelled") {
    out.state = "cancelled";
    out.cancelled_from = out.cancelled_from || status.state;
    out.message = "Workflow run was cancelled.";
  } else if (latest.conclusion && latest.conclusion !== "success") {
    out.state = "failed";
    out.failed_from = status.state;
    const info = await failedStepInfo(pat, latest.id);
    out.failed_step = info && info.step ? `${info.job} → ${info.step}` : (latest.name || latest.workflow);
    out.message = `Run ${latest.conclusion}: ${out.failed_step}`;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Job deletion
// ---------------------------------------------------------------------------

export async function deleteReleaseAndTag(pat, tag) {
  const rel = await getRelease(pat, tag);
  if (rel) {
    try { await gh(pat, `/repos/${REPO}/releases/${rel.id}`, { method: "DELETE" }); } catch { /* best effort */ }
  }
  try { await gh(pat, `/repos/${REPO}/git/refs/tags/${encodeURIComponent(tag)}`, { method: "DELETE" }); } catch { /* 404 ok */ }
}

// Delete every release and tag associated with a project (including overflow releases)
export async function deleteProjectReleasesAndTags(pat, jobId) {
  const { releases } = await getProjectReleases(pat, jobId);
  for (const rel of releases) {
    try { await gh(pat, `/repos/${REPO}/releases/${rel.id}`, { method: "DELETE" }); } catch { /* best effort */ }
    try { await gh(pat, `/repos/${REPO}/git/refs/tags/${encodeURIComponent(rel.tag_name)}`, { method: "DELETE" }); } catch { /* 404 ok */ }
  }
  // Also proactively attempt deletion of canonical tags in case release object wasn't found
  const canonicalTags = [`castcut-${jobId}`, `castcut-job-${jobId}`];
  for (const tag of canonicalTags) {
    try { await deleteReleaseAndTag(pat, tag); } catch { /* ok */ }
  }
}

// Delete every committed file under jobs/<jobId>/ (recursive via git trees).
export async function deleteJobFiles(pat, jobId) {
  const ref = await gh(pat, `/repos/${REPO}/git/ref/heads/main`);
  const tree = await gh(pat, `/repos/${REPO}/git/trees/${ref.object.sha}?recursive=1`);
  const blobs = (tree.tree || []).filter(t => t.type === "blob" && t.path.startsWith(`jobs/${jobId}/`));
  for (const b of blobs) {
    try { await deleteFile(pat, b.path, b.sha, `castcut: delete job ${jobId}`); } catch { /* keep going */ }
  }
  return blobs.length;
}
