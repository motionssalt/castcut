"""CastCut torrent/magnet inspection — ported from ClipForge stage_a/ingest.py.

Pure-python (stdlib only). Never contacts trackers or peers itself; the aria2
swarm transfer is driven by the workflow shell. Used by:

  * pipeline/select.py  — build jobs/<job_id>/files.json (metadata only)
  * the Pages API       — same logic mirrored in JS for instant .torrent parsing
  * pipeline/ingest.py  — resolve the user's selected file indices after
                          an aria2 --select-file download

Safety limits are preserved from ClipForge's magnet_source.py/torrent_source.py.
"""
from __future__ import annotations

import base64
import hashlib
import re
import urllib.parse
from pathlib import Path
from typing import Any

# Safety limits (preserved from ClipForge).
MAX_TORRENT_BYTES = 1 * 1024 * 1024
MAX_VIDEO_BYTES = 12 * 1024 * 1024 * 1024
MAX_MAGNET_URI_CHARS = 8_192
MAX_TRACKERS = 30
MAX_TRACKER_URI_CHARS = 512
MAX_METADATA_SOURCES = 3
MAX_METADATA_SOURCE_URI_CHARS = 1_024
MAX_DISPLAY_NAME_CHARS = 255
VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".ts", ".m2ts",
}
_ALLOWED_TRACKER_SCHEMES = {"udp", "http", "https"}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_HEX_INFOHASH_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_BASE32_INFOHASH_RE = re.compile(r"[A-Z2-7a-z2-7]{32}\Z")


class MagnetError(ValueError):
    """A magnet URI is malformed or exceeds safe limits."""


class BencodeError(ValueError):
    """A torrent manifest is malformed or exceeds safe limits."""


# --------------------------------------------------------------------------- #
# Magnet validation (offline)                                                  #
# --------------------------------------------------------------------------- #

def _reject_controls(value: str, field: str) -> str:
    if _CONTROL_RE.search(value):
        raise MagnetError(f"{field} contains a control character")
    return value


def _canonical_v1_infohash(value: str) -> str:
    if _HEX_INFOHASH_RE.fullmatch(value):
        return value.upper()
    if _BASE32_INFOHASH_RE.fullmatch(value):
        try:
            decoded = base64.b32decode(value.upper())
        except (ValueError, base64.binascii.Error) as exc:
            raise MagnetError("xt contains invalid base32 infohash data") from exc
        if len(decoded) != 20:
            raise MagnetError("xt does not contain a v1 20-byte infohash")
        return decoded.hex().upper()
    raise MagnetError("xt must contain one v1 urn:btih 40-hex or 32-base32 infohash")


def _validate_http_source(value: str, field: str) -> str:
    if len(value) > MAX_METADATA_SOURCE_URI_CHARS:
        raise MagnetError(f"{field} URI exceeds length limit")
    _reject_controls(value, f"{field} URI")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise MagnetError(f"{field} URI must use http or https with a host")
    if parsed.username is not None or parsed.password is not None:
        raise MagnetError(f"{field} URI must not embed credentials")
    return value


def _validate_tracker(value: str) -> str:
    if len(value) > MAX_TRACKER_URI_CHARS:
        raise MagnetError("tracker URI exceeds length limit")
    _reject_controls(value, "tracker URI")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() not in _ALLOWED_TRACKER_SCHEMES or not parsed.netloc:
        raise MagnetError("tracker URI must use udp, http, or https with a host")
    if parsed.username is not None or parsed.password is not None:
        raise MagnetError("tracker URI must not embed credentials")
    return value


