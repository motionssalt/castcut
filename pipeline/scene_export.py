"""CastCut Mode 2 export — validate production JSON, cut, join, and compress.
Tolerant of markdown fences, surrounding text, BOM, smart quotes, and trailing commas.
The JSON is validated with clear, numbered error messages written into job status.
Cuts are performed using fast intermediate slicing and joined with hard cuts, followed by
the shared HandBrake-style visually-lossless anime compression pass (x264 CRF 23, medium, tune animation, AAC 128k stereo).
Guarantees exactly ONE audio track in output (the chosen track).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

from .utils import (clean_and_parse_json, concat_segments, encode_final_export,
                    human_size, parse_time, probe_video)


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
    ap.add_argument("--audio-track", default=None, help="Audio track index (0-based audio_index)")
    ap.add_argument("--export-id", default=None, help="Unique export ID for hybrid project model")
    args = ap.parse_args()

    scene_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    prod_path = os.path.join(scene_dir, "production.json")

    with open(prod_path, encoding="utf-8") as fh:
        raw_text = fh.read()

    data, json_err = clean_and_parse_json(raw_text)
    if json_err or not data:
        raise RuntimeError(f"production.json parsing failed: {json_err or 'root must be an object'}")

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

    audio_tracks = meta.get("audio_tracks", [])
    has_audio = len(audio_tracks) > 0

    chosen_audio_track = None
    chosen_track_info = None
    if has_audio:
        if args.audio_track is not None:
            chosen_audio_track = int(args.audio_track)
        elif data.get("audio_track") is not None:
            chosen_audio_track = int(data.get("audio_track"))
        elif data.get("metadata", {}).get("audio_track") is not None:
            chosen_audio_track = int(data.get("metadata", {}).get("audio_track"))
        else:
            choice_p = os.path.join(args.jobs_root, args.job_id, "audio_choice.json")
            if os.path.isfile(choice_p):
                try:
                    chosen_audio_track = int(json.load(open(choice_p)).get("audio_track"))
                except Exception:
                    pass
        if chosen_audio_track is not None and not 0 <= chosen_audio_track < len(audio_tracks):
            raise ValueError("Selected audio track is unavailable; refusing to substitute another track")
        if chosen_audio_track is None:
            def_t = next((t for t in audio_tracks if t.get("is_default")), None)
            chosen_audio_track = def_t["audio_index"] if def_t else 0

        chosen_track_info = next((t for t in audio_tracks if t.get("audio_index") == chosen_audio_track), None)
        print(f"[scene] Chosen audio track: {chosen_audio_track} ({chosen_track_info.get('label') if chosen_track_info else 'unknown'})", flush=True)

        choice_p = os.path.join(args.jobs_root, args.job_id, "audio_choice.json")
        try:
            with open(choice_p, "w", encoding="utf-8") as fh:
                json.dump({"audio_track": chosen_audio_track, "updated_at_epoch": int(time.time())}, fh, indent=2)
        except Exception as exc:
            print(f"[scene] warning: could not write audio_choice.json: {exc}", flush=True)
    else:
        print("[scene] Source has no audio streams; exporting video without audio", flush=True)

    total_cut_duration = sum(e - s for s, e in segments)
    print(f"[scene-export] {len(segments)} segments, total duration {total_cut_duration:.1f}s", flush=True)

    # One shared literal ClipForge render -> terminal compression path.
    export_id = args.export_id or f"exp-{args.job_id}-scene"
    asset_name = f"castcut-export-{export_id}.mp4" if args.export_id else f"castcut-scene-{args.job_id}.mp4"
    final = os.path.join(args.work_dir, asset_name)
    verif = export_segments(src, segments, final, audio_track=chosen_audio_track,
                            temp_dir=args.work_dir)
    s_before = verif['size_before_bytes']
    s_after = verif["size_bytes"]

    print(f"[scene-export] size before compression: {human_size(s_before)}", flush=True)
    print(f"[scene-export] size after  compression: {human_size(s_after)} "
          f"({100 * s_after / max(1, s_before):.0f}% of intermediate)", flush=True)
    print(f"[scene-export] effective bitrate: {verif['bitrate_kbps']} kbps ({verif['bitrate_bps']} bps)", flush=True)

    ai_label = data.get("title", "") or "AI Scene Edit"
    summary = {
        "version": 1,
        "export_id": export_id,
        "project_id": args.job_id,
        "job_id": args.job_id,
        "type": "ai",
        "label": ai_label,
        "ok": True,
        "title": ai_label,
        "segments": len(segments),
        "total_duration": round(total_cut_duration, 1),
        "size_before_bytes": s_before,
        "size_after_bytes": s_after,
        "size_bytes": s_after,
        "size_before_human": human_size(s_before),
        "size_after_human": human_size(s_after),
        "size_human": human_size(s_after),
        "bitrate_kbps": verif["bitrate_kbps"],
        "video_bitrate_kbps": verif["video_bitrate_kbps"],
        "bitrate_bps": verif["bitrate_bps"],
        "effective_bitrate": f"{verif['bitrate_kbps']} kbps",
        "audio_track": chosen_audio_track,
        "audio_track_label": chosen_track_info.get("label") if chosen_track_info else ("No audio" if not has_audio else None),
        "asset_name": os.path.basename(final),
        "encode_path": "ClipForge vendored render/compress: H.264 High@L4.0, 1080p, 0 B-frames, mp42, faststart, AAC 128k stereo, <=2000 kbps",
        "status": "done",
    }

    # Write legacy scene/export.json
    os.makedirs(scene_dir, exist_ok=True)
    with open(os.path.join(scene_dir, "export.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    # Write project exports/<export_id>.json
    exports_dir = os.path.join(args.jobs_root, args.job_id, "exports")
    os.makedirs(exports_dir, exist_ok=True)
    with open(os.path.join(exports_dir, f"{export_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
