// CastCut single-page app. Mobile-first. Owner-PAT gated.
const REPO = "motionssalt/castcut";
const API = "/api";
const $ = (id) => document.getElementById(id);
const show = (id, on = true) => { const el = $(id); if (el) el.hidden = !on; };
const state = { pat: null, user: null, job: null, manifest: null, selection: [], ignored: new Set(), merged: [], poll: null };

async function api(path, { method = "GET", body = null, extraHeaders = {} } = {}) {
  const headers = { "x-castcut-pat": state.pat || "", ...extraHeaders };
  if (body) headers["content-type"] = "application/json";
  const r = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  let data; try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

// ---------- gate ----------
function savePat(pat) { localStorage.setItem("castcut.pat", pat); }
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
    show("gate", false); show("home", true);
    refreshJobs();
  } catch (e) {
    clearPat(); state.pat = null; showGate(e.message);
  }
}
function showGate(msg = "") { show("gate", true); show("home", false); show("job", false); $("gateErr").textContent = msg; }

$("btnLogin").onclick = async () => {
  const pat = $("patInput").value.trim();
  if (!pat) return;
  state.pat = pat; savePat(pat);
  await tryGate();
};
$("btnLogout").onclick = () => { clearPat(); location.reload(); };

// ---------- home ----------
$("srcKind").onchange = () => {
  const k = $("srcKind").value;
  $("srcUrl").hidden = (k === "torrent_file");
  $("srcFile").hidden = (k !== "torrent_file");
};

$("btnIngest").onclick = async () => {
  $("homeErr").textContent = "";
  try {
    const kind = $("srcKind").value;
    const body = { source: { kind }, title: $("jobTitle").value.trim() };
    if (kind === "direct_url" || kind === "gdrive") body.source.url = $("srcUrl").value.trim();
    if (kind === "magnet") body.source.magnet = $("srcUrl").value.trim();
    if (kind === "torrent_file") {
      const file = $("srcFile").files[0];
      if (!file) throw new Error("choose a .torrent file");
      const buf = await file.arrayBuffer();
      body.source.torrent_b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    }
    const r = await api("/jobs", { method: "POST", body });
    openJob(r.job_id);
  } catch (e) { $("homeErr").textContent = e.message; }
};

async function refreshJobs() {
  try {
    const r = await api("/jobs");
    const list = $("jobList"); list.innerHTML = "";
    (r.jobs || []).forEach(j => {
      const div = document.createElement("div");
      div.className = "job";
      div.innerHTML = `<b>${j.title || j.job_id}</b><br><small class="dim">${j.state || "?"} · ${j.updated_at || ""}</small>`;
      div.onclick = () => openJob(j.job_id);
      list.appendChild(div);
    });
  } catch (e) { console.warn(e); }
}

// ---------- job view ----------
async function openJob(id) {
  state.job = id; state.manifest = null; state.selection = []; state.ignored.clear(); state.merged = [];
  show("home", false); show("job", true);
  $("jobTitleH").textContent = id;
  show("modePicker", false); show("mClips", false); show("mScene", false);
  await tickJob();
  if (state.poll) clearInterval(state.poll);
  state.poll = setInterval(tickJob, 5000);
}
$("btnBack").onclick = () => { if (state.poll) clearInterval(state.poll); show("job", false); show("home", true); refreshJobs(); };

async function tickJob() {
  try {
    const r = await api(`/jobs/${state.job}`);
    const s = r.status || {};
    $("jobState").textContent = `${s.state || "?"} — ${s.message || ""}`;
    $("jobMeta").textContent = (r.ingest ? `source ${(r.ingest.duration || 0).toFixed(1)}s · ${r.ingest.width}x${r.ingest.height}` : "");
    show("modePicker", s.state === "ingested" || (s.state && s.state.startsWith("faces")) || (s.state && s.state.startsWith("scene")) || s.state === "clips_ready" || s.state === "scene_ready");
    if (r.clipsManifest && !state.manifest) {
      state.manifest = r.clipsManifest; renderGallery();
    }
    if (r.sceneAgent && !$("agentPrompt").value) {
      $("agentPrompt").value = r.sceneAgent;
      show("sceneAgent", true);
    }
    renderDownloads(r.release || {});
  } catch (e) { $("jobState").textContent = "error: " + e.message; }
}

$("btnModeClips").onclick = () => { show("mClips", true); show("mScene", false); };
$("btnModeScene").onclick = () => { show("mScene", true); show("mClips", false); };

$("btnFaces").onclick = async () => {
  try {
    await api(`/jobs/${state.job}/faces`, { method: "POST", body: { sample_fps: parseFloat($("sampleFps").value) || 1.0 } });
    $("jobState").textContent = "faces job dispatched…";
  } catch (e) { $("jobState").textContent = "error: " + e.message; }
};

