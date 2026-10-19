"""CastCut Stage A — analysis bundle for the AI agent (ClipForge-aligned).

Produces:
  transcript.json       — Dialogue extracted from embedded subtitle tracks (SRT/ASS)
                          with faster-whisper (tiny, CPU) speech recognition fallback.
                          Surfaces explicit failure reason if both fail.
  scene_index.json      — Complete, contiguous scene cut list (1..N, zero gaps).
  shots/                — Representative 480p JPEG per detected scene (scene_0001_*.jpg).
  00_READ_THIS_FIRST.txt— Comprehensive AI instructions, strict JSON schema, worked example.
  manifest.json         — Machine-readable manifest of all analysis assets.
  jobs/<job>/scene/* copies for the repo record.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

from .utils import fmt_time, probe_video, run


def parse_srt(srt_text: str) -> list[dict]:
    """Parse SRT text into timestamped dialogue segments."""
    entries = []
    text = srt_text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    time_pat = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    for block in blocks:
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        m = None
        text_lines = []
        for line in lines:
            if not m:
                m = time_pat.search(line)
            else:
                text_lines.append(line)
        if m and text_lines:
            sh, sm, ss, sms = map(int, m.groups()[:4])
            eh, em, es, ems = map(int, m.groups()[4:])
            start = sh * 3600 + sm * 60 + ss + sms / 1000.0
            end = eh * 3600 + em * 60 + es + ems / 1000.0
            raw_text = " ".join(text_lines)
            clean_text = re.sub(r"<[^>]+>", "", raw_text)
            clean_text = re.sub(r"\{[^}]+\}", "", clean_text).strip()
            if clean_text and end > start:
                entries.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "text": clean_text,
                })
    return entries


def _extract_embedded_subtitles(src: str, work_dir: str) -> dict | None:
    """Check for embedded subtitle streams and extract them to JSON."""
    try:
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=index,codec_name:stream_tags=language,title",
            "-select_streams", "s",
            "-of", "json", src
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe_data = json.loads(res.stdout)
        streams = probe_data.get("streams", [])
        if not streams:
            print("[scene] No embedded subtitle tracks found in source", flush=True)
            return None

        # Prioritize English, then Japanese, then first stream
        best_stream = None
        for s in streams:
            tags = s.get("tags", {})
            lang = str(tags.get("language", "")).lower()
            title = str(tags.get("title", "")).lower()
            if any(pref in lang or pref in title for pref in ("eng", "en", "english")):
                best_stream = s
                break
        if not best_stream:
            for s in streams:
                tags = s.get("tags", {})
                lang = str(tags.get("language", "")).lower()
                title = str(tags.get("title", "")).lower()
                if any(pref in lang or pref in title for pref in ("jpn", "ja", "jap", "japanese")):
                    best_stream = s
                    break
        if not best_stream:
            best_stream = streams[0]

        s_idx = best_stream.get("index")
        srt_out = os.path.join(work_dir, "extracted_subs.srt")
        extract_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", src,
            "-map", f"0:{s_idx}",
            "-c:s", "srt",
            srt_out
        ]
        subprocess.run(extract_cmd, capture_output=True, text=True)
        if os.path.exists(srt_out) and os.path.getsize(srt_out) > 0:
            with open(srt_out, "r", encoding="utf-8", errors="ignore") as f:
                srt_text = f.read()
            segments = parse_srt(srt_text)
            if segments:
                tags = best_stream.get("tags", {})
                lang = tags.get("language") or "eng"
                print(f"[scene] Extracted {len(segments)} dialogue cues from embedded subtitle track (stream {s_idx}, lang={lang})", flush=True)
                return {
                    "version": 1,
                    "source": "embedded_subtitles",
                    "language": lang,
                    "track_title": tags.get("title") or "Embedded Subtitles",
                    "model": f"embedded/{best_stream.get('codec_name', 'subtitles')}",
                    "stream_index": s_idx,
                    "segments": segments,
                }
    except Exception as exc:
        print(f"[scene] Embedded subtitle extraction attempt failed ({exc})", flush=True)
    return None


def _transcribe(src: str, work_dir: str) -> dict:
    """Extract embedded subtitles or fall back to speech recognition."""
    # 1. Try embedded subtitles first
    sub_result = _extract_embedded_subtitles(src, work_dir)
    if sub_result and len(sub_result.get("segments", [])) > 0:
        return sub_result

    # 2. Try faster-whisper speech recognition
    try:
        from faster_whisper import WhisperModel  # type: ignore

        print("[scene] Running faster-whisper/tiny CPU speech recognition...", flush=True)
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments_iter, info = model.transcribe(src, vad_filter=True)
        segments = []
        for seg in segments_iter:
            text = seg.text.strip()
            if text:
                segments.append({
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": text,
                })
        print(f"[scene] Whisper transcription finished: {len(segments)} segments (lang={info.language})", flush=True)
        return {
            "version": 1,
            "source": "speech_recognition",
            "language": info.language,
            "language_probability": round(info.language_probability, 3) if hasattr(info, "language_probability") else None,
            "model": "faster-whisper/tiny",
            "segments": segments,
        }
    except Exception as exc:
        err_msg = str(exc)[:500]
        print(f"[scene] Transcription failed completely: {err_msg}", flush=True)
        return {
            "version": 1,
            "source": "transcription_failed",
            "language": None,
            "model": "faster-whisper/tiny",
            "error": err_msg,
            "segments": [],
        }


def _scene_index(src: str, work_dir: str, duration: float, threshold: float = 0.35, max_shots: int = 300) -> list[dict]:
    """Detect scene cuts and build a complete contiguous list of scenes (1..N) without gaps."""
    shots_dir = os.path.join(work_dir, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    log = os.path.join(work_dir, "scdet.log")
    with open(log, "w") as lf:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", src,
             "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            stderr=lf, stdout=subprocess.DEVNULL
        )

    times = []
    pat = re.compile(r"pts_time:([0-9.]+)")
    if os.path.exists(log):
        with open(log, errors="ignore") as lf:
            for line in lf:
                if "showinfo" in line:
                    m = pat.search(line)
                    if m:
                        times.append(float(m.group(1)))

    raw_times = sorted(set(round(t, 2) for t in times))

    # Filter out cuts closer than 0.5s to prevent micro-cuts/noise
    clean_cuts = []
    prev_cut = 0.0
    for t in raw_times:
        if t <= 0.2:
            continue
        if duration and t >= duration - 0.2:
            continue
        if t - prev_cut >= 0.5:
            clean_cuts.append(t)
            prev_cut = t

    # Subsample if exceeds max_shots while keeping boundaries valid
    if len(clean_cuts) > max_shots:
        step = len(clean_cuts) / max_shots
        clean_cuts = [clean_cuts[int(i * step)] for i in range(max_shots)]

    # Build contiguous scenes spanning 0.0 to duration
    cut_points = [0.0] + clean_cuts
    if duration and (not cut_points or cut_points[-1] < duration):
        cut_points.append(duration)

    scenes = []
    for i in range(len(cut_points) - 1):
        s = round(cut_points[i], 3)
        e = round(cut_points[i + 1], 3)
        if e <= s:
            continue
        scene_num = len(scenes) + 1
        mid = round((s + e) / 2.0, 3)
        shot_filename = f"scene_{scene_num:04d}_{fmt_time(s).replace(':', '-')}.jpg"
        out_path = os.path.join(shots_dir, shot_filename)
        
        # Extract 480p representative JPEG screenshot for each scene
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(mid), "-i", src, "-frames:v", "1", "-vf", "scale=480:-2",
             "-q:v", "6", out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        scenes.append({
            "scene_number": scene_num,
            "start": s,
            "end": e,
            "duration": round(e - s, 3),
            "start_time": fmt_time(s),
            "end_time": fmt_time(e),
            "screenshot": shot_filename,
            "screenshot_pts": mid,
        })

    return scenes


def generate_instructions(
    job_id: str,
    title: str,
    duration: float,
    meta: dict,
    content_type: str,
    scenes: list[dict],
    transcript: dict,
) -> str:
    """Generate 00_READ_THIS_FIRST.txt release instructions."""
    trans_src = transcript.get("source", "unknown")
    trans_count = len(transcript.get("segments", []))
    trans_note = (
        f"{trans_src} ({trans_count} dialogue segments)"
        if trans_count > 0
        else f"Transcription unavailable / no speech detected ({transcript.get('error', 'None')})"
    )

    schema_example = json.dumps(
        {
            "version": 1,
            "request": "<echo user request, or describe chosen scene>",
            "title": "<short descriptive title for this cut>",
            "segments": [
                {
                    "start": 12.5,
                    "end": 28.0,
                    "note": "Opening dialogue and scene entrance"
                },
                {
                    "start": 45.2,
                    "end": 78.6,
                    "note": "Climactic narrative speech"
                }
            ],
            "metadata": {
                "mood": "dramatic",
                "characters": ["Character A", "Character B"]
            }
        },
        indent=2
    )

    return f"""================================================================================
  CASTCUT SCENE SELECTION & EDITING INSTRUCTIONS (ARCHITECTURE & CONTRACT)
