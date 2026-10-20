from __future__ import annotations

import glob
import json
import os
import re
import shutil
import struct
import subprocess
from typing import List, Tuple

# ---------- Fixed mobile-safe encoding profile (ported from ClipForge + owner's compression) ----------
EXPORT_VCODEC = "libx264"
EXPORT_PROFILE = "high"
EXPORT_LEVEL = "4.0"          # Universally decodable on all Android chipsets
EXPORT_PRESET = "medium"
EXPORT_CRF = "23"             # Established delivery quality
EXPORT_MAXRATE = "1872k"      # Strict video stream cap <= 1872 kbps (total with 128k audio <= 2000 kbps)
EXPORT_BUFSIZE = "3744k"      # Standard 2x buffer for tight VBV rate control
EXPORT_MAX_FPS = 30.0         # Capped to 30 fps; preserves <=30 fps (e.g. 23.976/24/25)
EXPORT_MAX_WIDTH = 1920       # At most 1080p; never upscales
EXPORT_MAX_HEIGHT = 1080
EXPORT_TIMESCALE = "15360"     # Standardized video track timescale
EXPORT_AUDIO_CODEC = "aac"
EXPORT_AUDIO_PROFILE = "aac_low"
EXPORT_AUDIO_BITRATE = "128k"
EXPORT_AUDIO_RATE = "48000"
EXPORT_AUDIO_CHANNELS = "2"


def run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, **kw)


def ffprobe_json(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout)


def probe_audio_tracks(path: str) -> list[dict]:
    """Extract all audio streams with index, language, title, and channel layout."""
    info = ffprobe_json(path)
    streams = info.get("streams", [])
    tracks = []
    a_idx = 0
    for s in streams:
        if s.get("codec_type") == "audio":
            tags = s.get("tags", {})
            lang = tags.get("language", "und").lower()
            title = tags.get("title", "").strip()
            channels = int(s.get("channels", 2))
            layout = s.get("channel_layout", "stereo" if channels == 2 else f"{channels}ch")
            codec = s.get("codec_name", "aac")
            bitrate = int(s.get("bit_rate", 0)) or 128000
            disposition = s.get("disposition", {})
            is_default = bool(disposition.get("default", 0))

            lang_map = {
                "jpn": "Japanese", "ja": "Japanese",
                "eng": "English", "en": "English",
                "fra": "French", "fre": "French", "fr": "French",
                "deu": "German", "ger": "German", "de": "German",
                "spa": "Spanish", "es": "Spanish",
                "ita": "Italian", "it": "Italian",
                "por": "Portuguese", "pt": "Portuguese",
                "rus": "Russian", "ru": "Russian",
                "zho": "Chinese", "chi": "Chinese", "zh": "Chinese",
                "kor": "Korean", "ko": "Korean",
                "und": "Undetermined",
            }
            lang_name = lang_map.get(lang, lang.upper())
            parts = [f"Track {a_idx + 1}", lang.upper(), layout, codec]
            if title:
                parts.append(f"{title!r}")
            if is_default:
                parts.append("(Default)")
            label = " · ".join(parts)

            tracks.append({
                "audio_index": a_idx,
                "stream_index": s.get("index"),
                "language": lang,
                "language_name": lang_name,
                "title": title,
                "channels": channels,
                "channel_layout": layout,
                "codec": codec,
                "is_default": is_default,
                "bitrate": bitrate,
                "label": label,
            })
            a_idx += 1
    return tracks


