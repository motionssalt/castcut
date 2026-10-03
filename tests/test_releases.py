"""Unit tests for pipeline.releases capacity and overflow logic."""
import pytest
from unittest.mock import patch
from pipeline import releases


def test_primary_release_selected_when_empty():
    with patch("pipeline.releases.get_release_info") as mock_info, \
         patch("pipeline.releases.ensure_release") as mock_ensure:
        mock_info.return_value = None
        mock_ensure.return_value = "castcut-proj-1"
        tag = releases.choose_release_for_asset("proj-1", 1000)
        assert tag == "castcut-proj-1"


def test_primary_release_selected_when_has_space():
    with patch("pipeline.releases.get_release_info") as mock_info:
        mock_info.return_value = {
            "tagName": "castcut-proj-1",
            "assets": [{"name": "asset1.mp4", "size": 1000}],
        }
        tag = releases.choose_release_for_asset("proj-1", 5000)
        assert tag == "castcut-proj-1"


def test_overflow_to_out_2_when_primary_full():
    with patch("pipeline.releases.get_release_info") as mock_info, \
         patch("pipeline.releases.ensure_release") as mock_ensure:
        # First call for primary release returns full
        # Second call for out-2 returns None (new release)
        mock_info.side_effect = [
            {
                "tagName": "castcut-proj-1",
                "assets": [{"name": f"asset{i}.mp4", "size": 100 * 1024 * 1024} for i in range(releases.MAX_ASSETS_PER_RELEASE)],
            },
            None,
        ]
        mock_ensure.return_value = "castcut-proj-1-out-2"
        tag = releases.choose_release_for_asset("proj-1", 1000)
        assert tag == "castcut-proj-1-out-2"


def test_overflow_on_size_threshold():
    with patch("pipeline.releases.get_release_info") as mock_info, \
         patch("pipeline.releases.ensure_release") as mock_ensure:
        # Primary release has 1.7 GiB, new asset is 200 MiB (total > 1.8 GiB)
        mock_info.side_effect = [
            {
                "tagName": "castcut-proj-1",
                "assets": [{"name": "big.mp4", "size": int(1.75 * 1024 ** 3)}],
            },
            None,
        ]
        mock_ensure.return_value = "castcut-proj-1-out-2"
        tag = releases.choose_release_for_asset("proj-1", 200 * 1024 * 1024)
        assert tag == "castcut-proj-1-out-2"
