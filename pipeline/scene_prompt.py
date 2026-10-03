"""CastCut Mode 2 — agent prompt generator.

Builds the prompt the user copies into his own AI. Embeds:
  * the scene/sequence request (captured at dispatch time, NOT at copy time)
  * video metadata, scene-boundary index, transcript excerpt
  * the strict production JSON schema verbatim, with output instructions

Output: jobs/<job>/scene/agent_prompt.txt
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .utils import fmt_time

SCHEMA_TEXT = """{
  "version": 1,
  "request": "<echo the scene request, or omit>",
  "title": "<short label for this cut>",
  "segments": [
    {"start": 12.5, "end": 27.0, "note": "why this segment"},
    {"start": "1:04", "end": "1:19.5", "note": "MM:SS also accepted"}
  ],
  "metadata": {"mood": "...", "characters": ["..."]}
}"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_id")
    ap.add_argument("--jobs-root", default="jobs")
    ap.add_argument("--max-transcript-chars", type=int, default=9000)
    args = ap.parse_args()

    scene_dir = os.path.join(args.jobs_root, args.job_id, "scene")
    with open(os.path.join(scene_dir, "scene-request.json"), encoding="utf-8") as fh:
        scene_req = json.load(fh)
    request = (scene_req.get("request") or "").strip()

    with open(os.path.join(scene_dir, "video_meta.json"), encoding="utf-8") as fh:
        meta = json.load(fh)
    with open(os.path.join(scene_dir, "scene_index.json"), encoding="utf-8") as fh:
        scene_index = json.load(fh)
    with open(os.path.join(scene_dir, "transcript.json"), encoding="utf-8") as fh:
        transcript = json.load(fh)

    duration = meta.get("duration", 0)
    boundaries = scene_index.get("boundaries", [])
    boundary_lines = []
    window = []
    prev = 0.0
    for i, b in enumerate(boundaries):
        window.append((prev, b))
        prev = b
    window.append((prev, duration))
    for i, (s, e) in enumerate(window, start=1):
        if e - s >= 1.5:
            boundary_lines.append(f"  scene {i:03d}: {fmt_time(s)} - {fmt_time(e)}  ({e - s:.1f}s)")
        if len(boundary_lines) >= 250:
            boundary_lines.append("  ... (truncated)")
            break

    seg_lines = []
    used = 0
    for seg in transcript.get("segments", []):
        line = f"  [{fmt_time(seg['start'])}] {seg['text']}"
        if used + len(line) > args.max_transcript_chars:
            seg_lines.append("  ... (transcript truncated)")
            break
        seg_lines.append(line)
        used += len(line)

    if request:
        task_block = (
            f"THE USER'S SCENE REQUEST (this is what they want — follow it):\n"
            f"\"\"\"\n{request}\n\"\"\""
        )
    else:
        task_block = (
            "The user left the scene request EMPTY. Pick the single most necessary /\n"
            "best scene or sequence — the one that carries the video — and cut that."
        )

    prompt = f"""# CastCut Scene-Cut Agent Prompt

You are the cutting-room assistant for an anime AMV editor. You are given the
analysis of ONE video. Choose the exact time segments to cut, and return ONLY
a JSON document in the strict schema at the bottom. No prose, no markdown
fences — JSON only.

## Video
- duration: {fmt_time(duration)} ({duration:.1f}s)
- resolution: {meta.get('width')}x{meta.get('height')} @ {meta.get('fps')}fps
- audio: {'yes' if meta.get('has_audio') else 'no'}
- transcript language: {transcript.get('language') or 'unavailable'}

## Task
{task_block}

Rules for your selection:
- Segments must be in PLAYBACK ORDER (chronological).
- Cut on the scene boundaries listed below whenever possible.
- Each segment: "start" and "end" as seconds (number) or "MM:SS"/"HH:MM:SS".
  end must be greater than start. Both within 0 and {duration:.1f}.
- 1 to 40 segments. Total runtime is your call — make it feel intentional.
- Add a short "note" per segment so the editor knows why it is there.
- The final video is the segments joined with HARD CUTS in the order given.

## Scene boundaries (detected cuts)
{os.linesep.join(boundary_lines) if boundary_lines else '  (no scene boundaries detected)'}

## Transcript (timestamped)
{os.linesep.join(seg_lines) if seg_lines else '  (no speech detected / transcription unavailable)'}

## OUTPUT FORMAT — return EXACTLY this JSON shape and nothing else
```json
{SCHEMA_TEXT}
```
"""
    out_path = os.path.join(scene_dir, "agent_prompt.txt")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(prompt)
    print(f"[scene] agent prompt written: {out_path} ({len(prompt)} chars)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
