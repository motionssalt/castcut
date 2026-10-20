// ==========================================================================
// CastCut Core Runtime — Fast, Resilient, Touch-First Client Core
// ==========================================================================

const OWNER = "motionssalt";
const PAT_STORAGE_KEY = "castcut.pat";
const THEME_STORAGE_KEY = "castcut.theme";

// ---------- Theme Management ----------
export function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY) || "auto";
  applyTheme(saved);
}

export function applyTheme(theme) {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  localStorage.setItem(THEME_STORAGE_KEY, theme);
}

export function getTheme() {
  return localStorage.getItem(THEME_STORAGE_KEY) || "auto";
}

// ---------- Authentication (PAT Gate) ----------
export function savePat(pat) {
  localStorage.setItem(PAT_STORAGE_KEY, pat.trim());
}

export function loadPat() {
  return (localStorage.getItem(PAT_STORAGE_KEY) || "").trim();
}

export function clearPat() {
  localStorage.removeItem(PAT_STORAGE_KEY);
}

export function renderGateUI(container, onAuthSuccess) {
  if (!container) return;
  container.innerHTML = `
    <div style="min-height: 80vh; display: flex; align-items: center; justify-content: center; padding: 16px;">
      <div class="card" style="width: 100%; max-width: 420px; padding: 24px;">
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 16px;">
          <div class="brand-dot" style="width: 12px; height: 12px;"></div>
          <h1 style="font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em;">Unlock CastCut</h1>
        </div>
        <p style="font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 20px; line-height: 1.45;">
          Enter your owner GitHub Personal Access Token to access video pipelines and exports.
        </p>
        <form id="gateForm">
          <div class="form-group">
            <label class="form-label" for="gatePatInput">GitHub PAT (Fine-grained or Classic)</label>
            <input type="password" id="gatePatInput" class="input mono" placeholder="ghp_... or github_pat_..." autocomplete="current-password" required spellcheck="false" />
            <div id="gateError" style="color: var(--state-error); font-size: 0.8125rem; margin-top: 6px;" hidden></div>
          </div>
          <button type="submit" id="btnGateUnlock" class="btn btn-primary btn-block" style="margin-top: 16px;">
            Unlock CastCut
          </button>
        </form>
      </div>
    </div>
  `;

  const form = container.querySelector("#gateForm");
  const input = container.querySelector("#gatePatInput");
  const btn = container.querySelector("#btnGateUnlock");
  const errEl = container.querySelector("#gateError");

  form.onsubmit = async (e) => {
    e.preventDefault();
    const token = input.value.trim();
    if (!token) return;

    errEl.hidden = true;
    await withButtonFeedback(btn, async () => {
      savePat(token);
      try {
        const u = await api("/api/whoami");
        if (u.login !== OWNER) {
          throw new Error(`Token belongs to @${u.login}, but only repo owner @${OWNER} has access.`);
        }
        toast("Unlocked successfully", "success");
        if (typeof onAuthSuccess === "function") onAuthSuccess(u);
      } catch (err) {
        clearPat();
        errEl.textContent = err.message || "Invalid GitHub token for owner.";
        errEl.hidden = false;
        input.focus();
      }
    }, "Verifying…");
  };
}

export async function requireAuth(onReady) {
  initTheme();
  const pat = loadPat();
  const gateContainer = document.getElementById("authGate");
  const appContainer = document.getElementById("appContent");

  if (!pat) {
    if (appContainer) appContainer.hidden = true;
    if (gateContainer) {
      gateContainer.hidden = false;
      renderGateUI(gateContainer, () => {
        gateContainer.hidden = true;
        if (appContainer) appContainer.hidden = false;
        if (typeof onReady === "function") onReady();
      });
    }
    return;
  }

  // PAT exists: show content immediately without any gate flicker
  if (gateContainer) gateContainer.hidden = true;
  if (appContainer) appContainer.hidden = false;
  if (typeof onReady === "function") onReady();
}

