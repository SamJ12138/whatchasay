"""Install the repository's git hooks: point git at scripts/hooks (core.hooksPath).

    python scripts/install-hooks.py

After this, every `git commit` in this clone runs scripts/hooks/pre-commit: the fast backend
suite and the extension tests; a red result refuses the commit. Run once per clone.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

HOOKS = "scripts/hooks"


def main() -> int:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        print("install-hooks: not inside a git repository", file=sys.stderr)
        return 1
    root = Path(top.stdout.strip())
    hooks = root / HOOKS
    if not (hooks / "pre-commit").exists():
        print(f"install-hooks: {hooks / 'pre-commit'} is missing", file=sys.stderr)
        return 1
    for hook in hooks.iterdir():
        if hook.is_file() and os.name != "nt":
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["git", "config", "core.hooksPath", HOOKS], cwd=root, check=True)
    print(f"install-hooks: git will run {HOOKS}/pre-commit before every commit in {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
