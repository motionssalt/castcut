import { requireAuth, api, escapeHtml, formatDate, getStatusBadge, toast, withButtonFeedback, setupPrefetching, clearPat, getTheme, applyTheme } from "./core.js";

const ACTIVE_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "analyzing", "faces_running", "scene_analyzing", "hybrid_running", "clips_exporting", "scene_exporting"
]);

let allProjects = [];
let pollTimer = null;

async function init() {
  requireAuth(async () => {
    setupThemeToggle();
    setupLogout();
    setupSearch();
    setupPrefetching();
    await loadProjects();
  });
}

function setupThemeToggle() {
  const btn = document.getElementById("btnThemeToggle");
  if (!btn) return;
  btn.onclick = () => {
    const cur = getTheme();
    const next = cur === "auto" ? "dark" : cur === "dark" ? "light" : "auto";
    applyTheme(next);
    toast(`Theme set to ${next}`);
  };
}

function setupLogout() {
  const btn = document.getElementById("btnLogout");
  if (!btn) return;
  btn.onclick = () => {
    if (confirm("Sign out of CastCut on this device?")) {
      clearPat();
      location.reload();
    }
  };
}

async function loadProjects(isQuiet = false) {
  const listEl = document.getElementById("projectsList");
  const countLabel = document.getElementById("projectsCountLabel");

  try {
    const res = await api("/api/jobs");
    allProjects = Array.isArray(res.jobs) ? res.jobs : [];
    
    if (countLabel) {
      countLabel.textContent = `${allProjects.length} total project${allProjects.length === 1 ? "" : "s"}`;
    }

    renderProjects(allProjects);

    // Only continue polling if any project is actively running
    clearTimeout(pollTimer);
    const hasActive = allProjects.some(p => ACTIVE_STATES.has(p.state));
    if (hasActive && document.visibilityState === "visible") {
      pollTimer = setTimeout(() => loadProjects(true), 3500);
    }
  } catch (err) {
    if (listEl && !isQuiet) {
      listEl.innerHTML = `
        <div class="card" style="border-color: var(--state-error); color: var(--state-error-text); background-color: var(--state-error-surface);">
          <div style="font-weight: 600; margin-bottom: 4px;">Failed to load projects</div>
          <div style="font-size: 0.8125rem;">${escapeHtml(err.message)}</div>
          <button id="btnRetryLoad" class="btn btn-secondary btn-sm" style="margin-top: 10px;">
            Retry
          </button>
        </div>
      `;
      const retryBtn = document.getElementById("btnRetryLoad");
      if (retryBtn) retryBtn.onclick = () => loadProjects();
    }
  }
}

function renderProjects(projects) {
  const listEl = document.getElementById("projectsList");
  const emptyEl = document.getElementById("projectsEmpty");
  if (!listEl) return;

  if (projects.length === 0) {
    listEl.innerHTML = "";
    if (emptyEl) emptyEl.hidden = false;
    return;
  }
  if (emptyEl) emptyEl.hidden = true;

  listEl.innerHTML = projects.map(p => {
    const st = getStatusBadge(p.state);
    const ct = p.content_type || "anime";
    const ctLabel = ct === "irl" ? "IRL" : "Anime";
    const ctClass = ct === "irl" ? "tag-irl" : "tag-anime";
    const title = p.title || p.torrent_name || p.job_id;
    const updated = formatDate(p.updated_at);
    const projectUrl = `/project/?id=${encodeURIComponent(p.job_id)}`;
    const facesUrl = `/faces/?id=${encodeURIComponent(p.job_id)}`;
    const sceneUrl = `/scene/?id=${encodeURIComponent(p.job_id)}`;
    const exportsUrl = `/exports/?id=${encodeURIComponent(p.job_id)}`;

    return `
      <div class="card card-hover" style="margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 8px;">
          <div style="min-width: 0; flex: 1;">
            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
              <span class="tag-content ${ctClass}">${ctLabel}</span>
              <a href="${projectUrl}" style="font-weight: 600; font-size: 1rem; color: var(--text-primary); text-decoration: none; word-break: break-word;">
                ${escapeHtml(title)}
              </a>
            </div>
            <div class="metadata-line">
              <span class="mono">${escapeHtml(p.job_id)}</span>
              ${updated ? `<span class="metadata-sep" aria-hidden="true">·</span><span>${escapeHtml(updated)}</span>` : ""}
            </div>
          </div>
          <span class="status-badge ${st.cls}">${escapeHtml(st.label)}</span>
        </div>

        ${p.message ? `
          <div style="font-size: 0.8125rem; color: var(--text-secondary); margin-bottom: 12px; background-color: var(--bg-sunken); padding: 6px 10px; border-radius: var(--radius-sm); word-break: break-word;">
            ${escapeHtml(p.message)}
          </div>
        ` : ""}

        <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; pt: 8px; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
          <div style="display: flex; flex-wrap: wrap; gap: 6px;">
            <a href="${facesUrl}" class="btn btn-ghost btn-sm" title="Faces Selector">
              🙂 Faces
            </a>
            <a href="${sceneUrl}" class="btn btn-ghost btn-sm" title="AI Scene Flow">
              🎞 Scene
            </a>
            <a href="${exportsUrl}" class="btn btn-ghost btn-sm" title="Finished Exports">
              📦 Exports
            </a>
          </div>

          <div style="display: flex; gap: 6px;">
            <button class="btn btn-ghost btn-sm btn-delete-project" data-id="${escapeHtml(p.job_id)}" title="Delete Project" style="color: var(--state-error);">
              Delete
            </button>
            <a href="${projectUrl}" class="btn btn-secondary btn-sm" style="font-weight: 600;">
              Open →
            </a>
          </div>
        </div>
      </div>
    `;
  }).join("");

  // Attach delete handlers
  listEl.querySelectorAll(".btn-delete-project").forEach(btn => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      if (!confirm(`Delete project "${id}"?\n\nThis will cancel any active workflow runs and permanently delete all its files, releases, and exports.`)) {
        return;
      }
      await withButtonFeedback(btn, async () => {
        try {
          await api(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
          toast("Project deleted", "info");
          allProjects = allProjects.filter(p => p.job_id !== id);
          renderProjects(allProjects);
        } catch (e) {
          toast(`Failed to delete: ${e.message}`, "error");
        }
      }, "Deleting…");
    };
  });

  setupPrefetching();
}

function setupSearch() {
  const input = document.getElementById("projectSearchInput");
  if (!input) return;
  input.oninput = () => {
    const q = input.value.toLowerCase().trim();
    if (!q) {
      renderProjects(allProjects);
      return;
    }
    const filtered = allProjects.filter(p => {
      return (p.title && p.title.toLowerCase().includes(q)) ||
             (p.torrent_name && p.torrent_name.toLowerCase().includes(q)) ||
             (p.job_id && p.job_id.toLowerCase().includes(q));
    });
    renderProjects(filtered);
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadProjects(true);
  } else {
    clearTimeout(pollTimer);
  }
});

window.addEventListener("DOMContentLoaded", init);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
