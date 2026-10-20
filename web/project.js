import { requireAuth, api, escapeHtml, formatBytes, formatSeconds, formatDate, getStatusBadge, toast, withButtonFeedback, setupPrefetching } from "./core.js";

const ACTIVE_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "analyzing", "faces_running", "scene_analyzing", "hybrid_running", "clips_exporting", "scene_exporting"
]);

let projectId = null;
let projectData = null;
let pollTimer = null;
let selectedFileIndices = new Set();

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
    await loadProject();
  });
}

function updateNavLinks() {
  const facesUrl = `/faces/?id=${encodeURIComponent(projectId)}`;
  const sceneUrl = `/scene/?id=${encodeURIComponent(projectId)}`;
  const exportsUrl = `/exports/?id=${encodeURIComponent(projectId)}`;
  const taskUrl = `/task/?id=${encodeURIComponent(projectId)}`;

  document.getElementById("btnLinkFaces").href = facesUrl;
  document.getElementById("btnLinkScene").href = sceneUrl;
  document.getElementById("btnLinkExports").href = exportsUrl;
  document.getElementById("btnHeaderTasksLink").href = taskUrl;
  document.getElementById("bottomTabFaces").href = facesUrl;
  document.getElementById("bottomTabScene").href = sceneUrl;
  document.getElementById("bottomTabExports").href = exportsUrl;
}

