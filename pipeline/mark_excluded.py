"""CastCut release-publish safety net (fix_round_3).

When ``gh release upload`` fails at publish time, the ingest workflow calls this
module instead of an inline ``python - <<'PY'`` heredoc (which cannot be safely
indented in a workflow ``run: |`` block — bash only recognises an unindented
delimiter with the plain ``<<`` form).

Behaviour is identical to the removed inline heredoc:

* sets ``video_in_release`` to ``false``
* sets ``video_excluded_reason`` to the standard upload-failure message
* leaves every other key (including an existing ``refetch`` block written by
  ``ingest.py``) untouched

It is safe to run when the source was already excluded by ``ingest.py`` (it is
idempotent for ``video_in_release`` and simply rewrites the reason to the
upload-failure text, matching the original heredoc).

CLI::

    python -m pipeline.mark_excluded <job_id> [--jobs-root jobs] [--reason "..."]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

UPLOAD_FAILURE_REASON = (
    "source upload failed at release-publish time (size-limit "
    "safety net); it will be re-downloaded from the original "
    "source when processing starts"
)


def mark_excluded(job_id: str, jobs_root: str = "jobs",
                  reason: str | None = None) -> dict:
    """Rewrite ``jobs/<job_id>/ingest.json`` to record an upload-failure
    exclusion, preserving all other fields (including ``refetch``)."""
    path = os.path.join(jobs_root, job_id, "ingest.json")
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)

    rec["video_in_release"] = False
    rec["video_excluded_reason"] = reason or UPLOAD_FAILURE_REASON

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)

    print(f"[mark_excluded] {job_id}: video_in_release=false "
          f"({reason or UPLOAD_FAILURE_REASON})", flush=True)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser(
        description="CastCut release-publish upload-failure safety net")
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--reason", default=None,
                    help="override the default upload-failure reason text")
    args = ap.parse_args()
    try:
        mark_excluded(args.job_id, jobs_root=args.jobs_root,
                      reason=args.reason)
    except Exception as exc:
        print(f"mark_excluded error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
