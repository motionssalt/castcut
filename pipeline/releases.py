"""CastCut GitHub Release Capacity & Overflow Manager.

Manages releases for projects and their multiple exports.
If the primary release `castcut-<project_id>` reaches safe capacity thresholds
(max 25 assets or 1.8 GiB total assets), it automatically provisions and routes
new export assets to sequential overflow releases:
  `castcut-<project_id>-out-2`
  `castcut-<project_id>-out-3`
  ...

CLI:
    python -m pipeline.releases --project-id <id> --asset <filepath>
Prints the chosen release tag to stdout for workflow upload steps.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# Conservative limits to stay well below GitHub API limits
MAX_ASSETS_PER_RELEASE = 25
MAX_RELEASE_TOTAL_BYTES = int(1.8 * 1024 ** 3)  # 1.8 GiB safe total limit


def _gh_json(cmd: list[str]) -> dict | list | None:
    res = subprocess.run(["gh"] + cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except Exception:
        return None


def get_release_info(tag: str, repo: str = "") -> dict | None:
    cmd = ["release", "view", tag, "--json", "tagName,assets,id,name"]
    if repo:
        cmd += ["--repo", repo]
    return _gh_json(cmd)


def ensure_release(tag: str, project_id: str, repo: str = "") -> str:
    """Ensure the specified release exists, creating it if necessary."""
    cmd_check = ["release", "view", tag, "--json", "tagName"]
    if repo:
        cmd_check += ["--repo", repo]
    if _gh_json(cmd_check):
        return tag

    cmd_create = [
        "gh", "release", "create", tag,
        "--title", f"castcut {tag}",
        "--notes", f"Project exports for {project_id}.",
    ]
    if repo:
        cmd_create += ["--repo", repo]
    print(f"+ {' '.join(cmd_create)}", file=sys.stderr, flush=True)
    res = subprocess.run(cmd_create, capture_output=True, text=True)
    if res.returncode != 0:
        # Check if another process created it concurrently
        if not _gh_json(cmd_check):
            raise RuntimeError(f"Failed to create release {tag}: {res.stderr}")
    return tag


def choose_release_for_asset(project_id: str, asset_size_bytes: int, repo: str = "") -> str:
    """Find or allocate the first release with available capacity."""
    candidate_index = 1
    while True:
        if candidate_index == 1:
            tag = f"castcut-{project_id}"
        else:
            tag = f"castcut-{project_id}-out-{candidate_index}"

        info = get_release_info(tag, repo)
        if not info:
            # Release doesn't exist yet: it has full capacity!
            ensure_release(tag, project_id, repo)
            return tag

        assets = info.get("assets", [])
        total_bytes = sum(int(a.get("size", 0)) for a in assets)

        if (len(assets) < MAX_ASSETS_PER_RELEASE) and (total_bytes + asset_size_bytes <= MAX_RELEASE_TOTAL_BYTES):
            return tag

        print(
            f"[releases] Release {tag} is near capacity ({len(assets)} assets, {total_bytes} bytes). "
            f"Checking next overflow release...",
            file=sys.stderr,
            flush=True,
        )
        candidate_index += 1


def main() -> None:
    ap = argparse.ArgumentParser(description="CastCut release capacity & overflow manager")
    ap.add_argument("--project-id", required=True, help="Project / Job ID")
    ap.add_argument("--asset", required=True, help="Path to asset file to upload")
    ap.add_argument("--repo", default="", help="GitHub repository (owner/name)")
    args = ap.parse_args()

    if not os.path.isfile(args.asset):
        raise FileNotFoundError(f"Asset file not found: {args.asset}")

    size_bytes = os.path.getsize(args.asset)
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    chosen_tag = choose_release_for_asset(args.project_id, size_bytes, repo=repo)
    # Output solely the tag name to stdout for workflow command substitution
    print(chosen_tag)


if __name__ == "__main__":
    main()