async function loadProject(isQuiet = false) {
  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(projectId)}`);
    renderProjectUI(projectData);

    clearTimeout(pollTimer);
    const st = projectData.status?.state || "";
    if (ACTIVE_STATES.has(st) && document.visibilityState === "visible") {
      pollTimer = setTimeout(() => loadProject(true), 3500);
    }
  } catch (err) {
    if (!isQuiet) {
      toast(`Failed to load project: ${err.message}`, "error");
    }
  }
}

function renderProjectUI(data) {
  const st = data.status || {};
  const title = st.title || st.torrent_name || projectId;
  const ct = st.content_type || "anime";
  const ctLabel = ct === "irl" ? "IRL" : "Anime";
  const ctClass = ct === "irl" ? "tag-irl" : "tag-anime";
  const badge = getStatusBadge(st.state);

  document.getElementById("headerProjectCrumb").textContent = title;

  // Header Card
  const headerCard = document.getElementById("projectHeaderCard");
  headerCard.innerHTML = `
    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 8px;">
      <div style="min-width: 0; flex: 1;">
        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
          <span class="tag-content ${ctClass}">${ctLabel}</span>
          <h1 style="font-size: 1.25rem; font-weight: 700; word-break: break-word;">${escapeHtml(title)}</h1>
        </div>
        <div class="metadata-line">
          <span class="mono">${escapeHtml(projectId)}</span>
          <span class="metadata-sep" aria-hidden="true">·</span>
          <span>Updated ${formatDate(st.updated_at)}</span>
        </div>
      </div>
      <span class="status-badge ${badge.cls}">${escapeHtml(badge.label)}</span>
    </div>
    ${st.message ? `
      <div style="font-size: 0.8125rem; color: var(--text-secondary); background-color: var(--bg-sunken); padding: 8px 12px; border-radius: var(--radius-sm); margin-top: 8px; word-break: break-word;">
        ${escapeHtml(st.message)}
      </div>
    ` : ""}
    ${(st.missing_assets || []).length ? `
      <div style="font-size: 0.8125rem; color: var(--state-error-text); background-color: var(--state-error-surface); padding: 8px 12px; border-radius: var(--radius-sm); margin-top: 8px;">
        ⚠️ Missing analysis assets: ${escapeHtml(st.missing_assets.join(", "))}. Click "Repair Analysis" below to regenerate.
      </div>
    ` : ""}
  `;

  // Stepper state
  const stepIngest = document.getElementById("stepIngest");
  const stepAnalyze = document.getElementById("stepAnalyze");
  const stepReady = document.getElementById("stepReady");
  const stateStr = st.state || "";

  stepIngest.className = "step-card";
  stepAnalyze.className = "step-card";
  stepReady.className = "step-card";

  if (stateStr === "queued" || stateStr === "ingest_running" || stateStr === "selecting" || stateStr === "awaiting_selection") {
    stepIngest.classList.add("active");
  } else if (["analyzing", "faces_running", "scene_analyzing", "hybrid_running"].includes(stateStr)) {
    stepIngest.classList.add("done");
    stepAnalyze.classList.add("active");
  } else if (["ready", "clips_ready", "scene_ready", "scene_awaiting_json"].includes(stateStr)) {
    stepIngest.classList.add("done");
    stepAnalyze.classList.add("done");
    stepReady.classList.add("done");
  }

  // Independent parallel analysis status indicators
  const parallelBox = document.getElementById("parallelStatusBox");
  const statusFacesSub = document.getElementById("statusFacesSub");
  const statusSceneSub = document.getElementById("statusSceneSub");

  if (["analyzing", "faces_running", "scene_analyzing", "hybrid_running", "clips_ready", "scene_ready", "scene_awaiting_json", "ready"].includes(stateStr)) {
    parallelBox.hidden = false;
    const hasFaces = !!data.clipsManifest;
    const hasScene = !!data.sceneAgent || stateStr === "scene_awaiting_json" || stateStr === "scene_ready";

    statusFacesSub.textContent = hasFaces ? "Ready ✓" : stateStr.includes("faces") ? "Analyzing…" : "Processing…";
    statusFacesSub.style.color = hasFaces ? "var(--state-ready)" : "var(--accent)";

    statusSceneSub.textContent = hasScene ? "Ready ✓" : stateStr.includes("scene") ? "Analyzing…" : "Processing…";
    statusSceneSub.style.color = hasScene ? "var(--state-ready)" : "var(--accent)";
  } else {
    parallelBox.hidden = true;
  }

  // Awaiting Selection Checkbox List
  const selectionCard = document.getElementById("selectionCard");
  if (stateStr === "awaiting_selection" && data.files && Array.isArray(data.files.files)) {
    selectionCard.hidden = false;
    renderSelectionList(data.files.files);
  } else {
    selectionCard.hidden = true;
  }

  // Source Video Specs Card
  const specsCard = document.getElementById("sourceSpecsCard");
  if (data.ingest) {
    specsCard.hidden = false;
    document.getElementById("metaResolution").textContent = `${data.ingest.width || 0}×${data.ingest.height || 0}`;
    document.getElementById("metaDuration").textContent = formatSeconds(data.ingest.duration);
    document.getElementById("metaFps").textContent = `${data.ingest.fps || 24} fps`;
    document.getElementById("metaSize").textContent = formatBytes(data.ingest.size_bytes);

    const audioTracks = data.ingest.audio_tracks || [];
    const trackText = audioTracks.length
      ? `🔊 Audio Tracks (${audioTracks.length}): ${audioTracks.map(t => t.label || t.language || `Track ${t.audio_index + 1}`).join(", ")}`
      : "🔊 Audio Tracks: Default track";
    document.getElementById("metaAudioTracks").textContent = trackText;
  } else {
    specsCard.hidden = true;
  }

  // Flow cards badges
  const badgeFaces = document.getElementById("badgeFacesReady");
  if (data.clipsManifest) {
    const charCount = (data.clipsManifest.characters || []).length;
    badgeFaces.textContent = `${charCount} character${charCount === 1 ? "" : "s"} ready`;
    badgeFaces.className = "status-badge status-ready";
  } else {
    badgeFaces.textContent = "Pending";
    badgeFaces.className = "status-badge status-dim";
  }

  const badgeScene = document.getElementById("badgeSceneReady");
  if (data.sceneAgent || ["scene_awaiting_json", "scene_ready", "ready"].includes(stateStr)) {
    badgeScene.textContent = "Prompt Ready";
    badgeScene.className = "status-badge status-ready";
  } else {
    badgeScene.textContent = "Pending";
    badgeScene.className = "status-badge status-dim";
  }

  // Exports Count
  const exportCount = (data.exports || []).length;
  document.getElementById("exportsCountLabel").textContent = `${exportCount} video export${exportCount === 1 ? "" : "s"} generated`;

  setupPrefetching();
}

function renderSelectionList(files) {
  const listEl = document.getElementById("projectFileList");
  const countBadge = document.getElementById("selectedCountBadge");
  const btnConfirm = document.getElementById("btnConfirmSelection");
  countBadge.textContent = `${selectedFileIndices.size} selected`;
  btnConfirm.disabled = selectedFileIndices.size === 0;

  listEl.innerHTML = files.map(f => {
    const isChecked = selectedFileIndices.has(f.index);
    return `
      <div class="file-row" data-idx="${f.index}">
        <input type="checkbox" id="ps_${f.index}" data-idx="${f.index}" ${isChecked ? 'checked' : ''} ${f.is_video ? '' : 'disabled'} style="width: 20px; height: 20px; accent-color: var(--accent); flex-shrink: 0;" />
        <label for="ps_${f.index}" class="file-name" style="cursor: pointer; ${f.is_video ? '' : 'opacity: 0.5;'}">
          ${escapeHtml(f.path)}
        </label>
        <span class="mono" style="font-size: 0.75rem; color: var(--text-muted); flex-shrink: 0;">
          ${formatBytes(f.length)}
        </span>
      </div>
    `;
  }).join("");

  listEl.querySelectorAll("input[type=checkbox]").forEach(cb => {
    cb.onchange = () => {
      const idx = Number(cb.dataset.idx);
      if (cb.checked) {
        selectedFileIndices.add(idx);
      } else {
        selectedFileIndices.delete(idx);
      }
      countBadge.textContent = `${selectedFileIndices.size} selected`;
      btnConfirm.disabled = selectedFileIndices.size === 0;
    };
  });

  const filterInput = document.getElementById("fileFilterInput");
  filterInput.oninput = () => {
    const q = filterInput.value.toLowerCase().trim();
    const rows = listEl.querySelectorAll(".file-row");
    rows.forEach(r => {
      const text = r.textContent.toLowerCase();
      r.style.display = text.includes(q) ? "flex" : "none";
    });
  };
}

function setupButtons() {
  const btnConfirm = document.getElementById("btnConfirmSelection");
  btnConfirm.onclick = async () => {
    if (selectedFileIndices.size === 0) return;
    await withButtonFeedback(btnConfirm, async () => {
      try {
        await api(`/api/jobs/${encodeURIComponent(projectId)}/selection`, {
          method: "PUT",
          body: { selected_indices: Array.from(selectedFileIndices) }
        });
        toast("Selection confirmed! Downloading source video…", "success");
        await loadProject();
      } catch (e) {
        toast(`Error: ${e.message}`, "error");
      }
    }, "Confirming…");
  };

  const btnRepair = document.getElementById("btnRepairAnalysis");
  btnRepair.onclick = async () => {
    await withButtonFeedback(btnRepair, async () => {
      try {
        await api(`/api/jobs/${encodeURIComponent(projectId)}/repair`, {
          method: "POST",
          body: { part: "all" }
        });
        toast("Repair analysis queued ✓", "success");
        await loadProject();
      } catch (e) {
        toast(`Repair error: ${e.message}`, "error");
      }
    }, "Queueing Repair…");
  };

  const btnRetry = document.getElementById("btnRetryWorkflow");
  btnRetry.onclick = async () => {
    await withButtonFeedback(btnRetry, async () => {
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(projectId)}/retry`, { method: "POST" });
        toast(`Retrying workflow: ${res.workflow || "queued"}`, "info");
        await loadProject();
      } catch (e) {
        toast(`Retry error: ${e.message}`, "error");
      }
    }, "Retrying…");
  };

  const btnCancel = document.getElementById("btnCancelWorkflow");
  btnCancel.onclick = async () => {
    if (!confirm("Cancel the running workflow run(s) for this project?")) return;
    await withButtonFeedback(btnCancel, async () => {
      try {
        await api(`/api/jobs/${encodeURIComponent(projectId)}/cancel`, { method: "POST" });
        toast("Cancellation dispatched", "info");
        await loadProject();
      } catch (e) {
        toast(`Cancel error: ${e.message}`, "error");
      }
    }, "Cancelling…");
  };

  const btnDelete = document.getElementById("btnDeleteProject");
  btnDelete.onclick = async () => {
    if (!confirm(`Delete project "${projectId}" completely?\n\nThis will remove all releases, tasks, and files.`)) return;
    await withButtonFeedback(btnDelete, async () => {
      try {
        await api(`/api/jobs/${encodeURIComponent(projectId)}`, { method: "DELETE" });
        toast("Project deleted", "info");
        location.href = "/";
      } catch (e) {
        toast(`Delete error: ${e.message}`, "error");
      }
    }, "Deleting…");
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadProject(true);
  } else {
    clearTimeout(pollTimer);
  }
});

window.addEventListener("DOMContentLoaded", init);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
