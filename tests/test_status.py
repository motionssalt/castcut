"""Automated test suite for pipeline.status CLI and library calls.

Verifies:
- Every state and argument combination used in workflows.
- --set-json edge cases (including duplicate job_id that caused the run #2 crash).
- Terminal success state protection against accidental failure overwrites.
- Direct write_status signature flexibility (positional, keyword, extra kwargs).
"""
import json
import os
import subprocess
import sys
import tempfile
import pytest

from pipeline.status import write_status, read_status, DEFAULT_TTL_SECONDS


WORKFLOW_STATES = [
    ("ingest_running", "Downloading source"),
    ("ingested", "Source ready. Pick a mode (Clips or Scene)."),
    ("ingest_failed", "Ingest failed — https://example.com/actions/runs/123"),
    ("selecting", "Fetching torrent metadata (listing files — no content download)"),
    ("awaiting_selection", "File list ready — pick which files to download."),
    ("select_failed", "Could not fetch torrent metadata"),
    ("faces_running", "Sampling frames and clustering unique faces"),
    ("faces_ready", "Face gallery ready. Pick characters in the UI."),
    ("faces_failed", "Face analysis failed; see workflow log."),
    ("scene_analyzing", "Transcribing and detecting scene boundaries"),
    ("scene_awaiting_json", "Agent prompt ready. Copy, run in your AI, paste the JSON back."),
    ("scene_failed", "Scene analysis failed; see workflow log."),
    ("clips_exporting", "Cutting and joining selected clips"),
    ("clips_ready", "Final clips file ready. Download from the release."),
    ("clips_failed", "Clips export failed; see workflow log."),
    ("scene_exporting", "Cutting and joining scene segments"),
    ("scene_ready", "Scene file ready. Download from the release."),
    ("cancelled", "Run cancelled."),
]


@pytest.fixture
def temp_jobs_dir(tmp_path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    return str(jobs_dir)


def run_status_cli(args, check=True):
    cmd = [sys.executable, "-m", "pipeline.status"] + [str(a) for a in args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


class TestStatusCLI:
    @pytest.mark.parametrize("state,message", WORKFLOW_STATES)
    def test_workflow_states_cli(self, temp_jobs_dir, state, message):
        job_id = f"job-{state}"
        res = run_status_cli([
            job_id,
            "--state", state,
            "--message", message,
            "--jobs-root", temp_jobs_dir,
            "--release-tag", f"castcut-{job_id}",
            "--workflow-run-url", "https://github.com/motionssalt/castcut/actions/runs/999",
        ])
        assert res.returncode == 0
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["job_id"] == job_id
        assert data["state"] == state
        assert data["message"] == message
        assert data["release_tag"] == f"castcut-{job_id}"
        assert data["workflow_run_url"] == "https://github.com/motionssalt/castcut/actions/runs/999"
        assert "created_at" in data
        assert "updated_at" in data

    def test_set_json_with_duplicate_job_id_does_not_crash(self, temp_jobs_dir):
        """Regression test for the bug that caused the run #2 failure:
        --set-json contains job_id, order, total_segments, etc."""
        job_id = "job-regr-duplicate-id"
        set_json = json.dumps({
            "job_id": job_id,
            "version": 1,
            "order": ["char1", "char2"],
            "total_segments": 72,
            "size_before_bytes": 1000,
            "size_after_bytes": 500,
            "asset_name": "castcut-clips-sample.mp4",
        })
        res = run_status_cli([
            job_id,
            "--state", "clips_ready",
            "--message", "Clips file ready",
            "--jobs-root", temp_jobs_dir,
            "--set-json", set_json,
        ])
        assert res.returncode == 0
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["job_id"] == job_id
        assert data["state"] == "clips_ready"
        assert data["total_segments"] == 72
        assert data["asset_name"] == "castcut-clips-sample.mp4"

    def test_set_json_malformed_does_not_crash(self, temp_jobs_dir):
        job_id = "job-malformed-json"
        res = run_status_cli([
            job_id,
            "--state", "ingest_running",
            "--message", "Running",
            "--jobs-root", temp_jobs_dir,
            "--set-json", "{not-valid-json",
        ])
        assert res.returncode == 0
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["state"] == "ingest_running"

    def test_set_json_non_dict_does_not_crash(self, temp_jobs_dir):
        job_id = "job-non-dict-json"
        res = run_status_cli([
            job_id,
            "--state", "ingest_running",
            "--message", "Running",
            "--jobs-root", temp_jobs_dir,
            "--set-json", "[1, 2, 3]",
        ])
        assert res.returncode == 0
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["state"] == "ingest_running"

    def test_terminal_success_state_cannot_be_overwritten_by_failed(self, temp_jobs_dir):
        job_id = "job-success-guard"
        # First write clips_ready
        run_status_cli([
            job_id,
            "--state", "clips_ready",
            "--message", "Export finished",
            "--jobs-root", temp_jobs_dir,
        ])
        # Attempt to overwrite with clips_failed without force
        run_status_cli([
            job_id,
            "--state", "clips_failed",
            "--message", "Should be ignored",
            "--jobs-root", temp_jobs_dir,
        ])
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["state"] == "clips_ready", "Terminal success was overwritten by failure!"

    def test_force_flag_allows_overwriting_terminal_state(self, temp_jobs_dir):
        job_id = "job-force-override"
        run_status_cli([
            job_id,
            "--state", "clips_ready",
            "--message", "Export finished",
            "--jobs-root", temp_jobs_dir,
        ])
        run_status_cli([
            job_id,
            "--state", "clips_failed",
            "--message", "Forced failure",
            "--jobs-root", temp_jobs_dir,
            "--force",
        ])
        data = read_status(job_id, jobs_root=temp_jobs_dir)
        assert data["state"] == "clips_failed"


class TestWriteStatusPythonLibrary:
    def test_positional_and_duplicate_kwargs(self, temp_jobs_dir):
        res = write_status(
            "job-py-1",
            "ingest_running",
            message="Test msg",
            jobs_root=temp_jobs_dir,
            job_id="job-py-1",
            extra_field="hello",
        )
        assert res["job_id"] == "job-py-1"
        assert res["state"] == "ingest_running"
        assert res["extra_field"] == "hello"

    def test_keyword_only_call(self, temp_jobs_dir):
        res = write_status(
            job_id="job-py-2",
            state="ingested",
            message="Ingested msg",
            jobs_root=temp_jobs_dir,
        )
        assert res["job_id"] == "job-py-2"
        assert res["state"] == "ingested"

    def test_atomic_file_write(self, temp_jobs_dir):
        write_status("job-py-3", "ready", jobs_root=temp_jobs_dir)
        target = os.path.join(temp_jobs_dir, "job-py-3", "status.json")
        assert os.path.isfile(target)
        # Ensure temp file does not remain
        assert not os.path.isfile(target + ".tmp")
