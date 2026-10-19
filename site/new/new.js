// CastCut New Project Page

const VIDEO_EXT = [".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"];

let parsedTorrent = null;
let torrentBase64 = null;
let selectedFileIndices = new Set();

function bdecode(u8) {
  let i = 0;
  const txt = (b) => new TextDecoder().decode(b);
  function parse() {
    const c = u8[i];
    if (c === 0x69) {
      i++; const e = u8.indexOf(0x65, i); if (e < 0) throw new Error("bad integer");
      const v = parseInt(txt(u8.slice(i, e)), 10); if (Number.isNaN(v)) throw new Error("bad integer");
      i = e + 1; return v;
    }
    if (c === 0x6c) { i++; const a = []; while (i < u8.length && u8[i] !== 0x65) a.push(parse()); i++; return a; }
    if (c === 0x64) { i++; const o = new Map(); while (i < u8.length && u8[i] !== 0x65) { const k = parse(); o.set(txt(k), parse()); } i++; return o; }
    if (c >= 0x30 && c <= 0x39) {
      const colon = u8.indexOf(0x3a, i); if (colon < 0) throw new Error("bad string");
      const len = parseInt(txt(u8.slice(i, colon)), 10); i = colon + 1;
      const v = u8.slice(i, i + len); if (v.length !== len) throw new Error("truncated");
      i += len; return v;
    }
    throw new Error("not a .torrent file");
  }
  return parse();
}

function parseTorrentFiles(buf) {
  const root = bdecode(new Uint8Array(buf));
  if (!(root instanceof Map)) throw new Error("not a .torrent file");
  const info = root.get("info");
  if (!(info instanceof Map)) throw new Error("torrent has no info dictionary");
  const dec = (b) => new TextDecoder().decode(b);
  const name = info.get("name") ? dec(info.get("name")) : "torrent";
  const files = [];
  const rawFiles = info.get("files");
  if (Array.isArray(rawFiles)) {
    rawFiles.forEach((f, idx) => {
      const parts = (f.get("path") || []).map(dec);
      const path = parts.join("/");
      files.push({ index: idx + 1, path, length: f.get("length") || 0 });
    });
  } else {
    files.push({ index: 1, path: name, length: info.get("length") || 0 });
  }
  files.forEach(f => {
    f.is_video = VIDEO_EXT.includes(("." + f.path.split(".").pop()).toLowerCase()) && f.length > 0;
  });
  return { name, files };
}

