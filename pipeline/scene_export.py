"""CastCut Mode 2 export — validate production JSON, cut FULL QUALITY, join.

The JSON is validated with clear, numbered error messages (written into the
job status so the UI can show them). Segments are joined in the given order
with hard cuts. Full quality here: x264 CRF 14, audio kept at 192k — the
heavy compression pass is a Mode 1 thing; Mode 2 output is the edit master.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .utils import (concat_segments, human_size, parse_time, probe_video,
                    run)


def validate_production(data, duration: float) -> tuple[list, list]:
    """Return (errors, segments). errors are human-readable strings."""
    errors = []
    if not isinstance(data, dict):
        return ["the JSON root must be an object"], []
    if "segments" not in data:
        return ['missing required key "segments"'], []
    segs = data["segments"]
    if not isinstance(segs, list) or not segs:
        return ['"segments" must be a non-empty array'], []
    if len(segs) > 200:
        return [f"too many segments ({len(segs)}); max is 200"], []
    out = []
    for i, seg in enumerate(segs, start=1):
        where = f"segment {i}"
        if not isinstance(seg, dict):
            errors.append(f"{where}: must be an object with start/end")
            continue
        if "start" not in seg or "end" not in seg:
            errors.append(f"{where}: missing 'start' or 'end'")
            continue
        try:
            s = parse_time(seg["start"])
            e = parse_time(seg["end"])
        except ValueError as exc:
            errors.append(f"{where}: {exc}")
            continue
        if s < 0:
            errors.append(f"{where}: start ({s:.2f}s) is negative")
            continue
        if e <= s:
            errors.append(f"{where}: end ({e:.2f}s) must be greater than start ({s:.2f}s)")
            continue
        if duration and s >= duration:
            errors.append(f"{where}: start ({s:.2f}s) is beyond the video duration ({duration:.1f}s)")
            continue
        if duration and e > duration + 1.0:
            errors.append(f"{where}: end ({e:.2f}s) exceeds the video duration ({duration:.1f}s) — clamped")
            e = duration
        out.append((s, e))
    return errors, out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    args = ap.parse_args()

    scene_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    prod_path = os.path.join(scene_dir, "production.json")
    with open(prod_path, encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"production.json is not valid JSON: line {exc.lineno} col {exc.colno}: {exc.msg}")

    src = None
    for cand in glob.glob(os.path.join(args.work_dir, "source.*")):
        if os.path.splitext(cand)[1].lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
            src = cand
            break
    if not src:
        raise RuntimeError("no source video found in work dir")
    meta = probe_video(src)

    errors, segments = validate_production(data, meta["duration"])
    if errors:
        report = {"version": 1, "ok": False, "errors": errors}
        with open(os.path.join(scene_dir, "validation.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        raise RuntimeError("production JSON failed validation:\n- " + "\n- ".join(errors))

    print(f"[scene-export] {len(segments)} segments, "
          f"total {sum(e - s for s, e in segments):.1f}s", flush=True)

    final = os.path.join(args.work_dir, f"castcut-scene-{args.job_id}.mp4")
    concat_segments(
        src=src,
        segments=segments,
        output_path=final,
        has_audio=meta["has_audio"],
        crf=14,
        preset="medium",
        temp_dir=args.work_dir,
    )

    size = os.path.getsize(final)
    summary = {
        "version": 1,
        "job_id": args.job_id,
        "ok": True,
        "title": data.get("title", ""),
        "segments": len(segments),
        "total_duration": round(sum(e - s for s, e in segments), 1),
        "size_bytes": size,
        "size_human": human_size(size),
        "asset_name": os.path.basename(final),
        "quality": "full (x264 CRF 14, no heavy compression)",
    }
    with open(os.path.join(scene_dir, "export.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
