# CastCut

Private web tool for an anime/AMV editor. CastCut takes a video source
(direct URL, Google Drive link, magnet, or `.torrent`) and runs the heavy
work in **GitHub Actions**, so the editor never has to download a full
HD video or pick clips by hand on his phone.

- **Repo**: `motionssalt/castcut` (private)
- **Site**: Cloudflare Pages → <https://castcut.pages.dev>
- **Storage**: ingested video + outputs live in a GitHub **release**
  (`castcut-<job_id>`), auto-deleted 24 hours after the job is created.

## Torrent & magnet file selection (fix_round_1 / Task A)

Multi-file torrents no longer download everything.

* **`.torrent` file** — the site parses the manifest **in the browser**
  (pure-JS bencode, mirrors `pipeline/torrents.py`) the moment you pick the
  file. The full list shows with checkboxes; video files are highlighted and
  pre-checked (a single-video torrent shows its one file pre-checked).
  Confirming creates the job already holding `files.json` + `selection.json`,
  and `ingest.yml` downloads **only** the picked files (aria2
  `--select-file=<indices>`).
* **Magnet** — a magnet carries no file list, so `POST /api/jobs` dispatches
  the lightweight `select.yml` run instead of ingest: aria2 runs with
  `--bt-metadata-only --bt-save-metadata` (**no content is ever downloaded**),
  the metadata is parsed, `jobs/<job_id>/files.json` is committed and the job
  parks in **`awaiting_selection`**. Pick files in the job page and confirm →
  `PUT /api/jobs/<id>/selection` writes `selection.json` and dispatches
  `ingest.yml` with the chosen indices.
* Failures surface clearly: metadata timeout / no seeds / unreachable
  trackers / no video files → state `select_failed` with the reason.
* Selected torrent paths are matched to downloaded files by relative-path
  suffix (`torrents.resolve_downloaded_files`), because aria2 still writes
  boundary pieces of neighbouring files.

## Job status, retry, cancel, delete (fix_round_1 / Tasks B & C)

* Canonical states: `queued`, `selecting`, `awaiting_selection`,
  `ingest_running`, `ingested`, `faces_running`/`faces_ready`,
  `clips_exporting`/`clips_ready`, `scene_analyzing`/`scene_awaiting_json`/
  `scene_exporting`/`scene_ready`, plus terminal `failed` and `cancelled`.
  Each maps to a colored badge in the UI (✖ red failed, ⛔ amber cancelled).
* Every job workflow sets `run-name` to `… — <job_id>` and has an
  `if: cancelled()` status step. The Pages API reconciles a job whose run
  failed/was cancelled while the user was away (`reconcileStatus` in
  `web/functions/_lib/gh.js`), pulling the failing step name and run-log URL
  from the Actions API.
* **Cancel**: `POST /api/jobs/<id>/cancel` cancels active runs + marks the
  job cancelled. **Retry**: `POST /api/jobs/<id>/retry` re-dispatches the
  failed step, inferred from `failed_from`/`cancelled_from` and durable
  artifacts (`selection.json`, `scene/production.json`, …).
* **Delete**: `DELETE /api/jobs/<id>` cancels live runs, deletes the
  `castcut-<job_id>` release **and** its tag, and removes every committed
  file under `jobs/<id>/`. Works on running/parked jobs. The job list has a
  per-job 🗑 button and a "Clear finished" bulk action.

## UI (fix_round_1 / Task D)

