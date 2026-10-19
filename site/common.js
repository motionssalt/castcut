// CastCut Common Core - Shared Authentication, Navigation, and Component Logic

const OWNER = "motionssalt";

// ---------- Authentication (PAT Gate) ----------
function savePat(p) { localStorage.setItem("castcut.pat", p.trim()); }
function loadPat() { return (localStorage.getItem("castcut.pat") || "").trim(); }
function clearPat() { localStorage.removeItem("castcut.pat"); }

async function api(path, { method = "GET", body = null } = {}) {
  const pat = loadPat();
  if (!pat) {
    showGate("Authentication required. Please enter your GitHub PAT.");
    throw new Error("Missing GitHub PAT");
  }
  const headers = {
    "x-castcut-pat": pat,
  };
  if (body !== null) {
    headers["content-type"] = "application/json";
  }
  const res = await fetch(path, {
    method,
    headers,
    body: body !== null ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
  });

  if (res.status === 401) {
    clearPat();
    showGate("Session expired or PAT invalid. Please unlock again.");
    throw new Error("Unauthorized (401)");
  }
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.error || errData.message || `Request failed (${res.status})`);
  }
  return res.json().catch(() => ({}));
}

function showGate(msg = "") {
  const gateEl = document.getElementById("gate");
  const appContent = document.getElementById("pageContent");
  if (gateEl) {
    gateEl.hidden = false;
    const gateErr = document.getElementById("gateErr");
    if (gateErr) gateErr.textContent = msg;
  }
  if (appContent) appContent.hidden = true;
}

function hideGate() {
  const gateEl = document.getElementById("gate");
  const appContent = document.getElementById("pageContent");
  if (gateEl) gateEl.hidden = true;
  if (appContent) appContent.hidden = false;
}

async function requireAuth(onSuccess) {
  const pat = loadPat();
  const btnLogin = document.getElementById("btnLogin");
  const patInput = document.getElementById("patInput");
  const btnLogout = document.getElementById("btnLogout");

  if (btnLogout) {
    btnLogout.onclick = () => {
      clearPat();
      location.reload();
    };
  }

  if (btnLogin && patInput) {
    btnLogin.onclick = async () => {
      const p = patInput.value.trim();
      if (!p) return;
      btnLogin.disabled = true;
      btnLogin.textContent = "Checking PAT…";
      savePat(p);
      try {
        const u = await api("/api/whoami");
        btnLogin.disabled = false;
        btnLogin.textContent = "Unlock";
        hideGate();
        updateWhoami(u.login);
        if (typeof onSuccess === "function") onSuccess();
      } catch (e) {
        clearPat();
        btnLogin.disabled = false;
        btnLogin.textContent = "Unlock";
        showGate(e.message || "Invalid GitHub PAT for owner");
      }
    };
  }

  if (!pat) {
    showGate();
    return;
  }

  // Already have PAT: show content immediately (no flash!)
  hideGate();
  if (typeof onSuccess === "function") onSuccess();

  // Background verify
  api("/api/whoami").then(u => {
    updateWhoami(u?.login);
  }).catch(() => {
    // Handled by 401 in api()
  });
}

function updateWhoami(login) {
  const el = document.getElementById("whoami");
  const btnLogout = document.getElementById("btnLogout");
  if (el) el.textContent = login ? `@${login}` : "";
  if (btnLogout) btnLogout.hidden = !login;
}

