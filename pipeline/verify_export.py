"""CastCut pre-publish export verification CLI.
Enforces that every finished export satisfies the Android playback contract
and stays at or below the 2000 kbps video bitrate ceiling.
Usage:
    python -m pipeline.verify_export <file-or-glob> [...]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pipeline.utils import validate_export_playback_and_size


def main() -> None:
    ap = argparse.ArgumentParser(description="CastCut export verification CLI")
    ap.add_argument("paths", nargs="+", help="MP4 file path(s) or glob patterns to verify")
    ap.add_argument("--no-audio", action="store_true", help="Expect 0 audio streams")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        matched = sorted(glob.glob(p))
        if matched:
            files.extend(matched)
        elif os.path.isfile(p):
            files.append(p)

    if not files:
        print(f"ERROR: No export files found matching: {args.paths}", file=sys.stderr)
        sys.exit(1)

    print(f"[verify_export] Verifying {len(files)} export file(s)...", flush=True)
    all_ok = True
    for f in files:
        try:
            report = validate_export_playback_and_size(f, expect_audio=not args.no_audio)
            print(f"[verify_export] PASS: {f}")
            print(f"  Duration: {report['duration']}s, Size: {report['size_human']} ({report['size_bytes']:,} bytes)")
            print(f"  Video: {report['vcodec']} {report['profile']}@L{report['level']/10:.1f}, {report['width']}x{report['height']}, {report['fps']} fps CFR")
            print(f"  Video bitrate: {report['video_bitrate_kbps']} kbps (Limit <= 2000 kbps)")
            print(f"  Audio: {report['audio_streams']} stream ({report['audio_codec']} stereo)")
            print(f"  Container: faststart={report['faststart']}, has_elst={report['has_elst']}")
        except Exception as exc:
            print(f"[verify_export] FAIL: {f}\n  Error: {exc}", file=sys.stderr)
            all_ok = False

    if not all_ok:
        sys.exit(1)
    print("[verify_export] All files verified successfully!", flush=True)


if __name__ == "__main__":
    main()
