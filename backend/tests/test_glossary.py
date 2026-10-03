"""The term glossary (observations T13, T14): a user teaches whatchasay a name, a dish, a
piece of jargon once; from then on it is recognised and translated the way they said.

A term has a canonical spelling in the source language, zero or more "heard as" spellings
and a target rendering per target language. On recognised text, before display and MT, a
heard-as spelling (exact, then fuzzy within a small edit distance for the script) becomes
the canonical one; through MT the term travels as a placeholder the engine copies and the
rendering is put back afterwards. OPUS-MT keeps no placeholder in every direction
(docs/observations.md T13 has the measurement), so the result is checked and the sentence
re-translated with the next placeholder of the direction's list before giving up."""

import pytest

from app.translation import glossary as G
from app.translation.glossary import Glossary, Term
from tests.fakes import pcm_frame

RAJBHOG = Term(source_lang="bn", canonical="রাজভোগ", heard_as=["রাজবুক"], renderings={"en": "rajbhog"})
MOSHAI = Term(source_lang="bn", canonical="মশায়", heard_as=[], renderings={"en": "sir"})


# ---------------------------------------------------------------- correct (heard as -> canonical)


def test_an_exact_heard_as_spelling_becomes_the_canonical_one():
    g = Glossary([RAJBHOG])
    text, hits = g.correct("একটা রাজবুক দেখা তো")
    assert text == "একটা রাজভোগ দেখা তো"
    assert hits == [("রাজবুক", "রাজভোগ", "exact")]


def test_the_canonical_spelling_itself_is_left_alone_and_unrelated_text_is_unchanged():
    g = Glossary([RAJBHOG])
    assert g.correct("একটা রাজভোগ দেখা তো") == ("একটা রাজভোগ দেখা তো", [])
    assert g.correct("এর চেয়ে বড় করতে গেলে অনেক দাম পড়ে যায়") == ("এর চেয়ে বড় করতে গেলে অনেক দাম পড়ে যায়", [])
    assert Glossary([]).correct("anything") == ("anything", [])


def test_a_near_spelling_is_matched_within_the_scripts_edit_distance():
    """The recognizer wrote রাজবুক, রাজব, কাজ বুক for the same word in different passes
    (docs/live-test-youtube.md). রাজব is two edits from the stored heard-as spelling."""
    g = Glossary([RAJBHOG])
    text, hits = g.correct("কাল যে আমার দশ হাজার রাজব লাগবে")
    assert text == "কাল যে আমার দশ হাজার রাজভোগ লাগবে"
    assert hits == [("রাজব", "রাজভোগ", "fuzzy")]
    # the recognizer also splits the word in two: "কাজ বুক" / "আজ বুক" (one edit from রাজবুক
    # run together; the live runs of 2026-10-02 wrote the second in 3 of 3)
    assert g.correct("কাজ বুক দেখা তো") == ("রাজভোগ দেখা তো", [("কাজ বুক", "রাজভোগ", "fuzzy")])
    assert g.correct("আজ বুক দেখেন তো")[0] == "রাজভোগ দেখেন তো"
    # short common words on their own are not touched (under 4 code points, or too far)
    assert g.correct("আজ বড় দোকান")[0] == "আজ বড় দোকান"
    assert g.correct("বুক ভরা আশা")[0] == "বুক ভরা আশা"
    assert g.correct("রাজা এসেছে")[0] == "রাজা এসেছে"            # রাজা: 3 edits from রাজবুক


def test_a_multiword_heard_as_spelling_is_matched_as_a_phrase():
    g = Glossary([Term("bn", "রাজভোগ", ["কাজ বুক"], {"en": "rajbhog"})])
    assert g.correct("কাজ বুক দেখা তো")[0] == "রাজভোগ দেখা তো"