// ---------- Project Navigation Component (Issue 5) ----------
function renderProjectNav(jobId, activeTab, jobTitle = "") {
  const wrap = document.getElementById("projectNav");
  if (!wrap) return;

  const title = jobTitle || jobId;
  const tabs = [
    { id: "overview", label: "📋 Overview", href: `/project/?id=${encodeURIComponent(jobId)}` },
    { id: "faces", label: "🙂 Faces", href: `/faces/?id=${encodeURIComponent(jobId)}` },
    { id: "scene", label: "🎞 AI Scene", href: `/scene/?id=${encodeURIComponent(jobId)}` },
    { id: "exports", label: "📦 Exports", href: `/exports/?id=${encodeURIComponent(jobId)}` },
    { id: "task", label: "⚙️ Tasks", href: `/task/?id=${encodeURIComponent(jobId)}` },
  ];

  wrap.innerHTML = `
    <div class="crumb-bar">
      <a href="/" class="crumb-link">← All Projects</a>
      <span class="dim">/</span>
      <span class="crumb-title" title="${esc(title)}">${esc(title)}</span>
    </div>
    <nav class="page-nav-tabs">
      ${tabs.map(t => `
        <a href="${t.href}" class="nav-tab-btn ${t.id === activeTab ? 'active' : ''}">
          ${t.label}
        </a>
      `).join("")}
    </nav>
  `;

  // Prefetch tabs on mouseover / touchstart for instant navigation
  tabs.forEach(t => {
    if (t.id !== activeTab) {
      prefetchUrl(t.href);
    }
  });
}

function prefetchUrl(url) {
  if (document.querySelector(`link[rel="prefetch"][href="${url}"]`)) return;
  const link = document.createElement("link");
  link.rel = "prefetch";
  link.href = url;
  document.head.appendChild(link);
}

// ---------- Audio Track Picker Component (Issue 2) ----------
function renderAudioTrackPicker(containerEl, tracks = [], currentChoice = null, onChange = null) {
  if (!containerEl) return;
  containerEl.innerHTML = "";

  const box = document.createElement("div");
  box.className = "picker-group";

  if (!tracks || tracks.length === 0) {
    box.innerHTML = `
      <div class="picker-label-row">
        <span class="picker-title">🔊 Audio Track</span>
        <span class="picker-badge">None</span>
      </div>
      <p class="audio-track-note">No audio tracks detected in source video. Export will produce a video without audio.</p>
    `;
    containerEl.appendChild(box);
    return;
  }

  // Determine preselection: currentChoice -> default flag -> 0
  let selectedIndex = 0;
  if (currentChoice !== null && currentChoice !== undefined) {
    selectedIndex = Number(currentChoice);
  } else {
    const def = tracks.find(t => t.is_default);
    selectedIndex = def ? def.audio_index : 0;
  }

  if (tracks.length === 1) {
    const t = tracks[0];
    box.innerHTML = `
      <div class="picker-label-row">
        <span class="picker-title">🔊 Audio Track</span>
        <span class="picker-badge selected">1 Track Available</span>
      </div>
      <div style="font-size: 13px; font-weight: 500; color: #e6edf3; padding: 4px 0;">
        ${esc(t.label || `Track 1: ${t.language || 'und'} ${t.codec}`)}
      </div>
      <p class="audio-track-note">Source has exactly one audio track. It will be re-encoded as clean AAC stereo.</p>
    `;
    containerEl.appendChild(box);
    if (onChange) onChange(0);
    return;
  }

  // Multiple tracks: Explicit picker
  const selectedTrack = tracks.find(t => t.audio_index === selectedIndex) || tracks[0];
  const isDefaultSelected = selectedTrack?.is_default && (currentChoice === null || currentChoice === undefined);

  box.innerHTML = `
    <div class="picker-label-row">
      <span class="picker-title">🔊 Choose Audio Track</span>
      <span class="picker-badge selected" id="audioSelectedBadge">
        Track ${selectedTrack.audio_index + 1}${isDefaultSelected ? ' (Default)' : ' (Selected)'}
      </span>
    </div>
    <select class="audio-track-select" id="audioSelect">
      ${tracks.map(t => {
        const isSel = t.audio_index === selectedIndex;
        return `
          <option value="${t.audio_index}" ${isSel ? 'selected' : ''}>
            ${esc(t.label)} ${t.is_default ? '★ (Source Default)' : ''}
          </option>
        `;
      }).join("")}
    </select>
    <p class="audio-track-note">
      Finished export will contain <b>exactly one audio track</b> (the chosen language), re-encoded as clean AAC stereo.
    </p>
  `;

  containerEl.appendChild(box);

  const sel = box.querySelector("#audioSelect");
  if (sel) {
    sel.onchange = () => {
      const val = Number(sel.value);
      const chosen = tracks.find(t => t.audio_index === val) || tracks[0];
      const badge = box.querySelector("#audioSelectedBadge");
      if (badge) {
        badge.textContent = `Track ${chosen.audio_index + 1} (Chosen)`;
      }
      if (onChange) onChange(val);
    };
  }
}

