"""CastCut Mode 2 Stage A — analysis bundle for the AI agent.

Produces (ClipForge-style):
  transcript.json   — faster-whisper (tiny, CPU) auto-language transcription.
                      Degrades gracefully to an empty transcript on failure.
  scene_index.json  — ffmpeg scene-cut detection (scdet threshold 0.35).
  screenshots/      — one 480p JPEG per detected scene boundary (max 300),
                      zipped by the workflow.
  jobs/<job>/scene/* copies for the repo record.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

from .utils import fmt_time, probe_video, run


def _scene_index(src: str, work_dir: str, threshold: float = 0.35, max_shots: int = 300):
    shots_dir = os.path.join(work_dir, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    log = os.path.join(work_dir, "scdet.log")
    with open(log, "w") as lf:
        proc = __import__("subprocess").run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", src,
             "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
            stderr=lf, stdout=__import__("subprocess").DEVNULL)
    times = []
    pat = re.compile(r"pts_time:([0-9.]+)")
    with open(log, errors="ignore") as lf:
        for line in lf:
            if "showinfo" in line:
                m = pat.search(line)
                if m:
                    times.append(float(m.group(1)))
    times = sorted(set(round(t, 2) for t in times))
    if len(times) > max_shots:
        step = len(times) / max_shots
        times = [times[int(i * step)] for i in range(max_shots)]
    # One 480p screenshot per boundary.
    for i, t in enumerate(times, start=1):
        out = os.path.join(shots_dir, f"{i:04d}_{fmt_time(t).replace(':', '-')}.jpg")
        __import__("subprocess").run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(t), "-i", src, "-frames:v", "1", "-vf", "scale=480:-2",
             "-q:v", "6", out],
            stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").DEVNULL)
    return times


def _transcribe(src: str):
    try:
        from faster_whisper import WhisperModel  # type: ignore

        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments_iter, info = model.transcribe(src, vad_filter=True)
        segments = []
        for seg in segments_iter:
            segments.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                             "text": seg.text.strip()})
        return {"version": 1, "language": info.language,
                "language_probability": round(info.language_probability, 3),
                "model": "faster-whisper/tiny", "segments": segments}
    except Exception as exc:
        print(f"[scene] transcription failed ({exc}); continuing with empty transcript", flush=True)
        return {"version": 1, "language": None, "model": "faster-whisper/tiny",
                "error": str(exc)[:500], "segments": []}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    args = ap.parse_args()

    src = None
    for cand in glob.glob(os.path.join(args.work_dir, "source.*")):
        if os.path.splitext(cand)[1].lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
            src = cand
            break
    if not src:
        raise RuntimeError("no source video found in work dir")

    meta = probe_video(src)
    print(f"[scene] analyzing {meta['duration']:.1f}s source", flush=True)

    transcript = _transcribe(src)
    with open(os.path.join(args.work_dir, "transcript.json"), "w", encoding="utf-8") as fh:
        json.dump(transcript, fh, ensure_ascii=False, indent=2)
    print(f"[scene] transcript: {len(transcript['segments'])} segments "
          f"(lang={transcript.get('language')})", flush=True)

    boundaries = _scene_index(src, args.work_dir)
    scene_index = {"version": 1, "threshold": 0.35, "boundaries": boundaries,
                   "count": len(boundaries)}
    with open(os.path.join(args.work_dir, "scene_index.json"), "w", encoding="utf-8") as fh:
        json.dump(scene_index, fh, indent=2)
    print(f"[scene] scene boundaries: {len(boundaries)}", flush=True)

    out_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    os.makedirs(out_dir, exist_ok=True)
    for name in ("transcript.json", "scene_index.json"):
        with open(os.path.join(args.work_dir, name), encoding="utf-8") as fh:
            data = fh.read()
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as fh:
            fh.write(data)
    with open(os.path.join(out_dir, "video_meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


if __name__ == "__main__":
    sys.exit(main())
