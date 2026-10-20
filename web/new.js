import { requireAuth, api, escapeHtml, formatBytes, parseTorrentBytes, bufferToBase64, toast, withButtonFeedback, setupPrefetching } from "./core.js";

let selectedContentType = "anime";
let parsedTorrent = null;
let torrentB64 = null;
let selectedFileIndices = new Set();

function init() {
  requireAuth(() => {
    setupContentTypeSelector();
    setupTorrentFileInput();
    setupLinkInput();
    setupFormSubmit();
    setupPrefetching();
    validateForm();
  });
}

function setupContentTypeSelector() {
  const btnAnime = document.getElementById("btnTypeAnime");
  const btnIrl = document.getElementById("btnTypeIrl");
  const hintText = document.getElementById("typeHintText");

  btnAnime.onclick = () => {
    selectedContentType = "anime";
    btnAnime.classList.add("active");
    btnAnime.setAttribute("aria-checked", "true");
    btnIrl.classList.remove("active");
    btnIrl.setAttribute("aria-checked", "false");
    hintText.textContent = "Optimized for 2D character faces and dialogue-driven anime scene structures.";
  };

  btnIrl.onclick = () => {
    selectedContentType = "irl";
    btnIrl.classList.add("active");
    btnIrl.setAttribute("aria-checked", "true");
    btnAnime.classList.remove("active");
    btnAnime.setAttribute("aria-checked", "false");
    hintText.textContent = "Optimized for real human faces, lighting variations, and live-action dialogue scenes.";
  };
}

function detectLink(raw) {
  const s = (raw || "").trim();
  if (!s) return null;
  if (/^magnet:\?/i.test(s)) return { kind: "magnet", label: "Magnet URI detected" };
  if (/drive\.google\.com\/(?:file\/d\/|open\?id=)/i.test(s)) return { kind: "gdrive", label: "Google Drive video link detected" };
  if (/^https?:\/\/.+\.(mp4|mkv|webm|mov|avi|ts|m4v)(\?.*)?$/i.test(s)) return { kind: "direct_url", label: "Direct video file URL detected" };
  if (/^https?:\/\//i.test(s)) return { kind: "direct_url", label: "Web stream / direct video link detected" };
  return { kind: "unsupported", label: "Unsupported link format" };
}

function setupLinkInput() {
  const linkInput = document.getElementById("linkInput");
  const detectedKind = document.getElementById("linkDetectedKind");
  const linkError = document.getElementById("linkError");

  linkInput.oninput = () => {
    linkError.hidden = true;
    const val = linkInput.value.trim();
    if (!val) {
      detectedKind.textContent = "";
      validateForm();
      return;
    }

    const detected = detectLink(val);
    if (!detected || detected.kind === "unsupported") {
      detectedKind.textContent = "";
      linkError.textContent = "Please enter a valid magnet:? URI, Google Drive link, or direct video URL.";
      linkError.hidden = false;
    } else {
      detectedKind.textContent = `✓ ${detected.label}`;
      detectedKind.style.color = "var(--state-ready)";
    }
    validateForm();
  };
}

function setupTorrentFileInput() {
  const fileInput = document.getElementById("torrentFileInput");
  const btnSelect = document.getElementById("btnSelectTorrent");
  const btnClear = document.getElementById("btnClearTorrent");
  const errEl = document.getElementById("torrentError");
  const linkInput = document.getElementById("linkInput");
  const linkNotice = document.getElementById("linkDisabledNotice");

  btnSelect.onclick = () => fileInput.click();

  fileInput.onchange = async () => {
    errEl.hidden = true;
    const file = fileInput.files[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".torrent")) {
      errEl.textContent = "Please select a valid .torrent file.";
      errEl.hidden = false;
      return;
    }

    try {
      const buf = await file.arrayBuffer();
      parsedTorrent = parseTorrentBytes(new Uint8Array(buf));
      torrentB64 = bufferToBase64(new Uint8Array(buf));

      // Disable link input
      linkInput.value = "";
      linkInput.disabled = true;
      linkNotice.hidden = false;

      renderLoadedTorrent(file.name);
    } catch (e) {
      parsedTorrent = null;
      torrentB64 = null;
      errEl.textContent = `Could not parse torrent: ${e.message}`;
      errEl.hidden = false;
    }
    validateForm();
  };

  btnClear.onclick = () => {
    parsedTorrent = null;
    torrentB64 = null;
    selectedFileIndices.clear();
    fileInput.value = "";

    document.getElementById("torrentPickerEmpty").hidden = false;
    document.getElementById("torrentPickerLoaded").hidden = true;
    errEl.hidden = true;

    // Re-enable link input
    linkInput.disabled = false;
    linkNotice.hidden = true;
    validateForm();
  };
}

