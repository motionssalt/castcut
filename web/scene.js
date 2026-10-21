import { requireAuth, api, escapeHtml, formatSeconds, tolerantParseJson, validateSceneProduction, toast, withButtonFeedback, setupPrefetching } from "./core.js";

let projectId = null;
let projectData = null;
let validatedCut = null;
let chosenAudioTrack = 0;
let chosenResolution = localStorage.getItem("castcut_export_resolution") || "1080p";

function init() {
  const params = new URLSearchParams(window.location.search);
  projectId = params.get("id");
  if (!projectId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    document.getElementById("btnBackToProject").href = `/project/?id=${encodeURIComponent(projectId)}`;
    setupPromptGenerators();
    setupJsonIntake();
    setupPrefetching();
    await loadSceneData();
  });
}

async function loadSceneData() {
  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(projectId)}`);
    renderPrompt();
    renderAudioPicker();
  } catch (err) {
    toast(`Failed to load scene data: ${err.message}`, "error");
  }
}

function renderPrompt() {
  const promptBox = document.getElementById("agentPromptDisplay");
  const copyBtn = document.getElementById("btnCopyPrompt");
  const userRequest = document.getElementById("sceneRequestInput").value.trim();

  // If server has stored agent prompt
  if (projectData?.sceneAgent) {
    let p = projectData.sceneAgent;
    if (userRequest) {
      p = `USER REQUEST: ${userRequest}\n\n` + p;
    }
    promptBox.value = p;
    copyBtn.disabled = false;
    return;
  }

  // Generate release-linked prompt dynamically
  const tag = `castcut-${projectId}`;
  const repo = "motionssalt/castcut";
  const assets = projectData?.release?.assets || projectData?.assets || [];
  const transcriptAsset = assets.find(a => a.name.includes("transcript"));
  const sheetsAsset = assets.find(a => a.name.includes("sheets") || a.name.includes("scene"));

  let promptText = `You are an expert anime and video editor assistant for CastCut project "${projectId}".\n`;
  if (userRequest) {
    promptText += `\nUSER SCENE FOCUS: ${userRequest}\n`;
  }
  promptText += `\nRelease Assets:\n`;
  promptText += `- Release Tag: https://github.com/${repo}/releases/tag/${tag}\n`;
  if (transcriptAsset) promptText += `- Dialogue Transcript: ${transcriptAsset.browser_download_url}\n`;
  if (sheetsAsset) promptText += `- Scene Sheets: ${sheetsAsset.browser_download_url}\n`;
  promptText += `\nTASK:\nSelect the best continuous segments based on the transcript and sheets, and return ONLY a valid JSON object matching this schema:\n`;
  promptText += `{\n  "title": "${escapeHtml(userRequest || "Scene Cut")}",\n  "segments": [\n    { "start": "00:00", "end": "00:30", "note": "opening scene" }\n  ]\n}`;

  promptBox.value = promptText;
  copyBtn.disabled = false;
}

function setupPromptGenerators() {
  const copyBtn = document.getElementById("btnCopyPrompt");
  const requestInput = document.getElementById("sceneRequestInput");
  const promptBox = document.getElementById("agentPromptDisplay");

  requestInput.oninput = () => {
    renderPrompt();
  };

  copyBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(promptBox.value);
      toast("Prompt copied to clipboard ✓", "success");
      copyBtn.textContent = "Copied ✓";
      setTimeout(() => { copyBtn.textContent = "📋 Copy Prompt"; }, 2000);
    } catch {
      promptBox.select();
      document.execCommand("copy");
      toast("Prompt copied to clipboard ✓", "success");
    }
  };
}

function setupResolutionToggle() {
  const container = document.getElementById("resolutionToggle");
  if (!container) return;
  const buttons = container.querySelectorAll(".segmented-btn");
  function applySelection(val) {
    chosenResolution = (val === "900p" || val === "900") ? "900p" : "1080p";
    try { localStorage.setItem("castcut_export_resolution", chosenResolution); } catch (_) {}
    buttons.forEach(btn => {
      const match = btn.getAttribute("data-res") === chosenResolution;
      btn.classList.toggle("active", match);
      btn.setAttribute("aria-checked", match ? "true" : "false");
    });
  }
  buttons.forEach(btn => {
    btn.onclick = () => applySelection(btn.getAttribute("data-res"));
  });
  applySelection(chosenResolution);
}