// ---------- Network Resilience API Client with Retry ----------
export async function api(endpoint, { method = "GET", body = null, headers = {}, timeoutMs = 35000, retries = 3 } = {}) {
  const pat = loadPat();
  if (!pat && endpoint !== "/api/whoami") {
    throw new Error("Authentication required. Please enter GitHub PAT.");
  }

  const reqHeaders = {
    "x-castcut-pat": pat,
    ...headers,
  };
  if (body !== null && typeof body === "object" && !(body instanceof FormData)) {
    reqHeaders["content-type"] = "application/json";
  }

  let lastError;
  for (let attempt = 1; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      if (!navigator.onLine && attempt === 1) {
        toast("Device is offline. Waiting for connection…", "error");
      }

      const res = await fetch(endpoint, {
        method,
        headers: reqHeaders,
        body: body !== null ? (typeof body === "string" ? body : JSON.stringify(body)) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (res.status === 401) {
        clearPat();
        requireAuth();
        throw new Error("Unauthorized (401) — PAT is invalid or expired.");
      }

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        const msg = errJson.error || errJson.message || `Request failed with HTTP ${res.status}`;
        
        // Retry 5xx server errors on unstable mobile connection
        if ([500, 502, 503, 504].includes(res.status) && attempt < retries) {
          toast(`Network hiccup (HTTP ${res.status}), retrying (${attempt}/${retries})…`);
          await new Promise(r => setTimeout(r, 600 * Math.pow(2, attempt - 1)));
          continue;
        }
        throw new Error(msg);
      }

      return await res.json().catch(() => ({}));
    } catch (err) {
      clearTimeout(timer);
      lastError = err;

      if (err.name === "AbortError") {
        lastError = new Error(`Request timed out after ${timeoutMs / 1000}s`);
      }

      // If network failed and we have retries left, wait and retry
      if (attempt < retries && !String(err.message).includes("401") && !String(err.message).includes("404")) {
        toast(`Poor connection, retrying (${attempt}/${retries})…`);
        await new Promise(r => setTimeout(r, 800 * Math.pow(2, attempt - 1)));
        continue;
      }
      break;
    }
  }

  throw lastError;
}

// ---------- Touch & Button Observability ----------
export async function withButtonFeedback(btn, asyncFn, loadingText = "Loading…") {
  if (!btn) return asyncFn();
  const originalHtml = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner" aria-hidden="true"></span> <span>${escapeHtml(loadingText)}</span>`;
  
  if (navigator.vibrate) {
    try { navigator.vibrate(10); } catch {}
  }

  try {
    return await asyncFn();
  } finally {
    btn.disabled = false;
    btn.innerHTML = originalHtml;
  }
}

// ---------- Toast Notifications ----------
export function toast(message, type = "info", durationMs = 3500) {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }

  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  el.innerHTML = `<span>${escapeHtml(message)}</span>`;
  container.appendChild(el);

  if (navigator.vibrate && type === "error") {
    try { navigator.vibrate([20, 40, 20]); } catch {}
  }

  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transform = "translateY(10px)";
    el.style.transition = "opacity 0.2s, transform 0.2s";
    setTimeout(() => el.remove(), 200);
  }, durationMs);
}

// ---------- Formatters & Status Badges ----------
export function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