================================================================================

Project: {job_id}
Title: {title or 'Video Project'}
Duration: {fmt_time(duration)} ({duration:.1f}s)
Resolution: {meta.get('width', 0)}x{meta.get('height', 0)} @ {meta.get('fps', 0)}fps
Audio: {'yes' if meta.get('has_audio') else 'no'}
Content Type: {content_type.upper()} ({'Anime footage' if content_type == 'anime' else 'Live-Action / IRL footage'})
Total Scenes Detected: {len(scenes)} (complete, contiguous 1..{len(scenes)} with NO gaps)
Dialogue / Transcript: {trans_note}

--------------------------------------------------------------------------------
1. YOUR ROLE AND TASK
--------------------------------------------------------------------------------
You are the cutting-room editor for CastCut.
Your task is to select the exact video segments to cut and join into ONE final
connected video edit in full source quality.

The user's specific scene or sequence request is delivered in the user's prompt message:
- If the user specified a scene request, locate and assemble that sequence faithfully.
- If the user left the scene request empty, pick the single best, most coherent,
  and impactful scene or sequence from the video that carries the narrative.

--------------------------------------------------------------------------------
2. HOW TO USE THE ANALYSIS ASSETS IN THIS RELEASE
--------------------------------------------------------------------------------
This release contains the following evidence files:
- 00_READ_THIS_FIRST.txt : This instructions and contract document.
- manifest.json          : Machine-readable listing of all release assets.
- scene_index.json       : Complete, contiguous index of all detected scenes (1 to {len(scenes)}).
                           Every scene lists start, end, duration, and matching screenshot.
