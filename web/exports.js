import { verifiedDownload, saveMp4 } from './download.js';
import { requireAuth, api, escapeHtml, formatBytes, formatSeconds, formatDate, toast, withButtonFeedback, setupPrefetching } from "./core.js";

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
    await loadExportsData();
  });
}

function updateNavLinks() {
  document.getElementById("btnBackToProject").href = `/project/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("btnGoFaces").href = `/faces/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("btnGoScene").href = `/scene/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("bottomTabFaces").href = `/faces/?id=${encodeURIComponent(projectId)}`;
  document.getElementById("bottomTabScene").href = `/scene/?id=${encodeURIComponent(projectId)}`;
}

function setupButtons() {
  const btnRefresh = document.getElementById("btnRefreshExports");
  btnRefresh.onclick = () => loadExportsData();
}

async function loadExportsData(isQuiet = false) {
  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(projectId)}`);
    renderExportsUI(projectData);

    clearTimeout(pollTimer);
    const st = projectData.status?.state || "";
    const isExporting = (st === "clips_exporting" || st === "scene_exporting");
    if (isExporting && document.visibilityState === "visible") {
      pollTimer = setTimeout(() => loadExportsData(true), 3500);
    }
  } catch (err) {
    if (!isQuiet) toast(`Failed to load exports: ${err.message}`, "error");
  }
}

function renderExportsUI(data) {
  const listEl = document.getElementById("exportsList");
  const emptyEl = document.getElementById("exportsEmpty");
  const subtitle = document.getElementById("exportsSubtitle");
  const activeBanner = document.getElementById("activeExportBanner");
  const activeMsg = document.getElementById("activeExportMsg");

  const st = data.status?.state || "";
  const isExporting = (st === "clips_exporting" || st === "scene_exporting");

  if (isExporting) {
    activeBanner.hidden = false;
    activeMsg.textContent = data.status?.message || "Encoding video cut…";
  } else {
    activeBanner.hidden = true;
  }

  const exportItems = data.exports || [];
  const assets = data.assets || data.release?.assets || [];
  subtitle.textContent = `${exportItems.length} export${exportItems.length === 1 ? "" : "s"} ready`;

  if (exportItems.length === 0 && !isExporting) {
    listEl.innerHTML = "";
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  listEl.innerHTML = exportItems.map(exp => {
    const expId = exp.export_id || exp.id || "export";
    const kind = exp.kind || (expId.includes("clips") ? "Faces Clips" : "Scene Cut");
    const title = exp.title || exp.name || kind;
    const dur = exp.total_duration ? formatSeconds(exp.total_duration) : (exp.duration ? formatSeconds(exp.duration) : "");
    const date = exp.created_at || (exp.created_at_epoch ? formatDate(new Date(exp.created_at_epoch * 1000).toISOString()) : "");
    const audioTrack = exp.audio_track !== undefined ? `Audio Track ${Number(exp.audio_track) + 1}` : "";

    // Find download url matching asset
    const matchingAsset = assets.find(a => (a.name && (a.name.includes(expId) || a.name === exp.asset_name))) || null;
    const canDownload = matchingAsset?.id && matchingAsset.name?.endsWith(".mp4") && matchingAsset.state === "uploaded";
    const fileSize = matchingAsset?.size ? formatBytes(matchingAsset.size) : (exp.size_bytes ? formatBytes(exp.size_bytes) : "");

    return `
      <div class="card card-hover" style="margin-bottom: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 8px;">
          <div style="min-width: 0;">
            <div style="font-weight: 600; font-size: 1rem; color: var(--text-primary); word-break: break-word;">
              ${escapeHtml(title)}
            </div>
            <div class="metadata-line" style="margin-top: 4px;">
              <span class="mono">${escapeHtml(expId)}</span>
              ${date ? `<span class="metadata-sep" aria-hidden="true">·</span><span>${escapeHtml(date)}</span>` : ""}
              ${dur ? `<span class="metadata-sep" aria-hidden="true">·</span><span>Duration: ${escapeHtml(dur)}</span>` : ""}
            </div>
          </div>
          <span class="status-badge status-ready">✓ Rendered</span>
        </div>

        <div class="metadata-line" style="margin-bottom: 12px; font-size: 0.8125rem;">
          ${fileSize ? `<span>${escapeHtml(fileSize)}</span>` : ""}
          ${audioTrack ? `<span class="metadata-sep" aria-hidden="true">·</span><span>${escapeHtml(audioTrack)}</span>` : ""}
        </div>

        <div style="display: flex; justify-content: space-between; align-items: center; border-top: 1px solid var(--border-subtle); padding-top: 10px;">
          <div>
            ${canDownload ? `
              <button type="button" class="btn btn-primary btn-sm btn-download-export" data-asset-id="${Number(matchingAsset.id)}">
                ⬇ Download MP4
              </button>
            ` : `
              <span style="font-size: 0.8125rem; color: var(--text-muted);">Asset processing or in release</span>
            `}
          </div>
          <button class="btn btn-ghost btn-sm btn-delete-export" data-id="${escapeHtml(expId)}" style="color: var(--state-error);">
            Delete
          </button>
        </div>
      </div>
    `;
  }).join("");

  listEl.querySelectorAll('.btn-download-export').forEach(btn => {
    btn.onclick = async () => {
      const asset = assets.find(a => Number(a.id) === Number(btn.dataset.assetId));
      btn.disabled = true;
      try {
        const bytes = await verifiedDownload(asset, (received,total) => {
          btn.textContent = `Downloading ${total ? Math.round(received/total*100) : 0}%…`;
        });
        btn.textContent = 'Verified — saving…';
        const method = await saveMp4(bytes, asset.name);
        toast(method === 'cancelled' ? 'Save cancelled' : 'MP4 verified and saved — open it directly in Files or Gallery.', 'info');
      } catch (error) { toast(error.message, 'error'); }
      finally { btn.disabled = false; btn.textContent = '⬇ Download MP4'; }
    };
  });

  listEl.querySelectorAll(".btn-delete-export").forEach(btn => {
    btn.onclick = async () => {
      const expId = btn.dataset.id;
      if (!confirm(`Delete export record "${expId}"?`)) return;
      await withButtonFeedback(btn, async () => {
        try {
          await api(`/api/jobs/${encodeURIComponent(projectId)}/exports/${encodeURIComponent(expId)}`, {
            method: "DELETE"
          });
          toast("Export deleted", "info");
          await loadExportsData();
        } catch (e) {
          toast(`Delete error: ${e.message}`, "error");
        }
      }, "Deleting…");
    };
  });
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadExportsData(true);
  } else {
    clearTimeout(pollTimer);
  }
});

window.addEventListener("DOMContentLoaded", init);
window.addEventListener("beforeunload", () => clearTimeout(pollTimer));