export function formatBytes(bytes) {
  if (!bytes || isNaN(bytes)) return "0 B";
  const num = Number(bytes);
  if (num < 1024) return `${num} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let u = -1;
  let val = num;
  do {
    val /= 1024;
    u++;
  } while (val >= 1024 && u < units.length - 1);
  return `${val.toFixed(1)} ${units[u]}`;
}

export function formatSeconds(sec) {
  if (isNaN(sec) || sec == null) return "0s";
  const num = Math.max(0, parseFloat(sec));
  const h = Math.floor(num / 3600);
  const m = Math.floor((num % 3600) / 60);
  const s = (num % 60).toFixed(1);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

export function formatDate(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export function getStatusBadge(state) {
  const map = {
    created: { label: "Created", cls: "status-dim" },
    queued: { label: "Queued", cls: "status-dim" },
    queued_for_ingest: { label: "Queued for Ingest", cls: "status-dim" },
    selecting: { label: "Listing Torrent…", cls: "status-warning" },
    awaiting_selection: { label: "Pick Files", cls: "status-warning" },
    ingest_running: { label: "Downloading Source…", cls: "status-running" },
    analyzing: { label: "Analyzing (Faces + AI)…", cls: "status-running" },
    faces_running: { label: "Analyzing Faces…", cls: "status-running" },
    scene_analyzing: { label: "Analyzing Scenes…", cls: "status-running" },
    hybrid_running: { label: "Analyzing Video…", cls: "status-running" },
    clips_ready: { label: "Faces Ready", cls: "status-ready" },
    scene_awaiting_json: { label: "Awaiting AI JSON", cls: "status-warning" },
    scene_exporting: { label: "Exporting Scene…", cls: "status-running" },
    clips_exporting: { label: "Exporting Clips…", cls: "status-running" },
    scene_ready: { label: "Scene Ready ✓", cls: "status-ready" },
    ready: { label: "Ready ✓", cls: "status-ready" },
    failed: { label: "Failed ✕", cls: "status-error" },
    ingest_failed: { label: "Ingest Failed ✕", cls: "status-error" },
    faces_failed: { label: "Faces Failed ✕", cls: "status-error" },
    scene_failed: { label: "Scene Failed ✕", cls: "status-error" },
    clips_failed: { label: "Export Failed ✕", cls: "status-error" },
    cancelled: { label: "Cancelled", cls: "status-dim" },
    incomplete: { label: "Incomplete (Repair)", cls: "status-error" }
  };
  return map[state] || { label: state || "Unknown", cls: "status-dim" };
}

// ---------- Link Prefetching for Fast Page Navigation ----------
export function setupPrefetching() {
  document.querySelectorAll("a[href^='/']").forEach(a => {
    const href = a.getAttribute("href");
    if (!href || href === "#" || href.startsWith("/api/")) return;

    const prefetch = () => {
      if (document.querySelector(`link[rel='prefetch'][href='${href}']`)) return;
      const link = document.createElement("link");
      link.rel = "prefetch";
      link.href = href;
      document.head.appendChild(link);
    };

    a.addEventListener("pointerenter", prefetch, { once: true, passive: true });
    a.addEventListener("touchstart", prefetch, { once: true, passive: true });
  });
}

// ---------- Pure JS Bencode Decoder for Torrents ----------
export const VIDEO_EXTENSIONS = new Set([".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"]);

export function parseTorrentBytes(u8) {
  let i = 0;
  const txt = (b) => new TextDecoder().decode(b);
  function parse() {
    const c = u8[i];
    if (c === 0x69) { // i...e integer
      i++;
      const e = u8.indexOf(0x65, i);
      if (e < 0) throw new Error("corrupt bencode integer");
      const v = parseInt(txt(u8.slice(i, e)), 10);
      i = e + 1;
      return v;
    }
    if (c === 0x6c) { // l...e list
      i++;
      const a = [];
      while (i < u8.length && u8[i] !== 0x65) a.push(parse());
      i++;
      return a;
    }
    if (c === 0x64) { // d...e dict
      i++;
      const o = new Map();
      while (i < u8.length && u8[i] !== 0x65) {
        const k = parse();
        o.set(txt(k), parse());
      }
      i++;
      return o;
    }
    if (c >= 0x30 && c <= 0x39) { // string
      const colon = u8.indexOf(0x3a, i);
      if (colon < 0) throw new Error("corrupt bencode string");
      const len = parseInt(txt(u8.slice(i, colon)), 10);
      i = colon + 1;
      const v = u8.slice(i, i + len);
      i += len;
      return v;
    }
    throw new Error("invalid bencode data");
  }

  const root = parse();
  if (!(root instanceof Map)) throw new Error("root is not a bencode dictionary");
  const info = root.get("info");
  if (!(info instanceof Map)) throw new Error("missing torrent info dictionary");

  const dec = (b) => new TextDecoder().decode(b);
  const name = info.get("name") ? dec(info.get("name")) : "torrent";
  const files = [];
  const rawFiles = info.get("files");

  if (Array.isArray(rawFiles)) {
    rawFiles.forEach((f, idx) => {
      const parts = (f.get("path") || []).map(dec);
      const path = parts.join("/");
      const length = f.get("length") || 0;
      const ext = ("." + (path.split(".").pop() || "")).toLowerCase();
      files.push({
        index: idx + 1,
        path,
        length,
        is_video: VIDEO_EXTENSIONS.has(ext) && length > 0
      });
    });
  } else {
    const length = info.get("length") || 0;
    const ext = ("." + (name.split(".").pop() || "")).toLowerCase();
    files.push({
      index: 1,
      path: name,
      length,
      is_video: VIDEO_EXTENSIONS.has(ext) && length > 0
    });
  }

  return { name, files };
}

// Convert ArrayBuffer or Uint8Array to base64
export function bufferToBase64(buffer) {
  const u8 = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const CHUNK_SIZE = 8192;
  let binary = "";
  for (let i = 0; i < u8.length; i += CHUNK_SIZE) {
    binary += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK_SIZE));
  }
  return btoa(binary);
}

// ---------- Tolerant JSON Parser for AI Scene Cuts ----------
export function tolerantParseJson(raw) {
  if (!raw || !raw.trim()) return { ok: false, error: "JSON input is empty" };
  let text = raw.trim();
  if (text.charCodeAt(0) === 0xFEFF) text = text.slice(1).trim();

  // 1. Strict parse
  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch {}

  // 2. Strip code fences (```json ... ```)
  const fence = text.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fence) {
    text = fence[1].trim();
  } else {
    const s = text.indexOf("{");
    const e = text.lastIndexOf("}");
    if (s !== -1 && e > s) text = text.slice(s, e + 1).trim();
  }

  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch {}

  // 3. Smart quotes & trailing commas
  text = text.replace(/[\u201c\u201d]/g, '"').replace(/[\u2018\u2019]/g, "'").replace(/,\s*([}\]])/g, "$1");
  try {
    const data = JSON.parse(text);
    if (typeof data === "object" && data !== null && !Array.isArray(data)) return { ok: true, data };
  } catch (err) {
    return { ok: false, error: "Invalid JSON: " + err.message };
  }

  return { ok: false, error: "Root must be a JSON object" };
}

export function parseTimestamp(val) {
  if (typeof val === "number") return val;
  const s = String(val).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
  const m = s.match(/^(?:(\d+):)?([0-5]?\d):([0-5]?\d(?:\.\d+)?)$/);
  if (!m) throw new Error(`Invalid time format: "${val}"`);
  const h = parseFloat(m[1] || 0);
  const min = parseFloat(m[2]);
  const sec = parseFloat(m[3]);
  return h * 3600 + min * 60 + sec;
}

export function validateSceneProduction(data, sourceDuration = 0) {
  if (!data || typeof data !== "object") return { ok: false, errors: ["Root must be an object"] };
  if (!Array.isArray(data.segments) || !data.segments.length) {
    return { ok: false, errors: ["Missing or empty 'segments' array"] };
  }

  const errors = [];
  const validSegments = [];
  let totalDur = 0;

  data.segments.forEach((seg, i) => {
    const idx = i + 1;
    if (!seg || typeof seg !== "object") {
      errors.push(`Segment ${idx}: must be an object`);
      return;
    }
    if (seg.start === undefined || seg.end === undefined) {
      errors.push(`Segment ${idx}: missing start or end`);
      return;
    }
    try {
      const s = parseTimestamp(seg.start);
      const e = parseTimestamp(seg.end);
      if (s < 0) errors.push(`Segment ${idx}: start cannot be negative (${s}s)`);
      if (e <= s) errors.push(`Segment ${idx}: end (${e}s) must be after start (${s}s)`);
      if (sourceDuration > 0 && s >= sourceDuration) {
        errors.push(`Segment ${idx}: start (${s}s) exceeds source duration (${sourceDuration}s)`);
      }
      const dur = Math.max(0, e - s);
      totalDur += dur;
      validSegments.push({ start: s, end: e, note: seg.note || "" });
    } catch (err) {
      errors.push(`Segment ${idx}: ${err.message}`);
    }
  });

  if (errors.length) return { ok: false, errors };
  return {
    ok: true,
    segments: validSegments,
    count: validSegments.length,
    totalDuration: totalDur,
    title: data.title || "Scene Cut",
  };
}
