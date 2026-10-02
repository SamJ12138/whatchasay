"""The FastAPI app in-process with fake engines (formerly test_integration_live.py,
which needed a server on :8765 and real models). Same five checks: health,
/translate in all three en/zh/bn directions, /ws/asr partial -> final -> translation."""

import time

import pytest

from tests.fakes import pcm_frame


def test_health_reports_engines(app_env):
    with app_env.client() as c:
        d = c.get("/health/json").json()
    assert d["status"] == "ok"
    assert d["mt_engines"], "no MT engine loaded"
    assert d["asr"]["available"]


@pytest.mark.parametrize("text,src,targets", [
    ("Where did you put the keys?", "en", ["zh", "bn"]),
    ("我五分钟后回来，什么都别碰。", "zh", ["en", "bn"]),
    ("আমি পাঁচ মিনিটের মধ্যে ফিরে আসব।", "bn", ["en", "zh"]),
])
def test_http_translate_all_directions_under_budget(app_env, text, src, targets):
    with app_env.client() as c:
        for attempt in ("miss", "cache hit"):
            t = time.perf_counter()
            r = c.post("/translate", json={"text": text + " ", "source_lang": src, "target_languages": targets})
            elapsed = (time.perf_counter() - t) * 1000
            assert r.status_code == 200, r.text
            d = r.json()
            for tgt in targets:
                out = d["translations"][tgt]["single_line"]
                assert out and out != text, (attempt, tgt, out)
                if tgt == "zh":
                    assert any("一" <= ch <= "鿿" for ch in out)
                if tgt == "bn":
                    assert any("ঀ" <= ch <= "৿" for ch in out)
            assert elapsed < 1500, f"{attempt}: {src}->{targets} took {elapsed:.0f} ms"
    fake = app_env.engines["fake"]
    assert len(fake.calls) == len(targets), "second request must come from the cache"


def test_ws_asr_streams_partials_finals_and_translations(app_env):
    app_env.asr.final_every = 10
    got = {"partial": [], "final": [], "translation": [], "lid": []}
    order = []
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(10):
                ws.send_bytes(pcm_frame())
            while not got["translation"]:
                m = ws.receive_json()
                order.append(m["type"])
                if m["type"] in got:
                    got[m["type"]].append(m)
    assert got["partial"], "no partial captions"
    assert got["final"], "no final caption"
    tr = got["translation"][0]
    assert "zh" in tr["translations"]
    assert order.index("final") < order.index("translation")
    assert tr["utterance_id"] == got["final"][0]["utterance_id"]
