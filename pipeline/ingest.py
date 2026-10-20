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


def _download_torrent(payload: str, is_file: bool, work_dir: str,
                      select_indices: list | None = None) -> str:
    """aria2 magnet/.torrent download, seeding disabled.

    With ``select_indices`` (1-based torrent file indices — the ClipForge
    convention) aria2 fetches ONLY those files' pieces and this returns the
    download directory. Without it everything is downloaded and the largest
    media file is returned (legacy fallback).
    """
    dl_dir = os.path.join(work_dir, "torrent")
    os.makedirs(dl_dir, exist_ok=True)
    fallback_trackers = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.tracker.cl:1337/announce",
        "udp://opentracker.i2p.rocks:6969/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://explodie.org:6969/announce",
        "http://tracker.openbittorrent.com:80/announce",
    ]
    cmd = [
        "aria2c", "--dir", dl_dir, "--seed-time=0",
        "--bt-tracker-connect-timeout=5", "--bt-tracker-timeout=5",
        "--summary-interval=15", "--max-connection-per-server=16", "--split=16",
        "--file-allocation=none", "--follow-torrent=mem",
        "--enable-dht=true", "--enable-peer-exchange=true",
        "--dht-listen-port=6881-6999", "--listen-port=6881-6999",
        "--dht-entry-point=router.bittorrent.com:6881",
        f"--bt-tracker={",".join(fallback_trackers)}",
    ]
    if select_indices:
        cmd.append("--select-file=" + ",".join(str(i) for i in select_indices))
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
    if select_indices:
        return dl_dir
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
    source_paths: list = []
    selected_indices: list | None = None

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
    elif kind in ("magnet", "torrent_file"):
        from pathlib import Path
        from .torrents import resolve_downloaded_files
        if kind == "magnet":
            payload, is_file = src["magnet"], False
        else:
            tpath = os.path.join(args.jobs_root, args.job_id, "source.torrent")
            if not os.path.exists(tpath):
                raise RuntimeError(f"missing {tpath} — the site must upload the .torrent first")
            payload, is_file = tpath, True
        # The user picks which torrent files to download BEFORE this runs
        # (jobs/<job_id>/selection.json, written by the site's selection step).
        sel_path = os.path.join(args.jobs_root, args.job_id, "selection.json")
        if not os.path.exists(sel_path):
            raise RuntimeError(
                "no file selection — open the job in CastCut and pick which "
                "torrent files to download first (job parks in awaiting_selection)"
            )
        with open(sel_path, encoding="utf-8") as fh:
            selection = json.load(fh)
        selected_indices = [int(i) for i in selection.get("selected_indices", [])]
        wanted = [str(p) for p in selection.get("paths", [])]
        if not selected_indices or len(selected_indices) != len(wanted):
            raise RuntimeError("selection.json is missing selected_indices/paths")
        dl_dir = _download_torrent(payload, is_file, args.work_dir, selected_indices)
        picked_list = resolve_downloaded_files(Path(dl_dir), wanted)
        for n, picked in enumerate(picked_list):
            ext = os.path.splitext(picked)[1].lower().lstrip(".") or "mkv"
            name = f"source.{ext}" if n == 0 else f"source.{n + 1}.{ext}"
            sp = os.path.join(args.work_dir, name)
            shutil.copy2(picked, sp)
            source_paths.append(sp)
        source_path = source_paths[0]
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
        "audio_tracks": info.get("audio_tracks", []),
        "ingested_at_epoch": int(time.time()),
        "sources": [os.path.basename(p) for p in (source_paths or [source_path])],
    }
    if selected_indices:
        rec["selected_indices"] = selected_indices

    # fix_round_2 (port of ClipForge's release_gate): decide BEFORE any
    # upload whether the source video(s) fit within GitHub's per-asset
    # release limit — real on-disk sizes, safety margin, env-overridable
    # limit for testing.
    from . import release_size
    all_paths = source_paths or [source_path]
    rec["asset_sizes"] = {os.path.basename(p): os.path.getsize(p) for p in all_paths}
    rec["video_in_release"] = all(
        release_size.fits(s) for s in rec["asset_sizes"].values())
    if not rec["video_in_release"]:
        rec["video_excluded_reason"] = (
            "source video exceeds the per-asset release size limit; excluded "
            "from the release — it will be re-downloaded from the original "
            "source when processing starts (pipeline.refetch)")
        rec["refetch"] = {
            "request_path": f"jobs/{args.job_id}/ingest-request.json",
            "selection_path": (f"jobs/{args.job_id}/selection.json"
                               if selected_indices else None),
            "selected_indices": selected_indices or [],
        }
        print("SOURCE EXCLUDED FROM RELEASE (oversized): "
              + json.dumps(rec["asset_sizes"]), flush=True)
    with open(os.path.join(args.jobs_root, args.job_id, "ingest.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    print(json.dumps(rec, indent=2), flush=True)


if __name__ == "__main__":
    sys.exit(main())