def probe_video(path: str) -> dict:
    info = ffprobe_json(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    fps = 0.0
    if v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        num, den = v["avg_frame_rate"].split("/")
        if float(den):
            fps = float(num) / float(den)
    audio_tracks = probe_audio_tracks(path)
    return {
        "duration": float(info.get("format", {}).get("duration", 0.0)),
        "size_bytes": int(info.get("format", {}).get("size", os.path.getsize(path))),
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": round(fps, 3),
        "vcodec": v.get("codec_name", ""),
        "has_audio": len(audio_tracks) > 0,
        "audio_tracks": audio_tracks,
    }


def parse_time(value) -> float:
    """Accept seconds (int/float) or 'MM:SS(.ms)' / 'HH:MM:SS(.ms)' strings."""
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(?:(\d+):)?([0-5]?\d):([0-5]?\d(?:\.\d+)?)", s)
    if not m:
        raise ValueError(f"unparseable time value: {value!r} (use seconds or MM:SS / HH:MM:SS)")
    h = float(m.group(1) or 0)
    return h * 3600 + float(m.group(2)) * 60 + float(m.group(3))


def fmt_time(t: float) -> str:
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return f"{int(m):02d}:{s:06.3f}"


def merge_intervals(intervals: List[Tuple[float, float]], gap: float) -> List[Tuple[float, float]]:
    """Merge intervals separated by <= gap seconds."""
    items = sorted((float(s), float(e)) for s, e in intervals if e > s)
    if not items:
        return []
    out = [list(items[0])]
    for s, e in items[1:]:
        if s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def pad_intervals(intervals, pad: float, duration: float):
    return [(max(0.0, s - pad), min(duration, e + pad)) for s, e in intervals]


def subtract_intervals(intervals, taken, min_len: float = 0.05):
    """Return the parts of `intervals` not covered by `taken`."""
    taken = sorted(taken)
    out = []
    for s, e in sorted(intervals):
        cur = s
        for ts, te in taken:
            if te <= cur:
                continue
            if ts >= e:
                break
            if ts > cur:
                out.append((cur, min(ts, e)))
            cur = max(cur, te)
            if cur >= e:
                break
        if cur < e:
            out.append((cur, e))
    return [(s, e) for s, e in out if e - s >= min_len]


def resolve_quality_settings(quality_arg: str | None = None) -> tuple[int, str]:
    """Deprecated: Returns fixed delivery settings (CRF 23, medium)."""
    return int(EXPORT_CRF), EXPORT_PRESET


def concat_segments(
    src: str,
    segments: list[tuple[float, float]],
    output_path: str,
    has_audio: bool,
    audio_track: int | None = 0,
    temp_dir: str | None = None,
    **_kwargs,
) -> None:
    """Extract and concatenate segments into an intermediate file.
    Uses mobile-safe no-B-frame chunks to eliminate edit list and timestamp anomalies.
    """
    if not segments:
        raise ValueError("No segments provided for concatenation")

    a_map = f"0:a:{audio_track}?" if (has_audio and audio_track is not None) else None

    # If only 1 segment, direct cut
    if len(segments) == 1:
        s, e = segments[0]
        dur = max(0.01, e - s)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{s:.3f}", "-i", src, "-t", f"{dur:.3f}",
            "-map", "0:v:0",
        ]
        if a_map:
            cmd += ["-map", a_map, "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd += [
            "-sn", "-dn", "-map_chapters", "-1",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-bf", "0", "-sc_threshold", "0",
            "-movflags", "+faststart", output_path,
        ]
        run(cmd)
        return

    work_base = temp_dir if temp_dir else os.path.dirname(output_path)
    chunks_dir = os.path.join(work_base, f"_chunks_{os.getpid()}")
    os.makedirs(chunks_dir, exist_ok=True)
    chunk_files = []

    try:
        total = len(segments)
        for i, (s, e) in enumerate(segments, start=1):
            dur = max(0.01, e - s)
            chunk_path = os.path.join(chunks_dir, f"seg_{i:05d}.ts")
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{s:.3f}", "-i", src, "-t", f"{dur:.3f}",
                "-map", "0:v:0",
            ]
            if a_map:
                cmd += ["-map", a_map, "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
            else:
                cmd += ["-an"]
            cmd += [
                "-sn", "-dn", "-map_chapters", "-1",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-bf", "0", "-sc_threshold", "0",
                chunk_path,
            ]
            run(cmd)
            chunk_files.append(chunk_path)
            if i % 10 == 0 or i == total:
                print(f"[export] rendered segment {i}/{total} ({dur:.1f}s)", flush=True)

        list_path = os.path.join(chunks_dir, "concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as fh:
            for p in chunk_files:
                fh.write(f"file '{os.path.abspath(p)}'\n")

        print(f"[export] joining {len(chunk_files)} segments via concat demuxer...", flush=True)
        concat_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", "-movflags", "+faststart", output_path,
        ]
        run(concat_cmd)
        print(f"[export] successfully created intermediate {output_path} ({human_size(os.path.getsize(output_path))})", flush=True)
    finally:
        if os.path.exists(chunks_dir):
            shutil.rmtree(chunks_dir, ignore_errors=True)


def check_faststart(path: str) -> bool:
    """Confirm moov atom is located before mdat at file start (faststart)."""
    with open(path, "rb") as fh:
        moov_pos = None
        mdat_pos = None
        pos = 0
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            size, name = struct.unpack(">I4s", hdr)
            name = name.decode("latin1", errors="replace")
            if name == "moov" and moov_pos is None:
                moov_pos = pos
            elif name == "mdat" and mdat_pos is None:
                mdat_pos = pos
            if size == 1:
                size = struct.unpack(">Q", fh.read(8))[0]
                fh.seek(size - 16, 1)
            elif size == 0:
                break
            else:
                fh.seek(size - 8, 1)
            pos += size
        return moov_pos is not None and mdat_pos is not None and moov_pos < mdat_pos


def check_no_editlist(path: str) -> bool:
    """Confirm no edit lists (elst box) exist in the container header."""
    with open(path, "rb") as fh:
        hdr = fh.read(2 * 1024 * 1024)
        return b"elst" not in hdr


def validate_export_playback_and_size(path: str, expect_audio: bool = True) -> dict:
    """Strict pre-publish gate: asserts Android playback compatibility and <=2000 kbps bitrate.
    Fails with descriptive RuntimeError on any mismatch.
    """
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"Export validation failed: file does not exist or is empty ({path})")

    # 1. Full decode integrity check
    decode_cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"]
    dec_res = subprocess.run(decode_cmd, capture_output=True, text=True)
    if dec_res.returncode != 0:
        raise RuntimeError(f"Export validation failed: full video decode error (code {dec_res.returncode}): {dec_res.stderr.strip()}")
    if dec_res.stderr and any(err in dec_res.stderr.lower() for err in ("error", "corrupt", "invalid", "failed")):
        raise RuntimeError(f"Export validation failed: decode output reported errors: {dec_res.stderr.strip()}")

    # 2. Faststart check (moov atom before mdat)
    if not check_faststart(path):
        raise RuntimeError(f"Export validation failed: faststart (+faststart) missing; moov atom is not at start of {path}")

    # 3. Edit lists check (no elst box)
    if not check_no_editlist(path):
        raise RuntimeError("Export validation failed: MP4 container contains edit list (elst) box which breaks Android MediaPlayer")

    # 4. Stream inspection via ffprobe
    info = ffprobe_json(path)
    streams = info.get("streams", [])
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    s_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    d_streams = [s for s in streams if s.get("codec_type") == "data"]

    if len(v_streams) != 1:
        raise RuntimeError(f"Export validation failed: expected exactly 1 video stream, found {len(v_streams)}")
    v = v_streams[0]

    # Video codec & profile
    if v.get("codec_name") != "h264":
        raise RuntimeError(f"Export validation failed: video codec must be h264, found {v.get('codec_name')}")
    if v.get("profile") != "High":
        raise RuntimeError(f"Export validation failed: video profile must be High, found {v.get('profile')}")
    lvl = int(v.get("level", 0))
    if lvl > 41:
        raise RuntimeError(f"Export validation failed: video level must be <= 4.1 (41), found {lvl}")
    if v.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"Export validation failed: pixel format must be yuv420p, found {v.get('pix_fmt')}")
    if v.get("has_b_frames", 0) > 0:
        raise RuntimeError(f"Export validation failed: video has {v.get('has_b_frames')} B-frames; mobile contract requires 0 B-frames")

    # Resolution cap (max 1080p, even dimensions)
    w = int(v.get("width", 0))
    h = int(v.get("height", 0))
    if w > 1920 or h > 1080 or (w * h > 1920 * 1080):
        raise RuntimeError(f"Export validation failed: resolution exceeds 1080p: {w}x{h}")
    if w % 2 != 0 or h % 2 != 0:
        raise RuntimeError(f"Export validation failed: resolution dimensions must be even numbers: {w}x{h}")

    # Frame rate cap (max 30 fps)
    fps = 0.0
    if v.get("r_frame_rate") and v["r_frame_rate"] != "0/0":
        num, den = v["r_frame_rate"].split("/")
        fps = float(num) / float(den)
    if fps > 30.05:
        raise RuntimeError(f"Export validation failed: frame rate exceeds 30 fps: {fps:.2f}")

    # Audio verification
    if expect_audio:
        if len(a_streams) != 1:
            raise RuntimeError(f"Export validation failed: expected exactly 1 audio stream, found {len(a_streams)}")
        a = a_streams[0]
        if a.get("codec_name") != "aac":
            raise RuntimeError(f"Export validation failed: audio codec must be aac, found {a.get('codec_name')}")
        if int(a.get("channels", 0)) != 2:
            raise RuntimeError(f"Export validation failed: audio must be stereo (2 channels), found {a.get('channels')}")
        sr = int(a.get("sample_rate", 0))
        if sr not in (44100, 48000):
            raise RuntimeError(f"Export validation failed: audio sample rate must be 44.1k or 48k, found {sr}")
    else:
        if len(a_streams) != 0:
            raise RuntimeError(f"Export validation failed: expected 0 audio streams for source without audio, found {len(a_streams)}")

    # Unwanted streams check
    if len(s_streams) != 0 or len(d_streams) != 0:
        raise RuntimeError(f"Export validation failed: unwanted streams present (subtitles: {len(s_streams)}, data: {len(d_streams)})")

    # Bitrate verification (must never exceed 2000 kbps for the video stream)
    fmt = info.get("format", {})
    dur = float(fmt.get("duration", 0.0))
    sz = int(fmt.get("size", os.path.getsize(path)))
    tot_br = int(fmt.get("bit_rate", 0))
    if not tot_br and dur > 0:
        tot_br = int((sz * 8) / dur)

    v_br = int(v.get("bit_rate", 0))
    if not v_br and dur > 0:
        a_br = int(a_streams[0].get("bit_rate", 128000)) if a_streams else 0
        a_bytes = (a_br * dur) / 8.0
        v_br = max(0, int(((sz - a_bytes) * 8) / dur))

    v_kbps = v_br / 1000.0
    # Hard requirement: video bitrate must never exceed 2000 kbps
    if v_kbps > 2000.0:
        raise RuntimeError(f"Export validation failed: video bitrate exceeds 2000 kbps ceiling! Measured: {v_kbps:.1f} kbps")

    tot_kbps = tot_br / 1000.0
    return {
        "ok": True,
        "duration": round(dur, 2),
        "size_bytes": sz,
        "size_human": human_size(sz),
        "bitrate_bps": tot_br,
        "bitrate_kbps": round(tot_kbps, 1),
        "video_bitrate_kbps": round(v_kbps, 1),
        "vcodec": v.get("codec_name"),
        "profile": v.get("profile"),
        "level": lvl,
        "width": w,
        "height": h,
        "fps": round(fps, 3),
        "audio_streams": len(a_streams),
        "audio_codec": a_streams[0].get("codec_name") if a_streams else None,
        "audio_channels": int(a_streams[0].get("channels", 0)) if a_streams else 0,
        "audio_lang": a_streams[0].get("tags", {}).get("language") if a_streams else None,
        "faststart": True,
        "has_elst": False,
    }


