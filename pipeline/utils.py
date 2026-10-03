"""Shared helpers for CastCut pipeline modules. Stdlib + subprocess only."""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import List, Tuple


def run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def ffprobe_json(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def probe_video(path: str) -> dict:
    info = ffprobe_json(path)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = 0.0
    if v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        num, den = v["avg_frame_rate"].split("/")
        if float(den):
            fps = float(num) / float(den)
    return {
        "duration": float(info.get("format", {}).get("duration", 0.0)),
        "size_bytes": int(info.get("format", {}).get("size", os.path.getsize(path))),
        "width": int(v.get("width", 0)),
        "height": int(v.get("height", 0)),
        "fps": round(fps, 3),
        "vcodec": v.get("codec_name", ""),
        "has_audio": bool(a),
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


def build_concat_filter(segments, has_audio: bool) -> str:
    """One ffmpeg filtergraph: trim every segment then concat with hard cuts."""
    lines = []
    concat_in = []
    for i, (s, e) in enumerate(segments):
        lines.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        concat_in.append(f"[v{i}]")
        if has_audio:
            lines.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
            concat_in.append(f"[a{i}]")
    n = len(segments)
    if has_audio:
        lines.append("".join(concat_in) + f"concat=n={n}:v=1:a=1[v][a]")
    else:
        lines.append("".join(concat_in) + f"concat=n={n}:v=1:a=0[v]")
    return ";\n".join(lines)


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f} {unit}" if unit != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} GB"