// ---------- Quality Settings Component (Issue 1) ----------
function renderQualityPicker(containerEl, currentVal = "normal", onChange = null) {
  if (!containerEl) return;
  containerEl.innerHTML = "";

  const box = document.createElement("div");
  box.className = "picker-group";
  box.innerHTML = `
    <div class="picker-label-row">
      <span class="picker-title">⚡ Video Quality & Size</span>
      <span class="picker-badge selected" id="qualitySelectedBadge">Anime HandBrake</span>
    </div>
    <select class="audio-track-select" id="qualitySelect">
      <option value="normal" ${currentVal === "normal" ? 'selected' : ''}>
        Normal — HandBrake x264 CRF 23, medium, tune animation (Recommended: ~1-1.5 Mbps, visually lossless)
      </option>
      <option value="small" ${currentVal === "small" ? 'selected' : ''}>
        Compact — HandBrake x264 CRF 26, medium, tune animation (Smallest files: ~0.8-1.0 Mbps)
      </option>
      <option value="high" ${currentVal === "high" ? 'selected' : ''}>
        High — HandBrake x264 CRF 20, medium, tune animation (Higher bitrate: ~2-3 Mbps)
      </option>
    </select>
    <p class="audio-track-note">
      All presets use shared 2-pass frame-accurate cutting with zero quality drops on anime line art.
    </p>
  `;
  containerEl.appendChild(box);

  const sel = box.querySelector("#qualitySelect");
  if (sel) {
    sel.onchange = () => {
      const val = sel.value;
      if (onChange) onChange(val);
    };
  }
}

// ---------- Tolerant JSON Parser (Issue 3) ----------
function cleanAndParseJson(raw) {
  if (!raw || !raw.trim()) return { ok: false, error: "JSON input is empty" };
  let text = raw.trim();
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1).trim();

  // 1. Strict parsing first
  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch {}

  // 2. Strip code fences
  const fenceMatch = text.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenceMatch) {
    text = fenceMatch[1].trim();
  } else {
    const s = text.indexOf("{");
    const e = text.lastIndexOf("}");
    if (s !== -1 && e !== -1 && e > s) {
      text = text.slice(s, e + 1).trim();
    }
  }

  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch {}

  // 3. Smart quotes replace
  text = text.replace(/[“”]/g, '"').replace(/[‘’]/g, "'");
  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch {}

  // 4. Trailing commas before } or ]
  const cleaned = text.replace(/,\s*([}\]])/g, "$1");
  try {
    const data = JSON.parse(cleaned);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch (e) {
    return { ok: false, error: "JSON syntax error: " + e.message };
  }

  return { ok: false, error: "Root must be a JSON object" };
}

function parseTime(val) {
  if (typeof val === "number") return val;
  const s = String(val).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  const m = s.match(/^(?:(\d+):)?([0-5]?\d):([0-5]?\d(?:\.\d+)?)$/);
  if (!m) throw new Error(`Invalid timestamp: "${val}"`);
  const h = parseFloat(m[1] || 0);
  const min = parseFloat(m[2]);
  const sec = parseFloat(m[3]);
  return h * 3600 + min * 60 + sec;
}