def verify_output_streams(path: str, expect_audio: bool) -> dict:
    """Legacy wrapper calling validate_export_playback_and_size."""
    return validate_export_playback_and_size(path, expect_audio=expect_audio)


def encode_final_export(
    input_path: str,
    output_path: str,
    has_audio: bool = True,
    audio_bitrate: str = "128k",
    **_ignored_kwargs,
) -> dict:
    """Single shared encode path for CastCut exports.
    Ports ClipForge mobile-safe architecture + owner compression parameters:
    - H.264 High@L4.0, 8-bit, yuv420p
    - Zero B-frames (-bf 0), fixed timescale 15360, force-cfr=1
    - Container: mp42 brand, +faststart, -use_editlist 0, no chapters/subtitles/metadata
    - Resolution: capped to 1080p, keeps source if <=1080p, even dimensions
    - Frame rate: constant frame rate <= 30 fps (keeps source if <=30)
    - Rate control: video bitrate strictly <= 2000 kbps (-crf 23 -maxrate 1872k -bufsize 3744k)
    - Audio: 1 AAC stereo stream at 128 kbps 48kHz, properly downmixed
    """
    meta = probe_video(input_path)
    src_fps = float(meta.get("fps") or 30.0)
    eff_fps = min(EXPORT_MAX_FPS, src_fps) if src_fps > 0 else EXPORT_MAX_FPS
    gop_size = max(24, int(round(eff_fps * 2)))

    # Video filters:
    # 1. Scale down proportionally if > 1080p (never upscale)
    # 2. Ensure even width & height
    # 3. setsar=1
    # 4. Enforce constant frame rate up to 30 fps
    # 5. Format yuv420p
    v_filters = [
        "scale=w='min(1920,iw)':h='min(1080,ih)':force_original_aspect_ratio=decrease",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "setsar=1",
        f"fps=fps={eff_fps:.4f}:round=near",
        "format=yuv420p",
    ]

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path,
        "-map", "0:v:0",
        "-vf", ",".join(v_filters),
        "-c:v", EXPORT_VCODEC,
        "-profile:v", EXPORT_PROFILE,
        "-level:v", EXPORT_LEVEL,
        "-preset", EXPORT_PRESET,
        "-crf", EXPORT_CRF,
        "-maxrate", EXPORT_MAXRATE,
        "-bufsize", EXPORT_BUFSIZE,
        "-pix_fmt", "yuv420p",
        "-bf", "0",
        "-g", str(gop_size),
        "-keyint_min", str(gop_size),
        "-sc_threshold", "0",
        "-x264-params", "force-cfr=1",
        "-video_track_timescale", EXPORT_TIMESCALE,
    ]

    if has_audio:
        cmd += [
            "-map", "0:a:0?",
            "-c:a", EXPORT_AUDIO_CODEC,
            "-profile:a", EXPORT_AUDIO_PROFILE,
            "-b:a", audio_bitrate or EXPORT_AUDIO_BITRATE,
            "-ar", EXPORT_AUDIO_RATE,
            "-ac", EXPORT_AUDIO_CHANNELS,
        ]
    else:
        cmd += ["-an"]

    cmd += [
        "-movflags", "+faststart",
        "-use_editlist", "0",
        "-brand", "mp42",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-sn", "-dn", "-ignore_unknown",
        "-fflags", "+genpts",
        "-max_muxing_queue_size", "9999",
        "-f", "mp4",
        output_path,
    ]
    run(cmd)

    # Validate output compatibility, streams, and bitrate
    verification = validate_export_playback_and_size(output_path, expect_audio=has_audio)
    return verification


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} GB"