Mobile-first (~360 px), dark theme, one accent color (#4f8cff), ≥48 px tap
targets, sticky confirm/extract bars, status badges everywhere, empty /
loading / error states on every screen, and no third-party JS — the whole
app is `index.html` + `styles.css` + `app.js`.

## Two modes

### Mode 1 — Clips (no AI)
1. Sample frames at ~1 fps across the whole video.
2. Detect anime faces, extract CCIP embeddings, cluster into unique
   characters.
3. Show the gallery of unique faces (tiny 112-px WebP thumbnails packed
   into a single JSON manifest). The editor taps to select characters
   (order matters), can merge split cards, and can mark ignored faces.
4. Build segments per character (merge nearby ranges, add small
   padding). **Order:** all of character 1's segments chronologically,
   then character 2's, etc. — never interleaved. A range containing
   two selected characters is kept only under the earliest-selected
   one, so nothing is duplicated.
5. Join everything into ONE file with **hard cuts only**.
6. Final HandBrake-style compression pass (x264 CRF 23, slow preset,
   same resolution/fps/aspect/audio) — before/after sizes are logged.

### Mode 2 — Scene (AI via JSON)
1. The editor types the scene/sequence request **before** analysis
   starts (or leaves it empty — the AI will pick the single best scene).
2. Stage A analysis: ffmpeg scene-cut detection + faster-whisper
   transcript + per-boundary 480p screenshots (zipped into the release).
3. Site shows an **agent prompt** with a copy button; the editor pastes
   it into his own AI.
4. The AI returns a **production JSON** (schema below).
5. The editor pastes the JSON back; the site validates it with clear,
   numbered errors.
6. Full-quality cut (x264 CRF 14) joined with hard cuts into ONE file.

## Production JSON schema (Mode 2)
```json
{
  "version": 1,
  "request": "echo the scene request",
  "title": "optional label",
  "segments": [
    {"start": 12.5, "end": 27.0, "note": "..."},
    {"start": "1:04", "end": "1:19.5", "note": "MM:SS also accepted"}
  ],
  "metadata": {"mood": "...", "characters": ["..."]}
}
```
Rules: segments in playback order; `end > start`; both inside
`[0, duration]`; 1–200 segments.

## Setup (one-time; already done for `motionssalt/castcut`)

1. Create private repo via GitHub API.
2. Create Cloudflare Pages project `castcut`.
3. Add repo secrets (sealed-box encrypted via the GitHub API):
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
4. Push to `main` → `.github/workflows/deploy.yml` auto-deploys to
   Cloudflare Pages.

The owner's GitHub PAT is **never** stored on the server. The editor
pastes it once on the site; it lives in `localStorage` and is forwarded
in the `x-castcut-pat` header. The Pages middleware validates it
against `https://api.github.com/user` and rejects anything whose
`login` is not `motionssalt`.

## Research decisions

- **Face detector**: `hysts/anime-face-detector` (YOLOv3), PyTorch CPU
  backend. Fallback: `lbpcascade_animeface` (OpenCV cascade). The
  cascade is only used when mmcv/mmdet install fails on the runner.
- **Embeddings**: deepghs **CCIP** via `imgutils` (512-d, cosine
  threshold 0.35). Fallback: HSV colour histogram embeddings.
- **Clustering**: online nearest-centroid assignment — deterministic,
  no `sklearn` needed.

## Resume protocol

Every session starts by reading `BUILD_STATE.json` at the repo root.
Resume from the first phase whose `status` is not `done`; re-verify
only the last `in_progress` phase. The file is committed after every
phase and sub-step so a cold restart is safe.

## Jobs & persistence

- Each ingest creates `jobs/<job_id>/` with `ingest-request.json`,
  `status.json`, and later `clips/manifest.json` / `scene/*.json`.
- A GitHub release `castcut-<job_id>` holds the source video(s) and all
  deliverables. Jobs that select several torrent files ingest them as
  `source.<ext>`, `source.2.<ext>`, … (the first is the pipeline's primary).
- The hourly `cleanup.yml` workflow deletes any release whose
  `status.expires_at_epoch` is past. **fix_round_4:** auto-expiry is disabled for projects — anything persistent or in a ready state is never swept; a project's source video is always re-downloadable via `pipeline.refetch`, so losing it is harmless.

## Oversized source videos (> ~1.9 GiB)

GitHub caps each release asset at 2 GiB. CastCut handles this automatically
(ported from ClipForge's bug-69 design):

1. **Ingestion** measures the real on-disk size of the downloaded source and
   compares it against `limit (2 GiB) - 128 MiB safety margin`
   (`pipeline/release_size.py`; override via the repo variable
   `CASTCUT_RELEASE_LIMIT_BYTES`). If it does not fit, the video is **not**
   uploaded and this is **not an error**: `jobs/<id>/ingest.json` records
   `video_in_release: false` plus the refetch info (ingest request path and,
   for torrents, the selected file indices). A failed upload at publish time
   falls back to the same exclusion path as a safety net.
2. **Every downstream step** (faces, scene analysis, clips export, scene
   export) calls `pipeline/refetch.py`, which downloads `source.*` from the
   release when present, or **re-downloads from the original source** (direct
   URL / Google Drive / magnet / .torrent with the saved `--select-file`
   indices) when it was excluded. Runner free disk is logged and checked
   first; the aria2 working directory is deleted right after copying the
   payload out.
3. **The final output** is checked against the same limit before upload; if
   even the joined video would not fit, the run fails with a clear message.
4. The UI shows an informational note ("large video: will be re-downloaded
   when processing") in the job meta line - not an error badge.
5. Delete-project and the hourly cleanup sweep are unaffected (they remove the release,
   tag and job files regardless of whether the video was in the release).

## Workflow hygiene

To keep GitHub Actions reliable and prevent shell parsing bugs across runner environments:

- **No inline heredocs in workflows**: Never use `<<`, `<<'PY'`, `<<EOF`, `<<-`, or any heredoc variant inside `.github/workflows/*.yml`. Indented heredocs in YAML `run: |` blocks fail because bash requires the delimiter to start at column 0 (unless tab-indented with `<<-`).
- **Use `pipeline/` modules**: All non-trivial logic, JSON manipulations, fallbacks, and safety nets belong in dedicated Python modules under `pipeline/` (e.g., `pipeline/mark_excluded.py`, `pipeline/status.py`) invoked as clean single-line commands from workflow steps.
- **Automated linting**: Workflows are automatically checked by `.github/workflows/lint-workflows.yml` on push/PR via:
  1. `scripts/lint_workflows.py`: Strictly disallows `<<` heredocs across all workflow files and runs `bash -n` on all extracted `run:` blocks to catch shell syntax errors before jobs run.
  2. `actionlint` (via `reviewdog/action-actionlint@v1`): Static analysis of workflow structures and ShellCheck verification.


## Hybrid project model (fix_round_4)

A **project** is the only unit the user creates. Creating a project picks a
source (with torrent/magnet file selection) and runs ONE automatic hybrid
analysis pipeline (`hybrid-analyze.yml`): ingest (incl. oversized-source
exclusion/refetch), face detection + character clustering with thumbnails,
and scene analysis (transcript + scene boundaries) so the agent prompt can be
generated on demand. There is no mode choice and no "pipeline finished" state
— a project stays usable until deleted.

- **Project page hub:** Clips and AI sections are always available once
  analysis is ready. Each export (Clips or AI) gets its own `export_id` and
  its own state file (`jobs/<id>/exports/<export_id>.json`), so concurrent
  exports cannot corrupt each other; output filenames carry the export id.
- **Exports list:** every export is listed newest-first with type, label,
  status, size, Download, Delete, and (on failure) the failing step, reason,
  run-log link and Retry.
- **Persistence:** no 24h auto-expiry on outputs, analysis artifacts or job
  state. Only the raw source video may be absent — every export re-downloads
  it via `pipeline/refetch` from the saved ingest request and file indices.
- **Release overflow:** GitHub allows 2 GiB per release asset (verified
  2026-10; no total-release cap). When a project's current release cannot
  take another export, the pipeline creates `castcut-job-<id>-out-2`,
  `-out-3`, ... and records which release holds which export in the project
  manifest. The UI aggregates all of them into one list.
- **Delete project:** cancels running workflow runs, deletes ALL of the
  project's releases and tags (base + overflow), removes `jobs/<id>/` state,
  exports, manifests and thumbnails, and removes it from the UI.
- **Migration:** `python -m pipeline.migrate_projects` maps legacy
  mode-based jobs (including the recovered `job-1791013658900-mp21`) into the
  project model without losing outputs; it is idempotent.

## Contract tests & CI

- `tests/test_workflow_contract.py` parses every workflow file, extracts each
  `python -m pipeline.<module>` invocation, and argument-parses it against
  the target module's CLI — a workflow/module mismatch fails CI before a real
  run. `tests/test_status.py` covers every status state and argument
  combination the workflows use. `tests/test_releases.py` covers the
  multi-release aggregation helpers.
- `.github/workflows/ci.yml` runs lint (`scripts/lint_workflows.py`,
  no-heredoc rule), actionlint, pytest, and `python -m pipeline.selftest`
  (end-to-end dry-run on a tiny generated video) on every push.

## Torrent selection defaults, call stack fix, long names (fix_round_5)

- **Call stack RangeError fix (Task A):** Fixed `RangeError: Maximum call stack size exceeded` triggered on Start with large multi-file torrents (50+ episodes). Cause: unchunked byte spreading `String.fromCharCode(...bytes)` during base64 serialization. Resolved using `FileReader.readAsDataURL` and 8 KB chunked `bytesToBase64` across client (`app.js`) and API (`gh.js`). Added visible error reporting with stack trace and "Copy error details" button, plus regression tests for 50, 200, and 1000 file torrent fixtures.
- **Selection defaults & search (Task B):** Torrents with multiple videos select nothing by default (Start disabled with "Select at least 1 file to start" hint); single-video torrents pre-select their 1 video. Added case-insensitive name filter/search that retains selections across queries, dynamic "Select all (N shown)" and "Clear shown" controls, 2 GiB large-selection warning, and natural episode sorting (episode 2 before episode 10). Mirrored identically in the magnet `awaiting_selection` flow.
- **Full names with tap-to-expand (Task C):** Long file names, torrent titles, and export labels are no longer truncated without a way to read them. Collapsed state wraps and line-clamps to 2 lines; dedicated 44px chevrons (`▾`/`▴`) and text taps toggle full multi-line display with `overflow-wrap: anywhere; word-break: break-all`. Checkbox tap targets remain isolated from name expansion.
- **Project cards show project name (Task D):** The project card in the Jobs list displays the user-typed project name as the primary title (falling back to torrent name, then job ID), with the raw job ID displayed small and secondary. Existing projects without custom names retain their format seamlessly.


## Frontend Rebuild (`web/`)
The frontend is reimagined and built from scratch in `web/` for mid-range Android phone editing over mobile connections:
- **Zero-framework & fast**: <50 KB critical-path HTML/CSS/JS payload, instant paint under 1 second.
- **Automated Light & Dark Themes**: Follows browser/OS `prefers-color-scheme` automatically with high-contrast tokens.
- **Separate Real Pages**: Real deep-linkable URLs with back button support (`/`, `/new/`, `/project/?id=`, `/faces/?id=`, `/scene/?id=`, `/exports/?id=`, `/task/?id=`).
- **Resilient Network Client**: Client and server-side exponential backoff retry for spotty mobile connections.
- **Deploy**: Cloudflare Pages connects to GitHub and auto-deploys `web/` on push to `main`.

## Smoke Testing & Verification

CastCut provides an automated end-to-end smoke test script and manually triggerable GitHub Actions workflow to verify job intake paths (direct URLs and torrents) without risking production data:

- **Local Execution:**
  ```bash
  python3 scripts/smoke_test.py --type direct_url
  # Or run all intake paths:
  python3 scripts/smoke_test.py --type all
  ```
- **GitHub Actions Workflow:**
  Trigger `.github/workflows/smoke-test.yml` from the GitHub Actions tab (`Smoke Test — Job Creation and Intake`) via `workflow_dispatch`.
