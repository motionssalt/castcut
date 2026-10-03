"""CastCut job status writer (ClipForge pattern, stdlib-only).

Every workflow writes jobs/<job_id>/status.json before and after risky steps so
the site can poll progress without a terminal. New records get created_at and
expires_at (now + TTL) for the cleanup sweep.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

DEFAULT_TTL_SECONDS = 86400  # 24h — see cleanup.yml / README


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_path(jobs_root: str, job_id: str) -> str:
    return os.path.join(jobs_root, job_id, "status.json")


def read_status(job_id: str, jobs_root: str = "jobs") -> dict:
    try:
        with open(status_path(jobs_root, job_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def write_status(job_id: str, state: str, message: str = "", ttl_seconds: int | None = None,
                 jobs_root: str = "jobs", **extra) -> dict:
    rec = read_status(job_id, jobs_root)
    now = time.time()
    if not rec:
        ttl = int(ttl_seconds or DEFAULT_TTL_SECONDS)
        rec = {
            "job_id": job_id,
            "created_at_epoch": int(now),
            "created_at": _iso(now),
            "expires_at_epoch": int(now + ttl),
            "expires_at": _iso(now + ttl),
        }
    rec.update({"state": state, "message": message,
                "updated_at_epoch": int(now), "updated_at": _iso(now)})
    for key, value in extra.items():
        if value is not None:
            rec[key] = value
    os.makedirs(os.path.join(jobs_root, job_id), exist_ok=True)
    tmp = status_path(jobs_root, job_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, status_path(jobs_root, job_id))
    print(f"[status] {job_id}: {state} — {message}", flush=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--state", required=True)
    ap.add_argument("--message", default="")
    ap.add_argument("--ttl-seconds", type=int, default=None)
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--release-tag", default=None)
    ap.add_argument("--workflow-run-url", default=None)
    ap.add_argument("--set-json", default=None, help="extra JSON object merged into the record")
    args = ap.parse_args()
    extra = {}
    if args.release_tag:
        extra["release_tag"] = args.release_tag
    if args.workflow_run_url:
        extra["workflow_run_url"] = args.workflow_run_url
    if args.set_json:
        extra.update(json.loads(args.set_json))
    write_status(args.job_id, args.state, args.message,
                 ttl_seconds=args.ttl_seconds, jobs_root=args.jobs_root, **extra)


if __name__ == "__main__":
    main()
