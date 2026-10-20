// CastCut Project Overview Page

const ACTIVE_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "analyzing", "faces_running", "scene_analyzing", "hybrid_running", "clips_exporting", "scene_exporting"
]);

let jobId = null;
let pollTimer = null;

function initProject() {
  const params = new URLSearchParams(window.location.search);
  jobId = params.get("id");
  if (!jobId) {
    location.replace("/");
    return;
  }

  requireAuth(() => {
    loadProject();
  });
}

async function loadProject(quiet = false) {
  const content = document.getElementById("projectOverviewContent");
  if (!quiet && content) {
    content.innerHTML = `
      <div class="card loading-skeleton" style="height: 100px; margin-bottom: 12px;"></div>
      <div class="card loading-skeleton" style="height: 160px;"></div>
    `;
  }

  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderProject(data);

    clearTimeout(pollTimer);
    if (data.status && ACTIVE_STATES.has(data.status.state)) {
      pollTimer = setTimeout(() => loadProject(true), 4000);
    }
  } catch (err) {
    if (content) {
      content.innerHTML = `<div class="card err">Failed to load project: ${esc(err.message)}</div>`;
    }
  }
}

function renderProject(data) {
  const content = document.getElementById("projectOverviewContent");
  if (!content) return;

  const st = data.status || {};
  const statusInfo = statusView(st.state);
  const title = st.title || st.torrent_name || jobId;
  const ct = st.content_type || "unknown";

  renderProjectNav(jobId, "overview", title);

  // Audio tracks summary
  const audioTracks = data.ingest?.audio_tracks || data.clipsManifest?.audio_tracks || st.audio_tracks || [];
  const trackLabels = audioTracks.map(t => t.label || t.language || `Track ${t.audio_index + 1}`).join(", ");

  // Export count
  const exportCount = (data.exports || []).length;

  content.innerHTML = `
    <div class="card" style="margin-bottom: 12px;">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
        <div>
          <span class="tag tag-${ct}">${ct.toUpperCase()}</span>
          <h2 style="margin: 4px 0 2px 0;">${esc(title)}</h2>
          <div class="mono dim sm">${esc(jobId)}</div>
        </div>
        <span class="status-pill status-${statusInfo.cls}">${statusInfo.label}</span>
      </div>
      ${st.message ? `<div class="dim sm" style="margin-top: 6px;">${esc(st.message)}</div>` : ""}
    </div>

    <button id="repairAnalysis" class="btn ghost">Repair analysis assets</button>
    ${(st.missing_assets || []).length ? `<p class="err">Missing assets: ${esc(st.missing_assets.join(', '))}</p>` : ''}
    <!-- Stepper -->
    <div class="card" style="margin-bottom: 12px; padding: 12px 14px;">
      <div style="font-size:12px; font-weight:600; color:#8b949e; margin-bottom:8px;">PIPELINE STATUS</div>
      <div class="stepper" style="display:flex; gap:6px;">
        <div class="step ${st.state === 'queued' || st.state === 'ingest_running' ? 'active' : (st.state && !st.state.includes('ingest_failed') ? 'done' : '')}">1. Ingest</div>
        <div class="step ${st.state === 'faces_running' || st.state === 'scene_analyzing' || st.state === 'hybrid_running' ? 'active' : (st.state === 'clips_ready' || st.state === 'scene_ready' || st.state === 'scene_awaiting_json' || st.state === 'ready' ? 'done' : '')}">2. Analysis</div>
        <div class="step ${st.state === 'clips_ready' || st.state === 'scene_ready' || st.state === 'ready' ? 'done' : ''}">3. Ready</div>
      </div>
    </div>

    <!-- Source Specs Card -->
    ${data.ingest ? `
      <div class="card" style="margin-bottom: 12px;">
        <div class="picker-title" style="margin-bottom: 8px;">🎞 Source Video Details</div>
        <div class="export-meta-grid">
          <div class="meta-item">
            <span class="meta-item-label">Resolution</span>
            <span class="meta-item-val">${data.ingest.width}×${data.ingest.height}</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">Duration</span>
            <span class="meta-item-val">${formatSec(data.ingest.duration)}</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">Framerate</span>
            <span class="meta-item-val">${data.ingest.fps || 24} fps</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">File Size</span>
            <span class="meta-item-val">${humanSize(data.ingest.size_bytes)}</span>
          </div>
        </div>
        <div style="font-size: 12px; color: #8b949e; margin-top: 6px;">
          🔊 <b>Audio Tracks (${audioTracks.length}):</b> ${audioTracks.length ? esc(trackLabels) : "No audio tracks detected"}
        </div>
      </div>
    ` : ""}

    <!-- Action Cards for the two flows -->
    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-bottom: 12px;">
      <!-- Faces Flow Card -->
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <h3 style="margin:0;">🙂 Faces Flow</h3>
          <span class="tag ${data.clipsManifest ? 'tag-anime' : 'tag-dim'}">
            ${data.clipsManifest ? `${data.clipsManifest.characters?.length || 0} characters` : 'Pending'}
          </span>
        </div>
        <p class="dim sm">
          Face detection & clustering. Select faces of interest, merge duplicate characters, choose audio track, and export joined cuts into one video.
        </p>
        <a href="/faces/?id=${encodeURIComponent(jobId)}" class="btn primary block" style="text-decoration:none; text-align:center; margin-top:12px;">
          Open Face Selector →
        </a>
      </div>

      <!-- AI Scene Flow Card -->
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <h3 style="margin:0;">🎞 AI Scene Flow</h3>
          <span class="tag ${data.sceneAgent ? 'tag-anime' : 'tag-dim'}">
            ${data.sceneAgent ? 'Prompt Ready' : 'Pending'}
          </span>
        </div>
        <p class="dim sm">
          Scene detection & AI cutting. Copy the release-linked prompt for Claude / ChatGPT, upload or paste the result JSON, and export full-quality cuts.
        </p>
        <a href="/scene/?id=${encodeURIComponent(jobId)}" class="btn primary block" style="text-decoration:none; text-align:center; margin-top:12px;">
          Open AI Scene →
        </a>
      </div>
    </div>

    <!-- Exports Link Card -->
    <div class="card" style="display:flex; justify-content:space-between; align-items:center;">
      <div>
        <h3 style="margin:0;">📦 Finished Exports</h3>
        <span class="dim sm">${exportCount} video export(s) generated</span>
      </div>
      <a href="/exports/?id=${encodeURIComponent(jobId)}" class="btn ghost" style="text-decoration:none;">
        View Exports (${exportCount}) →
      </a>
    </div>
  `;
  document.getElementById('repairAnalysis').onclick = async function() {
    this.disabled=true;
    try { await api(`/api/jobs/${encodeURIComponent(jobId)}/repair`,{method:'POST',body:{part:'scene'}}); this.textContent='Repair queued'; setTimeout(()=>loadProject(true),3000); }
    catch(e) { this.textContent=e.message; this.disabled=false; }
  };
}

window.addEventListener("DOMContentLoaded", initProject);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
