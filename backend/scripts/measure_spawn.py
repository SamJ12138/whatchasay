"""Measure the HY-MT llama-server first spawn in a fresh process (P9).

    venv/Scripts/python.exe scripts/measure_spawn.py [--runs N]

Prints JSON: for each run (a fresh interpreter) the time from HyMTEngine._start()
to subprocess.Popen (pre_popen_ms: everything the backend does before the child
exists) and from Popen to a healthy /health (popen_to_ready_ms). Needs the real
bin/llama/llama-server and the GGUF (GPU), like the slow tests.
"""

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

CHILD = r'''
import json, os, subprocess, sys, time
sys.path.insert(0, ".")
os.environ.setdefault("SUBTITLE_OBS_DIR", "logs/measure")
from app.translation import hymt_translator as h
marks = {}
real_popen = subprocess.Popen
def popen(*a, **k):
    marks["popen"] = time.perf_counter()
    return real_popen(*a, **k)
h.subprocess.Popen = popen
eng = h.HyMTEngine()
t0 = time.perf_counter()
with eng._lock:
    eng._start()
t1 = time.perf_counter()
eng.shutdown()
print(json.dumps({"pre_popen_ms": round((marks["popen"] - t0) * 1000, 1),
                  "popen_to_ready_ms": round((t1 - marks["popen"]) * 1000, 1),
                  "torch_imported": "torch" in sys.modules}))
'''


def main() -> None:
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 3
    out = []
    for _ in range(runs):
        r = subprocess.run([sys.executable, "-c", CHILD], cwd=str(BACKEND), capture_output=True, text=True, timeout=300)
        line = (r.stdout.strip().splitlines() or ["{}"])[-1]
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"error": (r.stderr or r.stdout)[-500:]})
    print(json.dumps({"runs": out}, indent=1))


if __name__ == "__main__":
    main()