function setupJsonIntake() {
  setupResolutionToggle();
  const pasteBox = document.getElementById("jsonPasteTextarea");
  const fileInput = document.getElementById("jsonFileInput");
  const uploadBtn = document.getElementById("btnUploadJson");
  const clearBtn = document.getElementById("btnClearJson");

  uploadBtn.onclick = () => fileInput.click();

  fileInput.onchange = async () => {
    const file = fileInput.files[0];
    if (!file) return;

    try {
      const text = await file.text();
      pasteBox.value = text;
      clearBtn.hidden = false;
      toast(`Loaded ${file.name}`, "info");
      validateInputJson(text);
    } catch (e) {
      toast(`Failed to read file: ${e.message}`, "error");
    }
  };

  clearBtn.onclick = () => {
    fileInput.value = "";
    pasteBox.value = "";
    clearBtn.hidden = true;
    validateInputJson("");
  };

  pasteBox.oninput = () => {
    clearBtn.hidden = !pasteBox.value.trim();
    validateInputJson(pasteBox.value);
  };

  const exportBtn = document.getElementById("btnExportScene");
  exportBtn.onclick = async () => {
    if (!validatedCut || !validatedCut.ok) return;
    const payload = {
      production: {
        title: validatedCut.title,
        segments: validatedCut.segments,
        audio_track: chosenAudioTrack,
        resolution: chosenResolution,
      },
      audio_track: chosenAudioTrack,
      resolution: chosenResolution,
    };

    await withButtonFeedback(exportBtn, async () => {
      try {
        const res = await api(`/api/jobs/${encodeURIComponent(projectId)}/scene-export`, {
          method: "POST",
          body: payload,
        });
        toast("Scene export started! Redirecting to exports…", "success");
        setTimeout(() => {
          location.href = `/exports/?id=${encodeURIComponent(projectId)}`;
        }, 1200);
      } catch (e) {
        toast(`Scene export failed: ${e.message}`, "error");
      }
    }, "Starting Scene Export…");
  };
}

function validateInputJson(raw) {
  const resultBox = document.getElementById("validationResultBox");
  const exportBtn = document.getElementById("btnExportScene");

  if (!raw || !raw.trim()) {
    resultBox.hidden = true;
    validatedCut = null;
    exportBtn.disabled = true;
    return;
  }

  const parseRes = tolerantParseJson(raw);
  if (!parseRes.ok) {
    resultBox.hidden = false;
    resultBox.innerHTML = `
      <div style="background-color: var(--state-error-surface); color: var(--state-error-text); padding: 10px 12px; border-radius: var(--radius-sm); font-size: 0.8125rem;">
        ✕ ${escapeHtml(parseRes.error)}
      </div>
    `;
    validatedCut = null;
    exportBtn.disabled = true;
    return;
  }

  const duration = projectData?.ingest?.duration || 0;
  const valRes = validateSceneProduction(parseRes.data, duration);

  if (!valRes.ok) {
    resultBox.hidden = false;
    resultBox.innerHTML = `
      <div style="background-color: var(--state-error-surface); color: var(--state-error-text); padding: 10px 12px; border-radius: var(--radius-sm); font-size: 0.8125rem;">
        <div style="font-weight: 600; margin-bottom: 4px;">Validation Errors:</div>
        <ul style="margin-left: 18px;">
          ${valRes.errors.map(e => `<li>${escapeHtml(e)}</li>`).join("")}
        </ul>
      </div>
    `;
    validatedCut = null;
    exportBtn.disabled = true;
    return;
  }

  // Valid! Render preview
  validatedCut = valRes;
  exportBtn.disabled = false;
  resultBox.hidden = false;
  resultBox.innerHTML = `
    <div style="background-color: var(--state-ready-surface); color: var(--state-ready-text); padding: 10px 12px; border-radius: var(--radius-sm); font-size: 0.8125rem;">
      <div style="display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: 6px;">
        <span>✓ Valid AI Cut JSON</span>
        <span class="mono">${valRes.count} segment${valRes.count === 1 ? "" : "s"} · Total ${formatSeconds(valRes.totalDuration)}</span>
      </div>
      <div style="max-height: 140px; overflow-y: auto; background-color: var(--bg-surface); padding: 6px 8px; border-radius: var(--radius-sm); color: var(--text-primary); border: 1px solid var(--border-subtle); margin-top: 6px;">
        ${valRes.segments.map((s, idx) => `
          <div style="display: flex; justify-content: space-between; font-size: 0.75rem; padding: 3px 0; border-bottom: 1px solid var(--border-subtle);">
            <span class="mono">${idx + 1}. ${formatSeconds(s.start)} → ${formatSeconds(s.end)}</span>
            <span style="color: var(--text-muted);">${escapeHtml(s.note || "")}</span>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

function renderAudioPicker() {
  const sel = document.getElementById("selectAudioTrack");
  const tracks = projectData?.ingest?.audio_tracks || projectData?.clipsManifest?.audio_tracks || [];

  const savedChoice = projectData?.audioChoice?.audio_track;
  if (savedChoice !== undefined && savedChoice !== null) {
    chosenAudioTrack = Number(savedChoice);
  } else if (tracks.length > 0) {
    chosenAudioTrack = tracks[tracks.length - 1].audio_index ?? 0;
  }

  if (tracks.length === 0) {
    sel.innerHTML = `<option value="0">Default Audio Track (Stream 0)</option>`;
    return;
  }

  sel.innerHTML = tracks.map(t => {
    const idx = t.audio_index ?? 0;
    const label = t.label || t.title || t.language || `Track ${idx + 1}`;
    const channels = t.channels ? ` (${t.channels}ch)` : "";
    const isSelected = idx === chosenAudioTrack;
    return `
      <option value="${idx}" ${isSelected ? "selected" : ""}>
        Track ${idx + 1}: ${escapeHtml(label)}${channels}
      </option>
    `;
  }).join("");

  sel.onchange = async () => {
    chosenAudioTrack = Number(sel.value);
    try {
      await api(`/api/jobs/${encodeURIComponent(projectId)}/audio-choice`, {
        method: "POST",
        body: { audio_track: chosenAudioTrack }
      });
      toast(`Audio Track ${chosenAudioTrack + 1} saved`);
    } catch {}
  };
}

window.addEventListener("DOMContentLoaded", init);
