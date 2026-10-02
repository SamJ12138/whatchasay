"""The fake llama-server fixture itself: HyMTEngine drives it like the real
binary (spawn, /health poll, /v1/chat/completions), and it can be told to die."""

import pytest


def test_engine_spawns_stub_and_translates(fake_llama):
    eng = fake_llama()
    eng.warmup()
    assert eng.status()["available"] and eng.status()["loaded"]
    assert eng.translate_batch_sync(["Where are the keys?"], "en", "bn") == ["<fake> Where are the keys?"]


def test_stub_exit_on_start_reports_its_exit_code(fake_llama):
    eng = fake_llama("--exit-on-start", "--exit-code", "7")
    with pytest.raises(RuntimeError, match="exited early with code 7"):
        with eng._lock:
            eng._start()


def test_stub_can_be_killed_between_requests(fake_llama):
    eng = fake_llama("--exit-after", "1", "--exit-code", "9")
    eng.warmup()  # one completion ("Hello") -> the stub exits with 9
    proc = eng._proc
    proc.wait(timeout=5)
    assert proc.returncode == 9
