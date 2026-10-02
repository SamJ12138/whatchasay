"""D4: cloud providers are off by default; keys live in the backend config only,
travel in headers, and never appear in a URL, an error message, a log line or a
reply. httpx is mocked (MockTransport): no network."""

import json
import logging

import httpx
import pytest

KEY = "test-secret-key-7f3a9c"  # not shaped like a real key, so obs's pattern redaction cannot hide a leak


def _no_key(*texts):
    for t in texts:
        assert KEY not in str(t), f"key leaked into: {str(t)[:200]}"


def recording_transport(status=200, body=None, raise_exc=None):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if raise_exc is not None:
            raise raise_exc(f"cannot connect to {request.url}", request=request)
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler), seen


@pytest.fixture
def cloud_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings.cloud, "enabled", True)
    monkeypatch.setattr(settings.cloud, "mt_provider", "google")
    monkeypatch.setattr(settings.cloud, "google_api_key", KEY)
    monkeypatch.setattr(settings.cloud, "azure_translator_key", "")
    return settings


# ------------------------------------------------------------------ defaults / factory

def test_cloud_is_off_by_default():
    from app.config import CloudConfig, Settings

    assert CloudConfig().enabled is False
    assert "cloud" not in Settings().mt.engine_order


def test_no_cloud_engine_while_off_even_with_a_key(monkeypatch):
    from app.config import settings
    from app.translation import cloud_translator

    monkeypatch.setattr(settings.cloud, "enabled", False)
    monkeypatch.setattr(settings.cloud, "google_api_key", KEY)
    assert cloud_translator.make_cloud_engine() is None


def test_factory_picks_the_configured_provider(cloud_on, monkeypatch):
    from app.translation import cloud_translator as ct

    assert isinstance(ct.make_cloud_engine(), ct.GoogleTranslateEngine)
    monkeypatch.setattr(cloud_on.cloud, "mt_provider", "azure")
    monkeypatch.setattr(cloud_on.cloud, "azure_translator_key", KEY)
    monkeypatch.setattr(cloud_on.cloud, "azure_translator_region", "eastus")
    eng = ct.make_cloud_engine()
    assert isinstance(eng, ct.AzureTranslatorEngine) and eng.region == "eastus"
    monkeypatch.setattr(cloud_on.cloud, "azure_translator_key", "")
    monkeypatch.setattr(cloud_on.cloud, "google_api_key", "")
    assert ct.make_cloud_engine() is None, "enabled without a key is not an engine"


def test_enabling_cloud_appends_it_to_the_router_order(monkeypatch):
    from app.config import Settings

    monkeypatch.setenv("SUBTITLE_CLOUD__ENABLED", "true")
    assert Settings().mt.engine_order == ["opus", "cloud"]


# ------------------------------------------------------------------ requests

def test_google_sends_the_key_in_a_header_never_the_url(cloud_on):
    from app.translation.cloud_translator import GoogleTranslateEngine

    transport, seen = recording_transport(body={"data": {"translations": [{"translatedText": "你好"}]}})
    eng = GoogleTranslateEngine(KEY, transport=transport)
    assert eng.translate_batch_sync(["hello"], "en", "zh") == ["你好"]
    req = seen[0]
    _no_key(req.url)
    assert req.headers["x-goog-api-key"] == KEY
    body = json.loads(req.content)
    assert body["target"] == "zh-CN" and body["q"] == ["hello"]
    assert eng.supports("en", "zh") and eng.status()["available"]


def test_azure_sends_key_and_region_in_headers(cloud_on):
    from app.translation.cloud_translator import AzureTranslatorEngine

    transport, seen = recording_transport(body=[{"translations": [{"text": "你好"}]}])
    eng = AzureTranslatorEngine(KEY, "eastus", transport=transport)
    assert eng.translate_batch_sync(["hello"], "en", "zh") == ["你好"]
    req = seen[0]
    _no_key(req.url)
    assert req.headers["ocp-apim-subscription-key"] == KEY
    assert req.headers["ocp-apim-subscription-region"] == "eastus"
    assert req.url.params["to"] == "zh-Hans"
    assert eng.status()["engine"] == "azure-translator"


@pytest.mark.parametrize("status", [400, 403, 500])
def test_http_errors_never_carry_the_key(cloud_on, status, caplog, obs_records):
    from app.translation.cloud_translator import CloudMTError, GoogleTranslateEngine

    echo = {"error": {"message": f"API key not valid: {KEY}", "code": status}}  # a provider echoing the key
    transport, _ = recording_transport(status=status, body=echo)
    eng = GoogleTranslateEngine(KEY, transport=transport)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CloudMTError) as exc:
            eng.translate_batch_sync(["hello"], "en", "zh")
    assert str(status) in str(exc.value)
    _no_key(exc.value, repr(exc.value), *[r.getMessage() for r in caplog.records], *[json.dumps(r) for r in obs_records])