- screenshots.zip        : Representative 480p frames for each scene (scene_0001_*.jpg).
                           Inspect these frames to verify visual content, characters, and action!
- transcript.json        : Timestamped dialogue (from embedded subtitles or audio transcription).
                           Use these lines to match dialogue, speeches, and character interactions.
- video_meta.json        : Exact video stream dimensions, framerate, and duration.

If the source video file (source_input.bin) is omitted from this release because it exceeded
GitHub's 2 GiB asset limit, DO NOT worry: Stage B re-fetches the original source directly.
You can perform all cut selection using scene_index.json, transcript.json, and screenshots.zip!

--------------------------------------------------------------------------------
3. EDITING RULES (CRITICAL - OBEY STRICTLY)
--------------------------------------------------------------------------------
1. PLAYBACK ORDER: Segments must be in chronological playback order (start times increasing).
2. HARD CUTS & COMPRESSION: The final video cuts each segment from the source, joins them with
   hard cuts, and applies HandBrake-style visually-lossless anime compression (x264 CRF 23, AAC 128k stereo).
3. COHERENCE & BOUNDARIES: Cut on natural scene or dialogue boundaries (refer to scene_index.json
   and transcript.json). Never cut mid-sentence or mid-action beat. Speeches must include their
   natural start and conclusion.
4. TIMESTAMPS: "start" and "end" must be valid timestamps in seconds (e.g. 14.5) or "MM:SS" / "HH:MM:SS".
   0 <= start < end <= {duration:.1f}.
