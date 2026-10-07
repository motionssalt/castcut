"""CastCut job status writer (ClipForge pattern, stdlib-only).

Every workflow writes jobs/<job_id>/status.json before and after risky steps so
the site can poll progress without a terminal. New records get created_at and
expires_at (now + TTL) for the cleanup sweep.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

DEFAULT_TTL_SECONDS = None  # Persistent by default (projects persist until deleted)

SUCCESS_STATES = frozenset({
    "clips_ready",
    "scene_ready",
    "ready",
    "done",
    "hybrid_ready",
    "ingested",
    "awaiting_selection",
    "faces_ready",
    "scene_awaiting_json",
})

FAILURE_STATES = frozenset({
    "failed",
    "clips_failed",
    "scene_failed",
    "ingest_failed",
    "faces_failed",
    "select_failed",
})


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


def write_status(
    *args,
    message: str = "",
    ttl_seconds: int | None = None,
    jobs_root: str = "jobs",
    force: bool = False,
    **extra,
) -> dict:
    # Resolve job_id and state from args or kwargs/extra without collision
    resolved_job_id = args[0] if len(args) >= 1 else extra.pop("job_id", None)
    extra.pop("job_id", None)
    resolved_state = args[1] if len(args) >= 2 else extra.pop("state", None)
    extra.pop("state", None)

    if not resolved_job_id:
        raise ValueError("write_status requires job_id (positional or keyword)")
    if not resolved_state:
        raise ValueError("write_status requires state (positional or keyword)")

    if len(args) >= 3 and not message:
        message = str(args[2])

    job_id = str(resolved_job_id)
    state = str(resolved_state)

    rec = read_status(job_id, jobs_root)
    now = time.time()

    # Guard: never overwrite a terminal successful export/ready state with a failure
    # unless explicitly forced (prevents failed status step from masking real work success).
    current_state = rec.get("state")
    if (
        not force
        and state in FAILURE_STATES
        and current_state in {"clips_ready", "scene_ready", "ready", "done", "hybrid_ready"}
    ):
        print(
            f"[status guard] Ignoring failure state '{state}' for {job_id} "
            f"because it already reached successful terminal state '{current_state}'.",
            file=sys.stderr,
            flush=True,
        )
        return rec

    if not rec:
        rec = {
            "job_id": job_id,
            "created_at_epoch": int(now),
            "created_at": _iso(now),
        }
        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
        if ttl is not None:
            rec["expires_at_epoch"] = int(now + ttl)
            rec["expires_at"] = _iso(now + ttl)

    rec["state"] = state
    rec["message"] = message
    rec["updated_at_epoch"] = int(now)
    rec["updated_at"] = _iso(now)

    # Automatically preserve or populate title and torrent_name from ingest-request.json or files.json
    if not rec.get("title"):
        try:
            req_path = os.path.join(jobs_root, job_id, "ingest-request.json")
            if os.path.exists(req_path):
                with open(req_path, encoding="utf-8") as fh:
                    req_data = json.load(fh)
                    req_title = req_data.get("title") or req_data.get("torrent_name")
                    if req_title:
                        rec["title"] = req_title
                    if req_data.get("torrent_name") and not rec.get("torrent_name"):
                        rec["torrent_name"] = req_data["torrent_name"]
        except Exception:
            pass
    if not rec.get("title"):
        try:
            files_path = os.path.join(jobs_root, job_id, "files.json")
            if os.path.exists(files_path):
                with open(files_path, encoding="utf-8") as fh:
                    files_data = json.load(fh)
                    t_name = files_data.get("torrent_name")
                    if t_name:
                        rec["title"] = t_name
                        if not rec.get("torrent_name"):
                            rec["torrent_name"] = t_name
        except Exception:
            pass

    # Sanitize and safely merge extra fields
    # Never allow extra to re-inject job_id, state, message, or internal controls
    for forbidden in ("job_id", "state", "ttl_seconds", "jobs_root", "force"):
        extra.pop(forbidden, None)

    for key, value in extra.items():
        if value is not None:
            try:
                # Test json serializability of value
                json.dumps(value)
                rec[key] = value
            except (TypeError, ValueError) as err:
                print(
                    f"[status warning] Omitting non-serializable extra key '{key}': {err}",
                    file=sys.stderr,
                    flush=True,
                )

    os.makedirs(os.path.join(jobs_root, job_id), exist_ok=True)
    tmp = status_path(jobs_root, job_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, status_path(jobs_root, job_id))
    print(f"[status] {job_id}: {state} — {message}", flush=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(description="CastCut status writer")
    ap.add_argument("job_id", help="Job / Project ID")
    ap.add_argument("--state", required=True, help="Status state string")
    ap.add_argument("--message", default="", help="Status message")
    ap.add_argument("--ttl-seconds", type=int, default=None, help="TTL in seconds (default 24h)")
    ap.add_argument("--jobs-root", default="jobs", help="Jobs directory root")
    ap.add_argument("--release-tag", default=None, help="Release tag")
    ap.add_argument("--workflow-run-url", default=None, help="GitHub Actions run URL")
    ap.add_argument("--step", default=None, help="Current step name")
    ap.add_argument("--percent", type=float, default=None, help="Progress percentage (0-100)")
    ap.add_argument("--failed-step", default=None, help="Name of failing step")
    ap.add_argument("--set-json", default=None, help="Extra JSON object merged into record")
    ap.add_argument("--force", action="store_true", help="Force overwrite even if guard triggers")
    ap.add_argument("--title", default=None, help="Job title / project name")
    args = ap.parse_args()

    extra = {}
    if args.title is not None:
        extra["title"] = args.title
    if args.release_tag is not None:
        extra["release_tag"] = args.release_tag
    if args.workflow_run_url is not None:
        extra["workflow_run_url"] = args.workflow_run_url
    if args.step is not None:
        extra["step"] = args.step
    if args.percent is not None:
        extra["percent"] = args.percent
    if args.failed_step is not None:
        extra["failed_step"] = args.failed_step

    if args.set_json:
        try:
            parsed = json.loads(args.set_json)
            if isinstance(parsed, dict):
                # Remove protected keys from user/caller provided JSON
                for k in ("job_id", "state", "ttl_seconds", "jobs_root", "force"):
                    parsed.pop(k, None)
                extra.update(parsed)
            else:
                print(
                    f"[status warning] --set-json was not a JSON object: {type(parsed)}",
                    file=sys.stderr,
                    flush=True,
                )
        except Exception as exc:
            print(
                f"[status warning] Could not parse --set-json ({exc}); ignoring extra metadata",
                file=sys.stderr,
                flush=True,
            )

    # Clean extra dictionary defensively before passing
    for k in ("job_id", "state", "ttl_seconds", "jobs_root", "force"):
        extra.pop(k, None)

    write_status(
        args.job_id,
        args.state,
        message=args.message,
        ttl_seconds=args.ttl_seconds,
        jobs_root=args.jobs_root,
        force=args.force,
        **extra,
    )


if __name__ == "__main__":
    main()