def test_network_errors_never_carry_the_key(cloud_on):
    from app.translation.cloud_translator import CloudMTError, GoogleTranslateEngine

    transport, _ = recording_transport(raise_exc=httpx.ConnectError)
    eng = GoogleTranslateEngine(KEY, transport=transport)
    with pytest.raises(CloudMTError) as exc:
        eng.translate_batch_sync(["hello"], "en", "zh")
    _no_key(exc.value, repr(exc.value))


def test_malformed_reply_is_an_error_without_the_key(cloud_on):
    from app.translation.cloud_translator import AzureTranslatorEngine, CloudMTError

    transport, _ = recording_transport(body={"unexpected": KEY})
    eng = AzureTranslatorEngine(KEY, "", transport=transport)
    with pytest.raises(CloudMTError) as exc:
        eng.translate_batch_sync(["hello"], "en", "zh")
    _no_key(exc.value)


@pytest.mark.asyncio
async def test_router_error_reply_and_logs_never_carry_the_key(cloud_on, obs_records, caplog):
    """End to end through the router: the error text that reaches the client."""
    from app.translation.cloud_translator import GoogleTranslateEngine
    from tests.fakes import make_translator

    transport, _ = recording_transport(status=403, body={"error": {"message": f"bad key {KEY}"}})
    tr = make_translator({"cloud": GoogleTranslateEngine(KEY, transport=transport)})
    cloud_on.mt.engine_order = ["cloud"]
    with caplog.at_level(logging.DEBUG):
        items = await tr.translate_batch_detailed(["hello"], "en", "zh")
    assert items[0].status == "error"
    _no_key(items[0].error, *[r.getMessage() for r in caplog.records], *[json.dumps(r) for r in obs_records])


@pytest.mark.asyncio
async def test_gemini_refiner_key_in_header_not_url(cloud_on, monkeypatch):
    from app.translation import refiner

    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})

    r = refiner.GeminiRefiner(KEY, "gemini-2.5-flash-lite", transport=httpx.MockTransport(handler))
    await r.refine([{"source_text": "hi", "source_lang": "en", "base_translations": {"zh": "你好"}}], ["zh"])
    _no_key(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == KEY


def test_cloud_refiners_need_cloud_enabled(monkeypatch):
    from app.config import settings
    from app.translation import refiner

    monkeypatch.setattr(refiner, "_refiner", None)
    monkeypatch.setattr(refiner, "_refiner_key", None)
    monkeypatch.setattr(settings.refiner, "provider", "groq")
    monkeypatch.setattr(settings.refiner, "groq_api_key", KEY)
    monkeypatch.setattr(settings.cloud, "enabled", False)
    assert refiner.get_refiner() is None
    monkeypatch.setattr(settings.cloud, "enabled", True)
    assert isinstance(refiner.get_refiner(), refiner.GroqRefiner)


# ------------------------------------------------------------------ no keys over the wire

def test_ws_config_cannot_set_keys(app_env, obs_records):
    from tests.conftest import ws_recv

    s = app_env.settings
    before = (s.cloud.google_api_key, s.refiner.groq_api_key)
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws")) as ws:
            ws.send_json({"type": "config", "correlation_id": "k", "payload": {
                "target_languages": ["zh"], "cloud_keys": {"google_api_key": KEY, "groq_api_key": KEY}}})
            reply = ws_recv(ws)
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="en", target_langs="zh")) as ws:
            ws.receive_json()
            ws.send_json({"type": "config", "cloud_keys": {"google_api_key": KEY}})
            ws_recv(ws)
        r = c.post("/config", json={"cloud_keys": {"google_api_key": KEY}})
    assert (s.cloud.google_api_key, s.refiner.groq_api_key) == before
    _no_key(json.dumps(reply), r.text, *[json.dumps(x) for x in obs_records])
    assert [x for x in obs_records if (x.get("context") or {}).get("ignored") == "cloud_keys"], "ignored keys not logged"


def test_health_and_startup_say_which_provider_receives_text(app_env, cloud_on, obs_records, caplog):
    with caplog.at_level(logging.INFO):
        with app_env.client() as c:
            d = c.get("/health/json").json()
    cloud = d["privacy"]["cloud"]
    assert cloud["enabled"] is True
    assert cloud["receivers"] == [{"use": "translation", "provider": "Google Cloud Translation",
                                   "host": "translation.googleapis.com"}]
    lines = [r for r in obs_records if r["stage"] == "startup" and (r.get("context") or {}).get("component") == "cloud"]
    assert len(lines) == 1 and lines[0]["context"]["receivers"] == cloud["receivers"]
    assert [r for r in caplog.records if "translation.googleapis.com" in r.getMessage()]
    _no_key(json.dumps(d), *[r.getMessage() for r in caplog.records])


def test_health_says_nothing_leaves_when_off(app_env):
    with app_env.client() as c:
        d = c.get("/health/json").json()
    assert d["privacy"]["cloud"] == {"enabled": False, "receivers": []}
