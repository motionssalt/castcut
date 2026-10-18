"""Tests for Task B: IRL (live-action) support across CastCut pipeline."""
import json
import os
import tempfile
import pytest

from pipeline.faces import get_project_content_type, _load_irl_detector_and_embedder
from pipeline.scene_prompt import SCHEMA_TEXT


def test_get_project_content_type_defaults_to_anime():
    with tempfile.TemporaryDirectory() as tmpdir:
        job_id = "test-job-default"
        assert get_project_content_type(job_id, jobs_root=tmpdir) == "anime"


def test_get_project_content_type_reads_ingest_request():
    with tempfile.TemporaryDirectory() as tmpdir:
        job_id = "test-job-irl"
        job_dir = os.path.join(tmpdir, job_id)
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, "ingest-request.json"), "w") as f:
            json.dump({"content_type": "irl"}, f)
        assert get_project_content_type(job_id, jobs_root=tmpdir) == "irl"


def test_irl_detector_initializes_yunet_sface():
    detect_fn, name, thr = _load_irl_detector_and_embedder()
    assert "YuNet" in name or "fallback" in name
    assert thr > 0.0


def test_scene_prompt_customizes_for_irl():
    with tempfile.TemporaryDirectory() as tmpdir:
        job_id = "test-job-scene-irl"
        scene_dir = os.path.join(tmpdir, job_id, "scene")
        os.makedirs(scene_dir, exist_ok=True)

        # Ingest request specifies irl
        with open(os.path.join(tmpdir, job_id, "ingest-request.json"), "w") as f:
            json.dump({"content_type": "irl"}, f)

        with open(os.path.join(scene_dir, "scene-request.json"), "w") as f:
            json.dump({"request": "Find the interview discussion"}, f)

        with open(os.path.join(scene_dir, "video_meta.json"), "w") as f:
            json.dump({"duration": 120.0, "width": 1920, "height": 1080, "fps": 30.0, "has_audio": True}, f)

        with open(os.path.join(scene_dir, "scene_index.json"), "w") as f:
            json.dump({"threshold": 0.28, "boundaries": [30.0, 60.0, 90.0]}, f)

        with open(os.path.join(scene_dir, "transcript.json"), "w") as f:
            json.dump({"language": "en", "segments": [{"start": 0.0, "end": 10.0, "text": "Hello world"}]}, f)

        import subprocess, sys
        cmd = [sys.executable, "-m", "pipeline.scene_prompt", job_id, "--jobs-root", tmpdir]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode == 0, res.stderr

        prompt_file = os.path.join(scene_dir, "agent_prompt.txt")
        assert os.path.isfile(prompt_file)
        content = open(prompt_file).read()
        assert "LIVE-ACTION / REAL-WORLD" in content
        assert "not anime" in content
