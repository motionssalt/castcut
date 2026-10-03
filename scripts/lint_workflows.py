#!/usr/bin/env python3
"""CastCut workflow linter (fix_round_3).

Enforces workflow hygiene rules:
1. Rejects any inline heredocs (``<<``, ``<<'PY'``, ``<<EOF``, etc.) in any workflow file.
   Per CastCut rules, all non-trivial logic must live in ``pipeline/`` modules and be called
   as single-line commands.
2. Extracts all ``run:`` script blocks and validates them with ``bash -n`` to detect
   syntax errors, unclosed quotes, malformed control structures, or invalid script syntax.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys


def find_workflow_files(workflows_dir: str = ".github/workflows") -> list[str]:
    files = sorted(glob.glob(os.path.join(workflows_dir, "*.yml")) +
                   glob.glob(os.path.join(workflows_dir, "*.yaml")))
    if not files:
        print(f"Warning: No workflow files found in {workflows_dir}", file=sys.stderr)
    return files


def check_no_heredocs(filepath: str) -> list[str]:
    """Scan file for forbidden '<<' heredoc syntax."""
    errors = []
    with open(filepath, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            # Check for << that is not part of a comment or already quoted literal
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "<<" in line:
                errors.append(
                    f"{filepath}:{lineno}: forbidden heredoc '<<' detected: {stripped}\n"
                    f"  Per CastCut workflow hygiene rules, inline heredocs are prohibited.\n"
                    f"  Move logic to a pipeline/ module and invoke as a single-line command."
                )
    return errors


def extract_run_blocks(filepath: str) -> list[tuple[int, str]]:
    """Extract (start_line, script_text) for each 'run:' block in a YAML workflow."""
    blocks = []
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        match = re.match(r"^(\s*)run:\s*([|>][-+]?)?\s*$", line)
        if match:
            start_lineno = i + 1
            base_indent = len(match.group(1))
            run_lines = []
            i += 1
            while i < len(lines):
                subline = lines[i]
                sub_stripped = subline.strip()
                if not sub_stripped:
                    # Empty lines in a multiline block
                    run_lines.append("")
                    i += 1
                    continue
                sub_indent = len(subline) - len(subline.lstrip(" "))
                if sub_indent <= base_indent:
                    break
                run_lines.append(subline)
                i += 1
            script = "".join(run_lines)
            blocks.append((start_lineno, script))
            continue

        # Single-line run:
        single_match = re.match(r"^(\s*)run:\s*(.+)$", line)
        if single_match and not line.strip().endswith(("|", ">", "|-", ">-", "|+", ">+")):
            start_lineno = i + 1
            cmd = single_match.group(2).strip()
            blocks.append((start_lineno, cmd))
        i += 1

    return blocks


def sanitize_github_expressions(script: str) -> str:
    """Replace ${{ ... }} with valid shell placeholders for bash -n checking."""
    # Handle ${{ ... }} by replacing with safe variable references
    return re.sub(r"\$\{\{[^}]+\}\}", "__GHA_EXPR__", script)


def check_bash_syntax(filepath: str, start_line: int, script: str) -> list[str]:
    """Run bash -n on the script block to check for syntax errors."""
    sanitized = sanitize_github_expressions(script)
    res = subprocess.run(
        ["bash", "-n"],
        input=sanitized,
        text=True,
        capture_output=True,
    )
    if res.returncode != 0:
        err = res.stderr.strip() or f"bash -n exited with code {res.returncode}"
        return [
            f"{filepath}:{start_line}: bash syntax error in run block:\n{err}\n--- Script ---\n{script.strip()}\n--------------"
        ]
    return []


def lint_workflows(workflows_dir: str = ".github/workflows") -> int:
    files = find_workflow_files(workflows_dir)
    all_errors = []
    checked_runs = 0

    for filepath in files:
        # 1. Check for heredocs
        heredoc_errors = check_no_heredocs(filepath)
        all_errors.extend(heredoc_errors)

        # 2. Check bash syntax on run: blocks
        run_blocks = extract_run_blocks(filepath)
        checked_runs += len(run_blocks)
        for start_lineno, script in run_blocks:
            syntax_errors = check_bash_syntax(filepath, start_lineno, script)
            all_errors.extend(syntax_errors)

    if all_errors:
        print(f"FAILED: Found {len(all_errors)} workflow errors:\n", file=sys.stderr)
        for err in all_errors:
            print(f"- {err}\n", file=sys.stderr)
        return 1

    print(f"PASSED: Linted {len(files)} workflow files ({checked_runs} run blocks). No heredocs, all bash -n clean.")
    return 0


if __name__ == "__main__":
    target_dir = sys.argv[1] if len(sys.argv) > 1 else ".github/workflows"
    sys.exit(lint_workflows(target_dir))