def test_latin_matching_ignores_case_and_keeps_punctuation():
    g = Glossary([Term("en", "Kubernetes", ["cooper netties", "kubernetis"], {"zh": "Kubernetes"})])
    assert g.correct("We deploy on Cooper Netties, right?")[0] == "We deploy on Kubernetes, right?"
    assert g.correct("kubernetis.")[0] == "Kubernetes."
    assert g.correct("Kubernetez is down")[0] == "Kubernetes is down"   # one edit


def test_cjk_terms_are_matched_as_substrings():
    g = Glossary([Term("zh", "拉吉博格", ["拉机博格"], {"en": "rajbhog"})])
    assert g.correct("给我看一个拉机博格。")[0] == "给我看一个拉吉博格。"
    assert g.correct("给我看一个拉基博格。")[0] == "给我看一个拉吉博格。"   # one character off
    assert g.correct("给我看一个蛋糕。")[0] == "给我看一个蛋糕。"


# ---------------------------------------------------------------- protect / restore


def test_the_term_travels_as_a_placeholder_and_comes_back_as_the_rendering():
    g = Glossary([RAJBHOG])
    protected, slots = g.protect("একটা রাজভোগ দেখা তো", "en", G.placeholders("bn", "en")[0])
    assert protected == "একটা X1X দেখা তো"
    assert slots == [("X1X", "rajbhog")]
    assert g.restore("Show me a X1X.", slots) == "Show me a rajbhog."


def test_a_term_without_a_rendering_for_the_target_keeps_its_canonical_spelling():
    g = Glossary([Term("bn", "প্রসেনজিৎ", [], {})])
    protected, slots = g.protect("প্রসেনজিৎ এসেছে", "en", "X{n}X")
    assert slots == [("X1X", "প্রসেনজিৎ")]
    assert g.restore("X1X has come.", slots) == "প্রসেনজিৎ has come."


def test_two_terms_get_two_placeholders():
    g = Glossary([RAJBHOG, MOSHAI])
    protected, slots = g.protect("মশায় একটা রাজভোগ দেখা তো", "en", "X{n}X")
    assert protected == "X1X একটা X2X দেখা তো"
    assert g.restore("X1X, show me a X2X.", slots) == "sir, show me a rajbhog."


def test_restore_refuses_a_lost_or_duplicated_placeholder():
    g = Glossary([RAJBHOG])
    _, slots = g.protect("একটা রাজভোগ দেখা তো", "en", "X{n}X")
    assert g.restore("I need ten thousand tomorrow", slots) is None
    assert g.restore("X1X and X1X", slots) is None
    assert g.restore("I want to eat x1x", slots) == "I want to eat rajbhog"   # re-cased: still one copy


def test_protect_without_a_term_in_the_text_is_a_no_op():
    g = Glossary([RAJBHOG])
    assert g.protect("এর চেয়ে বড় করতে গেলে", "en", "X{n}X") == ("এর চেয়ে বড় করতে গেলে", [])


def test_placeholder_lists_are_per_direction_with_a_default():
    assert G.placeholders("bn", "en")[0] == "X{n}X"
    assert G.placeholders("en", "zh")[0] == "[{n}]"
    assert len(G.placeholders("vi", "ja")) >= 3
    for src, tgt in (("bn", "en"), ("en", "zh"), ("zh", "en"), ("en", "bn"), ("bn", "zh"), ("zh", "bn"), ("vi", "ja")):
        for tpl in G.placeholders(src, tgt):
            assert "{n}" in tpl or tpl == "%s", (src, tgt, tpl)


# ---------------------------------------------------------------- translate with retry


class LosingTranslator:
    """A batch translator that copies the text but drops the placeholders it is told to."""

    def __init__(self, lose=()):
        self.lose = set(lose)
        self.calls = []

    def __call__(self, texts, src, tgt):
        self.calls.append(list(texts))
        out = []
        for t in texts:
            for ph in self.lose:
                t = t.replace(ph, "")
            out.append(f"EN[{t}]")
        return out