5. SEGMENT COUNT & LENGTH: 1 to 40 segments. Total duration should fit the narrative arc of the scene.
6. NOTES: Provide a concise "note" on each segment explaining why it was chosen.
7. RETURN FORMAT: Produce the production JSON either as a downloadable .json file (recommended filename:
   castcut-scene-{job_id}.json if you can create files) or output it inside a single ```json code block
   (one block, valid JSON, nothing else inside it). A brief explanation outside the file or code block is acceptable.

--------------------------------------------------------------------------------
4. PRODUCTION JSON SCHEMA & WORKED EXAMPLE
--------------------------------------------------------------------------------
Return EXACTLY this JSON shape:

{schema_example}
================================================================================
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--content-type", default=None)
    args = ap.parse_args()

    src = None
    for cand in glob.glob(os.path.join(args.work_dir, "source.*")):
        if os.path.splitext(cand)[1].lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
            src = cand
            break
    if not src:
        raise RuntimeError("no source video found in work dir")

    meta = probe_video(src)
    duration = meta.get("duration", 0.0)
    print(f"[scene] analyzing {duration:.1f}s source: {src}", flush=True)

    content_type = args.content_type
    title = ""
    for p in (
        os.path.join(args.jobs_root, args.job_id, "ingest-request.json"),
        os.path.join(args.jobs_root, args.job_id, "status.json"),
    ):
        if os.path.isfile(p):
            try:
                doc = json.load(open(p, encoding="utf-8"))
                if not content_type and doc.get("content_type") in ("anime", "irl"):
                    content_type = doc.get("content_type")
                if not title:
                    title = doc.get("title") or doc.get("torrent_name") or ""
            except Exception:
                pass
    if not content_type:
        content_type = "anime"

    # Live-action / IRL scene detection threshold (0.28) vs anime hard cuts (0.35)
    threshold = 0.28 if content_type == "irl" else 0.35
    print(f"[scene] content_type={content_type} threshold={threshold}", flush=True)

    # 1. Transcript / Subtitles
    transcript = _transcribe(src, args.work_dir)
    with open(os.path.join(args.work_dir, "transcript.json"), "w", encoding="utf-8") as fh:
        json.dump(transcript, fh, ensure_ascii=False, indent=2)

    # 2. Complete contiguous Scene Index
    scenes = _scene_index(src, args.work_dir, duration=duration, threshold=threshold)
    scene_index = {
        "version": 1,
        "content_type": content_type,
        "threshold": threshold,
        "total_scenes": len(scenes),
        "duration": duration,
        "has_audio": meta.get("has_audio", False),
        "audio_tracks": meta.get("audio_tracks", []),
        "scenes": scenes,
    }
    with open(os.path.join(args.work_dir, "scene_index.json"), "w", encoding="utf-8") as fh:
        json.dump(scene_index, fh, indent=2)
    print(f"[scene] contiguous scene index written: {len(scenes)} scenes", flush=True)

    # 3. 00_READ_THIS_FIRST.txt Instructions
    instructions = generate_instructions(
        job_id=args.job_id,
        title=title,
        duration=duration,
        meta=meta,
        content_type=content_type,
        scenes=scenes,
        transcript=transcript,
    )
    with open(os.path.join(args.work_dir, "00_READ_THIS_FIRST.txt"), "w", encoding="utf-8") as fh:
        fh.write(instructions)

    # 4. Manifest
    manifest = {
        "version": 1,
        "job_id": args.job_id,
        "release_tag": f"castcut-{args.job_id}",
        "content_type": content_type,
        "duration": duration,
        "assets": [
            {"name": "00_READ_THIS_FIRST.txt", "purpose": "agent_instructions"},
            {"name": "scene_index.json", "purpose": "scene_index"},
            {"name": "transcript.json", "purpose": "dialogue_transcript"},
            {"name": "screenshots.zip", "purpose": "scene_screenshots"},
            {"name": "video_meta.json", "purpose": "video_metadata"},
            {"name": "manifest.json", "purpose": "manifest"},
        ],
    }
    with open(os.path.join(args.work_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    with open(os.path.join(args.work_dir, "video_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    # 5. Copy to jobs/<job_id>/scene/
    out_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    os.makedirs(out_dir, exist_ok=True)
    for name in ("transcript.json", "scene_index.json", "00_READ_THIS_FIRST.txt", "manifest.json", "video_meta.json"):
        src_path = os.path.join(args.work_dir, name)
        if os.path.exists(src_path):
            with open(src_path, encoding="utf-8") as sf:
                data = sf.read()
            with open(os.path.join(out_dir, name), "w", encoding="utf-8") as df:
                df.write(data)

    print(f"[scene] analysis complete for {args.job_id}. Assets ready.", flush=True)


if __name__ == "__main__":
    sys.exit(main())
