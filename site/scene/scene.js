// CastCut AI Scene Flow Page (Issues 1, 2, 3, 4, 5)

let jobId = null;
let projectData = null;
let chosenAudioTrack = null;
let chosenQuality = "normal";

// Input State for Issue 4
let activeSource = "none"; // 'file' | 'paste' | 'none'
let uploadedFileContent = null;
let uploadedFileName = "";
let uploadedFileSize = 0;
let validatedProductionData = null;

function initScene() {
  const params = new URLSearchParams(window.location.search);
  jobId = params.get("id");
  if (!jobId) {
    location.replace("/");
    return;
  }

  requireAuth(async () => {
    await loadSceneData();
  });
}

async function loadSceneData() {
  const content = document.getElementById("sceneContent");
  if (content) {
    content.innerHTML = `
      <div class="card loading-skeleton" style="height: 120px; margin-bottom: 12px;"></div>
      <div class="card loading-skeleton" style="height: 250px;"></div>
    `;
  }

  try {
    projectData = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    const title = projectData.status?.title || projectData.status?.torrent_name || jobId;
    renderProjectNav(jobId, "scene", title);

    chosenAudioTrack = projectData.audioChoice?.audio_track ?? null;
    renderSceneUI();
  } catch (err) {
    if (content) {
      content.innerHTML = `<div class="card err">Failed to load scene data: ${esc(err.message)}</div>`;
    }
  }
}

