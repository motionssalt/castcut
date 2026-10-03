"""Migration script: maps legacy jobs into the Hybrid Project Model.

For all directories in jobs/:
- Maps legacy clips/export.json and scene/export.json into exports/<export_id>.json.
- Removes 24h auto-expiry (expires_at / expires_at_epoch) so project data persists.
- Sets is_project: true and normalizes state to hybrid_ready when analysis is present.
- Fully idempotent: can be run repeatedly without duplicating or corrupting records.

Run with:
    python -m pipeline.migrate_projects
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _load_json(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _save_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def migrate_all(jobs_root: str = "jobs") -> int:
    if not os.path.isdir(jobs_root):
        print(f"[migrate] No jobs directory found at {jobs_root}")
        return 0

    migrated_count = 0
    for item in sorted(os.listdir(jobs_root)):
        job_dir = os.path.join(jobs_root, item)
        if not os.path.isdir(job_dir):
            continue

        status_path = os.path.join(job_dir, "status.json")
        status = _load_json(status_path) or {}
        changed = False

        # Ensure exports directory exists
        exports_dir = os.path.join(job_dir, "exports")
        os.makedirs(exports_dir, exist_ok=True)

        # 1. Migrate clips/export.json
        clips_exp_path = os.path.join(job_dir, "clips", "export.json")
        clips_exp = _load_json(clips_exp_path)
        if clips_exp:
            exp_id = f"exp-{item}-clips"
            target_export = os.path.join(exports_dir, f"{exp_id}.json")
            if not os.path.exists(target_export):
                order = clips_exp.get("order", [])
                label = ", ".join(order[:3])
                if len(order) > 3:
                    label += f" (+{len(order) - 3} others)"
                exp_record = {
                    "version": 1,
                    "export_id": exp_id,
                    "project_id": item,
                    "job_id": item,
                    "type": "clips",
                    "label": label or "Clips cut",
                    "created_at": status.get("created_at") or clips_exp.get("created_at", ""),
                    "created_at_epoch": status.get("created_at_epoch") or int(time.time()),
                    "status": "done",
                    "size_bytes": clips_exp.get("size_after_bytes") or clips_exp.get("size_bytes", 0),
                    "size_human": clips_exp.get("size_after_human") or clips_exp.get("size_human", ""),
                    "asset_name": clips_exp.get("asset_name", f"castcut-clips-{item}.mp4"),
                    "release_tag": status.get("release_tag") or f"castcut-{item}",
                    "total_segments": clips_exp.get("total_segments", 0),
                    "total_duration": clips_exp.get("total_duration", 0.0),
                }
                _save_json(target_export, exp_record)
                print(f"[migrate] {item}: Migrated clips/export.json -> exports/{exp_id}.json")
                changed = True

        # 2. Migrate scene/export.json
        scene_exp_path = os.path.join(job_dir, "scene", "export.json")
        scene_exp = _load_json(scene_exp_path)
        if scene_exp:
            exp_id = f"exp-{item}-scene"
            target_export = os.path.join(exports_dir, f"{exp_id}.json")
            if not os.path.exists(target_export):
                label = scene_exp.get("title") or "AI Scene Edit"
                exp_record = {
                    "version": 1,
                    "export_id": exp_id,
                    "project_id": item,
                    "job_id": item,
                    "type": "ai",
                    "label": label,
                    "created_at": status.get("created_at") or scene_exp.get("created_at", ""),
                    "created_at_epoch": status.get("created_at_epoch") or int(time.time()),
                    "status": "done",
                    "size_bytes": scene_exp.get("size_bytes", 0),
                    "size_human": scene_exp.get("size_human", ""),
                    "asset_name": scene_exp.get("asset_name", f"castcut-scene-{item}.mp4"),
                    "release_tag": status.get("release_tag") or f"castcut-{item}",
                    "total_segments": scene_exp.get("segments", 0),
                    "total_duration": scene_exp.get("total_duration", 0.0),
                }
                _save_json(target_export, exp_record)
                print(f"[migrate] {item}: Migrated scene/export.json -> exports/{exp_id}.json")
                changed = True

        # 3. Update status.json: remove expiry, mark is_project: true
        if status:
            if "expires_at_epoch" in status:
                del status["expires_at_epoch"]
                changed = True
            if "expires_at" in status:
                del status["expires_at"]
                changed = True
            if not status.get("is_project"):
                status["is_project"] = True
                changed = True

            # If project has completed exports or manifests, normalize state to hybrid_ready
            current_state = status.get("state", "")
            has_manifest = os.path.exists(os.path.join(job_dir, "clips", "manifest.json"))
            if current_state in ("clips_ready", "scene_ready", "faces_ready") or has_manifest:
                if current_state not in ("hybrid_ready", "done"):
                    status["state"] = "hybrid_ready"
                    status["message"] = "Project ready! Select clips or generate AI scenes."
                    changed = True

            if changed:
                _save_json(status_path, status)
                print(f"[migrate] {item}: status.json updated to hybrid project model.")
                migrated_count += 1

    print(f"[migrate] Migration complete: {migrated_count} project(s) updated.")
    return migrated_count


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate legacy CastCut jobs to Hybrid Project Model")
    ap.add_argument("--jobs-root", default="jobs", help="Jobs directory root")
    args = ap.parse_args()
    migrate_all(jobs_root=args.jobs_root)


if __name__ == "__main__":
    main()
