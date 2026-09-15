#!/usr/bin/env python3
"""Enforce .large-file-guard.json for tracked files, from any working directory.

Counts LF-delimited lines, including a final unterminated line. Exclude patterns
are case-sensitive, repository-relative fnmatch patterns. No file types are
implicitly exempt. Git/config/read errors fail the check rather than skip files.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys


def count_lines(path: Path) -> int:
    count = 0
    last_byte = b""
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            count += chunk.count(b"\n")
            last_byte = chunk[-1:]
    return count + int(bool(last_byte) and last_byte != b"\n")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    config = json.loads((root / ".large-file-guard.json").read_text(encoding="utf-8"))
    if not config["enabled"]:
        print("Large-file guard disabled by configuration.")
        return 0

    maximum = config["maxLines"]
    excluded = config["exclude"]
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
    failed = False
    for raw_name in tracked.split(b"\0"):
        if not raw_name:
            continue
        name = os.fsdecode(raw_name)
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in excluded):
            continue
        try:
            lines = count_lines(root / name)
        except OSError as error:
            print(f"{name!r}: cannot count lines: {error}", file=sys.stderr)
            failed = True
            continue
        if lines > maximum:
            print(f"{name!r}: {lines} lines exceeds maxLines={maximum}", file=sys.stderr)
            failed = True
    if not failed:
        print(f"Large-file guard passed (maxLines={maximum}).")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
