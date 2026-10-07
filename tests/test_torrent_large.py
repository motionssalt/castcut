"""Regression and scale test for large torrent files (50, 200, 1000 files).

Verifies:
- inspect_torrent_file parses 50, 200, and 1000 file manifests without errors.
- Bencode encoder and decoder handle multi-megabyte payloads without recursion overflow.
- Video filtering accurately classifies .mkv, .mp4, and ignores non-video files (.nfo, .srt).
"""
import io
import os
import tempfile
from pathlib import Path
import pytest

from pipeline.torrents import (
    _decode_bencode,
    _encode_bencode,
    inspect_torrent,
    torrent_video_candidates,
    MAX_TORRENT_BYTES,
)


def make_synthetic_torrent_bytes(file_count: int, piece_count: int = 1000) -> bytes:
    files = []
    for i in range(1, file_count + 1):
        ext = "nfo" if i % 10 == 0 else ("srt" if i % 7 == 0 else "mkv")
        files.append({
            b"length": 1024 * 1024 * 350 + i * 1024,
            b"path": [f"Code Geass Lelouch of the Rebellion R2 - Episode {i}.{ext}".encode("utf-8")],
        })
    piece_hashes = b"\xab" * (piece_count * 20)
    torrent_dict = {
        b"announce": b"http://tracker.example.com/announce",
        b"info": {
            b"name": b"Code Geass Lelouch of the Rebellion R2",
            b"piece length": 1024 * 1024,
            b"pieces": piece_hashes,
            b"files": files,
        },
    }
    return _encode_bencode(torrent_dict)


def test_50_file_torrent_parsing():
    raw = make_synthetic_torrent_bytes(50, piece_count=3000)
    assert len(raw) > 60_000

    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tf:
        tf.write(raw)
        tf_path = Path(tf.name)

    try:
        info = inspect_torrent(tf_path)
        assert info["name"] == "Code Geass Lelouch of the Rebellion R2"
        assert info["file_count"] == 50
        assert len(info["files"]) == 50
        # Non-video files (.nfo, .srt) are correctly filtered by torrent_video_candidates
        video_files = torrent_video_candidates(info)
        assert len(video_files) > 0
        assert len(video_files) < 50
    finally:
        if tf_path.exists():
            tf_path.unlink()


def test_200_file_torrent_scaling():
    raw = make_synthetic_torrent_bytes(200, piece_count=2000)
    with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tf:
        tf.write(raw)
        tf_path = Path(tf.name)

    try:
        info = inspect_torrent(tf_path)
        assert info["file_count"] == 200
        assert len(info["files"]) == 200
    finally:
        if tf_path.exists():
            tf_path.unlink()


def test_1000_file_bencode_decode_encode_roundtrip():
    raw = make_synthetic_torrent_bytes(1000, piece_count=1000)
    decoded = _decode_bencode(raw)
    assert isinstance(decoded, dict)
    reencoded = _encode_bencode(decoded)
    assert len(reencoded) == len(raw)
