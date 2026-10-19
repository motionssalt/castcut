// CastCut Faces Flow Page

let jobId = null;
let projectData = null;
let manifest = null;
let selection = [];
let ignored = new Set();
let merged = [];
let chosenAudioTrack = null;
let chosenQuality = "normal";

function initFaces() {
  const params = new URLSearchParams(window.location.search);
  jobId = params.get("id");
  if (!jobId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    await loadFacesData();
  });
}

async function loadFacesData() {
  const content = document.getElementById("facesContent");
  if (content) {
    content.innerHTML = `
      <div class="card loading-skeleton" style="height: 120px; margin-bottom: 12px;"></div>
      <div class="card loading-skeleton" style="height: 200px;"></div>
    `;
  }

  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    const title = projectData.status?.title || projectData.status?.torrent_name || jobId;
    renderProjectNav(jobId, "faces", title);

    manifest = projectData.clipsManifest;
    if (!manifest) {
      renderWaiting();
      return;
    }

    // Initialize audio choice
    chosenAudioTrack = projectData.audioChoice?.audio_track ?? null;

    renderFacesUI();
  } catch (err) {
    if (content) {
      content.innerHTML = `<div class="card err">Failed to load face detection data: ${esc(err.message)}</div>`;
    }
  }
}

function renderWaiting() {
  const content = document.getElementById("facesContent");
  const st = projectData?.status?.state || "unknown";
  content.innerHTML = `
    <div class="card">
      <h2>🙂 Face Detection & Analysis</h2>
      <p class="dim sm">Face detection is either still running or has not completed for this project.</p>
      <div style="margin-top: 12px;">
        <span class="status-pill status-${statusView(st).cls}">${statusView(st).label}</span>
      </div>
      <div style="margin-top: 16px;">
        <a href="/project/?id=${encodeURIComponent(jobId)}" class="btn ghost sm">← Back to Overview</a>
      </div>
    </div>
  `;
}

function renderFacesUI() {
  const content = document.getElementById("facesContent");
  if (!content || !manifest) return;

  const chars = manifest.characters || [];
  const audioTracks = projectData.ingest?.audio_tracks || manifest.audio_tracks || [];

  content.innerHTML = `
    <div class="card" style="margin-bottom: 12px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div>
          <h2 style="margin:0;">🙂 Select Characters</h2>
          <span class="dim sm">${chars.length} character cluster(s) detected (${formatSec(manifest.duration || 0)} source)</span>
        </div>
        <div style="display:flex; gap:6px;">
          <button id="btnSelectAll" class="btn ghost sm">Select all</button>
          <button id="btnClearSel" class="btn ghost sm">Clear</button>
        </div>
      </div>

      <div class="gallery" id="facesGallery" style="margin-top: 14px;"></div>

      <div class="bar" id="extractBar" style="margin-top: 14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <div class="dim sm">
          <span id="selCount">0</span> selected · <span id="selDuration">0s</span> estimated
        </div>
        <div style="display:flex; gap:6px;">
          <button id="btnMerge" class="btn ghost sm">Merge selected</button>
          <button id="btnIgnore" class="btn ghost sm">Ignore</button>
        </div>
      </div>
    </div>

    <!-- Export Controls Card -->
    <div class="card">
      <h3>Export Joined Video</h3>
      <p class="dim sm">Extracts all clips of the selected characters and joins them into ONE frame-accurate video.</p>

      <!-- Issue 2: Audio Track Picker -->
      <div id="audioPickerSlot"></div>

      <!-- Issue 1: Quality Selector -->
      <div id="qualityPickerSlot"></div>

      <div class="row" style="margin-top: 10px; display:flex; gap:12px; flex-wrap:wrap;">
        <div style="flex:1; min-width: 130px;">
          <label class="lbl">Merge gap (s)</label>
          <input id="gapSec" class="field sm" type="number" step="0.1" value="2.0">
        </div>
        <div style="flex:1; min-width: 130px;">
          <label class="lbl">Padding (s)</label>
          <input id="padSec" class="field sm" type="number" step="0.1" value="0.4">
        </div>
      </div>

      <p id="exportErr" class="err" style="margin-top: 8px;"></p>

      <button id="btnExportClips" class="btn primary block" style="margin-top: 16px;" disabled>
        Export Selected Clips →
      </button>
    </div>
  `;

  // Render gallery
  renderGallery();

  // Render Audio Track Picker
  renderAudioTrackPicker(
    document.getElementById("audioPickerSlot"),
    audioTracks,
    chosenAudioTrack,
    (trackIdx) => {
      chosenAudioTrack = trackIdx;
      // Persist choice immediately
      api(`/api/jobs/${encodeURIComponent(jobId)}/audio-choice`, {
        method: "POST",
        body: { audio_track: trackIdx }
      }).catch(() => {});
    }
  );

  // Render Quality Picker
  renderQualityPicker(
    document.getElementById("qualityPickerSlot"),
    chosenQuality,
    (q) => { chosenQuality = q; }
  );

  // Bind Actions
  document.getElementById("btnSelectAll").onclick = () => {
    selection = chars.filter(c => !ignored.has(c.id)).map(c => c.id);
    updateGalleryClasses();
    updateExtractBar();
  };
  document.getElementById("btnClearSel").onclick = () => {
    selection = [];
    updateGalleryClasses();
    updateExtractBar();
  };
  document.getElementById("btnIgnore").onclick = () => {
    selection.forEach(id => ignored.add(id));
    selection = [];
    updateGalleryClasses();
    updateExtractBar();
  };
  document.getElementById("btnMerge").onclick = () => {
    if (selection.length < 2) {
      toast("Select 2+ characters to merge");
      return;
    }
    merged.push([...selection]);
    toast(`Merged: ${selection.join(" + ")}`);
  };

  document.getElementById("btnExportClips").onclick = startClipsExport;
  updateExtractBar();
}