function bytesToBase64(bytes) {
  const u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const CHUNK_SIZE = 8192;
  let binary = "";
  for (let i = 0; i < u8.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

async function fileToBase64(file) {
  const buf = await file.arrayBuffer();
  return bytesToBase64(new Uint8Array(buf));
}

function detectLinkKind(raw) {
  const s = (raw || "").trim();
  if (!s) return null;
  if (/^magnet:\?/i.test(s)) return "magnet";
  if (/drive\.google\.com\/(?:file\/d\/|open\?id=)/i.test(s)) return "gdrive";
  if (/^https?:\/\//i.test(s)) return "direct_url";
  return null;
}

function initNew() {
  requireAuth(() => {
    const srcUrl = document.getElementById("srcUrl");
    const srcFile = document.getElementById("srcFile");
    const btnPickTorrent = document.getElementById("btnPickTorrent");
    const btnRemoveTorrent = document.getElementById("btnRemoveTorrent");
    const btnStart = document.getElementById("btnStart");

    srcUrl.oninput = () => {
      if (srcUrl.value.trim() && parsedTorrent) {
        removeTorrent();
      }
      updateStartState();
    };

    btnPickTorrent.onclick = () => srcFile.click();
    srcFile.onchange = async () => {
      const file = srcFile.files[0];
      if (!file) return;
      try {
        const buf = await file.arrayBuffer();
        parsedTorrent = parseTorrentFiles(buf);
        torrentBase64 = await fileToBase64(file);
        srcUrl.value = "";
        showTorrentSelected(file.name);
        renderTorrentFiles();
      } catch (err) {
        document.getElementById("torrentErr").textContent = `Invalid torrent: ${err.message}`;
      }
      updateStartState();
    };

    btnRemoveTorrent.onclick = removeTorrent;
    btnStart.onclick = startProject;
    updateStartState();
  });
}

function showTorrentSelected(fileName) {
  document.getElementById("btnPickTorrent").hidden = true;
  document.getElementById("torrentSelectedInfo").hidden = false;
  document.getElementById("torrentSelectedName").textContent = fileName;
  document.getElementById("newJobFiles").hidden = false;
  document.getElementById("newJobTorrentName").textContent = parsedTorrent.name;
}

function removeTorrent() {
  parsedTorrent = null;
  torrentBase64 = null;
  selectedFileIndices.clear();
  document.getElementById("srcFile").value = "";
  document.getElementById("btnPickTorrent").hidden = false;
  document.getElementById("torrentSelectedInfo").hidden = true;
  document.getElementById("newJobFiles").hidden = true;
  document.getElementById("torrentErr").textContent = "";
  updateStartState();
}

function renderTorrentFiles() {
  const listEl = document.getElementById("newJobFileList");
  if (!listEl || !parsedTorrent) return;

  const videoFiles = parsedTorrent.files.filter(f => f.is_video);
  selectedFileIndices.clear();
  if (videoFiles.length === 1) {
    selectedFileIndices.add(videoFiles[0].index);
  }

  listEl.innerHTML = parsedTorrent.files.map(f => {
    const isSel = selectedFileIndices.has(f.index);
    return `
      <label class="filelist-item ${f.is_video ? 'is-video' : 'not-video'}" style="display:flex; align-items:center; gap:8px; padding:6px 8px; border-bottom:1px solid #21262d;">
        <input type="checkbox" data-idx="${f.index}" ${isSel ? 'checked' : ''} ${f.is_video ? '' : 'disabled'}>
        <span class="file-name" style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px;">${esc(f.path)}</span>
        <span class="dim sm mono">${humanSize(f.length)}</span>
      </label>
    `;
  }).join("");

  listEl.querySelectorAll("input[type=checkbox]").forEach(cb => {
    cb.onchange = () => {
      const idx = Number(cb.dataset.idx);
      if (cb.checked) selectedFileIndices.add(idx);
      else selectedFileIndices.delete(idx);
      updateStartState();
    };
  });
}

function updateStartState() {
  const srcUrl = document.getElementById("srcUrl").value.trim();
  const btnStart = document.getElementById("btnStart");
  const kind = detectLinkKind(srcUrl);

  let valid = false;
  if (kind) {
    valid = true;
  } else if (parsedTorrent) {
    valid = true;
  }
  btnStart.disabled = !valid;
}

async function startProject() {
  const srcUrl = document.getElementById("srcUrl").value.trim();
  const titleInput = document.getElementById("projectTitle").value.trim();
  const ct = document.querySelector('input[name="contentType"]:checked')?.value || "anime";
  const btnStart = document.getElementById("btnStart");
  const errEl = document.getElementById("startErr");
  errEl.textContent = "";

  const kind = detectLinkKind(srcUrl);

  let payload = {
    title: titleInput,
    content_type: ct,
  };

  if (kind === "direct_url") {
    payload.source = { kind: "direct_url", url: srcUrl };
  } else if (kind === "gdrive") {
    payload.source = { kind: "gdrive", url: srcUrl };
  } else if (kind === "magnet") {
    payload.source = { kind: "magnet", magnet: srcUrl };
  } else if (parsedTorrent && torrentBase64) {
    payload.source = {
      kind: "torrent_file",
      torrent_b64: torrentBase64,
    };
    payload.torrent_name = parsedTorrent.name;
    payload.files = parsedTorrent.files;
    if (selectedFileIndices.size > 0) {
      payload.selected_indices = Array.from(selectedFileIndices);
    }
  } else {
    errEl.textContent = "Please provide a valid video link or .torrent file.";
    return;
  }

  await withButtonLoading(btnStart, async () => {
    try {
      const res = await api("/api/jobs", { method: "POST", body: payload });
      toast("Project created ✓");
      // Instant transition to project overview page
      location.href = `/project/?id=${encodeURIComponent(res.job_id)}`;
    } catch (e) {
      errEl.textContent = e.message || "Failed to start project";
    }
  }, "Creating project…");
}

window.addEventListener("DOMContentLoaded", initNew);
