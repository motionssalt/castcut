"""CastCut Mode 1 Pipeline B — ordered character segment extraction + export.

Rules (from the build contract):
  * Characters appear in the order the user selected them; all of character
    1's segments (chronological), then character 2's, etc. Never interleaved.
  * A time range containing two selected characters is assigned to the
    earliest-selected character only — nothing is duplicated.
  * Hard cuts only, joined into ONE file.
  * Final pass: HandBrake-style visually-lossless compression (x264 CRF 23,
    slow preset), resolution/fps/aspect/audio unchanged. Before/after sizes
    are logged into the job status.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .utils import (build_concat_filter, human_size, merge_intervals,
                    pad_intervals, probe_video, run, subtract_intervals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--gap", type=float, default=None, help="merge gap override (s)")
    ap.add_argument("--pad", type=float, default=None, help="pad override (s)")
    ap.add_argument("--crf", type=int, default=23)
    args = ap.parse_args()

    clips_dir = os.path.join(args.jobs_root, args.job_id, "clips")
    with open(os.path.join(clips_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    with open(os.path.join(clips_dir, "selection.json"), encoding="utf-8") as fh:
        selection = json.load(fh)

    gap = args.gap if args.gap is not None else float(selection.get("gap_threshold", 2.0))
    pad = args.pad if args.pad is not None else float(selection.get("padding", 0.4))
    order = selection.get("order", [])
    ignored = set(selection.get("ignored", []))
    merge_groups = selection.get("merge_groups", [])
    if not order:
        raise RuntimeError("selection.json has an empty order list")

    chars = {c["id"]: c for c in manifest.get("characters", [])}

    # Apply merge groups: union ranges of grouped characters under the group's
    # first member id.
    grouped = {}
    for group in merge_groups:
        members = [m for m in group if m in chars and m not in ignored]
        if not members:
            continue
        ranges = []
        for m in members:
            ranges.extend(tuple(r) for r in chars[m]["ranges"])
        grouped[members[0]] = merge_intervals(ranges, gap=gap)

    duration = float(manifest.get("duration", 0))
    src = None
    for cand in glob.glob(os.path.join(args.work_dir, "source.*")):
        if os.path.splitext(cand)[1].lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
            src = cand
            break
    if not src:
        raise RuntimeError("no source video found in work dir")
    has_audio = probe_video(src)["has_audio"]

    segments = []
    taken = []
    per_char_counts = {}
    for cid in order:
        if cid in ignored:
            continue
        if cid in grouped:
            ranges = grouped[cid]
        elif cid in chars:
            ranges = merge_intervals([tuple(r) for r in chars[cid]["ranges"]], gap=gap)
        else:
            continue
        ranges = subtract_intervals(ranges, taken)  # no duplicated time ranges
        ranges = pad_intervals(ranges, pad, duration)
        ranges = merge_intervals(ranges, gap=0.2)
        for s, e in ranges:
            segments.append((s, e))
            taken.append((s, e))
        per_char_counts[cid] = len(ranges)

    if not segments:
        raise RuntimeError("selection produced zero segments")

    print(f"[clips] segments per character (selection order): {per_char_counts}", flush=True)
    print(f"[clips] total segments: {len(segments)}, total duration "
          f"{sum(e - s for s, e in segments):.1f}s", flush=True)

    filt = build_concat_filter(segments, has_audio)
    filter_path = os.path.join(args.work_dir, "concat.txt")
    with open(filter_path, "w", encoding="utf-8") as fh:
        fh.write(filt)

    # Pass 1: join with hard cuts (fast intermediate).
    joined = os.path.join(args.work_dir, "clips_joined.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-filter_complex_script", filter_path, "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[a]"]
    cmd += ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", joined]
    run(cmd)

    # Pass 2: HandBrake-style visually-lossless compression (CRF 23 slow).
    final = os.path.join(args.work_dir, f"castcut-clips-{args.job_id}.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", joined,
           "-c:v", "libx264", "-crf", str(args.crf), "-preset", "slow", "-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
    cmd += ["-movflags", "+faststart", final]
    run(cmd)

    s_before = os.path.getsize(joined)
    s_after = os.path.getsize(final)
    print(f"[clips] size before compression: {human_size(s_before)}", flush=True)
    print(f"[clips] size after  compression: {human_size(s_after)} "
          f"({100 * s_after / max(1, s_before):.0f}% of intermediate)", flush=True)

    summary = {
        "version": 1,
        "job_id": args.job_id,
        "order": [c for c in order if c in per_char_counts],
        "per_char_segments": per_char_counts,
        "total_segments": len(segments),
        "total_duration": round(sum(e - s for s, e in segments), 1),
        "size_before_bytes": s_before,
        "size_after_bytes": s_after,
        "size_before_human": human_size(s_before),
        "size_after_human": human_size(s_after),
        "asset_name": os.path.basename(final),
    }
    with open(os.path.join(clips_dir, "export.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
