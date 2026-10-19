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


def probe_audio_tracks(path: str) -> list[dict]:
    """List all audio streams in the source with container index, audio_index, codec, language, and human label."""
    info = ffprobe_json(path)
    tracks = []
    a_idx = 0
    for s in info.get("streams", []):
        if s.get("codec_type") == "audio":
            tags = s.get("tags") or {}
            lang = tags.get("language") or "und"
            title = tags.get("title") or ""
            codec = s.get("codec_name") or "unknown"
            channels = int(s.get("channels") or 2)
            layout = s.get("channel_layout") or f"{channels}ch"
            disp = s.get("disposition") or {}
            is_default = bool(disp.get("default", 0))
            bitrate = int(s.get("bit_rate")) if s.get("bit_rate") else None

            parts = [f"Track {a_idx + 1}"]
            if lang and lang != "und":
                parts.append(lang.upper())
            parts.append(f"{channels}.0" if channels <= 2 else layout)
            parts.append(codec)
            if title:
                parts.append(chr(34) + title + chr(34))
            if is_default:
                parts.append("[Default]")
            label = " · ".join(parts)

            tracks.append({
                "index": int(s.get("index", 0)),
                "audio_index": a_idx,
                "codec": codec,
                "channels": channels,
                "channel_layout": layout,
                "sample_rate": int(s.get("sample_rate") or 48000),
                "language": lang,
                "title": title,
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
    """Return (crf, preset) based on quality choice: 'normal' (default), 'small', 'high'."""
    q = (quality_arg or "normal").lower().strip()
    if q == "small":
        return 26, "medium"
    elif q == "high":
        return 20, "medium"
    return 23, "medium"


def concat_segments(
    src: str,
    segments: list[tuple[float, float]],
    output_path: str,
    has_audio: bool,
    audio_track: int | None = 0,
    crf: int = 18,
    preset: str = "veryfast",
    temp_dir: str | None = None,
) -> None:
    """Extract and concatenate segments using chunk-based concat demuxer.
    Extracts segments with fast intermediate encoding, mapping the chosen audio track explicitly.
    """
    if not segments:
        raise ValueError("No segments provided for concatenation")

    a_map = f"0:a:{audio_track}?" if (has_audio and audio_track is not None) else None

    # If only 1 segment, direct cut without concat overhead
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
            "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
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
                "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
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
        import shutil
        if os.path.exists(chunks_dir):
            shutil.rmtree(chunks_dir, ignore_errors=True)


def verify_output_streams(path: str, expect_audio: bool) -> dict:
    """Strictly verify that output contains 1 video stream, exactly 1 audio stream (if expect_audio),
    0 subtitles, and 0 data streams."""
    info = ffprobe_json(path)
    streams = info.get("streams", [])
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    s_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    d_streams = [s for s in streams if s.get("codec_type") == "data"]

    if len(v_streams) != 1:
        raise RuntimeError(f"Export validation failed: expected exactly 1 video stream, found {len(v_streams)}")
    if expect_audio:
        if len(a_streams) != 1:
            raise RuntimeError(f"Export validation failed: expected exactly 1 audio stream, found {len(a_streams)}")
        codec = a_streams[0].get("codec_name")
        if codec != "aac":
            raise RuntimeError(f"Export validation failed: audio stream codec is {codec}, expected aac")
    else:
        if len(a_streams) != 0:
            raise RuntimeError(f"Export validation failed: expected 0 audio streams for video without audio, found {len(a_streams)}")
    if len(s_streams) != 0 or len(d_streams) != 0:
        raise RuntimeError(f"Export validation failed: found unwanted subtitle/data streams (subtitles: {len(s_streams)}, data: {len(d_streams)})")

    fmt = info.get("format", {})
    dur = float(fmt.get("duration", 0.0))
    sz = int(fmt.get("size", os.path.getsize(path)))
    br = int(fmt.get("bit_rate", 0))
    if not br and dur > 0:
        br = int((sz * 8) / dur)

    return {
        "duration": round(dur, 2),
        "size_bytes": sz,
        "size_human": human_size(sz),
        "bitrate_bps": br,
        "bitrate_kbps": round(br / 1000, 1),
        "vcodec": v_streams[0].get("codec_name", ""),
        "width": int(v_streams[0].get("width", 0)),
        "height": int(v_streams[0].get("height", 0)),
        "audio_streams": len(a_streams),
        "audio_codec": a_streams[0].get("codec_name") if a_streams else None,
        "audio_channels": int(a_streams[0].get("channels", 0)) if a_streams else 0,
        "audio_lang": a_streams[0].get("tags", {}).get("language") if a_streams else None,
    }


def encode_final_export(
    input_path: str,
    output_path: str,
    has_audio: bool = True,
    crf: int = 23,
    preset: str = "medium",
    tune: str = "animation",
    audio_bitrate: str = "128k",
) -> dict:
    """Single shared encode path for CastCut exports.
    Produces visually-lossless HandBrake-style anime-tuned x264 MP4 with AAC stereo audio.
    Ensures exactly 1 audio stream (or 0 if has_audio is False), dropping subtitles, data, and chapters.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", input_path,
        "-map", "0:v:0",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
    ]
    if tune:
        cmd += ["-tune", tune]
    cmd += ["-pix_fmt", "yuv420p"]
    if has_audio:
        cmd += [
            "-map", "0:a:0?",
            "-c:a", "aac", "-b:a", audio_bitrate, "-ar", "48000", "-ac", "2"
        ]
    else:
        cmd += ["-an"]
    cmd += [
        "-sn", "-dn", "-map_chapters", "-1",
        "-movflags", "+faststart",
        output_path
    ]
    run(cmd)

    # Verify stream structure
    verification = verify_output_streams(output_path, expect_audio=has_audio)
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
