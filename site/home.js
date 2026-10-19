// CastCut Home - Fast Projects List

const ACTIVE_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "faces_running", "scene_analyzing", "hybrid_running", "clips_exporting", "scene_exporting"
]);

let allJobs = [];
let pollTimer = null;

async function initHome() {
  // Backward compatibility redirect for legacy bookmarks
  const params = new URLSearchParams(window.location.search);
  const hash = window.location.hash.replace(/^#/, "");
  const legacyJob = params.get("id") || params.get("job") || (hash.startsWith("job-") ? hash : null);
  if (legacyJob) {
    const tab = params.get("tab");
    if (tab === "faces" || tab === "clips") {
      location.replace(`/faces/?id=${encodeURIComponent(legacyJob)}`);
      return;
    }
    if (tab === "scene") {
      location.replace(`/scene/?id=${encodeURIComponent(legacyJob)}`);
      return;
    }
    if (tab === "exports") {
      location.replace(`/exports/?id=${encodeURIComponent(legacyJob)}`);
      return;
    }
    location.replace(`/project/?id=${encodeURIComponent(legacyJob)}`);
    return;
  }

  requireAuth(async () => {
    setupSearch();
    await loadJobs();
  });
}

async function loadJobs(quiet = false) {
  const listEl = document.getElementById("jobsList");
  const emptyEl = document.getElementById("jobsEmpty");
  const countEl = document.getElementById("projectsCount");

  if (!quiet && listEl) {
    listEl.innerHTML = `
      <div class="card loading-skeleton" style="height: 72px; margin-bottom: 8px;"></div>
      <div class="card loading-skeleton" style="height: 72px;"></div>
    `;
  }

  try {
    const data = await api("/api/jobs");
    allJobs = data.jobs || [];
    if (countEl) countEl.textContent = allJobs.length > 0 ? `(${allJobs.length})` : "";
    renderJobs(allJobs);

    // Only schedule next poll if at least one job is actively running!
    const hasActive = allJobs.some(j => ACTIVE_STATES.has(j.state));
    clearTimeout(pollTimer);
    if (hasActive) {
      pollTimer = setTimeout(() => loadJobs(true), 4000);
    }
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div class="card err">Failed to load projects: ${esc(err.message)}</div>`;
    }
  }
}

function renderJobs(jobs) {
  const listEl = document.getElementById("jobsList");
  const emptyEl = document.getElementById("jobsEmpty");
  if (!listEl) return;

  if (jobs.length === 0) {
    listEl.innerHTML = "";
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;

  listEl.innerHTML = jobs.map(j => {
    const st = statusView(j.state);
    const ct = j.content_type || "anime";
    const ctLabel = ct === "irl" ? "IRL" : "Anime";
    const title = j.title || j.torrent_name || j.job_id;
    const updated = j.updated_at ? new Date(j.updated_at).toLocaleString() : "";
    const pUrl = `/project/?id=${encodeURIComponent(j.job_id)}`;
    const fUrl = `/faces/?id=${encodeURIComponent(j.job_id)}`;
    const sUrl = `/scene/?id=${encodeURIComponent(j.job_id)}`;
    const eUrl = `/exports/?id=${encodeURIComponent(j.job_id)}`;

    return `
      <div class="job-item card" style="margin-bottom: 8px;">
        <div class="job-item-header" style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
          <div>
            <span class="tag tag-${ct}">${ctLabel}</span>
            <a href="${pUrl}" class="job-link" style="font-weight: 600; font-size: 15px; text-decoration: none; color: #fff; margin-left: 4px;">
              ${esc(title)}
            </a>
          </div>
          <span class="status-pill status-${st.cls}">${st.label}</span>
        </div>
        ${j.message ? `<div class="dim sm" style="margin-top: 4px;">${esc(j.message)}</div>` : ""}
        <div style="display:flex; justify-content:space-between; align-items:center; margin-top: 10px; font-size: 12px;" class="dim">
          <span class="mono">${esc(j.job_id)} · ${updated}</span>
          <div style="display: flex; gap: 6px;">
            <a href="${fUrl}" class="btn ghost sm" style="text-decoration:none;">🙂 Faces</a>
            <a href="${sUrl}" class="btn ghost sm" style="text-decoration:none;">🎞 Scene</a>
            <a href="${eUrl}" class="btn ghost sm" style="text-decoration:none;">📦 Exports</a>
            <a href="${pUrl}" class="btn primary sm" style="text-decoration:none;">Open →</a>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function setupSearch() {
  const searchInput = document.getElementById("searchProjects");
  if (!searchInput) return;
  searchInput.oninput = () => {
    const q = searchInput.value.toLowerCase().trim();
    if (!q) {
      renderJobs(allJobs);
      return;
    }
    const filtered = allJobs.filter(j => {
      return (j.title && j.title.toLowerCase().includes(q)) ||
             (j.torrent_name && j.torrent_name.toLowerCase().includes(q)) ||
             (j.job_id && j.job_id.toLowerCase().includes(q));
    });
    renderJobs(filtered);
  };
}

window.addEventListener("DOMContentLoaded", initHome);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
