"""Process/probe helpers copied verbatim from ClipForge a05846de7c76a66ad42ba6fd37126514bc452167."""
from __future__ import annotations
import json, subprocess
from pathlib import Path
class StageBError(RuntimeError):
    pass

def run(cmd: list[str], description: str) -> subprocess.CompletedProcess[str]:
    """Run ``cmd``, echoing it, and raise ``StageBError`` with a stderr tail."""
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        return subprocess.run(
            cmd,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise StageBError(f"{description}: required tool not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "")[-1500:] if exc.stderr else "no diagnostic output"
        raise StageBError(f"{description} failed: {tail}") from exc

def sh(cmd: list[str]) -> None:
    """Run a command streaming output (for long ffmpeg renders)."""
    print(f"$ {' '.join(cmd)}", flush=True)
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise StageBError(f"required tool not found: {cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        raise StageBError(f"command failed (exit {exc.returncode}): {cmd[0]}") from exc

def probe_json(path: str | Path) -> dict:
    """Return the full ffprobe JSON for a media file."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,codec_name,profile,level,pix_fmt,width,height,"
            "sample_rate,channels,r_frame_rate,has_b_frames,start_time,sample_aspect_ratio,time_base,"
            "avg_frame_rate"
            ,
            "-show_entries", "format=format_name,format_long_name,duration,bit_rate,start_time,size",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise StageBError(f"ffprobe failed on {path}: {out.stderr[-800:]}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise StageBError(f"ffprobe returned unparseable JSON for {path}") from exc

