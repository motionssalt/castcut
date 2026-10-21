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
EXPORT_PROFILE = "main"
EXPORT_LEVEL = "4.1"          # Universally decodable on all Android chipsets
EXPORT_PRESET = "medium"
EXPORT_CRF = "23"             # Established delivery quality
EXPORT_MAXRATE = "1800k"      # Video-only VBV ceiling; rolling 10s headroom under 2000k
EXPORT_BUFSIZE = "1800k"      # One-second VBV; no burst can escape rolling-window gate
EXPORT_MAX_FPS = 30.0         # Capped to 30 fps; preserves <=30 fps (e.g. 23.976/24/25)
EXPORT_MAX_WIDTH = 1280
EXPORT_MAX_PIXELS = 921600       # At most 1080p; never upscales
EXPORT_MAX_HEIGHT = 720
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


def concat_segments(src, segments, output_path, has_audio, audio_track=0, temp_dir=None, **_legacy):
    """Compatibility entry point: the same vendored reference path, never TS chunks."""
    from .clipforge_export import export_segments
    return export_segments(src, segments, output_path, audio_track=audio_track, temp_dir=temp_dir)


def check_faststart(path: str) -> bool:
    from .export_checks import mp4_boxes
    try:
        names = [b['type'] for b in mp4_boxes(path)]
        return names.index('moov') < names.index('mdat')
    except (ValueError, RuntimeError, OSError):
        return False


def check_no_editlist(path: str) -> bool:
    from .export_checks import mp4_boxes, flatten_boxes
    return not any(b['type']=='elst' for b in flatten_boxes(mp4_boxes(path)))


def validate_export_playback_and_size(path: str, expect_audio: bool = True) -> dict:
    from .export_checks import validate
    return validate(path, expect_audio=expect_audio)


def verify_output_streams(path: str, expect_audio: bool) -> dict:
    """Legacy wrapper calling validate_export_playback_and_size."""
    return validate_export_playback_and_size(path, expect_audio=expect_audio)


def encode_final_export(input_path, output_path, has_audio=True, **_legacy):
    """Compatibility entry point for callers exporting one full-source segment."""
    from .clipforge_export import export_segments
    meta = probe_video(input_path)
    return export_segments(input_path, [(0.0, meta['duration'])], output_path, audio_track=0)


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
