// CastCut Task, Runs & Actions Page

const ACTIVE_RUN_STATES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

let jobId = null;
let pollTimer = null;

function initTask() {
  const params = new URLSearchParams(window.location.search);
  jobId = params.get("id");
  if (!jobId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    await loadTaskData();
  });
}

async function loadTaskData(quiet = false) {
  const content = document.getElementById("taskContent");
  if (!quiet && content) {
    content.innerHTML = `
      <div class="card loading-skeleton" style="height: 100px; margin-bottom: 12px;"></div>
      <div class="card loading-skeleton" style="height: 140px;"></div>
    `;
  }

  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    const title = data.status?.title || data.status?.torrent_name || jobId;
    renderProjectNav(jobId, "task", title);
    renderTasksUI(data);

    // Poll if any run is active
    const hasActiveRun = (data.runs || []).some(r => ACTIVE_RUN_STATES.has(r.status));
    clearTimeout(pollTimer);
    if (hasActiveRun) {
      pollTimer = setTimeout(() => loadTaskData(true), 4000);
    }
  } catch (err) {
    if (content) {
      content.innerHTML = `<div class="card err">Failed to load tasks: ${esc(err.message)}</div>`;
    }
  }
}

function renderTasksUI(data) {
  const content = document.getElementById("taskContent");
  if (!content) return;

  const runs = data.runs || [];
  const status = data.status || {};

  content.innerHTML = `
    <!-- Workflow Runs -->
    <div class="card" style="margin-bottom: 12px;">
      <div class="rowhead" style="margin-bottom: 8px;">
        <h2 style="margin:0;">⚙️ Pipeline Tasks & Actions</h2>
        <button id="btnRefreshTasks" class="btn ghost sm">↻ Refresh</button>
      </div>
      <p class="dim sm">Real-time status of GitHub Actions workflows for this project.</p>

      <div class="tasks-list" id="runsList" style="margin-top: 12px;"></div>
    </div>

    <!-- Danger Zone: Delete Project -->
    <div class="card" style="border: 1px solid #da3633; background: rgba(218, 54, 51, 0.05);">
      <h3 style="color: #f85149; margin-top: 0;">Danger Zone</h3>
      <p class="dim sm">
        Permanently delete this project. This cancels any running workflows, deletes GitHub releases & assets, and wipes all job metadata.
      </p>
      <button id="btnDeleteProject" class="btn danger sm" style="margin-top: 8px;">
        Delete Project Completely
      </button>
    </div>
  `;

  document.getElementById("btnRefreshTasks").onclick = () => loadTaskData();
  document.getElementById("btnDeleteProject").onclick = deleteProject;

  const runsList = document.getElementById("runsList");
  if (runs.length === 0) {
    runsList.innerHTML = `<div class="dim sm" style="padding: 12px; text-align: center;">No workflow runs found yet for this project.</div>`;
    return;
  }

  runsList.innerHTML = runs.map(r => {
    const isActive = ACTIVE_RUN_STATES.has(r.status);
    const conclusion = r.conclusion || r.status || "queued";
    const stCls = (conclusion === "success" || conclusion === "done") ? "ok" : (isActive ? "run" : (conclusion === "failure" ? "err" : "dim"));

    return `
      <div class="task-row">
        <div class="task-info">
          <div class="task-title">
            <span>${esc(r.name || r.workflow)}</span>
            <span class="status-pill status-${stCls}">${esc(conclusion)}</span>
          </div>
          <div class="task-desc">
            ${r.created_at ? new Date(r.created_at).toLocaleString() : ""} · Run #${r.run_number || r.id}
          </div>
        </div>
        <div class="task-actions">
          ${r.html_url ? `
            <a href="${r.html_url}" target="_blank" class="btn ghost sm" style="text-decoration:none;">
              Logs ↗
            </a>
          ` : ""}
          ${isActive ? `
            <button class="btn danger sm btn-cancel-run" data-run-id="${r.id}">
              Cancel
            </button>
          ` : ""}
        </div>
      </div>
    `;
  }).join("");

  runsList.querySelectorAll(".btn-cancel-run").forEach(btn => {
    btn.onclick = async () => {
      const runId = btn.dataset.runId;
      btn.disabled = true;
      btn.textContent = "Cancelling…";
      try {
        await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
          method: "POST",
          body: { run_id: runId }
        });
        toast("Run cancelled ✓");
        await loadTaskData(true);
      } catch (e) {
        toast(`Cancel failed: ${e.message}`);
        btn.disabled = false;
        btn.textContent = "Cancel";
      }
    };
  });
}

async function deleteProject() {
  const confirmed = confirm(`Are you sure you want to permanently delete project ${jobId}? This cannot be undone.`);
  if (!confirmed) return;

  const btn = document.getElementById("btnDeleteProject");
  await withButtonLoading(btn, async () => {
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
      toast("Project deleted ✓");
      location.href = "/";
    } catch (e) {
      alert(`Delete failed: ${e.message}`);
    }
  }, "Deleting project…");
}

window.addEventListener("DOMContentLoaded", initTask);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