function renderSceneUI() {
  const content = document.getElementById("sceneContent");
  if (!content || !projectData) return;

  const st = projectData.status || {};
  const isSceneReady = !!(
    projectData.sceneAgent ||
    st.tasks?.scene?.state === "ready" ||
    st.release_url ||
    (projectData.releases && projectData.releases.length > 0) ||
    st.state === "scene_awaiting_json" ||
    st.state === "scene_ready" ||
    st.state === "ready"
  );

  const audioTracks = projectData.ingest?.audio_tracks || projectData.clipsManifest?.audio_tracks || [];
  const tag = `castcut-${jobId}`;
  const releaseUrl = st.tasks?.scene?.url || st.release_url || `https://github.com/motionssalt/castcut/releases/tag/${tag}`;

  content.innerHTML = `
    <!-- Top Step 1: Agent Prompt Generation -->
    <div class="card" style="margin-bottom: 12px;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <h2 style="margin:0;">1. Copy AI Agent Prompt</h2>
        <span class="tag ${isSceneReady ? 'tag-anime' : 'tag-dim'}">
          ${isSceneReady ? 'Release Assets Ready' : 'Preparing…'}
        </span>
      </div>
      <p class="dim sm">
        Describe the scene or sequence you want, then copy the prompt and give it to Claude / ChatGPT.
        The AI inspects the GitHub release evidence (scene index, transcript, screenshots) and creates the cut list.
      </p>

      <div style="margin-top: 10px;">
        <label class="lbl">Scene request (optional):</label>
        <input id="sceneReq" class="field" type="text" placeholder="e.g. Lelouch's grand coronation speech, or leave blank for best scene" autocomplete="off">
      </div>

      <div style="margin-top: 12px; display:flex; justify-content:space-between; align-items:center;">
        <span class="dim sm mono" style="word-break:break-all;">Release: <a href="${releaseUrl}" target="_blank" style="color:#6366f1;">${releaseUrl}</a></span>
        <button id="btnCopyPrompt" class="btn primary sm">Copy prompt</button>
      </div>

      <pre id="scenePromptText" class="mono-box" style="margin-top: 10px; max-height: 180px; overflow-y: auto; white-space: pre-wrap; font-size: 12px;"></pre>
    </div>

    <!-- Step 2: Submission via Paste OR File Upload (Issues 3 & 4) -->
    <div class="card" style="margin-bottom: 12px;">
      <h2>2. Submit AI Production JSON</h2>
      <p class="dim sm">
        Submit the cut plan produced by your AI agent. You can upload the <code>.json</code> or <code>.txt</code> file directly, or paste the text.
      </p>

      <!-- Active input indicator (Issue 4: Most recently provided input wins) -->
      <div id="sourceWinnerIndicator" class="source-winner-indicator" hidden></div>

      <!-- File Upload Box (Issue 4) -->
      <div style="margin-top: 10px;">
        <label class="lbl">Option A: Upload JSON file</label>
        <div id="uploadBox" class="file-upload-box">
          <div style="font-size: 24px; margin-bottom: 4px;">📄</div>
          <div style="font-weight: 500; font-size: 13px;">Choose .json or .txt file, or drag & drop here</div>
          <div class="dim sm" style="margin-top: 2px;">Works from your phone's Downloads folder</div>
          <input id="fileUploadInput" type="file" accept=".json,.txt,text/plain,application/json" hidden>
        </div>
        <div id="fileUploadInfo" class="file-upload-info" hidden>
          <div style="display:flex; align-items:center; gap:8px;">
            <span class="mono sm" id="uploadFileName"></span>
            <span class="dim sm mono" id="uploadFileSize"></span>
          </div>
          <button type="button" id="btnRemoveFile" class="btn danger sm">✕ Remove</button>
        </div>
      </div>

      <div style="margin: 14px 0; text-align: center;" class="dim sm">— OR —</div>

      <!-- Textarea Paste Box -->
      <div>
        <label class="lbl">Option B: Paste production JSON or markdown block</label>
        <textarea id="prodJson" class="field mono" style="min-height: 130px; font-size: 12px;" placeholder="Paste JSON here (fences and surrounding explanations are fine)..."></textarea>
      </div>

      <!-- Validation & Cut Preview Box (Issue 4) -->
      <div id="validationResultSlot" style="margin-top: 10px;"></div>
    </div>

    <!-- Step 3: Audio & Quality Settings & Export Action (Issues 1 & 2) -->
    <div class="card">
      <h3>3. Export Settings & Cut Video</h3>

      <!-- Issue 2: Audio Track Picker -->
      <div id="sceneAudioPickerSlot"></div>

      <!-- Issue 1: Quality Selector -->
      <div id="sceneQualityPickerSlot"></div>

      <p id="sceneExportErr" class="err" style="margin-top: 8px;"></p>

      <button id="btnScenePush" class="btn primary block" style="margin-top: 16px;" disabled>
        Cut & Export Scene →
      </button>
    </div>
  `;

  // Update prompt initially
  updateLivePrompt();

  // Bind live prompt update
  const sceneReq = document.getElementById("sceneReq");
  if (sceneReq) {
    sceneReq.oninput = () => updateLivePrompt();
  }

  // Bind Copy Prompt
  document.getElementById("btnCopyPrompt").onclick = async () => {
    const p = updateLivePrompt();
    try {
      await navigator.clipboard.writeText(p);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = p;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast("Prompt copied ✓");
  };

  // Setup File Upload (Issue 4)
  setupFileUpload();

  // Setup Paste Textarea
  setupPasteArea();

  // Render Audio Track Picker (Issue 2)
  renderAudioTrackPicker(
    document.getElementById("sceneAudioPickerSlot"),
    audioTracks,
    chosenAudioTrack,
    (trackIdx) => {
      chosenAudioTrack = trackIdx;
      api(`/api/jobs/${encodeURIComponent(jobId)}/audio-choice`, {
        method: "POST",
        body: { audio_track: trackIdx }
      }).catch(() => {});
    }
  );

  // Render Quality Selector (Issue 1)
  renderQualityPicker(
    document.getElementById("sceneQualityPickerSlot"),
    chosenQuality,
    (q) => { chosenQuality = q; }
  );

  // Bind Export Button
  document.getElementById("btnScenePush").onclick = startSceneExport;
}

function updateLivePrompt() {
  const st = projectData?.status || {};
  const tag = `castcut-${jobId}`;
  const releaseUrl = st.tasks?.scene?.url || st.release_url || `https://github.com/motionssalt/castcut/releases/tag/${tag}`;
  const title = st.title || st.torrent_name || jobId;
  const ct = st.content_type || "anime";
  const req = (document.getElementById("sceneReq")?.value || "").trim();
  const reqBlock = req
    ? `THE USER'S SCENE REQUEST:\n"""\n${req}\n"""`
    : "The user left the scene request EMPTY. Pick the single best, most coherent, and impactful scene or sequence from the video that carries the narrative.";
  const ctLabel = ct === "irl" ? "Live-Action / IRL" : "Anime";

  // Issue 3: Mirror ClipForge prompt wording: Download or single code block, no "no prose, no fences" rule.
  const prompt = [
    `Open this GitHub release: ${releaseUrl}`,
    "Download and read 00_READ_THIS_FIRST.txt FIRST, then inspect the evidence assets (scene_index.json, transcript.json, and screenshots.zip as needed).",
    "",
    `Project: ${title}`,
    `Content type: ${ctLabel}`,
    "",
    "Task:",
    reqBlock,
    "",
    "Rules:",
    "- Read 00_READ_THIS_FIRST.txt in the release before selecting any cuts.",
    "- Produce exactly one production.json containing the selected segments.",
    "- Segments must be in chronological playback order with start and end timestamps within the source video.",
    `- Deliver the production JSON either as a downloadable .json file (recommended filename: castcut-scene-${jobId}.json) if you can create files, or inside a single \`\`\`json code block (one block, valid JSON, nothing else inside it). If you include a short explanation, place it outside the file or code block.`
  ].join("\n");

  const promptEl = document.getElementById("scenePromptText");
  if (promptEl) promptEl.textContent = prompt;
  return prompt;
}

// ---------- Issue 4: File Upload Handling ----------
function setupFileUpload() {
  const box = document.getElementById("uploadBox");
  const fileInput = document.getElementById("fileUploadInput");
  const btnRemove = document.getElementById("btnRemoveFile");

  box.onclick = () => fileInput.click();

  box.ondragover = (e) => {
    e.preventDefault();
    box.classList.add("dragover");
  };
  box.ondragleave = () => box.classList.remove("dragover");
  box.ondrop = (e) => {
    e.preventDefault();
    box.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) {
      handleChosenFile(e.dataTransfer.files[0]);
    }
  };

  fileInput.onchange = () => {
    if (fileInput.files.length > 0) {
      handleChosenFile(fileInput.files[0]);
    }
  };

  btnRemove.onclick = () => {
    uploadedFileContent = null;
    uploadedFileName = "";
    uploadedFileSize = 0;
    fileInput.value = "";
    document.getElementById("uploadBox").hidden = false;
    document.getElementById("fileUploadInfo").hidden = true;
    if (activeSource === "file") {
      activeSource = document.getElementById("prodJson").value.trim() ? "paste" : "none";
    }
    validateCurrentInput();
  };
}