function renderLoadedTorrent(fileName) {
  document.getElementById("torrentPickerEmpty").hidden = true;
  document.getElementById("torrentPickerLoaded").hidden = false;
  document.getElementById("torrentLoadedName").textContent = parsedTorrent.name || fileName;

  const totalBytes = parsedTorrent.files.reduce((a, f) => a + f.length, 0);
  const videoFiles = parsedTorrent.files.filter(f => f.is_video);

  document.getElementById("torrentLoadedMeta").innerHTML = `
    <span>${formatBytes(totalBytes)}</span>
    <span class="metadata-sep">·</span>
    <span>${parsedTorrent.files.length} file${parsedTorrent.files.length === 1 ? "" : "s"}</span>
    <span class="metadata-sep">·</span>
    <span style="color: var(--accent);">${videoFiles.length} video candidate${videoFiles.length === 1 ? "" : "s"}</span>
  `;

  // Torrent file selection: Nothing is preselected by default (or only the file when there is just one)
  selectedFileIndices.clear();
  if (videoFiles.length === 1) {
    selectedFileIndices.add(videoFiles[0].index);
  }

  const filesBox = document.getElementById("torrentFilesBox");
  filesBox.hidden = false;
  renderTorrentFileList(parsedTorrent.files);
  setupTorrentFilter();
}

function renderTorrentFileList(files) {
  const listEl = document.getElementById("torrentFilesList");
  const countBadge = document.getElementById("selectedCountBadge");
  countBadge.textContent = `${selectedFileIndices.size} selected`;

  listEl.innerHTML = files.map(f => {
    const isChecked = selectedFileIndices.has(f.index);
    return `
      <div class="file-row" data-idx="${f.index}" data-video="${f.is_video ? '1' : '0'}">
        <input type="checkbox" id="tf_${f.index}" data-idx="${f.index}" ${isChecked ? 'checked' : ''} ${f.is_video ? '' : 'disabled'} style="width: 20px; height: 20px; accent-color: var(--accent); flex-shrink: 0;" />
        <label for="tf_${f.index}" class="file-name" style="cursor: pointer; ${f.is_video ? '' : 'opacity: 0.5;'}">
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
      validateForm();
    };
  });
}

function setupTorrentFilter() {
  const filterInput = document.getElementById("torrentFileFilter");
  if (!filterInput) return;
  filterInput.oninput = () => {
    const q = filterInput.value.toLowerCase().trim();
    if (!parsedTorrent) return;
    const filtered = q ? parsedTorrent.files.filter(f => f.path.toLowerCase().includes(q)) : parsedTorrent.files;
    renderTorrentFileList(filtered);
  };
}

function validateForm() {
  const btnSubmit = document.getElementById("btnSubmitProject");
  const linkVal = document.getElementById("linkInput").value.trim();

  let valid = false;
  if (parsedTorrent && torrentB64) {
    valid = true;
  } else if (linkVal) {
    const detected = detectLink(linkVal);
    if (detected && detected.kind !== "unsupported") {
      valid = true;
    }
  }

  btnSubmit.disabled = !valid;
}

function setupFormSubmit() {
  const form = document.getElementById("newProjectForm");
  const btnSubmit = document.getElementById("btnSubmitProject");
  const titleInput = document.getElementById("projectTitleInput");
  const linkInput = document.getElementById("linkInput");

  form.onsubmit = async (e) => {
    e.preventDefault();
    const userTitle = titleInput.value.trim();
    const linkVal = linkInput.value.trim();

    const payload = {
      title: userTitle,
      content_type: selectedContentType,
    };

    if (parsedTorrent && torrentB64) {
      payload.source = {
        kind: "torrent_file",
        torrent_b64: torrentB64,
      };
      payload.torrent_name = parsedTorrent.name;
      payload.files = parsedTorrent.files;
      if (selectedFileIndices.size > 0) {
        payload.selected_indices = Array.from(selectedFileIndices);
      }
    } else if (linkVal) {
      const detected = detectLink(linkVal);
      if (!detected || detected.kind === "unsupported") {
        toast("Please enter a supported video link or magnet.", "error");
        return;
      }
      if (detected.kind === "magnet") {
        payload.source = { kind: "magnet", magnet: linkVal };
      } else if (detected.kind === "gdrive") {
        payload.source = { kind: "gdrive", url: linkVal };
      } else {
        payload.source = { kind: "direct_url", url: linkVal };
      }
    } else {
      toast("Please provide a source video link or torrent file.", "error");
      return;
    }

    await withButtonFeedback(btnSubmit, async () => {
      const errEl = document.getElementById("projectSubmitError");
      if (errEl) errEl.hidden = true;
      try {
        const res = await api("/api/jobs", { method: "POST", body: payload });
        toast("Project created successfully!", "success");
        // Instant navigation to project overview
        location.href = `/project/?id=${encodeURIComponent(res.job_id)}`;
      } catch (err) {
        if (errEl) {
          errEl.textContent = `Error starting project: ${err.message}`;
          errEl.hidden = false;
        }
        toast(`Failed to start project: ${err.message}`, "error", 7000);
      }
    }, "Starting Project…");
  };
}

window.addEventListener("DOMContentLoaded", init);