def test_a_lost_placeholder_is_retried_with_the_next_one_of_the_direction():
    g = Glossary([RAJBHOG])
    tr = LosingTranslator(lose={"X1X"})
    outs, report = G.translate_protected(tr, ["একটা রাজভোগ দেখা তো", "এর চেয়ে বড়"], "bn", "en", g, max_attempts=3)
    assert outs[0] == "EN[একটা rajbhog দেখা তো]"
    assert outs[1] == "EN[এর চেয়ে বড়]"              # no term: translated once, never retried
    assert report[0]["attempts"] == 2 and report[0]["protected"] is True and report[0]["placeholder"] == "{1}"
    assert report[1] == {"terms": 0}
    assert len(tr.calls) == 2 and tr.calls[1] == ["একটা {1} দেখা তো"]


def test_when_every_placeholder_is_lost_the_sentence_is_translated_with_its_canonical_spelling():
    g = Glossary([RAJBHOG])
    tr = LosingTranslator(lose={"X1X", "{1}", "[1]", "KX1", "[X1]", "XX1"})
    outs, report = G.translate_protected(tr, ["একটা রাজভোগ দেখা তো"], "bn", "en", g, max_attempts=3)
    assert outs == ["EN[একটা রাজভোগ দেখা তো]"]
    assert report[0]["attempts"] == 3 and report[0]["protected"] is False
    assert len(tr.calls) == 4   # three attempts, then the plain sentence


def test_the_translator_is_called_once_for_a_batch_whose_placeholders_all_survive():
    g = Glossary([RAJBHOG, MOSHAI])
    tr = LosingTranslator()
    outs, report = G.translate_protected(tr, ["মশায় একটা রাজভোগ দেখা তো", "রাজভোগ"], "bn", "en", g, max_attempts=3)
    assert outs == ["EN[sir একটা rajbhog দেখা তো]", "EN[rajbhog]"]
    assert len(tr.calls) == 1
    assert [r["attempts"] for r in report] == [1, 1]


# ---------------------------------------------------------------- storage (translation memory)


@pytest.mark.asyncio
async def test_terms_are_stored_listed_merged_and_deleted(scratch_tm):
    t = await scratch_tm.upsert_glossary_term("bn", "রাজভোগ", heard_as=["রাজবুক"], renderings={"en": "rajbhog"})
    assert t["id"] and t["source_lang"] == "bn" and t["heard_as"] == ["রাজবুক"] and t["renderings"] == {"en": "rajbhog"}
    # the same canonical spelling extends the term: a new heard-as spelling, another rendering
    t2 = await scratch_tm.upsert_glossary_term("bn", "রাজভোগ", heard_as=["রাজব", "রাজবুক"], renderings={"zh": "拉吉博格"})
    assert t2["id"] == t["id"]
    assert t2["heard_as"] == ["রাজবুক", "রাজব"]
    assert t2["renderings"] == {"en": "rajbhog", "zh": "拉吉博格"}
    # by id: the canonical spelling and the lists can be replaced outright
    t3 = await scratch_tm.upsert_glossary_term("bn", "রাজভোগ ", heard_as=["রাজবুক"], renderings={"en": "rajbhog"},
                                               term_id=t["id"], replace=True)
    assert t3["id"] == t["id"] and t3["canonical"] == "রাজভোগ" and t3["heard_as"] == ["রাজবুক"] and t3["renderings"] == {"en": "rajbhog"}
    await scratch_tm.upsert_glossary_term("en", "Kubernetes", heard_as=[], renderings={})
    terms = await scratch_tm.list_glossary()
    assert [x["canonical"] for x in terms] == ["রাজভোগ", "Kubernetes"]
    assert [x["canonical"] for x in await scratch_tm.list_glossary("bn")] == ["রাজভোগ"]
    assert (await scratch_tm.get_stats())["glossary_size"] == 2
    assert await scratch_tm.delete_glossary_term(t["id"]) is True
    assert await scratch_tm.delete_glossary_term(t["id"]) is False
    assert [x["canonical"] for x in await scratch_tm.list_glossary()] == ["Kubernetes"]
    removed = await scratch_tm.clear()
    assert removed["glossary"] == 1
    assert await scratch_tm.list_glossary() == []