async function handleChosenFile(file) {
  const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5 MB
  if (file.size > MAX_FILE_SIZE) {
    showValidationResult({ ok: false, error: `File too large (${humanSize(file.size)}). Max allowed is 5 MB.` });
    return;
  }
  if (file.size === 0) {
    showValidationResult({ ok: false, error: "The uploaded file is empty (0 bytes)." });
    return;
  }

  try {
    const text = await file.text();
    uploadedFileContent = text;
    uploadedFileName = file.name;
    uploadedFileSize = file.size;

    document.getElementById("uploadBox").hidden = true;
    document.getElementById("fileUploadInfo").hidden = false;
    document.getElementById("uploadFileName").textContent = file.name;
    document.getElementById("uploadFileSize").textContent = humanSize(file.size);

    // Most recent input wins
    activeSource = "file";
    validateCurrentInput();
  } catch (err) {
    showValidationResult({ ok: false, error: `Failed to read file: ${err.message}` });
  }
}

function setupPasteArea() {
  const textarea = document.getElementById("prodJson");
  textarea.oninput = () => {
    if (textarea.value.trim()) {
      // User typed or pasted in textarea -> textarea is most recent
      activeSource = "paste";
    } else if (uploadedFileContent) {
      activeSource = "file";
    } else {
      activeSource = "none";
    }
    validateCurrentInput();
  };
}

