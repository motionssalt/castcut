"""CastCut release-size gate (fix_round_2) — ported from ClipForge
``pipeline/stage_a/release_gate.py``.

GitHub's documented limit is **2 GiB per release asset**, with no limit on the
total size of a release (docs.github.com — "About releases", verified
2026-10-03). ClipForge uses the same ``LIMIT_BYTES = 2 * 1024**3`` constant.

We subtract a safety margin from the limit because limits can change, and we
always compare against the REAL on-disk file size, never an estimate. The
effective limit can be overridden with the ``CASTCUT_RELEASE_LIMIT_BYTES``
environment variable (repo Actions variable) — this exists so the exclusion
path can be exercised in tests with a tiny limit instead of a >2 GiB video.

CLI: ``python -m pipeline.release_size --check <file-or-glob> [...]`` exits 1
with a clear message when any matched file would not fit in a release.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

#: GitHub's hard per-file release-asset limit (same as ClipForge).
LIMIT_BYTES = 2 * 1024 ** 3  # 2 GiB

#: Safety margin under the limit — limits can change, so we stay clear of it.
MARGIN_BYTES = 128 * 1024 ** 2  # 128 MiB


def limit_bytes() -> int:
    """Effective per-asset limit (env override for testing).

    An empty or unset ``CASTCUT_RELEASE_LIMIT_BYTES`` is treated as unset and
    falls back to the documented GitHub per-asset limit. This is what the
    workflow passes when the repo variable is not configured
    (``vars.CASTCUT_RELEASE_LIMIT_BYTES`` renders as the empty string), so the
    size gate must never parse an empty value as zero or crash.
    """
    raw = os.environ.get("CASTCUT_RELEASE_LIMIT_BYTES", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return LIMIT_BYTES


def fits(size_bytes: int) -> bool:
    """True when a file of ``size_bytes`` is safe to attach to a release."""
    return int(size_bytes) <= limit_bytes() - MARGIN_BYTES


def main() -> None:
    ap = argparse.ArgumentParser(description="CastCut release-size gate")
    ap.add_argument("--check", nargs="+", required=True,
                    help="files or glob patterns to check against the limit")
    args = ap.parse_args()
    paths = []
    for pat in args.check:
        paths.extend(sorted(glob.glob(pat)))
    if not paths:
        print(f"release-size check: no files match {args.check}", file=sys.stderr)
        raise SystemExit(1)
    lim = limit_bytes()
    bad = []
    for p in paths:
        size = os.path.getsize(p)
        ok = fits(size)
        print(f"{p}: {size} bytes — {'fits' if ok else 'DOES NOT FIT'} "
              f"(limit {lim} - margin {MARGIN_BYTES})", flush=True)
        if not ok:
            bad.append(p)
    if bad:
        print("release-size check FAILED: these files exceed GitHub's per-asset "
              f"release limit and cannot be published: {bad}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()


__all__ = ["LIMIT_BYTES", "MARGIN_BYTES", "limit_bytes", "fits"]
