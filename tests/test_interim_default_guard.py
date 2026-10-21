"""Guard for mobile profile resolution toggle (1080p and 900p / 1600x900).
Enforces:
- H.264 Main profile, Level 4.1
- Resolution toggle supports 1080p (bounded by 1920x1080) and 900p (bounded by 1600x900)
- Video bitrate <= 2000 kbps across every 10-second window
- Exactly ONE audio stream (the chosen track), AAC 128k stereo 48kHz
- 0 B-frames, constant frame rate (capped at 30), matching timescale
- Faststart, mp42 brand, no edit lists
"""

import os
import shutil
import tempfile
import subprocess
import unittest
from pathlib import Path
from fractions import Fraction

from pipeline.delivery_pass import command, reference_command, VERSION
from pipeline.export_checks import probe, validate
from pipeline.clipforge_export import export_segments
from pipeline.utils import (
    EXPORT_PROFILE, EXPORT_LEVEL, EXPORT_MAXRATE, EXPORT_BUFSIZE,
    EXPORT_MAX_WIDTH, EXPORT_MAX_HEIGHT, EXPORT_MAX_PIXELS,
    EXPORT_MAX_WIDTH_900P, EXPORT_MAX_HEIGHT_900P, EXPORT_MAX_PIXELS_900P
)

class TestResolutionToggleGuard(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="castcut_toggle_guard_")
        self.tmp_path = Path(self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_constants_contract(self):
        self.assertEqual(EXPORT_PROFILE, "main")
        self.assertEqual(EXPORT_LEVEL, "4.1")
        self.assertEqual(EXPORT_MAXRATE, "1800k")
        self.assertEqual(EXPORT_BUFSIZE, "1800k")
        self.assertEqual(EXPORT_MAX_WIDTH, 1920)
        self.assertEqual(EXPORT_MAX_HEIGHT, 1080)
        self.assertEqual(EXPORT_MAX_PIXELS, 2073600)
        self.assertEqual(EXPORT_MAX_WIDTH_900P, 1600)
        self.assertEqual(EXPORT_MAX_HEIGHT_900P, 900)
        self.assertEqual(EXPORT_MAX_PIXELS_900P, 1440000)

    def test_command_generation_1080p_and_900p(self):
        source = self.tmp_path / "source.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
            str(source)
        ], check=True)
        
        # Test 1080p command
        out_1080 = self.tmp_path / "output_1080.mp4"
        cmd_1080 = command(source, out_1080, resolution="1080p", safe=True)
        self.assertEqual(cmd_1080[cmd_1080.index("-c:v") + 1], "libx264")
        self.assertEqual(cmd_1080[cmd_1080.index("-profile:v") + 1], "main")
        self.assertEqual(cmd_1080[cmd_1080.index("-level:v") + 1], "4.1")
        self.assertEqual(cmd_1080[cmd_1080.index("-bf") + 1], "0")
        self.assertEqual(cmd_1080[cmd_1080.index("-b:v") + 1], "1800k")
        self.assertIn("+faststart", cmd_1080[cmd_1080.index("-movflags") + 1])
        self.assertEqual(cmd_1080[cmd_1080.index("-brand") + 1], "mp42")
        vf_1080 = cmd_1080[cmd_1080.index("-vf") + 1]
        self.assertIn("min(1920,iw)", vf_1080)
        self.assertIn("min(1080,ih)", vf_1080)

        # Test 900p command
        out_900 = self.tmp_path / "output_900.mp4"
        cmd_900 = command(source, out_900, resolution="900p", safe=True)
        vf_900 = cmd_900[cmd_900.index("-vf") + 1]
        self.assertIn("min(1600,iw)", vf_900)
        self.assertIn("min(900,ih)", vf_900)

    def test_full_export_and_validation_1080p_and_900p(self):
        source = self.tmp_path / "source_1080p.mkv"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "pcm_s16le",
            str(source)
        ], check=True)
        
        # 900p export (1600x900)
        out_900 = self.tmp_path / "export_900p.mp4"
        report_900 = export_segments(source, [(0.0, 1.5)], out_900, audio_track=0, resolution="900p", temp_dir=self.tmp_path)
        self.assertTrue(report_900["ok"])
        self.assertEqual(report_900["vcodec"], "h264")
        self.assertEqual(report_900["profile"], "Main")
        self.assertEqual(report_900["level"], 41)
        self.assertEqual(report_900["width"], 1600)
        self.assertEqual(report_900["height"], 900)
        self.assertLessEqual(report_900["width"] * report_900["height"], 1440000)
        self.assertLessEqual(report_900["video_peak_10s_kbps"], 2000.0)
        self.assertTrue(report_900["faststart"])
        self.assertTrue(report_900["full_decode_passed"])

        # 1080p export (1920x1080)
        out_1080 = self.tmp_path / "export_1080p.mp4"
        report_1080 = export_segments(source, [(0.0, 1.5)], out_1080, audio_track=0, resolution="1080p", temp_dir=self.tmp_path)
        self.assertTrue(report_1080["ok"])
        self.assertEqual(report_1080["width"], 1920)
        self.assertEqual(report_1080["height"], 1080)
        self.assertLessEqual(report_1080["width"] * report_1080["height"], 2073600)
        self.assertLessEqual(report_1080["video_peak_10s_kbps"], 2000.0)
        self.assertTrue(report_1080["faststart"])
        self.assertTrue(report_1080["full_decode_passed"])

if __name__ == "__main__":
    unittest.main()
