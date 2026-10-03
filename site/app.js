// CastCut single-page app. Mobile-first, owner-PAT gated, hybrid project model.
const API = "/api";
const $ = (id) => document.getElementById(id);
const show = (id, on = true) => { const el = $(id); if (el) el.hidden = !on; };
const VIDEO_EXT = [".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts"];

const ACTIVE_STATES = new Set([
  "created", "queued", "queued_for_ingest", "selecting", "ingest_running",
  "faces_running", "scene_analyzing", "hybrid_analyzing", "clips_exporting", "scene_exporting"
]);
const BUSY_STATES = ACTIVE_STATES;
const FINISHED_STATES = new Set(["clips_ready", "scene_ready", "hybrid_ready", "done"]);
const FAILED_RE = /failed/;

const state = {
  pat: null, user: null, job: null, jobData: null,
  poll: null, pollMs: 4000,
  manifest: null, selection: [], ignored: new Set(), merged: [],
  newJobTorrent: null,
  selChecked: new Set(),
  currentTab: "overview",
  tickerTimer: null,
};

// ---------- status presentation ----------
function statusView(s) {
  const st = String(s?.state || "");
  const m = {
    queued: ["Queued", "queued"], queued_for_ingest: ["Queued", "queued"], created: ["Queued", "queued"],
    selecting: ["Listing files", "running"], ingest_running: ["Downloading source", "running"],
    awaiting_selection: ["Pick files", "await"],
    ingested: ["Source ready", "ok"],
    faces_running: ["Analyzing faces", "running"], faces_ready: ["Gallery ready", "ok"],
    hybrid_analyzing: ["Hybrid analysis", "running"], hybrid_ready: ["Analyzed", "ok"],
    clips_exporting: ["Exporting clips", "running"], clips_ready: ["Clips done", "done"],
    scene_analyzing: ["Analyzing source", "running"], scene_awaiting_json: ["Paste JSON", "await"],
    scene_exporting: ["Exporting scene", "running"], scene_ready: ["Scene done", "done"],
    ready: ["Ready", "ok"], done: ["Done", "done"],
    failed: ["Failed", "failed"], cancelled: ["Cancelled", "cancelled"],
  };
  if (m[st]) return m[st];
  if (FAILED_RE.test(st)) return ["Failed", "failed"];
  if (st) return [st.replace(/_/g, " "), "queued"];
  return ["Unknown", "queued"];
}

