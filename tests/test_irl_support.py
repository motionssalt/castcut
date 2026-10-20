"""Tests for Task B: IRL (live-action) support across CastCut pipeline."""
import json
import os
import tempfile
import pytest

from pipeline.faces import get_project_content_type, _load_irl_detector_and_embedder
from pipeline.scene_prompt import SCHEMA_TEXT


def test_get_project_content_type_missing_fails_without_anime_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        job_id = "test-job-default"
        with pytest.raises(ValueError, match="missing stored content_type"):
            get_project_content_type(job_id, jobs_root=tmpdir)


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


def test_merge_clusters_combines_same_actor_across_scenes():
    import numpy as np
    from pipeline.faces import _merge_clusters
    vec_a = np.array([1.0, 0.0, 0.0], dtype='float32')
    vec_b = np.array([0.92, 0.08, 0.0], dtype='float32')
    vec_b /= np.linalg.norm(vec_b)

    clusters = [
        {'centroid': vec_a, 'count': 6, 'times': [1.0, 2.0, 3.0], 'best_score': 0.8, 'best_blur': 50.0, 'best_frame': 'f1.jpg', 'best_box': [0, 0, 10, 10]},
        {'centroid': vec_b, 'count': 4, 'times': [15.0, 16.0], 'best_score': 0.88, 'best_blur': 65.0, 'best_frame': 'f2.jpg', 'best_box': [0, 0, 10, 10]},
    ]
    merged = _merge_clusters(clusters, cos_threshold=0.60)
    assert len(merged) == 1
    assert merged[0]['count'] == 10
    assert merged[0]['times'] == [1.0, 2.0, 3.0, 15.0, 16.0]
    assert merged[0]['best_frame'] == 'f2.jpg'


def test_merge_clusters_never_merges_simultaneously_visible_actors():
    import numpy as np
    from pipeline.faces import _merge_clusters
    vec_a = np.array([1.0, 0.0, 0.0], dtype='float32')
    vec_b = np.array([0.95, 0.05, 0.0], dtype='float32')
    vec_b /= np.linalg.norm(vec_b)

    clusters = [
        {'centroid': vec_a, 'count': 3, 'times': [2.0, 4.0], 'best_score': 0.8, 'best_blur': 50.0, 'best_frame': 'f1.jpg', 'best_box': [0, 0, 10, 10]},
        {'centroid': vec_b, 'count': 3, 'times': [2.0, 5.0], 'best_score': 0.85, 'best_blur': 60.0, 'best_frame': 'f2.jpg', 'best_box': [0, 0, 10, 10]},
    ]
    merged = _merge_clusters(clusters, cos_threshold=0.60)
    assert len(merged) == 2


def test_face_thumb_generates_data_uri():
    import numpy as np
    from pipeline.faces import _thumb
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    box = (10, 10, 50, 50)
    thumb_uri = _thumb(img, box)
    assert thumb_uri.startswith('data:image/')