def inspect_magnet(magnet_uri: str) -> dict[str, Any]:
    """Return a safe, normalized summary of one v1 BitTorrent magnet URI."""
    if not isinstance(magnet_uri, str):
        raise MagnetError("magnet URI must be text")
    value = magnet_uri.strip()
    if not value:
        raise MagnetError("magnet URI is empty")
    if len(value) > MAX_MAGNET_URI_CHARS:
        raise MagnetError(f"magnet URI exceeds {MAX_MAGNET_URI_CHARS} character limit")
    _reject_controls(value, "magnet URI")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "magnet" or parsed.netloc or parsed.path:
        raise MagnetError("source must be a magnet:? URI")
    try:
        pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise MagnetError("magnet URI has malformed query parameters") from exc

    xt_values: list[str] = []
    display_names: list[str] = []
    trackers: list[str] = []
    metadata_sources: list[str] = []
    for key, raw_value in pairs:
        _reject_controls(key, "magnet parameter name")
        _reject_controls(raw_value, "magnet parameter value")
        if key == "xt":
            if raw_value.lower().startswith("urn:btih:"):
                xt_values.append(raw_value[9:])
        elif key == "dn":
            if len(raw_value) > MAX_DISPLAY_NAME_CHARS:
                raise MagnetError("display name exceeds length limit")
            display_names.append(raw_value)
        elif key == "tr":
            trackers.append(_validate_tracker(raw_value))
        elif key == "xs":
            metadata_sources.append(_validate_http_source(raw_value, "exact source"))

    if len(xt_values) != 1:
        raise MagnetError("magnet URI must include exactly one v1 xt=urn:btih value")
    if len(display_names) > 1:
        raise MagnetError("magnet URI must not include multiple display names")
    if len(trackers) > MAX_TRACKERS:
        raise MagnetError(f"magnet URI has more than {MAX_TRACKERS} trackers")
    if len(metadata_sources) > MAX_METADATA_SOURCES:
        raise MagnetError(f"magnet URI has more than {MAX_METADATA_SOURCES} exact sources")

    return {
        "version": 1,
        "infohash_v1": _canonical_v1_infohash(xt_values[0]),
        "display_name": display_names[0] if display_names else "",
        "tracker_count": len(trackers),
        "trackers": trackers,
        "metadata_sources": metadata_sources,
    }


def find_saved_metadata(directory: Path, infohash_v1: str) -> Path:
    """Return aria2's single saved ``.torrent`` metadata file for the infohash."""
    expected = _canonical_v1_infohash(infohash_v1)
    if not directory.is_dir():
        raise FileNotFoundError("magnet metadata directory is missing — metadata was never fetched (no seeds?)")
    matches = [
        path for path in directory.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".torrent"
        and path.stem.upper() == expected
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError("aria2 did not save metadata for the magnet — no seeds or unreachable trackers")
    raise FileNotFoundError("aria2 saved more than one matching magnet metadata file")


# --------------------------------------------------------------------------- #
# Torrent manifest inspection (bencode)                                        #
# --------------------------------------------------------------------------- #

def _decode_bencode(data: bytes) -> Any:
    index = 0

    def parse() -> Any:
        nonlocal index
        if index >= len(data):
            raise BencodeError("unexpected end of bencoded data")
        token = data[index:index + 1]
        if token == b"i":
            index += 1
            end = data.find(b"e", index)
            if end < 0:
                raise BencodeError("unterminated integer")
            raw = data[index:end]
            if not raw or raw in (b"-0",) or (raw.startswith(b"0") and len(raw) > 1):
                raise BencodeError("invalid integer")
            try:
                value = int(raw)
            except ValueError as exc:
                raise BencodeError("invalid integer") from exc
            index = end + 1
            return value
        if token == b"l":
            index += 1
            values = []
            while index < len(data) and data[index:index + 1] != b"e":
                values.append(parse())
            if index >= len(data):
                raise BencodeError("unterminated list")
            index += 1
            return values
        if token == b"d":
            index += 1
            values: dict[bytes, Any] = {}
            while index < len(data) and data[index:index + 1] != b"e":
                key = parse()
                if not isinstance(key, bytes):
                    raise BencodeError("dictionary key is not a byte string")
                values[key] = parse()
            if index >= len(data):
                raise BencodeError("unterminated dictionary")
            index += 1
            return values
        if token.isdigit():
            colon = data.find(b":", index)
            if colon < 0:
                raise BencodeError("missing byte-string separator")
            try:
                length = int(data[index:colon])
            except ValueError as exc:
                raise BencodeError("invalid byte-string length") from exc
            if length < 0:
                raise BencodeError("negative byte-string length")
            index = colon + 1
            end = index + length
            if end > len(data):
                raise BencodeError("truncated byte string")
            value = data[index:end]
            index = end
            return value
        raise BencodeError("unknown bencode token")

    value = parse()
    if index != len(data):
        raise BencodeError("trailing bencoded data")
    return value


def _encode_bencode(value: Any) -> bytes:
    if isinstance(value, int):
        return f"i{value}e".encode("ascii")
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_encode_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        if not all(isinstance(key, bytes) for key in value):
            raise BencodeError("torrent dictionary has a non-byte key")
        payload = []
        for key in sorted(value):
            payload.append(_encode_bencode(key))
            payload.append(_encode_bencode(value[key]))
        return b"d" + b"".join(payload) + b"e"
    raise BencodeError("torrent contains an unsupported bencode value")


def torrent_infohash_v1(torrent_path: Path) -> str:
    raw = torrent_path.read_bytes()
    if not raw or len(raw) > MAX_TORRENT_BYTES:
        raise BencodeError("torrent file is empty or exceeds the allowed size")
    root = _decode_bencode(raw)
    if not isinstance(root, dict) or not isinstance(root.get(b"info"), dict):
        raise BencodeError("torrent has no info dictionary")
    return hashlib.sha1(_encode_bencode(root[b"info"])).hexdigest().upper()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, bytes):
        raise BencodeError(f"{field} is missing or invalid")
    return value.decode("utf-8", errors="replace")


