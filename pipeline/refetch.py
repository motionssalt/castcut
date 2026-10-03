"""CastCut source re-fetch (fix_round_2) — port of ClipForge
``pipeline/stage_a/refetch_source.py``.

When ingestion excluded the source video from the release
(``video_in_release: false`` in ``jobs/<id>/ingest.json``), every downstream
step (faces, scene_analyze, clips_export, scene_export) calls this module
INSTEAD of a plain ``gh release download --pattern 'source.*'``.

  * ``video_in_release: true``  -> download ``source.*`` from the release
    (exactly what the workflows used to do — one code path for callers).
  * ``video_in_release: false`` -> re-download from the ORIGINAL source
    reference durably committed in ``jobs/<id>/ingest-request.json`` (plus
    ``selection.json`` for torrents), reusing ``pipeline/ingest.py``'s own
    download functions so the re-fetch picks EXACTLY the same file(s),
    including the saved torrent file indices — no re-prompting, no new
    download code (same contract as ClipForge).

Disk safety: the runner's free space is logged and checked against the source
size recorded at ingest time before downloading, and the aria2 working
directory is deleted right after the payload is copied out, so a big
re-download cannot silently eat the runner's disk.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import ingest as _ing
from .utils import human_size, probe_video


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _fail(reason: str) -> RuntimeError:
    return RuntimeError(
        "Could not re-obtain the source video. This is a RE-DOWNLOAD: the "
        "release intentionally omitted the source video because it exceeded "
        "GitHub's per-asset release limit (see jobs/<id>/ingest.json "
        "video_in_release=false). Reason: " + reason
    )


def ensure_source(job_id: str, jobs_root: str = "jobs", work_dir: str = "work",
                  release_tag: str = "", repo: str = "") -> dict:
    """Make sure ``work/source.*`` exists and return a record of how.

    Callers use this single entry point regardless of whether the video is in
    the release or must be re-downloaded — processing works identically.
    """
    job_dir = os.path.join(jobs_root, job_id)
    ing = _load_json(os.path.join(job_dir, "ingest.json"))
    names = list(ing.get("sources") or [ing.get("asset_name")])
    if not names or not names[0]:
        raise _fail("ingest.json has no sources/asset_name.")
    os.makedirs(work_dir, exist_ok=True)

    if ing.get("video_in_release", True):
        tag = release_tag or f"castcut-{job_id}"
        repo = repo or os.environ.get("GITHUB_REPOSITORY", "")
        cmd = ["gh", "release", "download", tag, "--dir", work_dir,
               "--pattern", "source.*", "--clobber"]
        if repo:
            cmd += ["--repo", repo]
        print("+ " + " ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
        missing = [n for n in names
                   if not os.path.exists(os.path.join(work_dir, n))]
        if missing:
            raise RuntimeError(
                f"release {tag} is missing expected assets: {missing}")
        print(f"[refetch] video_in_release=true — fetched {names} "
              f"from release {tag}", flush=True)
        return {"mode": "release", "files": names}

    # ---------------- re-download from the original source ----------------
    req = _load_json(os.path.join(job_dir, "ingest-request.json"))
    src = req.get("source", {})
    kind = src.get("kind", "")
    need = int(ing.get("size_bytes", 0))
    free = shutil.disk_usage(work_dir).free
    print(f"[refetch] video_in_release=false — re-downloading from the "
          f"original {kind} source", flush=True)
    print(f"[refetch] runner disk free: {human_size(free)}; source size at "
          f"ingest: {human_size(need)}", flush=True)
    required = int(need * 1.15) + (512 << 20) if need else (2 << 30)
    if free < required:
        raise _fail(f"not enough free disk on the runner (free "
                    f"{human_size(free)}, need ~{human_size(required)}).")

    tmp = os.path.join(work_dir, "download.bin")
    try:
        if kind == "direct_url":
            _ing._stream_download(src["url"], tmp)
            os.replace(tmp, os.path.join(work_dir, names[0]))

        elif kind == "gdrive":
            _ing._download_gdrive(src["url"], tmp)
            os.replace(tmp, os.path.join(work_dir, names[0]))

        elif kind in ("magnet", "torrent_file"):
            from pathlib import Path
            from .torrents import resolve_downloaded_files
            sel_path = os.path.join(job_dir, "selection.json")
            if not os.path.exists(sel_path):
                raise _fail("selection.json is missing — cannot re-download "
                            "the originally selected torrent files without it.")
            selection = _load_json(sel_path)
            indices = [int(i) for i in selection.get("selected_indices", [])]
            wanted = [str(p) for p in selection.get("paths", [])]
            if not indices or len(indices) != len(wanted):
                raise _fail("selection.json is missing selected_indices/paths.")
            if kind == "magnet":
                payload, is_file = src["magnet"], False
            else:
                payload = os.path.join(job_dir, "source.torrent")
                is_file = True
                if not os.path.exists(payload):
                    raise _fail(f"the saved torrent manifest {payload} is not "
                                "present in the checkout.")
            # Same downloader, same --select-file indices => same file(s).
            dl_dir = _ing._download_torrent(payload, is_file, work_dir, indices)
            try:
                picked = resolve_downloaded_files(Path(dl_dir), wanted)
                if len(picked) != len(names):
                    raise _fail(f"expected {len(names)} file(s) but resolved "
                                f"{len(picked)} from the torrent.")
                for n, p in enumerate(picked):
                    shutil.copy2(str(p), os.path.join(work_dir, names[n]))
            finally:
                # Free the runner's disk immediately — the full payload copy
                # in work/ is all downstream steps need.
                shutil.rmtree(dl_dir, ignore_errors=True)

        else:
            raise _fail(f"unsupported source kind {kind!r} for re-download.")
    except RuntimeError:
        raise
    except Exception as exc:  # surface anything unexpected as diagnosable
        raise _fail(f"{type(exc).__name__}: {exc}") from exc
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    for n in names:
        p = os.path.join(work_dir, n)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            raise _fail(f"the re-download produced no usable file for {n}.")
    info = probe_video(os.path.join(work_dir, names[0]))
    if not info["duration"]:
        raise _fail("the re-downloaded file does not look like a video.")
    print(f"[refetch] re-downloaded {names} ok "
          f"({human_size(os.path.getsize(os.path.join(work_dir, names[0])))})",
          flush=True)
    return {"mode": "redownload", "files": names}


def main() -> None:
    ap = argparse.ArgumentParser(description="CastCut source re-fetch "
                                           "(fix_round_2)")
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--work-dir", default="work")
    ap.add_argument("--release-tag", default="")
    args = ap.parse_args()
    try:
        rec = ensure_source(args.job_id, jobs_root=args.jobs_root,
                            work_dir=args.work_dir,
                            release_tag=args.release_tag)
    except RuntimeError as exc:
        print(f"source fetch error: {exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(rec, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = ["ensure_source"]