@pytest.mark.asyncio
async def test_a_blank_canonical_spelling_is_refused(scratch_tm):
    with pytest.raises(ValueError):
        await scratch_tm.upsert_glossary_term("bn", "  ", heard_as=["x"], renderings={})


@pytest.mark.asyncio
async def test_the_old_glossary_table_is_migrated_into_terms(tmp_path):
    """Schema 2 had a storage-only `glossary` table (source_term, target_term per target
    language, lower-cased). Its rows become terms with renderings; the file is backed up."""
    import sqlite3
    from app.cache.translation_memory import TranslationMemory, SCHEMA_VERSION

    db = tmp_path / "tm.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE translations (id INTEGER PRIMARY KEY, content_hash TEXT UNIQUE NOT NULL, source_text TEXT NOT NULL,
            source_lang TEXT NOT NULL, target_lang TEXT NOT NULL, translation TEXT NOT NULL, formatted_lines TEXT NOT NULL,
            created_at TIMESTAMP, updated_at TIMESTAMP, use_count INTEGER, is_user_corrected BOOLEAN, quality_score REAL, engine TEXT);
        CREATE TABLE corrections (id INTEGER PRIMARY KEY, source_text TEXT, source_lang TEXT, target_lang TEXT,
            original_translation TEXT, corrected_translation TEXT, created_at TIMESTAMP, applied BOOLEAN);
        CREATE TABLE glossary (id INTEGER PRIMARY KEY AUTOINCREMENT, source_term TEXT NOT NULL, source_lang TEXT NOT NULL,
            target_term TEXT NOT NULL, target_lang TEXT NOT NULL, context TEXT, created_at TIMESTAMP,
            UNIQUE(source_term, source_lang, target_lang));
        INSERT INTO glossary (source_term, source_lang, target_term, target_lang) VALUES ('রাজভোগ', 'bn', 'rajbhog', 'en');
        INSERT INTO glossary (source_term, source_lang, target_term, target_lang) VALUES ('রাজভোগ', 'bn', '拉吉博格', 'zh');
        PRAGMA user_version = 2;
    """)
    con.commit()
    con.close()
    tm = TranslationMemory(db_path=db)
    await tm.initialize()
    try:
        terms = await tm.list_glossary()
        assert len(terms) == 1 and terms[0]["canonical"] == "রাজভোগ" and terms[0]["renderings"] == {"en": "rajbhog", "zh": "拉吉博格"}
        assert SCHEMA_VERSION == 3
        assert (tmp_path / "tm.db.bak-2").exists()
    finally:
        await tm.close()


# ---------------------------------------------------------------- the app: endpoints, MT, live sessions

TERM = {"source_lang": "bn", "canonical": "রাজভোগ", "heard_as": ["রাজবুক"], "renderings": {"en": "rajbhog"}}


def test_glossary_endpoints(app_env):
    with app_env.client() as c:
        assert c.get("/glossary").json() == {"terms": []}
        r = c.post("/glossary", json=TERM)
        assert r.status_code == 200, r.text
        term = r.json()["term"]
        assert term["canonical"] == "রাজভোগ" and term["heard_as"] == ["রাজবুক"] and term["renderings"] == {"en": "rajbhog"}
        assert c.post("/glossary", json={"source_lang": "bn", "canonical": " ", "heard_as": [], "renderings": {}}).status_code == 422
        assert c.post("/glossary", json={"canonical": "x"}).status_code == 422
        listed = c.get("/glossary").json()["terms"]
        assert [t["id"] for t in listed] == [term["id"]]
        assert c.delete(f"/glossary/{term['id']}").json() == {"status": "deleted", "id": term["id"]}
        assert c.delete(f"/glossary/{term['id']}").status_code == 404
        assert c.get("/glossary").json() == {"terms": []}


def test_a_term_is_protected_through_translation_for_page_subtitles(app_env):
    """The fake engine copies the text, so the placeholder survives; the rendering must be
    in the result, and the fake must never have seen the canonical spelling."""
    with app_env.client() as c:
        assert c.post("/glossary", json=TERM).status_code == 200
        d = c.post("/translate", json={"text": "একটা রাজভোগ দেখা তো", "source_lang": "bn", "target_languages": ["en"]}).json()
    assert d["translations"]["en"]["single_line"] == "EN[fake:bn->en] একটা rajbhog দেখা তো", d
    assert all("রাজভোগ" not in t for texts, _, _ in app_env.engines["fake"].calls for t in texts)


def test_adding_a_term_clears_the_caches_and_forgets_machine_rows_with_the_spelling(app_env):
    from app.translation import pipeline as pipe_mod

    with app_env.client() as c:
        # a cached and a stored machine translation of the sentence, from before the term
        d0 = c.post("/translate", json={"text": "একটা রাজভোগ দেখা তো", "source_lang": "bn", "target_languages": ["en"]}).json()
        assert "rajbhog" not in d0["translations"]["en"]["single_line"]
        live = pipe_mod.audio_session_policy()
        live.cache.set("একটা রাজভোগ দেখা তো", "bn", ("en",), {"en": "stale"})
        assert c.post("/glossary", json=TERM).status_code == 200
        assert live.cache.stats["size"] == 0
        d1 = c.post("/translate", json={"text": "একটা রাজভোগ দেখা তো", "source_lang": "bn", "target_languages": ["en"]}).json()
    assert d1["translations"]["en"]["single_line"] == "EN[fake:bn->en] একটা rajbhog দেখা তো", d1


def _next(ws, kind):
    while True:
        m = ws.receive_json()
        if m["type"] == kind:
            return m


def test_a_live_session_corrects_the_recognised_line_and_renders_the_term(app_env):
    app_env.asr.text = "একটা রাজবুক দেখা তো"
    app_env.asr.numbered = False
    app_env.asr.final_every = 3
    with app_env.client() as c:
        assert c.post("/glossary", json=TERM).status_code == 200
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="bn", target_langs="en")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(3):
                ws.send_bytes(pcm_frame())
            final = _next(ws, "final")
            assert final["text"] == "একটা রাজভোগ দেখা তো"
            assert final["raw_text"] == "একটা রাজবুক দেখা তো"
            assert final["glossary"] == [{"heard": "রাজবুক", "canonical": "রাজভোগ", "how": "exact"}]
            tr = _next(ws, "translation")
            assert tr["source_text"] == "একটা রাজভোগ দেখা তো"
            assert tr["translations"]["en"]["single_line"] == "EN[fake:bn->en] একটা rajbhog দেখা তো"


def test_partials_and_drafts_of_an_open_line_are_corrected_too(app_env, monkeypatch):
    """A scripted recognizer grows the line word by word (as the draft tests do), so a
    stable prefix with the misheard word gets a draft before the line ends."""
    import time
    from app import asr as asr_pkg
    from tests.conftest import ws_recv
    from tests.test_draft_translation import ScriptedEngine

    monkeypatch.setattr(app_env.settings.asr, "draft_debounce_ms", 0)
    monkeypatch.setattr(app_env.settings.asr, "draft_stable_partials", 2)
    monkeypatch.setattr(app_env.settings.asr, "partial_interval_ms", 0)
    words = "একটা রাজবুক দেখা তো আপনার দোকানে আছে".split()
    script = [("partial", " ".join(words[:i])) for i in range(1, len(words) + 1)] + [("final", " ".join(words))]
    monkeypatch.setattr(asr_pkg, "_engines", {"sherpa-zipformer": ScriptedEngine(script)})
    msgs = []
    with app_env.client() as c:
        assert c.post("/glossary", json=TERM).status_code == 200
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="bn", target_langs="en")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(len(script)):
                ws.send_bytes(pcm_frame())
                time.sleep(0.03)            # let the draft of this partial go out, as live audio would
            while (m := ws_recv(ws, timeout=1.0)) is not None:
                msgs.append(m)
    partials = [m for m in msgs if m["type"] == "partial"]
    drafts = [m for m in msgs if m["type"] == "draft"]
    assert partials and drafts, [m["type"] for m in msgs]
    assert not any("রাজবুক" in m["text"] for m in partials), [m["text"] for m in partials]
    assert any(m["text"].startswith("একটা রাজভোগ দেখা") for m in partials), [m["text"] for m in partials]
    assert not any("রাজবুক" in d["source_text"] for d in drafts), [d["source_text"] for d in drafts]
    assert any("rajbhog" in d["translations"]["en"]["single_line"] for d in drafts), drafts


def test_an_open_session_picks_up_a_new_term_on_its_next_line(app_env):
    app_env.asr.text = "একটা রাজবুক দেখা তো"
    app_env.asr.numbered = False
    app_env.asr.final_every = 3
    with app_env.client() as c:
        with c.websocket_connect(app_env.ws_url("/ws/asr", source_lang="bn", target_langs="en")) as ws:
            assert ws.receive_json()["type"] == "ready"
            for _ in range(3):
                ws.send_bytes(pcm_frame())
            first = _next(ws, "translation")
            assert first["source_text"] == "একটা রাজবুক দেখা তো"
            assert "rajbhog" not in first["translations"]["en"]["single_line"]
            assert c.post("/glossary", json=TERM).status_code == 200
            for _ in range(3):
                ws.send_bytes(pcm_frame())
            second = _next(ws, "translation")
            assert second["source_text"] == "একটা রাজভোগ দেখা তো"
            assert second["translations"]["en"]["single_line"] == "EN[fake:bn->en] একটা rajbhog দেখা তো"


def test_the_glossary_stage_is_logged_with_how_the_term_was_protected(app_env, monkeypatch):
    seen = []
    from app import obs

    real = obs.log

    def spy(stage, event, **kw):
        if stage == "glossary":
            seen.append((event, kw))
        return real(stage, event, **kw)

    monkeypatch.setattr(obs, "log", spy)
    monkeypatch.setattr("app.translation.glossary.obs.log", spy)
    with app_env.client() as c:
        assert c.post("/glossary", json=TERM).status_code == 200
        c.post("/translate", json={"text": "একটা রাজভোগ দেখা তো", "source_lang": "bn", "target_languages": ["en"]})
    protect = [kw for ev, kw in seen if kw.get("kind") == "protect"]
    assert protect and protect[0]["terms"] == 1 and protect[0]["attempts"] == 1 and protect[0]["protected"] is True
    assert "রাজভোগ" not in str(protect) and "rajbhog" not in str(protect)   # the log carries counts, never text


def test_an_english_article_follows_the_rendering_not_the_placeholder():
    g = Glossary([RAJBHOG, Term("bn", "আম", [], {"en": "aam"})])
    _, slots = g.protect("একটা রাজভোগ দেখা তো", "en", "X{n}X")
    assert g.restore("I've seen an X1X.", slots, "en") == "I've seen a rajbhog."
    assert g.restore("An X1X, please.", slots, "en") == "A rajbhog, please."
    _, slots = g.protect("একটা আম দেখা তো", "en", "X{n}X")
    assert g.restore("Show me a X1X.", slots, "en") == "Show me an aam."
    assert g.restore("Show me a X1X.", slots) == "Show me a aam."   # other targets: untouched
