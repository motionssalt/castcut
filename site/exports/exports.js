// CastCut Finished Exports & Downloads Page (Issues 1, 2, 5)

let jobId = null;
let pollTimer = null;

function initExports() {
  const params = new URLSearchParams(window.location.search);
  jobId = params.get("id");
  if (!jobId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    await loadExportsData();
  });
}

async function loadExportsData(quiet = false) {
  const content = document.getElementById("exportsContent");
  if (!quiet && content) {
    content.innerHTML = `
      <div class="card loading-skeleton" style="height: 100px; margin-bottom: 12px;"></div>
      <div class="card loading-skeleton" style="height: 100px;"></div>
    `;
  }

  try {
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    const title = data.status?.title || data.status?.torrent_name || jobId;
    renderProjectNav(jobId, "exports", title);
    renderExports(data);

    // Auto poll if an export is actively running
    const st = data.status?.state || "";
    const isExporting = (st === "clips_exporting" || st === "scene_exporting");
    clearTimeout(pollTimer);
    if (isExporting) {
      pollTimer = setTimeout(() => loadExportsData(true), 3500);
    }
  } catch (err) {
    if (content) {
      content.innerHTML = `<div class="card err">Failed to load exports: ${esc(err.message)}</div>`;
    }
  }
}

function renderExports(data) {
  const content = document.getElementById("exportsContent");
  if (!content) return;

  const exportsList = data.exports || [];
  const assets = data.assets || data.release?.assets || [];
  const st = data.status?.state || "";
  const isExporting = (st === "clips_exporting" || st === "scene_exporting");

  let html = `
    <div class="rowhead" style="margin-bottom: 12px;">
      <h2 style="margin:0;">📦 Finished Exports <span class="dim sm">(${exportsList.length})</span></h2>
      <button id="btnRefreshExports" class="btn ghost sm">↻ Refresh</button>
    </div>
  `;

  if (isExporting) {
    html += `
      <div class="card" style="border: 1px solid #6366f1; margin-bottom: 12px; background: rgba(99, 102, 241, 0.08);">
        <div style="display:flex; align-items:center; gap:8px;">
          <span class="status-pill status-run">Exporting…</span>
          <b>Pipeline is cutting and encoding your video in full quality</b>
        </div>
        <p class="dim sm" style="margin: 6px 0 0 0;">
          Applying shared anime HandBrake compression (x264 CRF 23, AAC 128k stereo). Download will appear automatically here.
        </p>
      </div>
    `;
  }

  if (exportsList.length === 0 && !isExporting) {
    html += `
      <div class="card empty">
        <p>No exports created for this project yet.</p>
        <p class="dim sm">Choose a flow below to start an export:</p>
        <div style="display:flex; justify-content:center; gap:10px; margin-top:12px;">
          <a href="/faces/?id=${encodeURIComponent(jobId)}" class="btn primary sm" style="text-decoration:none;">🙂 Faces Export</a>
          <a href="/scene/?id=${encodeURIComponent(jobId)}" class="btn primary sm" style="text-decoration:none;">🎞 AI Scene Export</a>
        </div>
      </div>
    `;
    content.innerHTML = html;
    document.getElementById("btnRefreshExports").onclick = () => loadExportsData();
    return;
  }

  html += `<div class="exports-list" style="display:flex; flex-direction:column; gap:10px;">`;

  exportsList.forEach(exp => {
    const asset = assets.find(a => a.name === exp.asset_name) || assets[0];
    const dlUrl = asset ? asset.browser_download_url : (exp.asset_name ? `https://github.com/motionssalt/castcut/releases/download/${exp.release_tag || `castcut-${jobId}`}/${exp.asset_name}` : "#");

    const isScene = exp.type === "ai" || exp.type === "scene";
    const tagClass = isScene ? "tag-anime" : "tag-irl";
    const tagLabel = isScene ? "🎞 Scene Edit" : "🙂 Character Clips";

    const sBefore = exp.size_before_human || (exp.size_before_bytes ? humanSize(exp.size_before_bytes) : null);
    const sAfter = exp.size_after_human || exp.size_human || humanSize(exp.size_bytes);
    const pct = (exp.size_before_bytes && exp.size_after_bytes) ? (100 * exp.size_after_bytes / exp.size_before_bytes).toFixed(0) : null;
    const sizeComparison = sBefore ? `${sAfter} <span class="dim" style="font-size:11px;">(was ${sBefore}, ${pct}% of raw cut)</span>` : sAfter;

    html += `
      <div class="card export-card" style="margin-bottom:0;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
          <div>
            <span class="tag ${tagClass}">${tagLabel}</span>
            <b style="font-size: 15px; margin-left: 4px;">${esc(exp.label || exp.title || exp.export_id)}</b>
          </div>
          <span class="status-pill status-ok">Ready ✓</span>
        </div>

        <!-- Issue 1 & Issue 2 Details Grid -->
        <div class="export-meta-grid" style="margin-top: 10px;">
          <div class="meta-item">
            <span class="meta-item-label">Duration & Cuts</span>
            <span class="meta-item-val">${exp.total_duration ? `${exp.total_duration.toFixed(1)}s` : "0s"} (${exp.total_segments || exp.segments || 0} cuts)</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">Export Size</span>
            <span class="meta-item-val">${sizeComparison}</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">Effective Bitrate</span>
            <span class="meta-item-val">${exp.effective_bitrate || (exp.bitrate_kbps ? `${exp.bitrate_kbps} kbps` : "Normal anime")}</span>
          </div>
          <div class="meta-item">
            <span class="meta-item-label">Audio Track</span>
            <span class="meta-item-val" title="${esc(exp.audio_track_label || 'Single AAC track')}">${esc(exp.audio_track_label ? (exp.audio_track_label.length > 25 ? exp.audio_track_label.slice(0, 25) + '…' : exp.audio_track_label) : 'AAC Stereo')}</span>
          </div>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-top: 8px;">
          <span class="dim sm mono">${esc(exp.export_id || exp.asset_name)}</span>
          <a href="${dlUrl}" class="btn primary sm" style="text-decoration:none;" download target="_blank">
            ⬇ Download Video
          </a>
        </div>
      </div>
    `;
  });

  html += `</div>`;
  content.innerHTML = html;

  document.getElementById("btnRefreshExports").onclick = () => loadExportsData();
}

window.addEventListener("DOMContentLoaded", initExports);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
