import { requireAuth, api, escapeHtml, formatDate, getStatusBadge, toast, withButtonFeedback, setupPrefetching } from "./core.js";

const ACTIVE_RUN_STATES = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

let projectId = null;
let projectData = null;
let pollTimer = null;

function init() {
  const params = new URLSearchParams(window.location.search);
  projectId = params.get("id");
  if (!projectId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    updateNavLinks();
    setupButtons();
    setupPrefetching();
    await loadTaskData();
  });
}

function updateNavLinks() {
  document.getElementById("btnBackToProject").href = `/project/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("bottomTabFaces").href = `/faces/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("bottomTabScene").href = `/scene/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("bottomTabExports").href = `/exports/?id=${encodeURIComponent(projectId)}`;
}

function setupButtons() {
  document.getElementById("btnRefreshTasks").onclick = () => loadTaskData();
}

async function loadTaskData(isQuiet = false) {
  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(projectId)}`);
    renderTasksUI(projectData);

    clearTimeout(pollTimer);
    const runs = projectData.runs || [];
    const hasActiveRun = runs.some(r => ACTIVE_RUN_STATES.has(r.status));
    if (hasActiveRun && document.visibilityState === "visible") {
      pollTimer = setTimeout(() => loadTaskData(true), 3500);
    }
  } catch (err) {
    if (!isQuiet) toast(`Failed to load tasks: ${err.message}`, "error");
  }
}

function renderTasksUI(data) {
  const st = data.status || {};
  const runs = data.runs || [];
  const subtitle = document.getElementById("taskSubtitle");
  subtitle.textContent = `${runs.length} workflow run${runs.length === 1 ? "" : "s"} tracked`;

  // Status Banner
  const banner = document.getElementById("taskStatusBanner");
  const badge = getStatusBadge(st.state);
  banner.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
      <div>
        <h2 style="font-size: 1rem; font-weight: 700;">Job State: ${escapeHtml(badge.label)}</h2>
        <div class="metadata-line" style="margin-top: 4px;">
          <span class="mono">${escapeHtml(projectId)}</span>
          <span class="metadata-sep">·</span>
          <span>Last updated: ${formatDate(st.updated_at)}</span>
        </div>
      </div>
      <span class="status-badge ${badge.cls}">${escapeHtml(badge.label)}</span>
    </div>
    ${st.message ? `
      <div style="font-size: 0.8125rem; color: var(--text-secondary); background-color: var(--bg-sunken); padding: 8px 12px; border-radius: var(--radius-sm); margin-top: 10px;">
        ${escapeHtml(st.message)}
      </div>
    ` : ""}
    ${st.failed_step ? `
      <div style="font-size: 0.8125rem; color: var(--state-error-text); background-color: var(--state-error-surface); padding: 8px 12px; border-radius: var(--radius-sm); margin-top: 8px;">
        <b>Failed Step:</b> ${escapeHtml(st.failed_step)}
      </div>
    ` : ""}
  `;

  // Runs List
  const runsList = document.getElementById("runsList");
  if (runs.length === 0) {
    runsList.innerHTML = `<div style="font-size: 0.8125rem; color: var(--text-muted); text-align: center; padding: 16px;">No workflow runs recorded yet.</div>`;
    return;
  }

  runsList.innerHTML = runs.map(r => {
    const isSuccess = r.conclusion === "success";
    const isFailed = r.conclusion === "failure" || r.conclusion === "timed_out";
    const isCancelled = r.conclusion === "cancelled";
    const isRunning = ACTIVE_RUN_STATES.has(r.status);

    let statusLabel = r.status;
    let badgeClass = "status-dim";

    if (isRunning) {
      statusLabel = "Running…";
      badgeClass = "status-running";
    } else if (isSuccess) {
      statusLabel = "Success ✓";
      badgeClass = "status-ready";
    } else if (isFailed) {
      statusLabel = "Failed ✕";
      badgeClass = "status-error";
    } else if (isCancelled) {
      statusLabel = "Cancelled";
      badgeClass = "status-dim";
    }

    return `
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border-subtle); gap: 10px;">
        <div style="min-width: 0;">
          <div style="font-weight: 600; font-size: 0.875rem; word-break: break-word;">
            ${escapeHtml(r.name || r.workflow)}
          </div>
          <div class="metadata-line" style="margin-top: 2px;">
            <span class="mono">${escapeHtml(r.workflow)}</span>
            <span class="metadata-sep">·</span>
            <span>Run #${r.id}</span>
            <span class="metadata-sep">·</span>
            <span>${formatDate(r.created_at)}</span>
          </div>
        </div>

        <div style="display: flex; align-items: center; gap: 8px; flex-shrink: 0;">
          <span class="status-badge ${badgeClass}">${escapeHtml(statusLabel)}</span>
          ${r.html_url ? `
            <a href="${escapeHtml(r.html_url)}" class="btn btn-ghost btn-sm" target="_blank" rel="noopener noreferrer" title="View on GitHub Actions">
              View ↗
            </a>
          ` : ""}
        </div>
      </div>
    `;
  }).join("");
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadTaskData(true);
  } else {
    clearTimeout(pollTimer);
  }
});

window.addEventListener("DOMContentLoaded", init);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
