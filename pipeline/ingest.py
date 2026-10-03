"""CastCut Stage A ingestion — ported from ClipForge's stage_a/ingest.py pattern.

Supported sources (same families ClipForge accepts):
  direct_url   — any direct https:// video file URL
  gdrive       — Google Drive share link (confirm-token flow)
  magnet       — magnet: URI via aria2 (no seeding)
  torrent_file — a .torrent committed at jobs/<job_id>/source.torrent, via aria2

Output: work/source.<ext> + jobs/<job_id>/ingest.json (duration/size/codec).
The workflow uploads work/source.* to release castcut-<job_id>.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.parse

from .utils import probe_video, run

CHUNK = 1 << 20
UA = "castcut/1.0 (github actions)"


def _pick_ext(url: str, content_type: str = "") -> str:
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("mp4", "mkv", "webm", "mov", "avi", "m4v", "ts"):
        return ext
    if "webm" in content_type:
        return "webm"
    if "matroska" in content_type:
        return "mkv"
    return "mp4"


def _stream_download(url: str, dest: str, headers: dict | None = None) -> int:
    import requests

    for attempt in range(1, 5):
        mode, existing = "wb", 0
        hdrs = dict(headers or {})
        hdrs.setdefault("User-Agent", UA)
        if os.path.exists(dest):
            existing = os.path.getsize(dest)
            if existing:
                hdrs["Range"] = f"bytes={existing}-"
                mode = "ab"
        with requests.get(url, headers=hdrs, stream=True, timeout=(30, 120)) as resp:
            if resp.status_code in (401, 403, 404):
                raise RuntimeError(f"download rejected: HTTP {resp.status_code} for {url}")
            if attempt == 1 and resp.status_code >= 400:
                raise RuntimeError(f"download failed: HTTP {resp.status_code} for {url}")
            with open(dest, mode) as fh:
                for chunk in resp.iter_content(CHUNK):
                    if chunk:
                        fh.write(chunk)
        return os.path.getsize(dest)
    raise RuntimeError("unreachable")


def _download_gdrive(url: str, dest: str) -> None:
    """Google Drive share link -> direct bytes, handling the virus-scan confirm page."""
    import requests

    m = re.search(r"/d/([A-Za-z0-9_-]{10,})", url) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", url)
    if not m:
        raise RuntimeError("could not parse a Google Drive file id from the URL")
    file_id = m.group(1)
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    resp = sess.get(f"https://drive.google.com/uc?export=download&id={file_id}", stream=True, timeout=60)
    token = None
    for key in resp.cookies:
        if key.startswith("download_warning"):
            token = resp.cookies[key]
    if not token and "confirm=" in resp.text[:200000]:
        m2 = re.search(r'confirm=([0-9A-Za-z_-]+)&amp;id=', resp.text) or re.search(r'confirm=([0-9A-Za-z_-]+)&id=', resp.text)
        if m2:
            token = m2.group(1)
    params = {"export": "download", "id": file_id}
    if token:
        params["confirm"] = token
    resp = sess.get("https://drive.google.com/uc", params=params, stream=True, timeout=(30, 120))
    if "text/html" in resp.headers.get("Content-Type", ""):
        raise RuntimeError("Google Drive returned an HTML page (file not public / quota exceeded)")
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(CHUNK):
            if chunk:
                fh.write(chunk)


def _download_torrent(payload: str, is_file: bool, work_dir: str) -> str:
    """aria2 magnet/.torrent download, seeding disabled; returns largest media file."""
    dl_dir = os.path.join(work_dir, "torrent")
    os.makedirs(dl_dir, exist_ok=True)
    cmd = [
        "aria2c", "--dir", dl_dir, "--seed-time=0", "--max-upload-limit=1K",
        "--bt-tracker-connect-timeout=20", "--bt-tracker-timeout=20",
        "--summary-interval=15", "--max-connection-per-server=16", "--split=16",
        "--file-allocation=none", "--follow-torrent=mem",
    ]
    if is_file:
        cmd.append(payload)
    else:
        cmd.append(payload if payload.startswith("magnet:") else f"magnet:?xt=urn:btih:{payload}")
    print("+ " + " ".join(cmd[:3]) + " ...", flush=True)
    subprocess_ok = False
    last_rc = -1
    for attempt in range(1, 4):
        proc = __import__("subprocess").run(cmd + ["--max-download-limit=0"], timeout=3600)
        last_rc = proc.returncode
        if last_rc == 0:
            subprocess_ok = True
            break
        print(f"aria2 exited rc={last_rc} (attempt {attempt})", flush=True)
        time.sleep(10)
    if not subprocess_ok:
        raise RuntimeError(f"aria2 failed (rc={last_rc}) — torrent may have no seeds")
    media = []
    for root, _dirs, files in os.walk(dl_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts"):
                media.append(os.path.join(root, name))
    if not media:
        raise RuntimeError("torrent contained no video file")
    return max(media, key=os.path.getsize)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    args = ap.parse_args()

    req_path = os.path.join(args.jobs_root, args.job_id, "ingest-request.json")
    with open(req_path, encoding="utf-8") as fh:
        req = json.load(fh)
    src = req.get("source", {})
    kind = src.get("kind", "")
    os.makedirs(args.work_dir, exist_ok=True)

    tmp = os.path.join(args.work_dir, "download.bin")
    source_path = None

    if kind == "direct_url":
        url = src["url"]
        _stream_download(url, tmp)
        ext = _pick_ext(url)
        source_path = os.path.join(args.work_dir, f"source.{ext}")
        os.replace(tmp, source_path)
    elif kind == "gdrive":
        _download_gdrive(src["url"], tmp)
        ext = _pick_ext(src["url"])
        source_path = os.path.join(args.work_dir, f"source.{ext}")
        os.replace(tmp, source_path)
    elif kind == "magnet":
        picked = _download_torrent(src["magnet"], False, args.work_dir)
        ext = os.path.splitext(picked)[1].lower().lstrip(".") or "mkv"
        source_path = os.path.join(args.work_dir, f"source.{ext}")
        shutil.copy2(picked, source_path)
    elif kind == "torrent_file":
        tpath = os.path.join(args.jobs_root, args.job_id, "source.torrent")
        if not os.path.exists(tpath):
            raise RuntimeError(f"missing {tpath} — the site must upload the .torrent first")
        picked = _download_torrent(tpath, True, args.work_dir)
        ext = os.path.splitext(picked)[1].lower().lstrip(".") or "mkv"
        source_path = os.path.join(args.work_dir, f"source.{ext}")
        shutil.copy2(picked, source_path)
    else:
        raise RuntimeError(f"unsupported source kind: {kind!r}")

    info = probe_video(source_path)
    if not info["duration"] or info["duration"] < 1:
        raise RuntimeError(f"downloaded file does not look like a video (duration={info['duration']})")

    rec = {
        "version": 1,
        "job_id": args.job_id,
        "source_kind": kind,
        "title": req.get("title", ""),
        "asset_name": os.path.basename(source_path),
        "size_bytes": info["size_bytes"],
        "duration": round(info["duration"], 3),
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": info["has_audio"],
        "ingested_at_epoch": int(time.time()),
    }
    with open(os.path.join(args.jobs_root, args.job_id, "ingest.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps(rec, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