function renderGallery() {
  const chars = state.manifest.characters || [];
  const g = $("gallery"); g.innerHTML = "";
  chars.forEach(c => {
    const d = document.createElement("div"); d.className = "face"; d.dataset.id = c.id;
    d.innerHTML = `<img src="${c.thumb || ""}" alt="${c.id}"><small>${c.appearances}× · ${c.screen_time}s</small>`;
    d.onclick = () => toggleFace(c.id, d);
    g.appendChild(d);
  });
  show("galleryActions", chars.length > 0);
  updateSelectedRow();
}
function toggleFace(id, el) {
  if (state.ignored.has(id)) return;
  const idx = state.selection.indexOf(id);
  if (idx >= 0) state.selection.splice(idx, 1); else state.selection.push(id);
  el.classList.toggle("sel");
  const ord = el.querySelector(".ord");
  if (ord) ord.remove();
  const pos = state.selection.indexOf(id);
  if (pos >= 0) {
    const n = document.createElement("span"); n.className = "ord"; n.textContent = pos + 1; el.appendChild(n);
  }
  // Re-number others
  document.querySelectorAll(".face.sel").forEach(f => {
    const p = state.selection.indexOf(f.dataset.id);
    const o = f.querySelector(".ord"); if (o && p >= 0) o.textContent = p + 1;
  });
  updateSelectedRow();
}
function updateSelectedRow() {
  const row = $("gallerySelected");
  if (!state.selection.length) { row.hidden = true; return; }
  row.hidden = false; row.innerHTML = "";
  const chars = Object.fromEntries((state.manifest.characters || []).map(c => [c.id, c]));
  state.selection.forEach(id => {
    const img = document.createElement("img"); img.src = chars[id]?.thumb || ""; row.appendChild(img);
  });
}
$("btnIgnore").onclick = () => {
  state.selection.forEach(id => state.ignored.add(id));
  state.selection = [];
  renderGallery();
  document.querySelectorAll(".face").forEach(f => { if (state.ignored.has(f.dataset.id)) f.classList.add("ignored"); });
};
$("btnMerge").onclick = () => {
  if (state.selection.length < 2) { alert("select 2+ faces to merge"); return; }
  state.merged.push([...state.selection]);
  alert("merged into one character: " + state.selection.join(" + "));
};
$("btnExportClips").onclick = async () => {
  if (!state.selection.length) { alert("select at least one face"); return; }
  try {
    await api(`/jobs/${state.job}/clips-export`, {
      method: "POST",
      body: {
        order: state.selection,
        ignored: [...state.ignored],
        merge_groups: state.merged,
        gap_threshold: parseFloat($("gapSec").value) || 2,
        padding: parseFloat($("padSec").value) || 0.4,
      },
    });
    $("jobState").textContent = "clips export dispatched…";
  } catch (e) { $("jobState").textContent = "error: " + e.message; }
};

// ---------- Mode 2 ----------
$("btnSceneAnalyze").onclick = async () => {
  try {
    await api(`/jobs/${state.job}/scene-analyze`, { method: "POST", body: { request: $("sceneReq").value.trim() } });
    $("jobState").textContent = "scene analysis dispatched…";
  } catch (e) { $("jobState").textContent = "error: " + e.message; }
};
$("btnCopyPrompt").onclick = async () => {
  await navigator.clipboard.writeText($("agentPrompt").value);
  $("btnCopyPrompt").textContent = "copied ✓";
  setTimeout(() => $("btnCopyPrompt").textContent = "copy prompt", 1500);
};
$("btnScenePush").onclick = async () => {
  $("validationOut").textContent = "";
  let parsed;
  try { parsed = JSON.parse($("prodJson").value); }
  catch (e) { $("validationOut").textContent = "invalid JSON: " + e.message; return; }
  try {
    await api(`/jobs/${state.job}/scene-export`, { method: "POST", body: { production: parsed } });
    $("jobState").textContent = "scene export dispatched…";
  } catch (e) { $("validationOut").textContent = e.message; }
};

function renderDownloads(release) {
  const d = $("downloads"); d.innerHTML = "";
  (release.assets || []).forEach(a => {
    const el = document.createElement("a"); el.href = a.browser_download_url; el.textContent = `${a.name} (${(a.size / 1024 / 1024).toFixed(1)} MB)`;
    el.target = "_blank"; d.appendChild(el);
  });
  if (!release.assets?.length) d.innerHTML = `<small class="dim">no files yet</small>`;
}

tryGate();