def clean_and_parse_json(raw: str) -> tuple[dict | None, str | None]:
    """Tolerantly parse JSON: strips markdown code fences, handles surrounding text,
    BOM, smart quotes, and trailing commas. Returns (data, error_str)."""
    if not raw or not raw.strip():
        return None, "JSON input is empty"
    text = raw.strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    # 1. Try strict parsing first
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None, None if isinstance(data, dict) else "JSON root must be an object"
    except Exception:
        pass

    # 2. Strip code fences
    fence_m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_m:
        text = fence_m.group(1).strip()
    else:
        # Extract outer object if text before or after
        s_idx = text.find("{")
        e_idx = text.rfind("}")
        if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
            text = text[s_idx:e_idx+1].strip()

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None, None if isinstance(data, dict) else "JSON root must be an object"
    except Exception:
        pass

    # Replace smart quotes
    text = text.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None, None if isinstance(data, dict) else "JSON root must be an object"
    except Exception:
        pass

    # Strip trailing commas before } or ]
    cleaned = re.sub(r",\s*([\}\]])", r"\1", text)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None, None if isinstance(data, dict) else "JSON root must be an object"
    except json.JSONDecodeError as exc:
        lines = cleaned.splitlines()
        err_line = lines[exc.lineno - 1] if 0 < exc.lineno <= len(lines) else ""
        return None, f"JSON syntax error at line {exc.lineno}, col {exc.colno}: {exc.msg}. Near: {err_line.strip()!r}"