// ---------- helpers ----------
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 2200);
}
function confirmBox(text, yesLabel = "Delete") {
  return new Promise((resolve) => {
    $("confirmText").textContent = text;
    $("confirmYes").textContent = yesLabel;
    show("confirmModal", true);
    $("confirmYes").onclick = () => { show("confirmModal", false); resolve(true); };
    $("confirmNo").onclick = () => { show("confirmModal", false); resolve(false); };
  });
}
function humanSize(n) {
  n = Number(n) || 0; const u = ["B", "KB", "MB", "GB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n >= 100 ? Math.round(n) : n.toFixed(1)} ${u[i]}`;
}
function formatSec(s) {
  s = Math.max(0, Math.floor(s || 0));
  const m = Math.floor(s / 60); const sec = s % 60;
  return m > 0 ? `${m}m ${sec < 10 ? "0" : ""}${sec}s` : `${sec}s`;
}
async function api(path, { method = "GET", body = null } = {}) {
  const headers = { "x-castcut-pat": state.pat || "" };
  if (body) headers["content-type"] = "application/json";
  const r = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (r.status === 401) { clearPat(); showGate("Session expired — paste the PAT again."); throw new Error("unauthorized"); }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}
async function withButtonLoading(btn, fn, loadingText) {
  if (!btn) return fn();
  const origText = btn.textContent;
  btn.classList.add("loading");
  btn.disabled = true;
  if (loadingText) btn.textContent = loadingText;
  try {
    return await fn();
  } finally {
    btn.classList.remove("loading");
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ---------- client-side .torrent parser (bencode, mirrors pipeline/torrents.py) ----------
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
  files.forEach(f => { f.is_video = VIDEO_EXT.includes(("." + f.path.split(".").pop()).toLowerCase()) && f.length > 0; });
  return { name, files };
}

// ---------- gate ----------
function savePat(p) { localStorage.setItem("castcut.pat", p); }
function loadPat() { return localStorage.getItem("castcut.pat"); }
function clearPat() { localStorage.removeItem("castcut.pat"); }

async function tryGate() {
  const pat = loadPat();
  if (!pat) { showGate(); return; }
  state.pat = pat;
  try {
    const who = await api("/whoami");
    state.user = who.login;
    $("whoami").textContent = "· " + who.login;
    $("btnLogout").hidden = false;
    show("gate", false); show("home", true); show("job", false);
    refreshJobs();
  } catch (e) { if (e.message !== "unauthorized") { clearPat(); showGate(e.message); } }
}
function showGate(msg = "") { show("gate", true); show("home", false); show("job", false); $("gateErr").textContent = msg; }
$("btnLogin").onclick = async () => {
  const pat = $("patInput").value.trim(); if (!pat) return;
  state.pat = pat; savePat(pat); await tryGate();
};
$("btnLogout").onclick = () => { clearPat(); location.reload(); };

// ---------- home: new project ----------
let srcKind = "direct_url";
$("srcKindRow").addEventListener("click", (e) => {
  const b = e.target.closest(".pill"); if (!b) return;
  srcKind = b.dataset.kind;
  document.querySelectorAll("#srcKindRow .pill").forEach(p => p.classList.toggle("sel", p === b));
  show("srcUrlWrap", srcKind !== "torrent_file");
  show("srcFileWrap", srcKind === "torrent_file");
  $("srcUrl").placeholder = srcKind === "magnet" ? "magnet:?xt=urn:btih:…" : srcKind === "gdrive" ? "https://drive.google.com/…" : "https://…/video.mp4";
});
$("btnPickTorrent").onclick = () => $("srcFile").click();
$("srcFile").onchange = async () => {
  $("torrentErr").textContent = ""; show("newJobFiles", false); state.newJobTorrent = null;
  const file = $("srcFile").files[0]; if (!file) return;
  if (!file.size || file.size > 1024 * 1024) { $("torrentErr").textContent = "The .torrent must be 1 byte – 1 MB."; return; }
  try {
    const t = parseTorrentFiles(await file.arrayBuffer());
    t.raw = file;
    state.newJobTorrent = t;
    renderNewJobFiles(t);
    $("btnPickTorrent").textContent = `📦 ${t.name}`;
  } catch (e) { $("torrentErr").textContent = "Could not parse that .torrent: " + e.message; }
};
function renderNewJobFiles(t) {
  const list = $("newJobFileList"); list.innerHTML = "";
  const videos = t.files.filter(f => f.is_video);
  const checked = new Set(videos.map(f => f.index));
  t.files.forEach(f => {
    const row = document.createElement("div");
    row.className = "frow" + (f.is_video ? " sel" : " novideo");
    row.innerHTML = `<span class="chk">${f.is_video ? "✓" : ""}</span>
      <span class="fi"><b>${esc(f.path)}</b><small>${humanSize(f.length)}</small></span>
      ${f.is_video ? '<span class="vbadge">VIDEO</span>' : ""}`;
    if (f.is_video) row.onclick = () => {
      if (checked.has(f.index)) { checked.delete(f.index); row.classList.remove("sel"); row.querySelector(".chk").textContent = ""; }
      else { checked.add(f.index); row.classList.add("sel"); row.querySelector(".chk").textContent = "✓"; }
      info();
    };
    list.appendChild(row);
  });
  const info = () => {
    const selBytes = t.files.filter(f => checked.has(f.index)).reduce((a, f) => a + f.length, 0);
    $("newJobSelInfo").textContent = `${checked.size} of ${videos.length} video file(s) selected · ${humanSize(selBytes)}`;
    t.checked = checked;
  };
  if (!videos.length) $("newJobSelInfo").textContent = "⚠ no video files in this torrent";
  else info();
  show("newJobFiles", true);
}

$("btnIngest").onclick = async () => {
  $("homeErr").textContent = "";
  await withButtonLoading($("btnIngest"), async () => {
    const body = { source: { kind: srcKind }, title: $("jobTitle").value.trim() };
    if (srcKind === "direct_url" || srcKind === "gdrive") {
      body.source.url = $("srcUrl").value.trim();
      if (!body.source.url) throw new Error("paste a URL first");
    }
    if (srcKind === "magnet") {
      body.source.magnet = $("srcUrl").value.trim();
      if (!body.source.magnet.startsWith("magnet:?")) throw new Error("that doesn't look like a magnet:? link");
    }
    if (srcKind === "torrent_file") {
      const t = state.newJobTorrent;
      if (!t) throw new Error("choose a .torrent file first");
      const checked = [...(t.checked || [])];
      if (!checked.length) throw new Error("tick at least one video file");
      const buf = await t.raw.arrayBuffer();
      body.source.torrent_b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
      body.torrent_name = t.name;
      body.files = t.files;
      body.selected_indices = checked;
    }
    const r = await api("/jobs", { method: "POST", body });
    $("srcUrl").value = ""; $("jobTitle").value = ""; $("srcFile").value = "";
    state.newJobTorrent = null; show("newJobFiles", false); $("btnPickTorrent").textContent = "Choose .torrent file";
    openJob(r.job_id);
  }, "Starting…").catch(e => { $("homeErr").textContent = e.message; });
};

// ---------- home: project list ----------
async function refreshJobs() {
  try {
    const r = await api("/jobs");
    const list = $("jobList"); list.innerHTML = "";
    const jobs = r.jobs || [];
    show("jobListEmpty", !jobs.length);
    let clearable = 0;
    jobs.forEach(j => {
      const [label, cls] = statusView(j);
      if (FINISHED_STATES.has(j.state) || FAILED_RE.test(j.state) || j.state === "cancelled") clearable++;
      const div = document.createElement("div");
      div.className = `jobcard ${cls === "failed" ? "failed" : cls === "cancelled" ? "cancelled" : ""}`;
      div.innerHTML = `<span class="badge ${cls}">${label}</span>
        <span class="jt"><b>${esc(j.title || j.job_id)}</b><small>${esc(j.message || j.job_id)}</small></span>
        <button class="btn ghost sm" data-del title="Delete project">🗑</button>`;
      div.onclick = (e) => { if (e.target.closest("[data-del]")) return; openJob(j.job_id); };
      div.querySelector("[data-del]").onclick = () => deleteJob(j.job_id, j.title, true);
      list.appendChild(div);
    });
    const cb = $("btnCleanup");
    cb.hidden = clearable === 0;
    cb.textContent = `Clear finished (${clearable})`;
  } catch (e) { console.warn(e); }
}

async function deleteJob(id, title, fromList) {
  const ok = await confirmBox(`Delete “${title || id}”? This cancels running workflows, removes all releases and tags, and cleans up project files.`);
  if (!ok) return;
  try {
    await api(`/jobs/${id}`, { method: "DELETE" });
    toast("Project deleted");
    if (!fromList) { stopPoll(); show("job", false); show("home", true); }
    refreshJobs();
  } catch (e) { toast("Delete failed: " + e.message); }
}

$("btnCleanup").onclick = async () => {
  const r = await api("/jobs");
  const doomed = (r.jobs || []).filter(j => FINISHED_STATES.has(j.state) || FAILED_RE.test(j.state) || j.state === "cancelled");
  if (!doomed.length) return;
  const ok = await confirmBox(`Delete ${doomed.length} finished/failed project(s) and all their files?`);
  if (!ok) return;
  for (const j of doomed) { try { await api(`/jobs/${j.job_id}`, { method: "DELETE" }); } catch { /* ignore */ } }
  toast("Cleared"); refreshJobs();
};

// ---------- project page ----------
function stopPoll() {
  if (state.poll) { clearInterval(state.poll); state.poll = null; }
  if (state.tickerTimer) { clearInterval(state.tickerTimer); state.tickerTimer = null; }
}

async function openJob(id) {
  stopPoll();
  state.job = id; state.manifest = null; state.selection = []; state.ignored = new Set(); state.merged = [];
  state.selChecked = new Set(); state.currentTab = "overview";
  show("home", false); show("job", true);
  ["selCard", "jobFailed", "galleryWrap", "sceneAgent", "hybridActionCard"].forEach(x => show(x, false));
  $("jobTitleH").textContent = id; $("jobMsg").textContent = "Loading…";
  $("jobBadge").className = "badge queued"; $("jobBadge").textContent = "…";
  $("jobMeta").textContent = ""; $("jobErr").textContent = "";
  setupTabs();
  await tickJob();
  schedulePoll();
}

function schedulePoll() {
  stopPoll();
  startLiveTicker();
  const st = state.jobData?.status?.state || "";
  const ms = ACTIVE_STATES.has(st) ? 3500 : 12000;
  state.poll = setInterval(async () => { await tickJob(); schedulePoll(); }, ms);
}

$("btnBack").onclick = () => { stopPoll(); show("job", false); show("home", true); refreshJobs(); };

async function tickJob() {
  try {
    const r = await api(`/jobs/${state.job}`);
    state.jobData = r;
    renderJob(r);
  } catch (e) { if (e.message !== "unauthorized") $("jobMsg").textContent = "⚠ " + e.message; }
}

// ----- tabs -----
function setupTabs() {
  document.querySelectorAll("#projectTabs .tabbtn").forEach(btn => {
    btn.onclick = () => switchTab(btn.dataset.tab);
  });
}
function switchTab(tabId) {
  state.currentTab = tabId;
  document.querySelectorAll("#projectTabs .tabbtn").forEach(btn => {
    btn.classList.toggle("sel", btn.dataset.tab === tabId);
  });
  ["tabOverview", "tabClips", "tabScene", "tabExports"].forEach(id => {
    const panel = $(id);
    if (panel) panel.hidden = (id.toLowerCase() !== `tab${tabId}`);
  });
}

// ----- stepper stage computation -----
function getStage(st) {
  if (!st || ["created", "queued", "queued_for_ingest", "selecting", "ingest_running", "awaiting_selection"].includes(st)) return "ingest";
  if (["faces_running", "scene_analyzing", "hybrid_analyzing"].includes(st)) return "analysis";
  if (["ingested", "faces_ready", "scene_awaiting_json", "hybrid_ready", "ready"].includes(st)) return "ready";
  if (["clips_exporting", "scene_exporting"].includes(st)) return "export";
  if (["clips_ready", "scene_ready", "done"].includes(st)) return "done";
  return "ready";
}

function updateStepper(st) {
  const currentStage = getStage(st);
  const stages = ["ingest", "analysis", "ready", "export", "done"];
  const curIdx = stages.indexOf(currentStage);

  show("liveProgress", true);
  document.querySelectorAll(".stepper .step").forEach(stepEl => {
    const sName = stepEl.dataset.step;
    const sIdx = stages.indexOf(sName);
    stepEl.classList.remove("active", "done");
    if (sIdx < curIdx) stepEl.classList.add("done");
    else if (sIdx === curIdx) stepEl.classList.add("active");
  });
  document.querySelectorAll(".stepper .step-line").forEach((lineEl, idx) => {
    lineEl.classList.toggle("done", idx < curIdx);
  });
}

function startLiveTicker() {
  if (state.tickerTimer) clearInterval(state.tickerTimer);
  const update = () => {
    const s = state.jobData?.status || {};
    const st = s.state || "";
    const ticker = $("liveTicker");
    const link = $("liveRunLink");

    const url = s.run_url || s.workflow_run_url;
    if (url) { link.href = url; link.hidden = false; } else link.hidden = true;

    if (ACTIVE_STATES.has(st)) {
      const epoch = s.updated_at_epoch || s.created_at_epoch || Math.floor(Date.now() / 1000);
      const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
      ticker.textContent = `⏱ Active step: ${formatSec(elapsed)}`;
    } else {
      ticker.textContent = s.updated_at ? `Updated: ${new Date(s.updated_at).toLocaleTimeString()}` : "";
    }
  };
  update();
  state.tickerTimer = setInterval(update, 1000);
}

// ----- main project render -----
function renderJob(r) {
  const s = r.status || {};
  const st = s.state || "";
  const [label, cls] = statusView(s);

  $("jobTitleH").textContent = s.title || r.project_id || r.job_id;
  const badge = $("jobBadge"); badge.className = "badge " + cls; badge.textContent = label;
  $("jobMsg").textContent = s.message || "";

  const ing = r.ingest;
  $("jobMeta").textContent = ing
    ? `source ${(ing.duration || 0).toFixed(1)}s · ${ing.width}×${ing.height} · ${humanSize(ing.size_bytes)}${(ing.sources || []).length > 1 ? ` · ${ing.sources.length} files` : ""}`
    : "";
  if (ing && ing.video_in_release === false) {
    $("jobMeta").textContent += " · oversized video: refetched on demand";
  }

  updateStepper(st);

  // failed / cancelled panel
  const isFailed = cls === "failed", isCancelled = cls === "cancelled";
  show("jobFailed", isFailed);
  if (isFailed) {
    $("jobFailReason").innerHTML = `<b>Failed${s.failed_step ? " at: " + esc(s.failed_step) : ""}.</b> ${esc(s.message || "")}`;
    const url = s.run_url || s.workflow_run_url;
    const link = $("jobRunLink");
    if (url) { link.href = url; link.hidden = false; } else link.hidden = true;
  }

  // action buttons
  show("btnCancel", BUSY_STATES.has(st));
  show("btnRetry", isFailed || isCancelled);

  // torrent selection card
  const filesDoc = r.files;
  const showSel = !!filesDoc && (st === "awaiting_selection" || st === "selecting");
  show("selCard", showSel);
  if (showSel) renderSelection(filesDoc, st);

  // Tabs visibility: available once ingested or any subsequent step
  const readyForTabs = !["created", "queued", "queued_for_ingest", "selecting", "ingest_running", "awaiting_selection"].includes(st);
  show("projectTabs", readyForTabs);

  if (readyForTabs) {
    renderOverview(r);
    renderClipsTab(r);
    renderSceneTab(r);
    renderExportsTab(r);
    switchTab(state.currentTab);
  }
}

// ----- Tab 1: Overview -----
function renderOverview(r) {
  const ing = r.ingest;
  const grid = $("sourceDetails");
  if (!ing) {
    grid.innerHTML = `<div class="detail-item"><span class="detail-k">Status</span><span class="detail-v">Awaiting source ingestion</span></div>`;
    show("hybridActionCard", false);
    return;
  }
  grid.innerHTML = `
    <div class="detail-item"><span class="detail-k">Duration</span><span class="detail-v">${(ing.duration || 0).toFixed(1)}s</span></div>
    <div class="detail-item"><span class="detail-k">Resolution</span><span class="detail-v">${ing.width || 0} × ${ing.height || 0}</span></div>
    <div class="detail-item"><span class="detail-k">Size</span><span class="detail-v">${humanSize(ing.size_bytes)}</span></div>
    <div class="detail-item"><span class="detail-k">Source Storage</span><span class="detail-v">${ing.video_in_release === false ? "Refetched on demand" : "Release asset"}</span></div>
  `;
  // Show hybrid action card if neither faces nor scene have been analyzed
  const hasFaces = !!r.clipsManifest;
  const hasScene = !!r.sceneAgent;
  show("hybridActionCard", (!hasFaces || !hasScene));
}

$("btnHybridAnalyze").onclick = async () => {
  await withButtonLoading($("btnHybridAnalyze"), async () => {
    await api(`/jobs/${state.job}/hybrid-analyze`, { method: "POST", body: { sample_fps: 1.0 } });
    toast("Hybrid analysis dispatched ⚡");
    await tickJob(); schedulePoll();
  }, "Dispatching…").catch(e => { $("jobErr").textContent = e.message; });
};

// ----- Tab 2: Clips -----
function renderClipsTab(r) {
  if (r.clipsManifest && !state.manifest) {
    state.manifest = r.clipsManifest;
    renderGallery();
    show("facesStart", false);
    show("galleryWrap", true);
  } else if (!r.clipsManifest) {
    show("facesStart", true);
    show("galleryWrap", false);
  }
}

$("btnFaces").onclick = async () => {
  await withButtonLoading($("btnFaces"), async () => {
    await api(`/jobs/${state.job}/faces`, { method: "POST", body: { sample_fps: parseFloat($("sampleFps").value) || 1.0 } });
    toast("Face analysis started");
    await tickJob(); schedulePoll();
  }, "Analyzing…").catch(e => { $("jobErr").textContent = e.message; });
};

function renderGallery() {
  const chars = state.manifest.characters || [];
  const g = $("gallery"); g.innerHTML = "";
  chars.forEach(c => {
    const d = document.createElement("div"); d.className = "face"; d.dataset.id = c.id;
    d.innerHTML = `<img loading="lazy" src="${c.thumb || ""}" alt=""><small>${c.appearances}× · ${c.screen_time}s</small>`;
    d.onclick = () => toggleFace(c.id, d);
    g.appendChild(d);
  });
  updateExtractBar();
}

function toggleFace(id, el) {
  if (state.ignored.has(id)) return;
  const idx = state.selection.indexOf(id);
  if (idx >= 0) state.selection.splice(idx, 1); else state.selection.push(id);
  el.classList.toggle("sel");
  const ord = el.querySelector(".ord"); if (ord) ord.remove();
  document.querySelectorAll(".face.sel").forEach(f => {
    const p = state.selection.indexOf(f.dataset.id);
    let o = f.querySelector(".ord");
    if (p >= 0) { if (!o) { o = document.createElement("span"); o.className = "ord"; f.appendChild(o); } o.textContent = p + 1; }
  });
  updateExtractBar();
}

function updateExtractBar() {
  $("extractInfo").textContent = `${state.selection.length} selected`;
  $("btnExportClips").disabled = !state.selection.length;
}

$("btnIgnore").onclick = () => {
  if (!state.selection.length) return;
  state.selection.forEach(id => state.ignored.add(id));
  state.selection = [];
  document.querySelectorAll(".face").forEach(f => {
    if (state.ignored.has(f.dataset.id)) { f.classList.add("ignored"); f.classList.remove("sel"); }
  });
  updateExtractBar();
};
$("btnMerge").onclick = () => {
  if (state.selection.length < 2) { toast("Select 2+ faces to merge"); return; }
  state.merged.push([...state.selection]);
  toast("Merged: " + state.selection.join(" + "));
};
$("btnExportClips").onclick = async () => {
  if (!state.selection.length) return;
  await withButtonLoading($("btnExportClips"), async () => {
    const r = await api(`/jobs/${state.job}/clips-export`, {
      method: "POST",
      body: {
        order: state.selection, ignored: [...state.ignored], merge_groups: state.merged,
        gap_threshold: parseFloat($("gapSec").value) || 2, padding: parseFloat($("padSec").value) || 0.4,
      },
    });
    toast("Clips export started");
    await tickJob(); schedulePoll();
    switchTab("exports");
  }, "Exporting…").catch(e => { $("jobErr").textContent = e.message; });
};

// ----- Tab 3: Scene -----
function renderSceneTab(r) {
  if (r.sceneAgent) {
    show("sceneStart", false);
    show("sceneAgent", true);
  } else {
    show("sceneStart", true);
    show("sceneAgent", false);
  }
}

$("btnSceneAnalyze").onclick = async () => {
  await withButtonLoading($("btnSceneAnalyze"), async () => {
    await api(`/jobs/${state.job}/scene-analyze`, { method: "POST", body: { request: $("sceneReq").value.trim() } });
    toast("Scene analysis started");
    await tickJob(); schedulePoll();
  }, "Analyzing…").catch(e => { $("jobErr").textContent = e.message; });
};

$("btnCopyPrompt").onclick = async () => {
  const text = state.jobData?.sceneAgent || "";
  if (!text) { toast("Prompt not ready yet"); return; }
  try { await navigator.clipboard.writeText(text); } catch {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Prompt copied ✓");
};

$("btnScenePush").onclick = async () => {
  $("validationOut").textContent = "";
  const raw = $("prodJson").value.trim();
  if (!raw) { $("validationOut").textContent = "Paste the production JSON first."; return; }
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { $("validationOut").textContent = "Invalid JSON: " + e.message; return; }
  if (!Array.isArray(parsed.segments) || !parsed.segments.length) {
    $("validationOut").textContent = 'The JSON needs a non-empty "segments" array.'; return;
  }
  await withButtonLoading($("btnScenePush"), async () => {
    await api(`/jobs/${state.job}/scene-export`, { method: "POST", body: { production: parsed } });
    toast("Scene export started");
    await tickJob(); schedulePoll();
    switchTab("exports");
  }, "Exporting…").catch(e => { $("validationOut").textContent = e.message; });
};

// ----- Tab 4: Exports & Downloads -----
function renderExportsTab(r) {
  const exportsList = r.exports || [];
  const assets = r.assets || r.release?.assets || [];
  const count = exportsList.length || assets.length;

  const badge = $("exportsBadge");
  badge.textContent = count;
  badge.hidden = count === 0;
  $("exportsSummary").textContent = count > 0 ? `${count} file(s)` : "";

  const list = $("exportsList");
  list.innerHTML = "";

  if (exportsList.length > 0) {
    show("exportsEmpty", false);
    exportsList.forEach(exp => {
      const card = document.createElement("div");
      card.className = "export-card " + (exp.status || "done");
      // Find matching release asset if exists
      const asset = assets.find(a => a.name === exp.asset_name) || assets[0];
      const dlUrl = asset ? asset.browser_download_url : (exp.asset_name ? `https://github.com/motionssalt/castcut/releases/download/${exp.release_tag || `castcut-${r.job_id}`}/${exp.asset_name}` : "#");

      card.innerHTML = `
        <div class="export-top">
          <span class="export-tag ${exp.type || "clips"}">${exp.type === "scene" ? "🎞 Scene" : "🙂 Clips"}</span>
          <b class="export-title">${esc(exp.label || exp.export_id)}</b>
        </div>
        <div class="export-meta">
          <span>${exp.total_duration ? `${exp.total_duration.toFixed(1)}s` : ""} ${exp.total_segments ? `(${exp.total_segments} cuts)` : ""}</span>
          <span>${exp.size_human || humanSize(exp.size_bytes)}</span>
          <span class="dim">${exp.created_at ? new Date(exp.created_at).toLocaleDateString() : ""}</span>
        </div>
        <div class="export-actions">
          <span class="dim sm mono">${esc(exp.export_id)}</span>
          ${asset || exp.asset_name ? `<a class="export-dl-btn" href="${dlUrl}" target="_blank" rel="noopener">⬇ Download MP4</a>` : '<span class="dim sm">Exporting…</span>'}
        </div>
      `;
      list.appendChild(card);
    });
  } else if (assets.length > 0) {
    show("exportsEmpty", false);
    assets.forEach(a => {
      const card = document.createElement("div");
      card.className = "export-card done";
      card.innerHTML = `
        <div class="export-top">
          <span class="export-tag clips">📦 Video</span>
          <b class="export-title">${esc(a.name)}</b>
        </div>
        <div class="export-meta">
          <span>${humanSize(a.size)}</span>
        </div>
        <div class="export-actions">
          <span class="dim sm mono">${esc(a.release_tag || "release")}</span>
          <a class="export-dl-btn" href="${a.browser_download_url}" target="_blank" rel="noopener">⬇ Download</a>
        </div>
      `;
      list.appendChild(card);
    });
  } else {
    show("exportsEmpty", true);
  }

  // Additional release files card
  const unlinkedAssets = assets.filter(a => !exportsList.some(e => e.asset_name === a.name));
  show("dlCard", unlinkedAssets.length > 0 && exportsList.length > 0);
  if (unlinkedAssets.length > 0 && exportsList.length > 0) {
    const d = $("downloads"); d.innerHTML = "";
    unlinkedAssets.forEach(a => {
      const el = document.createElement("a");
      el.href = a.browser_download_url; el.target = "_blank"; el.rel = "noopener";
      el.innerHTML = `<span>⬇ ${esc(a.name)}</span><small>${humanSize(a.size)}</small>`;
      d.appendChild(el);
    });
  }
}

// ----- torrent selection (job page) -----
function renderSelection(filesDoc, st) {
  const list = $("selList"); list.innerHTML = "";
  if (!state.selChecked.size) {
    const videos = (filesDoc.files || []).filter(f => f.is_video);
    videos.forEach(f => state.selChecked.add(f.index));
  }
  (filesDoc.files || []).forEach(f => {
    const on = state.selChecked.has(f.index);
    const row = document.createElement("div");
    row.className = "frow" + (on ? " sel" : "") + (f.is_video ? "" : " novideo");
    row.innerHTML = `<span class="chk">${on ? "✓" : ""}</span>
      <span class="fi"><b>${esc(f.path)}</b><small>${humanSize(f.length)}</small></span>
      ${f.is_video ? '<span class="vbadge">VIDEO</span>' : ""}`;
    if (f.is_video) row.onclick = () => {
      if (state.selChecked.has(f.index)) state.selChecked.delete(f.index); else state.selChecked.add(f.index);
      row.classList.toggle("sel"); row.querySelector(".chk").textContent = state.selChecked.has(f.index) ? "✓" : "";
      selInfo(filesDoc);
    };
    list.appendChild(row);
  });
  selInfo(filesDoc);
  const btn = $("btnConfirmSel");
  btn.disabled = st === "selecting";
  btn.textContent = st === "selecting" ? "Fetching metadata…" : "Download selected";
}
function selInfo(filesDoc) {
  const files = filesDoc.files || [];
  const n = state.selChecked.size;
  const bytes = files.filter(f => state.selChecked.has(f.index)).reduce((a, f) => a + (f.length || 0), 0);
  $("selInfo").textContent = `${n} selected · ${humanSize(bytes)}`;
}
$("btnConfirmSel").onclick = async () => {
  const indices = [...state.selChecked];
  if (!indices.length) { toast("Tick at least one file"); return; }
  await withButtonLoading($("btnConfirmSel"), async () => {
    await api(`/jobs/${state.job}/selection`, { method: "PUT", body: { selected_indices: indices } });
    toast("Download started");
    await tickJob(); schedulePoll();
  }, "Starting…").catch(e => { $("jobErr").textContent = e.message; });
};

// ----- job actions -----
$("btnCancel").onclick = async () => {
  const ok = await confirmBox("Cancel the running workflow for this project?", "Cancel run");
  if (!ok) return;
  await withButtonLoading($("btnCancel"), async () => {
    await api(`/jobs/${state.job}/cancel`, { method: "POST" });
    toast("Cancelling…");
    await tickJob(); schedulePoll();
  }, "Cancelling…").catch(e => { $("jobErr").textContent = e.message; });
};
$("btnRetry").onclick = async () => {
  await withButtonLoading($("btnRetry"), async () => {
    await api(`/jobs/${state.job}/retry`, { method: "POST" });
    toast("Retry dispatched");
    await tickJob(); schedulePoll();
  }, "Retrying…").catch(e => { $("jobErr").textContent = e.message; });
};
$("btnDelete").onclick = () => deleteJob(state.job, $("jobTitleH").textContent, false);

function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

tryGate();