function validateProduction(data, duration = 0) {
  if (!data || typeof data !== "object") return { ok: false, errors: ["Root must be an object"] };
  if (!Array.isArray(data.segments) || !data.segments.length) {
    return { ok: false, errors: ['The JSON requires a non-empty "segments" array'] };
  }
  const errors = [];
  const segments = [];
  let totalDur = 0;

  data.segments.forEach((seg, i) => {
    const idx = i + 1;
    if (!seg || typeof seg !== "object") {
      errors.push(`Segment ${idx}: must be an object with start and end`);
      return;
    }
    if (seg.start === undefined || seg.end === undefined) {
      errors.push(`Segment ${idx}: missing "start" or "end"`);
      return;
    }
    try {
      const s = parseTime(seg.start);
      const e = parseTime(seg.end);
      if (s < 0) errors.push(`Segment ${idx}: start (${s.toFixed(2)}s) cannot be negative`);
      if (e <= s) errors.push(`Segment ${idx}: end (${e.toFixed(2)}s) must be greater than start (${s.toFixed(2)}s)`);
      if (duration > 0 && s >= duration) errors.push(`Segment ${idx}: start (${s.toFixed(2)}s) is beyond source duration (${duration.toFixed(1)}s)`);
      const segDur = Math.max(0, e - s);
      totalDur += segDur;
      segments.push({ start: s, end: e, note: seg.note || "" });
    } catch (err) {
      errors.push(`Segment ${idx}: ${err.message}`);
    }
  });

  if (errors.length > 0) {
    return { ok: false, errors };
  }
  return {
    ok: true,
    errors: [],
    segments,
    count: segments.length,
    totalDuration: totalDur,
    title: data.title || "AI Scene Cut",
  };
}

// ---------- General Utilities ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function humanSize(n) {
  if (!n || isNaN(n)) return "0 B";
  let f = parseFloat(n);
  for (const u of ["B", "KB", "MB", "GB"]) {
    if (f < 1024 || u === "GB") return u === "B" ? `${Math.round(f)} B` : `${f.toFixed(1)} ${u}`;
    f /= 1024;
  }
  return `${f.toFixed(1)} GB`;
}

function formatSec(s) {
  if (isNaN(s) || s == null) return "0s";
  const num = Math.max(0, parseFloat(s));
  const h = Math.floor(num / 3600);
  const m = Math.floor((num % 3600) / 60);
  const sec = (num % 60).toFixed(1);
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

function toast(msg, duration = 3000) {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    t.className = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove("show"), duration);
}

async function withButtonLoading(btn, fn, loadingText = "Loading…") {
  if (!btn) return fn();
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = loadingText;
  try {
    return await fn();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function statusView(s) {
  const map = {
    created: { label: "Created", cls: "dim" },
    queued: { label: "Queued", cls: "dim" },
    queued_for_ingest: { label: "Queued", cls: "dim" },
    selecting: { label: "Listing torrent", cls: "warn" },
    awaiting_selection: { label: "Pick torrent files", cls: "warn" },
    ingest_running: { label: "Ingesting video…", cls: "run" },
    faces_running: { label: "Analyzing faces…", cls: "run" },
    scene_analyzing: { label: "Analyzing scenes…", cls: "run" },
    hybrid_running: { label: "Analyzing video…", cls: "run" },
    clips_ready: { label: "Faces ready", cls: "ok" },
    scene_awaiting_json: { label: "Awaiting AI JSON", cls: "warn" },
    scene_exporting: { label: "Cutting scene…", cls: "run" },
    clips_exporting: { label: "Exporting clips…", cls: "run" },
    scene_ready: { label: "Scene ready ✓", cls: "ok" },
    ready: { label: "Ready ✓", cls: "ok" },
    failed: { label: "Failed ✕", cls: "err" },
    ingest_failed: { label: "Ingest failed", cls: "err" },
    faces_failed: { label: "Face analysis failed", cls: "err" },
    scene_failed: { label: "Scene failed", cls: "err" },
    clips_failed: { label: "Export failed", cls: "err" },
    cancelled: { label: "Cancelled", cls: "dim" },
  };
  return map[s] || { label: s || "Unknown", cls: "dim" };
}
