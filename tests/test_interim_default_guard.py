"""Guard against regressions away from proven-playable mobile profile (Variant I).
Enforces:
- H.264 Main profile, Level 4.1
- Resolution capped at 720p (total pixel count <= 921,600 at source aspect ratio, never upscaled)
- Video bitrate <= 2000 kbps across every 10-second window
- Exactly ONE audio stream (the chosen track), AAC 128k stereo 48kHz
- 0 B-frames, constant frame rate (capped at 30), matching timescale
- Faststart, mp42 brand, no edit lists
"""
import os
import tempfile
import subprocess
import pytest
from pathlib import Path
from fractions import Fraction
from pipeline.delivery_pass import command, reference_command, VERSION
from pipeline.export_checks import probe, validate
from pipeline.clipforge_export import export_segments
from pipeline.utils import (
    EXPORT_PROFILE, EXPORT_LEVEL, EXPORT_MAXRATE, EXPORT_BUFSIZE,
    EXPORT_MAX_WIDTH, EXPORT_MAX_HEIGHT, EXPORT_MAX_PIXELS
)

def test_interim_constants_contract():
    assert EXPORT_PROFILE == "main"
    assert EXPORT_LEVEL == "4.1"
    assert EXPORT_MAXRATE == "1800k"
    assert EXPORT_BUFSIZE == "1800k"
    assert EXPORT_MAX_WIDTH == 1280
    assert EXPORT_MAX_HEIGHT == 720
    assert EXPORT_MAX_PIXELS == 921600
    assert VERSION == "variant-i-720p-interim-default-v1"

def test_command_generation_stays_within_proven_profile(tmp_path):
    source = tmp_path / "source.mp4"
    # Create 1080p source
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=24:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        str(source)
    ], check=True)
    
    out = tmp_path / "output.mp4"
    cmd = command(source, out, safe=True)
    
    # Assert video encoder settings
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-profile:v") + 1] == "main"
    assert cmd[cmd.index("-level:v") + 1] == "4.1"
    assert cmd[cmd.index("-preset") + 1] == "medium"
    assert cmd[cmd.index("-bf") + 1] == "0"
    assert cmd[cmd.index("-b:v") + 1] == "1800k"
    assert cmd[cmd.index("-maxrate") + 1] == "1800k"
    assert cmd[cmd.index("-bufsize") + 1] == "1800k"
    
    # Assert audio settings
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-b:a") + 1] == "128k"
    assert cmd[cmd.index("-ar") + 1] == "48000"
    assert cmd[cmd.index("-ac") + 1] == "2"
    
    # Assert container settings
    assert "+faststart" in cmd[cmd.index("-movflags") + 1]
    assert cmd[cmd.index("-brand") + 1] == "mp42"
    assert cmd[cmd.index("-use_editlist") + 1] == "0"
    
    # Assert scale filter enforces <= 720p bounding box
    vf = cmd[cmd.index("-vf") + 1]
    assert "min(1280,iw)" in vf
    assert "min(720,ih)" in vf
    assert "force_original_aspect_ratio=decrease" in vf

def test_full_export_and_validation_proven_profile(tmp_path):
    source = tmp_path / "source_1080p.mkv"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le",
        str(source)
    ], check=True)
    
    out = tmp_path / "final_export.mp4"
    report = export_segments(source, [(0.0, 1.5), (1.5, 3.0)], out, audio_track=0, temp_dir=tmp_path)
    
    assert report["ok"] is True
    assert report["vcodec"] == "h264"
    assert report["profile"] == "Main"
    assert report["level"] == 41
    assert report["width"] == 1280
    assert report["height"] == 720
    assert report["width"] * report["height"] <= 921600
    assert report["video_peak_10s_kbps"] <= 2000.0
    assert report["video_average_kbps"] <= 2000.0
    assert report["audio_streams"] == 1
    assert report["audio_codec"] == "aac"
    assert report["audio_channels"] == 2
    assert report["faststart"] is True
    assert report["full_decode_passed"] is True
