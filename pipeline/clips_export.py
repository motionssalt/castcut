"""CastCut Mode 1 Pipeline B — ordered character segment extraction + export.
Rules (from the build contract):
  * Characters appear in the order the user selected them; all of character
    1's segments (chronological), then character 2's, etc. Never interleaved.
  * A time range containing two selected characters is assigned to the
    earliest-selected character only — nothing is duplicated.
  * Hard cuts only, joined into ONE file.
  * Shared anime HandBrake-style compression (x264 CRF 23, medium preset, tune animation).
  * Exactly ONE audio track in output (the chosen track, stereo AAC 128k).
  * Before/after sizes and effective bitrates logged and recorded in export summary.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

from .utils import (concat_segments, encode_final_export, human_size,
                    merge_intervals, pad_intervals, probe_video,
                    resolve_quality_settings, subtract_intervals)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--gap", type=float, default=None, help="merge gap override (s)")
    ap.add_argument("--pad", type=float, default=None, help="pad override (s)")
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--quality", default="normal", help="Quality profile: normal (default), small, high")
    ap.add_argument("--audio-track", default=None, help="Audio track index (0-based audio_index)")
    ap.add_argument("--export-id", default=None, help="Unique export ID for hybrid project model")
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

    # Apply merge groups: union ranges of grouped characters under the group's first member id.
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

    meta = probe_video(src)
    audio_tracks = meta.get("audio_tracks", [])
    has_audio = len(audio_tracks) > 0

    chosen_audio_track = None
    chosen_track_info = None
    if has_audio:
        if args.audio_track is not None:
            chosen_audio_track = int(args.audio_track)
        elif selection.get("audio_track") is not None:
            chosen_audio_track = int(selection.get("audio_track"))
        else:
            choice_p = os.path.join(args.jobs_root, args.job_id, "audio_choice.json")
            if os.path.isfile(choice_p):
                try:
                    chosen_audio_track = int(json.load(open(choice_p)).get("audio_track"))
                except Exception:
                    pass
        if chosen_audio_track is None or chosen_audio_track >= len(audio_tracks):
            def_t = next((t for t in audio_tracks if t.get("is_default")), None)
            chosen_audio_track = def_t["audio_index"] if def_t else 0

        chosen_track_info = next((t for t in audio_tracks if t.get("audio_index") == chosen_audio_track), None)
        lbl = chosen_track_info.get("label") if chosen_track_info else "unknown"
        print(f"[clips] Chosen audio track: {chosen_audio_track} ({lbl})", flush=True)

        choice_p = os.path.join(args.jobs_root, args.job_id, "audio_choice.json")
        try:
            with open(choice_p, "w", encoding="utf-8") as fh:
                json.dump({"audio_track": chosen_audio_track, "updated_at_epoch": int(time.time())}, fh, indent=2)
        except Exception as exc:
            print(f"[clips] warning: could not write audio_choice.json: {exc}", flush=True)
    else:
        print("[clips] Source has no audio streams; exporting video without audio", flush=True)

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
        ranges = subtract_intervals(ranges, taken)
        ranges = pad_intervals(ranges, pad, duration)
        ranges = merge_intervals(ranges, gap=0.2)
        for s, e in ranges:
            segments.append((s, e))
            taken.append((s, e))
        per_char_counts[cid] = len(ranges)

    if not segments:
        raise RuntimeError("selection produced zero segments")

    total_cut_duration = sum(e - s for s, e in segments)
    print(f"[clips] segments per character: {per_char_counts}", flush=True)
    print(f"[clips] total segments: {len(segments)}, duration: {total_cut_duration:.1f}s", flush=True)

    # Pass 1: fast intermediate join
    joined = os.path.join(args.work_dir, "clips_joined.mp4")
    concat_segments(
        src=src,
        segments=segments,
        output_path=joined,
        has_audio=has_audio,
        audio_track=chosen_audio_track,
        crf=18,
        preset="veryfast",
        temp_dir=args.work_dir,
    )
    s_before = os.path.getsize(joined)

    # Pass 2: shared anime HandBrake-style compression
    crf, preset = resolve_quality_settings(args.quality)
    if args.crf and args.crf != 23:
        crf = args.crf

    export_id = args.export_id or f"exp-{args.job_id}-clips"
    asset_name = f"castcut-export-{export_id}.mp4" if args.export_id else f"castcut-clips-{args.job_id}.mp4"
    final = os.path.join(args.work_dir, asset_name)

    verif = encode_final_export(
        input_path=joined,
        output_path=final,
        has_audio=has_audio,
        crf=crf,
        preset=preset,
        tune="animation",
        audio_bitrate="128k",
    )
    s_after = verif["size_bytes"]

    print(f"[clips] size before compression: {human_size(s_before)}", flush=True)
    print(f"[clips] size after  compression: {human_size(s_after)} "
          f"({100 * s_after / max(1, s_before):.0f}% of intermediate)", flush=True)
    print(f"[clips] effective bitrate: {verif['bitrate_kbps']} kbps ({verif['bitrate_bps']} bps)", flush=True)

    selected_chars = [c for c in order if c in per_char_counts]
    char_label = ", ".join(selected_chars[:3])
    if len(selected_chars) > 3:
        char_label += f" (+{len(selected_chars) - 3} others)"

    summary = {
        "version": 1,
        "export_id": export_id,
        "project_id": args.job_id,
        "job_id": args.job_id,
        "type": "clips",
        "label": char_label or "Selected characters",
        "order": selected_chars,
        "per_char_segments": per_char_counts,
        "total_segments": len(segments),
        "total_duration": round(total_cut_duration, 1),
        "size_before_bytes": s_before,
        "size_after_bytes": s_after,
        "size_bytes": s_after,
        "size_before_human": human_size(s_before),
        "size_after_human": human_size(s_after),
        "size_human": human_size(s_after),
        "bitrate_kbps": verif["bitrate_kbps"],
        "bitrate_bps": verif["bitrate_bps"],
        "effective_bitrate": f"{verif['bitrate_kbps']} kbps",
        "audio_track": chosen_audio_track,
        "audio_track_label": chosen_track_info.get("label") if chosen_track_info else ("No audio" if not has_audio else None),
        "asset_name": os.path.basename(final),
        "quality": f"anime HandBrake (x264 CRF {crf}, {preset}, tune animation, AAC 128k)",
        "status": "done",
    }

    # Write legacy clips/export.json
    os.makedirs(clips_dir, exist_ok=True)
    with open(os.path.join(clips_dir, "export.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    # Write project exports/<export_id>.json
    exports_dir = os.path.join(args.jobs_root, args.job_id, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    with open(os.path.join(exports_dir, f"{export_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
