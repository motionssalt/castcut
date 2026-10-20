"""Automated test for CastCut mobile-safe encode profile and playback validation.
Guards against regressions in codec, level, container flags, edit lists, and bitrate ceiling.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from pipeline.utils import (
    EXPORT_CRF,
    EXPORT_LEVEL,
    EXPORT_MAXRATE,
    EXPORT_PROFILE,
    EXPORT_VCODEC,
    check_faststart,
    check_no_editlist,
    encode_final_export,
    validate_export_playback_and_size,
)


class TestEncodeProfileAndValidation(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="castcut_test_encode_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_constants_contract(self):
        """Verify profile constants adhere to the ClipForge mobile contract."""
        self.assertEqual(EXPORT_VCODEC, "libx264")
        self.assertEqual(EXPORT_PROFILE, "high")
        self.assertEqual(EXPORT_LEVEL, "4.0")
        self.assertEqual(EXPORT_CRF, "23")
        self.assertEqual(EXPORT_MAXRATE, "1872k")

    def test_sample_encode_and_validation(self):
        """Generate a synthetic 1080p test video and verify encode_final_export produces
        a fully-compliant, playable mobile MP4 passing all validation gates.
        """
        raw_src = os.path.join(self.test_dir, "test_input.mp4")
        out_dst = os.path.join(self.test_dir, "test_output.mp4")

        # Generate 2-second 1080p synthetic sample with audio
        gen_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=1920x1080:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
            raw_src
        ]
        subprocess.run(gen_cmd, check=True)

        # Run encode_final_export
        verif = encode_final_export(raw_src, out_dst, has_audio=True)

        self.assertTrue(os.path.isfile(out_dst))
        self.assertTrue(verif["ok"])
        self.assertEqual(verif["vcodec"], "h264")
        self.assertEqual(verif["profile"], "High")
        self.assertLessEqual(verif["level"], 41)
        self.assertEqual(verif["width"], 1920)
        self.assertEqual(verif["height"], 1080)
        self.assertLessEqual(verif["fps"], 30.05)
        self.assertEqual(verif["audio_streams"], 1)
        self.assertEqual(verif["audio_codec"], "aac")
        self.assertEqual(verif["audio_channels"], 2)
        self.assertTrue(verif["faststart"])
        self.assertFalse(verif["has_elst"])
        self.assertLessEqual(verif["video_bitrate_kbps"], 2000.0)

        # Direct container checks
        self.assertTrue(check_faststart(out_dst))
        self.assertTrue(check_no_editlist(out_dst))

        # Explicit run of validate_export_playback_and_size
        val = validate_export_playback_and_size(out_dst, expect_audio=True)
        self.assertTrue(val["ok"])

    def test_validation_fails_on_unsupported_level_or_bitrate(self):
        """Ensure validate_export_playback_and_size rejects non-compliant files."""
        bad_file = os.path.join(self.test_dir, "bad_level.mp4")

        # Create file with Level 5.0 and B-frames
        bad_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=1920x1080:rate=24",
            "-c:v", "libx264", "-level:v", "5.0", "-bf", "3", "-pix_fmt", "yuv420p",
            bad_file
        ]
        subprocess.run(bad_cmd, check=True)

        with self.assertRaises(RuntimeError) as ctx:
            validate_export_playback_and_size(bad_file, expect_audio=False)
        self.assertTrue("level" in str(ctx.exception).lower() or "b-frames" in str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
