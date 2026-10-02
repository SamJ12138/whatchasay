"""Fail if any file git would track is over the size limit or has a blocked type.

Usage (from the repo root):
    python scripts/check_large_files.py            # files tracked or staged
    python scripts/check_large_files.py --dry-run  # files `git add -n .` would add

Prints every file with its size. Exit 1 on any violation. Stdlib only.
"""

from __future__ import annotations

import os
import subprocess
import sys

LIMIT = 10 * 1024 * 1024
BLOCKED_EXT = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".zip", ".gguf",
               ".onnx", ".bin", ".safetensors", ".exe", ".dll", ".wav", ".jsonl", ".pyc")


def candidate_files(dry_run: bool) -> list[str]:
    if dry_run:
        out = subprocess.run(["git", "add", "-n", "."], capture_output=True, text=True, check=True).stdout
        # lines look like: add 'path/to/file'
        return [line[5:-1] for line in out.splitlines() if line.startswith("add '")]
    out = subprocess.run(["git", "ls-files", "--cached"], capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def main() -> int:
    files = candidate_files("--dry-run" in sys.argv)
    bad = []
    total = 0
    for path in files:
        size = os.path.getsize(path) if os.path.exists(path) else 0
        total += size
        flag = ""
        if size > LIMIT:
            flag = "  <-- over 10 MB"
        elif path.lower().endswith(BLOCKED_EXT):
            flag = "  <-- blocked type"
        if flag:
            bad.append(path)
        print(f"{size:>12,}  {path}{flag}")
    print(f"{len(files)} files, {total:,} bytes total")
    if bad:
        print(f"FAIL: {len(bad)} file(s) must not be committed")
        return 1
    print("OK: no file over 10 MB and no blocked type")
    return 0


if __name__ == "__main__":
    sys.exit(main())
