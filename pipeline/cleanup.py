"""CastCut cleanup sweep — expiry is DISABLED for projects (fix_round_4).

Projects (hybrid model) and everything attached to them — exports, face
manifests, thumbnails, scene-analysis data, job state — persist until the
user explicitly deletes the project. The raw source video is always safe to
lose because every downstream step re-downloads it via pipeline/refetch.py
from the durably committed ingest request + selection.

This sweep only touches legacy records that are BOTH non-persistent
(persistent == false) AND in a non-ready state; every ready state
(hybrid_ready, clips_ready, scene_ready, ingested, faces_ready,
scene_awaiting_json) and every record without an explicit
persistent:false flag is skipped. Runs on an hourly schedule.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = time.time()
    expired = []
    if not os.path.isdir(args.jobs_root):
        print("no jobs directory; nothing to do")
        return
    for job_id in sorted(os.listdir(args.jobs_root)):
        spath = os.path.join(args.jobs_root, job_id, "status.json")
        if not os.path.exists(spath):
            continue
        try:
            with open(spath, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        exp = rec.get("expires_at_epoch")
        state = rec.get("state", "")
        # Projects and their outputs persist until deleted by the user.
        # Active, ready, or project states never expire.
        if (
            not exp
            or state in ("expired", "cleaned")
            or rec.get("persistent", True)
            or state in ("hybrid_ready", "clips_ready", "scene_ready", "ingested", "faces_ready", "scene_awaiting_json")
        ):
            continue
        if now > float(exp):
            expired.append((job_id, spath, rec))

    for job_id, spath, rec in expired:
        print(f"expired: {job_id} (state={rec.get('state')}, "
              f"expires_at={rec.get('expires_at')})", flush=True)
        if args.dry_run:
            continue
        rec["state"] = "expired"
        rec["message"] = "Job expired (24h TTL) — source video and outputs were deleted."
        rec["expired_at_epoch"] = int(now)
        with open(spath, "w", encoding="utf-8") as fh:
            json.dump(rec, fh, indent=2, ensure_ascii=False)

    # The workflow reads this file and deletes the releases.
    with open(os.path.join(args.jobs_root, ".expired.json"), "w", encoding="utf-8") as fh:
        json.dump([j for j, _s, _r in expired], fh)
    print(f"{len(expired)} job(s) expired", flush=True)


if __name__ == "__main__":
    sys.exit(main())
