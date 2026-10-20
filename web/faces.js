import { requireAuth, api, escapeHtml, formatSeconds, toast, withButtonFeedback, setupPrefetching } from "./core.js";

let projectId = null;
let projectData = null;
let clipsManifest = null;
let characters = [];
let selectedCharIds = new Set();
let chosenAudioTrack = 0;
let pollTimer = null;

function init() {
  const params = new URLSearchParams(window.location.search);
  projectId = params.get("id");
  if (!projectId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    document.getElementById("btnBackToProject").href = `/project/?id=${encodeURIComponent(projectId)}`;
    setupExportHandlers();
    setupPrefetching();
    await loadFacesData();
  });
}

async function loadFacesData(isQuiet = false) {
  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(projectId)}`);
    clipsManifest = projectData.clipsManifest;

    if (!clipsManifest) {
      renderWaiting();
      schedulePoll();
      return;
    }

    document.getElementById("facesWaitingCard").hidden = true;
    characters = clipsManifest.characters || [];
    renderAudioPicker();
    renderFacesGrid();

    // Auto poll if an export is actively running
    const st = projectData.status?.state || "";
    if (st === "clips_exporting") {
      const pBox = document.getElementById("exportProgressBox");
      pBox.textContent = `⚡ Exporting clips: ${projectData.status.message || "encoding cuts…"}`;
      pBox.hidden = false;
      schedulePoll();
    } else {
      document.getElementById("exportProgressBox").hidden = true;
    }
  } catch (err) {
    if (!isQuiet) toast(`Failed to load faces: ${err.message}`, "error");
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (document.visibilityState === "visible") {
    pollTimer = setTimeout(() => loadFacesData(true), 3500);
  }
}

function renderWaiting() {
  const container = document.getElementById("facesContainer");
  const waitingCard = document.getElementById("facesWaitingCard");
  const badge = document.getElementById("facesWaitingBadge");

  container.innerHTML = "";
  waitingCard.hidden = false;

  const st = projectData.status?.state || "analyzing";
  badge.textContent = projectData.status?.message || "Analyzing faces…";
}

function renderAudioPicker() {
  const sel = document.getElementById("selectAudioTrack");
  const tracks = projectData.ingest?.audio_tracks || clipsManifest?.audio_tracks || [];

  // Preselect last choice (from audioChoice.json)
  const savedChoice = projectData.audioChoice?.audio_track;
  if (savedChoice !== undefined && savedChoice !== null) {
    chosenAudioTrack = Number(savedChoice);
  } else if (tracks.length > 0) {
    chosenAudioTrack = tracks[tracks.length - 1].audio_index ?? 0;
  }

  if (tracks.length === 0) {
    sel.innerHTML = `<option value="0">Default Audio Track (Stream 0)</option>`;
    return;
  }

  sel.innerHTML = tracks.map(t => {
    const idx = t.audio_index ?? 0;
    const label = t.label || t.title || t.language || `Track ${idx + 1}`;
    const channels = t.channels ? ` (${t.channels}ch)` : "";
    const isSelected = idx === chosenAudioTrack;
    return `
      <option value="${idx}" ${isSelected ? "selected" : ""}>
        Track ${idx + 1}: ${escapeHtml(label)}${channels}
      </option>
    `;
  }).join("");

  sel.onchange = async () => {
    chosenAudioTrack = Number(sel.value);
    // Persist choice immediately
    try {
      await api(`/api/jobs/${encodeURIComponent(projectId)}/audio-choice`, {
        method: "POST",
        body: { audio_track: chosenAudioTrack }
      });
      toast(`Audio Track ${chosenAudioTrack + 1} saved`);
    } catch {}
  };
}

function renderFacesGrid() {
  const container = document.getElementById("facesContainer");
  const subtitle = document.getElementById("facesSubtitle");
  subtitle.textContent = `${characters.length} character${characters.length === 1 ? "" : "s"} detected`;

  if (characters.length === 0) {
    container.innerHTML = `
      <div class="card" style="text-align: center; padding: 32px 16px;">
        <p style="color: var(--text-secondary);">No character faces detected in this video source.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="faces-grid" id="facesList">
      ${characters.map(c => {
        const isSelected = selectedCharIds.has(c.id);
        const appearances = (c.clusters || []).length || c.appearances || 0;
        const totalSec = (c.total_duration || c.screen_time) ? formatSeconds(c.total_duration || c.screen_time) : "";
        const thumbUrl = c.thumb || c.thumbnail_url || c.thumbnail || c.preview_url || "";

        return `
          <div class="face-card ${isSelected ? 'selected' : ''}" data-id="${escapeHtml(c.id)}" role="checkbox" aria-checked="${isSelected}">
            ${thumbUrl ? `
              <img src="${escapeHtml(thumbUrl)}" alt="${escapeHtml(c.name || c.id)}" class="face-thumb" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';" />
              <div class="face-thumb" style="display: none; align-items: center; justify-content: center; font-size: 1.5rem; background-color: var(--bg-sunken);">👤</div>
            ` : `
              <div class="face-thumb" style="display: flex; align-items: center; justify-content: center; font-size: 1.5rem; background-color: var(--bg-sunken);">👤</div>
            `}
            <div style="font-weight: 600; font-size: 0.875rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-top: 4px;">
              ${escapeHtml(c.name || `Character ${c.id}`)}
            </div>
            <div class="metadata-line" style="justify-content: center; margin-top: 2px;">
              <span>${appearances} cut${appearances === 1 ? "" : "s"}</span>
              ${totalSec ? `<span class="metadata-sep">·</span><span>${totalSec}</span>` : ""}
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;

  // Attach card toggle
  container.querySelectorAll(".face-card").forEach(card => {
    card.onclick = () => {
      const id = card.dataset.id;
      if (selectedCharIds.has(id)) {
        selectedCharIds.delete(id);
        card.classList.remove("selected");
        card.setAttribute("aria-checked", "false");
      } else {
        selectedCharIds.add(id);
        card.classList.add("selected");
        card.setAttribute("aria-checked", "true");
      }
      updateExportButton();
    };
  });

  updateExportButton();
}

function updateExportButton() {
  const btn = document.getElementById("btnExportClips");
  const count = selectedCharIds.size;
  btn.disabled = count === 0;
  btn.textContent = count > 0 ? `Export Joined Clips (${count} character${count === 1 ? "" : "s"})` : "Select at least 1 character to export";
}

function setupExportHandlers() {
  const btnAll = document.getElementById("btnSelectAll");
  const btnNone = document.getElementById("btnDeselectAll");
  const btnExport = document.getElementById("btnExportClips");

  btnAll.onclick = () => {
    characters.forEach(c => selectedCharIds.add(c.id));
    renderFacesGrid();
  };

  btnNone.onclick = () => {
    selectedCharIds.clear();
    renderFacesGrid();
  };

  btnExport.onclick = async () => {
    if (selectedCharIds.size === 0) return;
    const quality = document.getElementById("selectQuality").value;

    const payload = {
      order: Array.from(selectedCharIds),
      ignored: characters.filter(c => !selectedCharIds.has(c.id)).map(c => c.id),
      merge_groups: [],
      audio_track: chosenAudioTrack,
      quality: quality,
      gap_threshold: 2.0,
      padding: 0.4,
    };

    await withButtonFeedback(btnExport, async () => {
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(projectId)}/clips-export`, {
          method: "POST",
          body: payload
        });
        toast("Clips export started! Redirecting to exports…", "success");
        setTimeout(() => {
          location.href = `/exports/?id=${encodeURIComponent(projectId)}`;
        }, 1200);
      } catch (e) {
        toast(`Export failed: ${e.message}`, "error");
      }
    }, "Starting Export…");
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadFacesData(true);
  } else {
    clearTimeout(pollTimer);
  }
});

window.addEventListener("DOMContentLoaded", init);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
