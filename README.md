# CastCut

Private web tool for an anime/AMV editor. CastCut takes a video source
(direct URL, Google Drive link, magnet, or `.torrent`) and runs the heavy
work in **GitHub Actions**, so the editor never has to download a full
HD video or pick clips by hand on his phone.

- **Repo**: `motionssalt/castcut` (private)
- **Site**: Cloudflare Pages → <https://castcut.pages.dev>
- **Storage**: ingested video + outputs live in a GitHub **release**
  (`castcut-<job_id>`), auto-deleted 24 hours after the job is created.

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

## Jobs & TTL

- Each ingest creates `jobs/<job_id>/` with `ingest-request.json`,
  `status.json`, and later `clips/manifest.json` / `scene/*.json`.
- A GitHub release `castcut-<job_id>` holds the source video and all
  deliverables.
- The hourly `cleanup.yml` workflow deletes any release whose
  `status.expires_at_epoch` is past (24h TTL).
