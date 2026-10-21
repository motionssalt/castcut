"""Round 2 Phone Playback Test Pack generator and publisher.

Constructs resolution ladder (720p, 768p, 810p, 900p, 1080p), owner's exact commands (O1, O2),
stream complexity diagnostics (CAVLC, DPB, 12fps), and intact known-good/known-bad controls.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from fractions import Fraction
from pathlib import Path

from .delivery_pass import color_filter, fps_mode_flag, timescale
from .export_checks import probe
from .playback_inspect import inspect
from .verify_delivery import verify


def run(cmd: list[str]) -> None:
    print(json.dumps(cmd), flush=True)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode:
        raise RuntimeError(f"Command failed: {json.dumps(cmd)}\n{res.stderr[-2500:]}")


def fetch_asset(repo: str, asset_id: int, path: Path, token: str | None = None) -> None:
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    with open(path, "wb") as f:
        subprocess.run(
            ["gh", "api", f"repos/{repo}/releases/assets/{int(asset_id)}", "-H", "Accept: application/octet-stream"],
            env=env, stdout=f, check=True
        )


def build_ladder_command(source: Path, dest: Path, width: int, height: int, fps: Fraction, duration: float) -> list[str]:
    v = next(s for s in probe(source)["streams"] if s["codec_type"] == "video")
    vf = (
        f"setpts=PTS-STARTPTS,fps={fps},"
        f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        f"{color_filter(v)},setsar=1"
    )
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(source), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", vf, "-r", str(fps),
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.1",
        "-preset", "medium", "-bf", "0", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", str(timescale(fps)),
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(dest),
    ]
    return cmd


def build(castcut_src: Path, clipforge_src: Path, out_dir: Path) -> list[dict]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:4])

    v_meta = next(s for s in probe(castcut_src)["streams"] if s["codec_type"] == "video")
    src_fps = min(Fraction(v_meta["r_frame_rate"]), Fraction(30))
    frames = round(10 * src_fps)
    duration = float(frames / src_fps)

    # 1. Prepare 10-second reference input for O1 and O2 commands
    input_10s_mkv = out_dir.parent / "source_10s.mkv"
    if not input_10s_mkv.exists():
        run([
            "ffmpeg", "-y", "-ss", "0", "-i", str(castcut_src), "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy", "-c:a", "copy",
            str(input_10s_mkv)
        ])

    jobs: list[tuple[Path, list[str], str]] = []

    # --- Resolution Ladder (all else equal: Variant I profile, 2000 kbps ceiling) ---
    ladder_steps = [
        ("03_ladder_1280x720", 1280, 720, "Resolution ladder step 1: 1280x720 (921,600 px). Known-good 720p baseline."),
        ("04_ladder_1366x768", 1366, 768, "Resolution ladder step 2: 1366x768 (1,049,088 px, +13.8% over 720p)."),
        ("05_ladder_1440x810", 1440, 810, "Resolution ladder step 3: 1440x810 (1,166,400 px, +26.6% over 720p)."),
        ("06_ladder_1600x900", 1600, 900, "Resolution ladder step 4: 1600x900 (1,440,000 px, +56.2% over 720p)."),
        ("07_ladder_1920x1080", 1920, 1080, "Resolution ladder step 5: 1920x1080 (2,073,600 px, +125% over 720p)."),
    ]
    for name, w, h, desc in ladder_steps:
        p = out_dir / f"{name}.mp4"
        cmd = build_ladder_command(castcut_src, p, w, h, src_fps, duration)
        jobs.append((p, cmd, desc))

    # --- Owner exact command O1 ---
    # ffmpeg -i input.mkv -map 0:v:0 -map 0:a:0? -vf "scale=-2:1080,setsar=1,format=yuv420p" -c:v libx264 -preset medium -crf 20 -profile:v main -level:v 4.1 -r 30 -fps_mode cfr -c:a aac -profile:a aac_low -b:a 192k -ar 48000 -ac 2 -sn -movflags +faststart output.mp4
    p_o1 = out_dir / "08_owner-command_O1-exact.mp4"
    cmd_o1 = [
        "ffmpeg", "-y", "-i", str(input_10s_mkv),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=-2:1080,setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "main", "-level:v", "4.1",
        "-r", "30",
    ] + fps_mode_flag("cfr") + [
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-sn", "-movflags", "+faststart",
        str(p_o1),
    ]
    jobs.append((p_o1, cmd_o1, "Owner exact ffmpeg command untouched on the 10-second clip (CRF 20, 30fps conversion, 192k audio)."))

    # --- Owner command O2 (with source fps and 2000k ceiling) ---
    p_o2 = out_dir / "09_owner-command_O2-source-fps-2000k-ceiling.mp4"
    cmd_o2 = [
        "ffmpeg", "-y", "-i", str(input_10s_mkv),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=-2:1080,setsar=1,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", "2000k", "-maxrate", "2000k", "-bufsize", "4000k",
        "-profile:v", "main", "-level:v", "4.1",
        "-r", str(src_fps),
    ] + fps_mode_flag("cfr") + [
        "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-sn", "-movflags", "+faststart",
        str(p_o2),
    ]
    jobs.append((p_o2, cmd_o2, "Owner command with source frame rate (no 30fps conversion) and 2000 kbps video ceiling."))

    # --- Simplest possible 1080p stream: refs=1, bf=0, weightp=0, CAVLC (Level 4.0 and 4.1) ---
    for lvl, name, desc in [
        ("4.0", "10_1080p_simplest-cavlc_refs1_nobframes_level4.0", "1080p simplest CAVLC stream: Main@4.0, refs=1, bf=0, no B-pyramid, weightp=0."),
        ("4.1", "11_1080p_simplest-cavlc_refs1_nobframes_level4.1", "1080p simplest CAVLC stream: Main@4.1, refs=1, bf=0, no B-pyramid, weightp=0."),
    ]:
        p = out_dir / f"{name}.mp4"
        vf = f"setpts=PTS-STARTPTS,fps={src_fps},scale=-2:1080,format=yuv420p,{color_filter(v_meta)},setsar=1"
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-threads", "2",
            "-i", str(castcut_src), "-t", f"{duration:.9f}",
            "-map", "0:v:0", "-vf", vf, "-r", str(src_fps),
        ] + fps_mode_flag("cfr") + [
            "-c:v", "libx264", "-profile:v", "main", "-level:v", lvl,
            "-preset", "medium", "-x264-params", "no-cabac=1:ref=1:bframes=0:weightp=0",
            "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
            "-video_track_timescale", str(timescale(src_fps)),
            "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
            "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
            "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
            "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-f", "mp4", str(p),
        ]
        jobs.append((p, cmd, desc))

    # --- 1080p with ClipForge exact x264 options (High@4.0, cabac=1, ref=3, bf=0, weightp=2, 8x8dct=1, 1800k maxrate) ---
    p_cf_opts = out_dir / "12_1080p_exact-clipforge-x264-options.mp4"
    vf_cf = f"setpts=PTS-STARTPTS,fps={src_fps},scale=-2:1080,format=yuv420p,{color_filter(v_meta)},setsar=1"
    cmd_cf_opts = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(castcut_src), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", vf_cf, "-r", str(src_fps),
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0",
        "-preset", "medium", "-bf", "0", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-x264-params", "force-cfr=1:ref=3:weightp=2:8x8dct=1:me=hex:subme=7:trellis=1",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", str(timescale(src_fps)),
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(p_cf_opts),
    ]
    jobs.append((p_cf_opts, cmd_cf_opts, "1080p with ClipForge exact x264 encoder options: High@4.0, ref=3, bf=0, weightp=2, 8x8dct=1, 2000k ceiling."))

    # --- 1080p with limited decoded picture buffer (DPB): ref=1, bframes=2, b-pyramid=none ---
    p_dpb = out_dir / "13_1080p_limited-dpb_refs1_bframes2_nobpyramid.mp4"
    cmd_dpb = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(castcut_src), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", vf_cf, "-r", str(src_fps),
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.1",
        "-preset", "medium", "-x264-params", "ref=1:bframes=2:b-pyramid=none",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", str(timescale(src_fps)),
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(p_dpb),
    ]
    jobs.append((p_dpb, cmd_dpb, "1080p with restricted DPB: ref=1, bframes=2, b-pyramid=none, level 4.1, 2000k ceiling."))

    # --- 1080p at lower frame rate (12 fps diagnostic for macroblock/second decoder limit) ---
    p_12fps = out_dir / "14_1080p_diagnostic_12fps-reduced-rate.mp4"
    cmd_12fps = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(castcut_src), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", f"setpts=PTS-STARTPTS,fps=12,scale=-2:1080,format=yuv420p,{color_filter(v_meta)},setsar=1",
        "-r", "12",
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.1",
        "-preset", "medium", "-bf", "0", "-g", "24", "-keyint_min", "24", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", "12000",
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(p_12fps),
    ]
    jobs.append((p_12fps, cmd_12fps, "1080p at 12 fps diagnostic: tests whether hardware decoder limit is macroblocks/second processing rate."))

    # --- 1080p High profile level 4.0 control ---
    p_high = out_dir / "15_1080p_high-profile_level4.0.mp4"
    cmd_high = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(castcut_src), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", vf_cf, "-r", str(src_fps),
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "high", "-level:v", "4.0",
        "-preset", "medium", "-bf", "0", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", str(timescale(src_fps)),
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(p_high),
    ]
    jobs.append((p_high, cmd_high, "1080p High profile Level 4.0 control: compare High vs Main profile at 1080p."))

    # --- 720p portrait (720x1280) vertical video sanity check ---
    p_vert = out_dir / "16_720p-portrait_720x1280_vertical.mp4"
    vf_vert = f"setpts=PTS-STARTPTS,fps={src_fps},scale=720:1280:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,{color_filter(v_meta)},setsar=1"
    cmd_vert = [
        "ffmpeg", "-y", "-v", "error", "-threads", "2",
        "-i", str(castcut_src), "-t", f"{duration:.9f}",
        "-map", "0:v:0", "-vf", vf_vert, "-r", str(src_fps),
    ] + fps_mode_flag("cfr") + [
        "-c:v", "libx264", "-profile:v", "main", "-level:v", "4.1",
        "-preset", "medium", "-bf", "0", "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-pix_fmt", "yuv420p", "-b:v", "1800k", "-maxrate", "1800k", "-bufsize", "1800k",
        "-video_track_timescale", str(timescale(src_fps)),
        "-map", "0:a:0", "-c:a", "aac", "-profile:a", "aac_low", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-af", f"aresample=48000:async=1:first_pts=0,apad,atrim=duration={duration:.9f},asetpts=N/SR/TB+1024/SR/TB",
        "-movflags", "+faststart", "-use_editlist", "0", "-brand", "mp42", "-avoid_negative_ts", "make_zero",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn", "-max_interleave_delta", "0",
        "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-f", "mp4", str(p_vert),
    ]
    jobs.append((p_vert, cmd_vert, "720p portrait (720x1280, 921,600 px): vertical orientation sanity check."))

    def process_job(job: tuple[Path, list[str], str]) -> dict:
        path, cmd, desc = job
        run(cmd)
        run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"])
        rep = inspect(path)
        rep["probe"]["format"].pop("filename", None)
        path.with_suffix(".inspection.json").write_text(json.dumps(rep, indent=2) + "\n")
        return {
            "name": path.name,
            "description": desc,
            "command": cmd,
            "sha256": rep["sha256"],
            "size_bytes": path.stat().st_size,
            "ffprobe": rep["probe"],
            "max_av_byte_separation": rep["max_nearest_av_byte_separation"],
        }

    variants: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        variants.extend(pool.map(process_job, jobs))

    # Add 02_control_720p-known-good by copying the 1280x720 ladder output
    p_02 = out_dir / "02_control_720p-known-good.mp4"
    shutil.copyfile(out_dir / "03_ladder_1280x720.mp4", p_02)
    entry_02 = process_job((p_02, ["copy", "03_ladder_1280x720.mp4", p_02.name], "Known-good 720p baseline control (Variant I exact matching settings)."))
    entry_02["command"] = ["byte-copy-from-variant-I"]
    variants.append(entry_02)

    # Add 01_control_clipforge-known-good (untouched ClipForge output)
    p_01 = out_dir / "01_control_clipforge-known-good.mp4"
    shutil.copyfile(clipforge_src, p_01)
    entry_01 = process_job((p_01, ["true"], "Untouched real ClipForge final.mp4, byte-identical known-good control (1080x1200)."))
    entry_01["command"] = ["byte-for-byte-copy", str(clipforge_src), str(p_01)]
    variants.append(entry_01)

    # Add 17_control_castcut-known-bad (untouched pre-fix CastCut export)
    p_17 = out_dir / "17_control_castcut-known-bad.mp4"
    shutil.copyfile(castcut_src, p_17)
    entry_17 = process_job((p_17, ["true"], "Untouched real pre-fix CastCut export, byte-identical owner-reported known-bad control."))
    entry_17["command"] = ["byte-for-byte-copy", str(castcut_src), str(p_17)]
    variants.append(entry_17)

    variants.sort(key=lambda x: x["name"])
    (out_dir / "variants.json").write_text(json.dumps(variants, indent=2) + "\n")
    checksums = "".join(f"{x['sha256']}  {x['name']}\n" for x in variants)
    (out_dir / "SHA256SUMS.txt").write_text(checksums)

    # Build README with testing instructions and results table
    readme = [
        "==================================================================",
        "CASTCUT ROUND 2 PHONE PLAYBACK TEST PACK",
        "==================================================================",
        "",
        "INSTRUCTIONS FOR TESTING ON YOUR ANDROID PHONE:",
        "1. Download all files or the complete ZIP archive (playback-test-pack-round2.zip).",
        "2. Test each file in the ORDER listed below.",
        "3. First try opening the file directly from Android Files / your default Video Player.",
        "4. Then try IMPORTING the file into your editing app (CapCut, Premiere Rush, VN, etc.).",
        "5. Do NOT use VLC as evidence and do NOT run any conversion step.",
        "",
        "WHAT WE NEED YOU TO REPORT BACK:",
        "- The HIGHEST file number on the Resolution Ladder that plays successfully (e.g. 720p, 768p, 810p, 900p, or 1080p).",
        "- Whether your own ffmpeg commands played (08_owner-command_O1-exact or 09_owner-command_O2).",
        "- Whether any of the 1080p diagnostic files played (10_simplest-cavlc, 12_clipforge-options, 14_12fps).",
        "- Your PHONE MODEL (e.g. Samsung Galaxy A52, Google Pixel 6a, Redmi Note 11, etc.).",
        "- The NAME of your DEFAULT VIDEO PLAYER and EDITING APP.",
        "",
        "TESTING ORDER & VARIANT SUMMARY:",
    ]
    for x in variants:
        readme.append(f"{x['name']} ({x['size_bytes']} bytes, SHA256 {x['sha256']})")
        readme.append(f"  Description: {x['description']}")
        readme.append("")

    readme += [
        "==================================================================",
        "RESULTS TABLE (PLEASE FILL IN AND SEND BACK):",
        "==================================================================",
        "Phone Model: ________________________________________",
        "Android Version: ____________________________________",
        "Default Player: _____________________________________",
        "Editing App: ________________________________________",
        "",
        "File | Default Player (Yes/No) | Editing App (Yes/No) | Notes / Error Message",
    ]
    for x in variants:
        readme.append(f"{x['name']} | _____ | _____ | _________________________________")

    (out_dir / "README.txt").write_text("\n".join(readme) + "\n")

    # Build ZIP archive
    zip_path = out_dir / "playback-test-pack-round2.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as z:
        for path in sorted(out_dir.iterdir()):
            if path.suffix in (".mp4", ".txt") or path.name == "variants.json":
                z.write(path, path.name)

    return variants


def publish(out_dir: Path, tag: str, variants: list[dict]) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "motionssalt/castcut")
    exists = subprocess.run(["gh", "release", "view", tag, "-R", repo], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        run(["gh", "release", "create", tag, "-R", repo, "--title", "CastCut phone playback test pack — Round 2", "--notes-file", str(out_dir / "README.txt")])
    for path in sorted(out_dir.iterdir()):
        if path.is_file():
            run(["gh", "release", "upload", tag, str(path), "-R", repo, "--clobber"])
    for x in variants:
        receipt = verify(out_dir / x["name"], tag, repository=repo)
        x["delivery"] = receipt
    rel_json = subprocess.check_output(["gh", "api", f"repos/{repo}/releases/tags/{tag}"])
    release = json.loads(rel_json)
    return release["html_url"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--castcut", default="")
    p.add_argument("--clipforge", default="")
    p.add_argument("--out", default="work/playback-pack-round2")
    p.add_argument("--publish", action="store_true")
    p.add_argument("--tag", default="playback-test-pack-round2")
    args = p.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = out_dir.parent / "playback-inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    castcut = Path(args.castcut) if args.castcut else inputs_dir / "castcut-real.mp4"
    clipforge = Path(args.clipforge) if args.clipforge else inputs_dir / "clipforge-real.mp4"

    if not castcut.exists():
        fetch_asset("motionssalt/castcut", 661445503, castcut)
    if not clipforge.exists():
        fetch_asset("motionssalt/clipforge", 654439970, clipforge, os.environ.get("CLIPFORGE_READ_TOKEN"))

    variants = build(castcut, clipforge, out_dir)
    url = publish(out_dir, args.tag, variants) if args.publish else ""

    root = Path("BUILD_STATE.json")
    if root.exists():
        s = json.loads(root.read_text())
        round2 = s.setdefault("playback_round2", {})
        round2["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        round2["round2_pack"] = {
            "release": url,
            "tag": args.tag,
            "variants": variants,
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "controls": {"B_asset_id": 654439970, "M_asset_id": 661445503},
        }
        if "create_round2_pack_workflow_and_release_6.3" not in round2.get("done", []):
            round2.setdefault("done", []).append("create_round2_pack_workflow_and_release_6.3")
        if "create_round2_pack_workflow_and_release_6.3" in round2.get("pending", []):
            round2["pending"].remove("create_round2_pack_workflow_and_release_6.3")
        root.write_text(json.dumps(s, indent=2) + "\n")

    print(f"ROUND 2 TEST PACK RELEASE: {url}", flush=True)


if __name__ == "__main__":
    main()