// ---------- Live Validation & Cut Preview (Issues 3 & 4) ----------
function validateCurrentInput() {
  const winnerEl = document.getElementById("sourceWinnerIndicator");
  const btnPush = document.getElementById("btnScenePush");

  let raw = "";
  if (activeSource === "file") {
    raw = uploadedFileContent || "";
    if (winnerEl) {
      winnerEl.hidden = false;
      winnerEl.innerHTML = `✓ Active Input: <b>Uploaded File</b> (${esc(uploadedFileName)}) — <i>Most recent input wins</i>`;
    }
  } else if (activeSource === "paste") {
    raw = document.getElementById("prodJson").value || "";
    if (winnerEl) {
      winnerEl.hidden = false;
      winnerEl.innerHTML = `✓ Active Input: <b>Pasted Text</b> — <i>Most recent input wins</i>`;
    }
  } else {
    if (winnerEl) winnerEl.hidden = true;
    showValidationResult(null);
    btnPush.disabled = true;
    validatedProductionData = null;
    return;
  }

  if (!raw.trim()) {
    showValidationResult(null);
    btnPush.disabled = true;
    validatedProductionData = null;
    return;
  }

  // Tolerant parsing (Issue 3)
  const parsed = cleanAndParseJson(raw);
  if (!parsed.ok) {
    showValidationResult({ ok: false, error: parsed.error });
    btnPush.disabled = true;
    validatedProductionData = null;
    return;
  }

  // Schema validation
  const dur = projectData.ingest?.duration || 0;
  const val = validateProduction(parsed.data, dur);
  if (!val.ok) {
    showValidationResult({ ok: false, error: val.errors.join("; ") });
    btnPush.disabled = true;
    validatedProductionData = null;
    return;
  }

  // Valid! Show rich human preview (Issue 4)
  validatedProductionData = parsed.data;
  btnPush.disabled = false;
  showValidationResult({
    ok: true,
    data: parsed.data,
    segments: val.segments,
    count: val.count,
    totalDuration: val.totalDuration,
    title: val.title,
  });
}

function showValidationResult(res) {
  const slot = document.getElementById("validationResultSlot");
  if (!slot) return;

  if (!res) {
    slot.innerHTML = "";
    return;
  }

  if (!res.ok) {
    slot.innerHTML = `
      <div class="alert-inline warn" style="margin-top: 8px;">
        ⚠️ <b>Validation Issue:</b> ${esc(res.error)}
      </div>
    `;
    return;
  }

  // Render Rich Human Preview Card (Issue 4)
  const firstSegs = res.segments.slice(0, 5);
  slot.innerHTML = `
    <div class="json-preview-box">
      <div class="json-preview-title">
        <span>✓ Cut Plan Validated</span>
        <span class="dim sm mono">${esc(res.title)}</span>
      </div>
      <div class="json-preview-stats">
        <div class="json-preview-stat-item"><b>${res.count}</b> segments</div>
        <div class="json-preview-stat-item"><b>${res.totalDuration.toFixed(1)}s</b> total cut duration</div>
        <div class="json-preview-stat-item">Hard cuts at source quality</div>
      </div>
      <div style="font-size: 11px; color: #8b949e; margin-bottom: 4px;">Segment Preview:</div>
      <div style="max-height: 120px; overflow-y: auto; background: #1a202c; border-radius: 4px; padding: 6px 8px; font-family: monospace; font-size: 11px;">
        ${firstSegs.map((s, idx) => `
          <div style="padding: 2px 0; border-bottom: 1px solid #283141;">
            <b>#${idx + 1}</b>: ${s.start.toFixed(1)}s → ${s.end.toFixed(1)}s (${(s.end - s.start).toFixed(1)}s)
            ${s.note ? `<span class="dim" style="font-family: sans-serif;"> · ${esc(s.note)}</span>` : ""}
          </div>
        `).join("")}
        ${res.count > 5 ? `<div class="dim" style="padding-top: 4px;">… and ${res.count - 5} more segment(s)</div>` : ""}
      </div>
    </div>
  `;
}

async function startSceneExport() {
  if (!validatedProductionData) return;
  const btnPush = document.getElementById("btnScenePush");
  const errEl = document.getElementById("sceneExportErr");
  errEl.textContent = "";

  await withButtonLoading(btnPush, async () => {
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/scene-export`, {
        method: "POST",
        body: {
          production: validatedProductionData,
          audio_track: chosenAudioTrack,
          quality: chosenQuality,
        }
      });
      toast("Scene export started ✓");
      // Instant transition to exports page
      location.href = `/exports/?id=${encodeURIComponent(jobId)}`;
    } catch (e) {
      errEl.textContent = e.message || "Failed to start scene export";
    }
  }, "Cutting scene…");
}

window.addEventListener("DOMContentLoaded", initScene);
