"""Clean-install proof (S1, S4): a fresh virtualenv with only

    pip install -r requirements.txt -r requirements-dev.txt

can `import app.main` and run the fast test suite. CI runs this on every
platform; locally:

    python backend/scripts/clean_install_check.py              # with this interpreter
    python backend/scripts/clean_install_check.py --python py  # another interpreter
    python backend/scripts/clean_install_check.py --keep       # keep the venv (prints its path)

Nothing from backend/venv, backend/data or the models is used: the data
paths point at the scratch dir and the fast suite needs no models. Prints one
JSON line; exit code 0 when every step passed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def run(cmd, cwd=None, env=None, timeout=1800):
    t = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return r, round(time.perf_counter() - t, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable, help="interpreter used to create the venv")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--pytest-args", default="", help="extra arguments for the fast suite run")
    args = ap.parse_args()

    scratch = Path(tempfile.mkdtemp(prefix="st-clean-install-"))
    venv = scratch / "venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("SUBTITLE_") or k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
            env.pop(k)
    env.update(SUBTITLE_DATA_DIR=str(scratch / "data"), SUBTITLE_CACHE__TM_DATABASE_PATH=str(scratch / "tm.db"),
               SUBTITLE_OBS_DIR=str(scratch / "logs"), PIP_DISABLE_PIP_VERSION_CHECK="1")
    summary = {"ok": False, "python": args.python, "steps": {}}
    try:
        r, s = run([args.python, "-m", "venv", str(venv)])
        summary["steps"]["venv"] = {"ok": r.returncode == 0, "s": s}
        if r.returncode:
            summary["error"] = r.stderr[-1500:]
            return 1
        r, s = run([str(py), "-c", "import sys; print(sys.version.split()[0])"])
        summary["python_version"] = r.stdout.strip()

        r, s = run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], env=env)
        r, s = run([str(py), "-m", "pip", "install", "-q", "-r", "requirements.txt", "-r", "requirements-dev.txt"],
                   cwd=str(BACKEND), env=env)
        summary["steps"]["pip_install"] = {"ok": r.returncode == 0, "s": s}
        if r.returncode:
            summary["error"] = (r.stderr or r.stdout)[-2500:]
            return 1

        r, s = run([str(py), "-c", "import app.main; print('import ok')"], cwd=str(BACKEND), env=env, timeout=300)
        summary["steps"]["import_app_main"] = {"ok": r.returncode == 0 and "import ok" in r.stdout, "s": s}
        if not summary["steps"]["import_app_main"]["ok"]:
            summary["error"] = (r.stderr or r.stdout)[-2500:]
            return 1

        cmd = [str(py), "-m", "pytest", "-q", "-p", "no:cacheprovider", *args.pytest_args.split()]
        r, s = run(cmd, cwd=str(BACKEND), env=env, timeout=1800)
        tail = (r.stdout.strip().splitlines() or [""])[-1]
        summary["steps"]["fast_suite"] = {"ok": r.returncode == 0, "s": s, "result": tail}
        if r.returncode:
            summary["error"] = r.stdout[-3000:]
            return 1
        r, _ = run([str(py), "-m", "pip", "freeze"], env=env)
        summary["freeze_count"] = len(r.stdout.splitlines())
        summary["ok"] = True
        return 0
    finally:
        if args.keep:
            summary["venv"] = str(venv)
        else:
            shutil.rmtree(scratch, ignore_errors=True)
        print(json.dumps(summary))


if __name__ == "__main__":
    sys.exit(main())
