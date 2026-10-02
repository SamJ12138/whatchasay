"""Batch 5: llama-server lifecycle (P1-P3, P9) against tests/fake_llama_server.py."""

import subprocess
import sys
import time

import pytest

from app.config import settings
from tests.fakes import FakeMTEngine, make_translator


def proc_lines(records, **match):
    out = []
    for r in records:
        if r["stage"] != "translator_process":
            continue
        ctx = r.get("context") or {}
        if all((r.get(k) if k in ("event", "error_type") else ctx.get(k)) == v for k, v in match.items()):
            out.append(r)
    return out


def wait_for(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fast_backoff(monkeypatch):
    monkeypatch.setattr(settings.mt, "hymt_warmup_backoff_s", 0.05)
    monkeypatch.setattr(settings.mt, "hymt_retry_initial_s", 0.3)
    monkeypatch.setattr(settings.mt, "hymt_retry_max_s", 2.0)
    monkeypatch.setattr(settings.mt, "hymt_restart_timeout_s", 10.0)


# ---------------------------------------------------------------- P1


def test_child_output_is_captured_not_discarded(fake_llama):
    eng = fake_llama()
    eng.warmup()
    assert eng._proc.stdout is not None, "child output goes to DEVNULL"


def test_stderr_tail_is_attached_when_the_child_dies(fake_llama, obs_records):
    eng = fake_llama("--stderr-line", "ggml_cuda_init: CUDA error: out of memory", "--exit-after", "1", "--exit-code", "9")
    eng.warmup()  # one completion ("Hello"), then the stub exits with 9
    assert wait_for(lambda: proc_lines(obs_records, event="fail", action="exit"))
    line = proc_lines(obs_records, event="fail", action="exit")[0]
    assert line["context"]["exit_code"] == 9
    tail = line["context"]["stderr_tail"]
    assert isinstance(tail, list) and len(tail) <= 50
    assert any("CUDA error: out of memory" in t for t in tail)
    assert any("exiting with 9" in t for t in tail)


def test_stderr_tail_is_attached_on_early_exit(fake_llama, obs_records):
    eng = fake_llama("--exit-on-start", "--exit-code", "7", "--stderr-line", "error: failed to load model 'x.gguf'")
    with pytest.raises(RuntimeError, match="code 7"):
        with eng._lock:
            eng._start()
    line = proc_lines(obs_records, event="fail", action="early_exit")[0]
    assert any("failed to load model" in t for t in line["context"]["stderr_tail"])


# ---------------------------------------------------------------- P2


def _router(eng):
    backup = FakeMTEngine("backup")
    return make_translator({"hymt": eng, "backup": backup}), backup


@pytest.mark.asyncio
async def test_killed_child_is_restarted_and_serves_the_next_request(fake_llama, monkeypatch, obs_records, fast_backoff):
    monkeypatch.setattr(settings.mt, "engine_order", ["hymt", "backup"])
    eng = fake_llama()
    router, backup = _router(eng)
    first = await router.translate_batch_detailed(["Where are the keys?"], "en", "bn")
    assert first[0].status == "ok" and first[0].engine == "hymt"
    old = eng._proc
    old_pid = old.pid
    old.kill()  # crash between two requests ...
    old.wait(timeout=10)
    assert wait_for(lambda: proc_lines(obs_records, event="fail", action="exit"))  # watcher thread done
    # ... and the P2 window: the child is gone (port closed) but poll() still
    # says "running", as on Windows before the process is reaped
    old.poll = lambda: None
    old.returncode = None
    second = await router.translate_batch_detailed(["Where is the car?"], "en", "bn")
    assert second[0].status == "ok" and second[0].engine == "hymt", second[0]
    assert second[0].text == "<fake> Where is the car?"
    assert backup.calls == [], "the request went to the fallback engine"
    assert eng._proc.pid != old_pid
    restarts = proc_lines(obs_records, event="start", action="restart")
    assert restarts and "request failed" in restarts[-1]["context"]["reason"]


@pytest.mark.asyncio
async def test_child_that_exits_on_its_own_is_restarted_too(fake_llama, monkeypatch, fast_backoff):
    monkeypatch.setattr(settings.mt, "engine_order", ["hymt", "backup"])
    eng = fake_llama("--exit-after", "1")
    router, backup = _router(eng)
    eng.warmup()  # served "Hello", stub exits ~50 ms later
    res = await router.translate_batch_detailed(["Where is the car?"], "en", "bn")
    assert res[0].status == "ok" and res[0].engine == "hymt"
    assert backup.calls == []


@pytest.mark.asyncio
async def test_restart_that_fails_falls_back_within_the_bound(fake_llama, monkeypatch, fast_backoff):
    monkeypatch.setattr(settings.mt, "engine_order", ["hymt", "backup"])
    monkeypatch.setattr(settings.mt, "hymt_restart_timeout_s", 3.0)
    eng = fake_llama(plan=[[], ["--never-healthy"]])
    router, backup = _router(eng)
    assert (await router.translate_batch_detailed(["one"], "en", "bn"))[0].engine == "hymt"
    eng._proc.kill()
    t = time.perf_counter()
    res = await router.translate_batch_detailed(["two"], "en", "bn")
    elapsed = time.perf_counter() - t
    assert res[0].status == "fallback" and res[0].engine == "backup"
    assert elapsed < 3.0 + 3.0, f"restart wait was not bounded ({elapsed:.1f}s)"


# ---------------------------------------------------------------- P3


def test_warmup_failure_is_retried_with_backoff(fake_llama, obs_records, fast_backoff):
    eng = fake_llama(plan=[["--exit-on-start"], ["--exit-on-start"], []])
    eng.warmup()
    assert eng.spawned == 3
    st = eng.status()
    assert st["available"] and st["loaded"] and st["error"] is None
    fails = proc_lines(obs_records, event="fail", action="warmup")
    assert len(fails) == 2


def test_engine_comes_back_after_exhausted_warmup(fake_llama, fast_backoff):
    eng = fake_llama(plan=[["--exit-on-start"]] * 4 + [[]])
    eng.warmup()  # 3 attempts, all fail
    assert eng.spawned == 3
    assert not eng.supports("en", "bn"), "engine must pause after an exhausted warmup"
    assert eng.status()["error"] and eng.status()["retry_in_s"] > 0
    time.sleep(0.35)  # initial retry backoff (0.3 s) elapsed
    assert eng.supports("en", "bn"), "engine stayed disabled for the life of the process"
    with pytest.raises(RuntimeError):
        eng.translate_batch_sync(["x"], "en", "bn")  # 4th spawn fails -> backoff doubles
    assert not eng.supports("en", "bn")
    time.sleep(0.65)
    assert eng.supports("en", "bn")
    assert eng.translate_batch_sync(["x"], "en", "bn") == ["<fake> x"]
    assert eng.status()["error"] is None


# ---------------------------------------------------------------- P9


def test_spawn_to_popen_is_fast_and_does_not_import_torch(fake_llama, monkeypatch, obs_records):
    from app.translation import hymt_translator as h

    marks = {}
    real_popen = subprocess.Popen

    def popen(*a, **k):
        marks["popen"] = time.perf_counter()
        return real_popen(*a, **k)

    monkeypatch.setattr(h.subprocess, "Popen", popen)
    eng = fake_llama()
    t0 = time.perf_counter()
    with eng._lock:
        eng._start()
    pre_popen_ms = (marks["popen"] - t0) * 1000
    assert pre_popen_ms < 200, f"spawn-to-Popen took {pre_popen_ms:.0f} ms"
    ready = proc_lines(obs_records, event="success", action="ready")[-1]
    assert ready["context"]["pre_popen_ms"] < 200

    probe = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.');"
         "from app.translation.hymt_translator import _cuda_dll_dirs; _cuda_dll_dirs();"
         "print('torch' in sys.modules)"],
        capture_output=True, text=True, timeout=60)
    assert probe.stdout.strip().splitlines()[-1] == "False", probe.stdout + probe.stderr