function renderGallery() {
  const g = document.getElementById("facesGallery");
  if (!g || !manifest) return;
  const chars = manifest.characters || [];

  g.innerHTML = chars.map(c => {
    const isIgnored = ignored.has(c.id);
    const isSel = selection.includes(c.id);
    const imgSrc = c.exemplar ? `data:image/webp;base64,${c.exemplar}` : "";
    return `
      <div class="face ${isSel ? 'sel' : ''} ${isIgnored ? 'ignored' : ''}" data-id="${c.id}" style="cursor:pointer;">
        ${imgSrc ? `<img src="${imgSrc}" alt="${c.id}" loading="lazy">` : `<div style="height:100px; display:flex; align-items:center; justify-content:center; background:#21262d;">🙂</div>`}
        <div class="face-lbl">${esc(c.id)}</div>
        <div class="face-meta">${c.ranges?.length || 0} clips · ${formatSec(c.total_time || 0)}</div>
      </div>
    `;
  }).join("");

  g.querySelectorAll(".face").forEach(el => {
    el.onclick = () => {
      const id = el.dataset.id;
      if (ignored.has(id)) {
        ignored.delete(id);
      } else {
        const idx = selection.indexOf(id);
        if (idx >= 0) selection.splice(idx, 1);
        else selection.push(id);
      }
      updateGalleryClasses();
      updateExtractBar();
    };
  });
}

function updateGalleryClasses() {
  document.querySelectorAll(".face").forEach(el => {
    const id = el.dataset.id;
    el.classList.toggle("sel", selection.includes(id));
    el.classList.toggle("ignored", ignored.has(id));
  });
}

function updateExtractBar() {
  const countEl = document.getElementById("selCount");
  const durEl = document.getElementById("selDuration");
  const btnExport = document.getElementById("btnExportClips");
  if (!countEl || !durEl || !btnExport || !manifest) return;

  const chars = manifest.characters || [];
  let dur = 0;
  chars.forEach(c => {
    if (selection.includes(c.id)) {
      dur += (c.total_time || 0);
    }
  });

  countEl.textContent = selection.length;
  durEl.textContent = formatSec(dur);
  btnExport.disabled = selection.length === 0;
}

async function startClipsExport() {
  if (!selection.length) return;
  const btnExport = document.getElementById("btnExportClips");
  const errEl = document.getElementById("exportErr");
  errEl.textContent = "";

  const gap = parseFloat(document.getElementById("gapSec").value) || 2.0;
  const pad = parseFloat(document.getElementById("padSec").value) || 0.4;

  await withButtonLoading(btnExport, async () => {
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/clips-export`, {
        method: "POST",
        body: {
          order: selection,
          ignored: [...ignored],
          merge_groups: merged,
          gap_threshold: gap,
          padding: pad,
          audio_track: chosenAudioTrack,
          quality: chosenQuality,
        }
      });
      toast("Export started ✓");
      // Instant transition to exports page
      location.href = `/exports/?id=${encodeURIComponent(jobId)}`;
    } catch (e) {
      errEl.textContent = e.message || "Failed to start export";
    }
  }, "Exporting clips…");
}

window.addEventListener("DOMContentLoaded", initFaces);
