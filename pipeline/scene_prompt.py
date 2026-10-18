"""CastCut Stage A — agent prompt generator (ClipForge-aligned).

Builds the concise, project-specific, release-linked prompt that the owner
copies to their external AI agent. The release itself contains the full
instructions (00_READ_THIS_FIRST.txt), scene index, screenshots, and transcript.

Output: jobs/<job>/scene/agent_prompt.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .utils import fmt_time


def build_release_linked_prompt(
    job_id: str,
    title: str = "",
    request: str = "",
    content_type: str = "anime",
    release_url: str = "",
) -> str:
    """Assemble the short release-linked prompt."""
    tag = f"castcut-{job_id}"
    rel_url = release_url or f"https://github.com/motionssalt/castcut/releases/tag/{tag}"
    req_clean = (request or "").strip()

    if req_clean:
        task_block = f'THE USER\'S SCENE REQUEST:\n"""\n{req_clean}\n"""'
    else:
        task_block = (
            "The user left the scene request EMPTY. Pick the single best, most coherent, "
            "and impactful scene or sequence from the video that carries the narrative."
        )

    project_name = title or job_id
    ct_label = "Anime" if content_type == "anime" else "Live-Action / IRL"

    return f"""Open this GitHub release: {rel_url}
Download and read 00_READ_THIS_FIRST.txt FIRST, then inspect the evidence assets (scene_index.json, transcript.json, and screenshots.zip as needed).

Project: {project_name}
Content type: {ct_label}

Task:
{task_block}

Rules:
- Read 00_READ_THIS_FIRST.txt in the release before selecting any cuts.
- Produce exactly one production.json containing the selected segments.
- Segments must be in chronological playback order with start and end timestamps within the source video.
- The pipeline cuts the source video at full quality and joins the segments with hard cuts.
- Return ONLY the valid JSON document conforming to the schema in 00_READ_THIS_FIRST.txt. No commentary, no prose, no markdown fences."""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--request", default=None, help="Scene request from user")
    ap.add_argument("--max-transcript-chars", type=int, default=9000, help="Compatibility flag")
    args = ap.parse_args()

    scene_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    os.makedirs(scene_dir, exist_ok=True)

    request = args.request
    if request is None:
        req_path = os.path.join(scene_dir, "scene-request.json")
        if os.path.isfile(req_path):
            try:
                with open(req_path, encoding="utf-8") as fh:
                    request = json.load(fh).get("request", "")
            except Exception:
                request = ""
        else:
            request = ""

    title = ""
    content_type = "anime"
    release_url = ""
    for p in (
        os.path.join(args.jobs_root, args.job_id, "status.json"),
        os.path.join(args.jobs_root, args.job_id, "ingest-request.json"),
    ):
        if os.path.isfile(p):
            try:
                doc = json.load(open(p, encoding="utf-8"))
                if not title:
                    title = doc.get("title") or doc.get("torrent_name") or ""
                if doc.get("content_type") in ("anime", "irl"):
                    content_type = doc.get("content_type")
                if not release_url:
                    release_url = doc.get("release_url") or ""
            except Exception:
                pass

    prompt = build_release_linked_prompt(
        job_id=args.job_id,
        title=title,
        request=request,
        content_type=content_type,
        release_url=release_url,
    )

    out_path = os.path.join(scene_dir, "agent_prompt.txt")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)

    print(f"[scene] agent prompt written: {out_path} ({len(prompt)} chars)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
