"""Failure report for one obs run log (stdlib only).

Usage (from backend/):
    python scripts/failure_report.py                  # newest logs/run_*.jsonl
    python scripts/failure_report.py logs/run_X.jsonl # a given run
    python scripts/failure_report.py A.jsonl B.jsonl  # several runs, aggregated
    python scripts/failure_report.py --json           # machine-readable

Sections:
  1. per-layer totals
  2. per-stage events and duration p50 / p95 (nearest rank, records with duration_ms)
  3. stage x error_type counts (fail + skip records that carry an error_type)
  4. per-session summary
  5. most frequent error messages
  6. per-line latency (stage line_latency, logged by the content script per subtitle
     line; docs/latency.md): first_display_ms = audio of the line's first word -> first
     text of the line on screen; final_ms = audio of its last word -> final translation
     on screen; first_translation_ms = first word -> first translated text; and the
     time from a session's first audio to its first confirmed-language subtitle
Per-frame stages (ws_receive, asr_chunk, ext_ws_send) log one summary line per
100 frames whose duration_ms is the window mean, so their p50/p95 are over
window means. Records with context.degraded mark silently-degraded paths.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ERROR_TYPES = ["input_invalid", "external_api", "parse", "timeout", "process", "unknown"]
EVENTS = ["start", "success", "fail", "skip"]
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1))
    return round(xs[k], 1)


def load(path: Path):
    recs, bad = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return recs, bad


def build(recs):
    layers = defaultdict(Counter)
    stages = defaultdict(lambda: {"layers": set(), "events": Counter(), "dur": [], "degraded": 0})
    matrix = defaultdict(Counter)
    sessions = defaultdict(lambda: {"layers": set(), "n": 0, "events": Counter(), "fail_stages": Counter(),
                                    "first": None, "last": None, "translate_ms": []})
    messages = Counter()
    for r in recs:
        layer, stage, event = r.get("layer") or "?", r.get("stage") or "?", r.get("event") or "?"
        ctx = r.get("context") or {}
        layers[layer]["records"] += 1
        layers[layer][event] += 1
        s = stages[stage]
        s["layers"].add(layer)
        s["events"][event] += 1
        if isinstance(r.get("duration_ms"), (int, float)) and event != "start":
            s["dur"].append(float(r["duration_ms"]))
        if ctx.get("degraded"):
            s["degraded"] += 1
            layers[layer]["degraded"] += 1
        et = r.get("error_type")
        if et and event in ("fail", "skip"):
            matrix[stage][et] += 1
            messages[(stage, et, (r.get("error_message") or "")[:110])] += 1
        sid = r.get("session_id") or "(none)"
        ss = sessions[sid]
        ss["layers"].add(layer)
        ss["n"] += 1
        ss["events"][event] += 1
        if event == "fail":
            ss["fail_stages"][stage] += 1
        ts = r.get("ts")
        if ts:
            ss["first"] = ts if ss["first"] is None or ts < ss["first"] else ss["first"]
            ss["last"] = ts if ss["last"] is None or ts > ss["last"] else ss["last"]
        if stage == "translate" and event == "success" and isinstance(r.get("duration_ms"), (int, float)):
            ss["translate_ms"].append(float(r["duration_ms"]))
    return layers, stages, matrix, sessions, messages


LINE_FIELDS = ("first_display_ms", "final_ms", "first_translation_ms")


def _dist(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95)}


def line_latency(recs):
    """Per-line latency over the run and per session, from the `line_latency` records."""
    def empty():
        return {"lines": 0, **{f: [] for f in LINE_FIELDS}, "first_confirmed_ms": []}

    total, sessions = empty(), defaultdict(empty)
    for r in recs:
        if r.get("stage") != "line_latency" or r.get("event") != "success":
            continue
        ctx = r.get("context") or {}
        for bucket in (total, sessions[r.get("session_id") or "(none)"]):
            if ctx.get("kind") == "first_confirmed":
                if isinstance(ctx.get("since_session_ms"), (int, float)):
                    bucket["first_confirmed_ms"].append(ctx["since_session_ms"])
            elif ctx.get("kind") == "line":
                bucket["lines"] += 1
                for f in LINE_FIELDS:
                    if isinstance(ctx.get(f), (int, float)):
                        bucket[f].append(ctx[f])

    def shape(b, per_session=False):
        fc = b["first_confirmed_ms"]
        return {"lines": b["lines"], **{f: _dist(b[f]) for f in LINE_FIELDS},
                "first_confirmed_ms": (fc[0] if fc else None) if per_session else fc}

    return {**shape(total), "sessions": {sid: shape(b, True) for sid, b in sessions.items()}}


def table(headers, rows):
    cols = [list(map(str, c)) for c in zip(headers, *rows)] if rows else [[h] for h in headers]
    widths = [max(len(x) for x in c) for c in cols]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    out = [line, "  ".join("-" * w for w in widths)]
    for row in rows:
        out.append("  ".join(str(v).ljust(w) for v, w in zip(row, widths)))
    return "\n".join(out)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    if args:
        paths = [Path(a) for a in args]
    else:
        runs = sorted(LOG_DIR.glob("run_*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not runs:
            print(f"no run logs in {LOG_DIR}")
            return 1
        paths = [runs[-1]]
    recs, bad = [], 0
    for p in paths:
        r, b = load(p)
        recs += r
        bad += b
    layers, stages, matrix, sessions, messages = build(recs)
    per_run = Counter(r.get("run_id") for r in recs)
    run_ids = sorted(k for k in per_run if k)

    if as_json:
        print(json.dumps({
            "files": [str(p) for p in paths], "records": len(recs), "unparseable_lines": bad, "run_ids": run_ids,
            "layers": {k: dict(v) for k, v in layers.items()},
            "stages": {k: {"layers": sorted(v["layers"]), "events": dict(v["events"]), "degraded": v["degraded"],
                           "n_duration": len(v["dur"]), "p50_ms": pct(v["dur"], 50), "p95_ms": pct(v["dur"], 95)}
                       for k, v in stages.items()},
            "stage_x_error_type": {k: dict(v) for k, v in matrix.items()},
            "line_latency": line_latency(recs),
            "sessions": {k: {"layers": sorted(v["layers"]), "records": v["n"], "events": dict(v["events"]),
                             "fail_stages": dict(v["fail_stages"]), "first": v["first"], "last": v["last"],
                             "translate_n": len(v["translate_ms"]), "translate_p50_ms": pct(v["translate_ms"], 50)}
                         for k, v in sessions.items()},
        }, indent=1, ensure_ascii=False))
        return 0

    print("Failure report: " + ", ".join(str(p) for p in paths))
    print(f"records {len(recs)}, unparseable lines {bad}, run_id(s): " +
          (", ".join(f"{k} ({per_run[k]} records)" for k in run_ids) or "-") + "\n")

    print("1. Per-layer totals")
    rows = [[l, c["records"], c["start"], c["success"], c["fail"], c["skip"], c["degraded"]] for l, c in sorted(layers.items())]
    print(table(["layer", "records", "start", "success", "fail", "skip", "degraded"], rows), "\n")

    print("2. Per-stage events and durations (ms)")
    rows = []
    for name, s in sorted(stages.items(), key=lambda kv: (sorted(kv[1]["layers"])[0], kv[0])):
        e = s["events"]
        rows.append([name, ",".join(sorted(s["layers"])), sum(e.values()), e["start"], e["success"], e["fail"], e["skip"],
                     s["degraded"], len(s["dur"]), pct(s["dur"], 50) if s["dur"] else "-", pct(s["dur"], 95) if s["dur"] else "-"])
    print(table(["stage", "layer", "total", "start", "success", "fail", "skip", "degraded", "n_dur", "p50", "p95"], rows), "\n")

    print("3. Stage x error_type (fail + skip records)")
    rows = [[st] + [m[et] or "." for et in ERROR_TYPES] + [sum(m.values())] for st, m in sorted(matrix.items())]
    if rows:
        print(table(["stage"] + ERROR_TYPES + ["total"], rows), "\n")
    else:
        print("(none)\n")

    print("4. Per-session summary")
    rows = []
    for sid, s in sorted(sessions.items(), key=lambda kv: kv[1]["first"] or ""):
        top = ", ".join(f"{k}:{v}" for k, v in s["fail_stages"].most_common(3)) or "-"
        rows.append([sid, ",".join(sorted(s["layers"])), s["n"], s["events"]["fail"], s["events"]["skip"],
                     len(s["translate_ms"]), pct(s["translate_ms"], 50) if s["translate_ms"] else "-",
                     (s["first"] or "")[11:23], (s["last"] or "")[11:23], top])
    print(table(["session_id", "layers", "records", "fail", "skip", "translates", "tr_p50", "first", "last", "top fail stages"], rows), "\n")

    print("5. Most frequent error messages (fail + skip)")
    rows = [[n, st, et, msg] for (st, et, msg), n in messages.most_common(15)]
    print(table(["n", "stage", "error_type", "error_message"], rows) if rows else "(none)")

    ll = line_latency(recs)
    print()
    print("6. Per-line latency (ms; first word -> first text, last word -> final translation, first word -> first translated text)")
    if not ll["lines"] and not ll["first_confirmed_ms"]:
        print("(no line_latency records)")
        return 0

    def cells(b):
        return [b["lines"]] + [v if v is not None else "-" for f in LINE_FIELDS for v in (b[f]["p50"], b[f]["p95"])]

    rows = [["(all)"] + cells(ll) + [", ".join(str(round(x)) for x in ll["first_confirmed_ms"]) or "-"]]
    for sid, b in sorted(ll["sessions"].items()):
        rows.append([sid] + cells(b) + [round(b["first_confirmed_ms"]) if b["first_confirmed_ms"] is not None else "-"])
    print(table(["session_id", "lines", "first_display p50", "p95", "final p50", "p95", "first_translation p50", "p95",
                 "first confirmed subtitle"], rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