def _safe_path_part(value: str, field: str) -> str:
    if (not value or value in {".", ".."} or "\x00" in value or
            "/" in value or "\\" in value):
        raise BencodeError(f"{field} contains an unsafe path component")
    return value


def inspect_torrent(torrent_path: Path) -> dict[str, Any]:
    """Return safe display metadata for a single- or multi-file torrent."""
    raw = torrent_path.read_bytes()
    if not raw:
        raise BencodeError("torrent file is empty")
    if len(raw) > MAX_TORRENT_BYTES:
        raise BencodeError(f"torrent file exceeds {MAX_TORRENT_BYTES} byte limit")
    root = _decode_bencode(raw)
    if not isinstance(root, dict):
        raise BencodeError("torrent root must be a dictionary")
    info = root.get(b"info")
    if not isinstance(info, dict):
        raise BencodeError("torrent has no info dictionary")

    name = _safe_path_part(_text(info.get(b"name"), "info.name"), "info.name")
    files: list[dict[str, Any]] = []
    if b"files" in info:
        raw_files = info[b"files"]
        if not isinstance(raw_files, list) or not raw_files:
            raise BencodeError("info.files is invalid")
        for entry in raw_files:
            if not isinstance(entry, dict) or not isinstance(entry.get(b"length"), int):
                raise BencodeError("torrent contains an invalid file entry")
            parts = entry.get(b"path")
            if not isinstance(parts, list) or not parts:
                raise BencodeError("torrent file entry has no path")
            clean_parts = [
                _safe_path_part(_text(part, "file path"), "file path")
                for part in parts
            ]
            files.append({
                "index": len(files) + 1,
                "path": "/".join(clean_parts),
                "length": entry[b"length"],
            })
    else:
        length = info.get(b"length")
        if not isinstance(length, int):
            raise BencodeError("single-file torrent has no valid length")
        files.append({"index": 1, "path": name, "length": length})

    metadata = {
        "name": name,
        "file_count": len(files),
        "total_bytes": sum(max(0, item["length"]) for item in files),
        "files": files,
    }
    metadata["video_candidates"] = torrent_video_candidates(metadata)
    return metadata


def torrent_video_candidates(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        item for item in metadata["files"]
        if Path(item["path"]).suffix.lower() in VIDEO_EXTENSIONS and item["length"] > 0
    ]
    for item in candidates:
        if item["length"] > MAX_VIDEO_BYTES:
            raise BencodeError(
                f"video candidate {item['index']} exceeds the {MAX_VIDEO_BYTES} byte safety limit"
            )
    if not candidates:
        wanted = ", ".join(sorted(VIDEO_EXTENSIONS))
        raise BencodeError(f"torrent has no supported video file ({wanted})")
    return candidates


def resolve_downloaded_files(root: Path, wanted_paths: list[str]) -> list[Path]:
    """Match downloaded files under ``root`` to the selected torrent paths.

    aria2 ``--select-file`` still writes boundary pieces of neighbouring
    files, so we match strictly by relative-path suffix (ClipForge's
    ``select_video`` logic, extended to a list) and return matches in the
    order of ``wanted_paths``.
    """
    found: list[Path] = []
    all_files = [p for p in root.rglob("*") if p.is_file()]
    for wanted in wanted_paths:
        expected = wanted.replace("\\", "/").lstrip("/")
        matches = []
        for path in all_files:
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if relative == expected or relative.endswith("/" + expected):
                matches.append(path)
        if len(matches) == 1:
            found.append(matches[0])
        elif not matches:
            raise FileNotFoundError(f"selected file was not downloaded: {expected}")
        else:
            raise FileNotFoundError(f"more than one downloaded path matches: {expected}")
    return found
