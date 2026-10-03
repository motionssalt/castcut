"""CastCut torrent metadata step — lists a torrent's files WITHOUT downloading
any content (ClipForge's ``awaiting_torrent_selection`` flow).

  * ``torrent_file`` — parses the committed ``jobs/<job_id>/source.torrent``.
  * ``magnet``       — aria2 ``--bt-metadata-only --bt-save-metadata`` fetch
                       (metadata only, never payload), then parses it.

Writes ``jobs/<job_id>/files.json``; the workflow then parks the job in
``awaiting_selection`` until the user picks file indices in the UI.

CLI: ``python -m pipeline.select <job_id> [--jobs-root jobs] [--work-dir work]``
Exit code 0 on success; any failure raises with a user-facing message.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import torrents

METADATA_TIMEOUT_SECONDS = 420  # 7 min to find a peer with the metadata


def fetch_magnet_metadata(magnet: str, meta_dir: str, timeout: int = METADATA_TIMEOUT_SECONDS) -> Path:
    """Fetch ONLY the .torrent metadata for a magnet via aria2 (no content)."""
    os.makedirs(meta_dir, exist_ok=True)
    cmd = [
        "aria2c", "--bt-metadata-only=true", "--bt-save-metadata=true",
        "--dir", meta_dir, "--seed-time=0", "--max-upload-limit=1K",
        "--bt-tracker-connect-timeout=15", "--bt-tracker-timeout=15",
        "--summary-interval=15", "--file-allocation=none",
        magnet,
    ]
    print("+ aria2c --bt-metadata-only=true --bt-save-metadata=true <magnet>", flush=True)
    try:
        proc = subprocess.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "metadata fetch timed out — the torrent may have no seeds, or its trackers are unreachable"
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"aria2 metadata fetch failed (rc={proc.returncode}) — no seeds or unreachable trackers"
        )
    info = torrents.inspect_magnet(magnet)
    return torrents.find_saved_metadata(Path(meta_dir), info["infohash_v1"])


def build_files_json(job_id: str, jobs_root: str = "jobs", work_dir: str = "work") -> dict:
    req_path = Path(jobs_root) / job_id / "ingest-request.json"
    with open(req_path, encoding="utf-8") as fh:
        req = json.load(fh)
    src = req.get("source", {})
    kind = src.get("kind")

    if kind == "torrent_file":
        tpath = Path(jobs_root) / job_id / "source.torrent"
        if not tpath.exists():
            raise RuntimeError(f"missing {tpath} — the site must commit the .torrent first")
        infohash = torrents.torrent_infohash_v1(tpath)
    elif kind == "magnet":
        magnet = src.get("magnet") or ""
        # Validate first so a malformed magnet fails fast with a clear error.
        infohash = torrents.inspect_magnet(magnet)["infohash_v1"]
        tpath = fetch_magnet_metadata(magnet, os.path.join(work_dir, "meta"))
    else:
        raise RuntimeError(f"the select step only applies to torrent sources, got {kind!r}")

    meta = torrents.inspect_torrent(tpath)  # raises if zero video candidates
    files = []
    for item in meta["files"]:
        is_video = (
            Path(item["path"]).suffix.lower() in torrents.VIDEO_EXTENSIONS
            and item["length"] > 0
        )
        files.append({
            "index": item["index"],
            "path": item["path"],
            "length": item["length"],
            "is_video": is_video,
        })
    videos = [f for f in files if f["is_video"]]
    if not videos:
        raise RuntimeError("torrent has no video files (.mp4/.mkv/.webm/.mov/.avi/.m4v/.ts)")

    payload = {
        "version": 1,
        "job_id": job_id,
        "source_kind": kind,
        "torrent_name": meta["name"],
        "infohash": infohash,
        "file_count": len(files),
        "total_bytes": meta["total_bytes"],
        "files": files,
        "video_indices": [f["index"] for f in videos],
        "single_video": len(videos) == 1,
        "fetched_at_epoch": int(time.time()),
    }
    out = Path(jobs_root) / job_id / "files.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, out)
    print(f"Wrote {out}: {len(files)} file(s), {len(videos)} video candidate(s)", flush=True)
    return payload


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    args = ap.parse_args()
    payload = build_files_json(args.job_id, args.jobs_root, args.work_dir)
    print(json.dumps({
        "torrent_name": payload["torrent_name"],
        "file_count": payload["file_count"],
        "video_indices": payload["video_indices"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, torrents.MagnetError, torrents.BencodeError, FileNotFoundError) as exc:
        print(f"SELECT-ERROR: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
