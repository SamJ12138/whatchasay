"""Which signals on a video's page say what language is spoken (docs/page-prior.md).

For each page: fetch it as a browser would (desktop Chrome User-Agent, Accept-Language
en-US), read the candidate signals and print each with the language it implies, out of
the ones we recognise (en, zh, bn; anything else implies nothing):

  yt_caption_asr            YouTube: the language of the auto-generated caption track
                            (captionTracks[kind=asr]); YouTube's own speech recognition
  yt_caption_tracks         YouTube: the uploaded caption tracks' languages (one language only)
  yt_audio_track            YouTube: the default audio track of a multi-language video
                            (adaptiveFormats[].audioTrack, audioIsDefault)
  yt_default_audio_language YouTube: a `defaultAudioLanguage` field anywhere in the page
  title_script              the script of the title (YouTube: the player's title; else
                            og:title or <title>): Han or Bengali if there are 2+ such
                            characters (the more of the two), else Latin -> en
  title_script_majority     the same title, the script with the most characters
  description_script        the description (YouTube: the player's only; else og:description
                            or meta description), the `title_script` rule
  channel_script            YouTube: the channel name, the `title_script` rule
  page_script               the three above as one vote (one uploader wrote them): the first
                            Han or Bengali script among title, channel, description; else
                            the title's (Latin -> en)
  html_lang                 <html lang>
  og_locale                 <meta property="og:locale">

    python scripts/page_prior_eval.py [--json out.json] [--cache DIR] bn=URL zh=URL ...
    python scripts/page_prior_eval.py --pages pages.tsv     (lines: spoken<TAB>URL[<TAB>label])

The spoken language of each page is given (it is what the signals are scored against).
Prints one block per page and a Markdown table: per signal, on how many pages it was
present, and how often it named the spoken language. Reads the network only; writes
only --json and --cache.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
LANGS = ("en", "zh", "bn")
SIGNALS = ("yt_caption_asr", "yt_caption_tracks", "yt_audio_track", "yt_default_audio_language", "title_script",
           "title_script_majority", "description_script", "channel_script", "page_script", "html_lang", "og_locale")

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_BENGALI = re.compile(r"[ঀ-৿]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")


def script_language(text: Optional[str], rule: str = "any") -> Optional[str]:
    """The language a text's script implies. rule "any": Han or Bengali when the text has
    at least 2 of those characters (the more of the two), else Latin (3+ letters) -> en.
    rule "majority": the script with the most characters (Latin counted by letters)."""
    if not text:
        return None
    han, bn, latin = len(_HAN.findall(text)), len(_BENGALI.findall(text)), len(_LATIN.findall(text))
    if rule == "majority":
        best = max((han, "zh"), (bn, "bn"), (latin, "en"))
        return best[1] if best[0] >= (3 if best[1] == "en" else 2) else None
    if max(han, bn) >= 2:
        return "zh" if han >= bn else "bn"
    return "en" if latin >= 3 else None


def lang_from_code(code: Optional[str]) -> Optional[str]:
    """en / zh / bn from a language tag (en-US, zh_CN, zh-Hans, cmn, ben...), else None."""
    if not code:
        return None
    base = re.split(r"[-_.]", code.strip().lower())[0]
    return {"en": "en", "eng": "en", "zh": "zh", "cmn": "zh", "zho": "zh", "chi": "zh",
            "bn": "bn", "ben": "bn"}.get(base)


class _Head(HTMLParser):
    """<html lang>, <title>, and the <meta> tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang: Optional[str] = None
        self.title = ""
        self.meta: Dict[str, str] = {}
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html" and self.html_lang is None:
            self.html_lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = a.get("property") or a.get("name") or a.get("itemprop")
            if key and a.get("content") is not None and key not in self.meta:
                self.meta[key] = a["content"]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def player_response(html: str) -> Optional[dict]:
    """YouTube's ytInitialPlayerResponse object from the page's inline script."""
    m = re.search(r"ytInitialPlayerResponse\s*=\s*\{", html)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[m.end() - 1:])
        return obj
    except ValueError:
        return None


def _sig(value, lang) -> dict:
    return {"value": value, "lang": lang}


def extract_signals(html: str, url: str) -> Dict[str, dict]:
    head = _Head()
    head.feed(html)
    sig: Dict[str, dict] = {}
    is_yt = (urlparse(url).hostname or "").endswith("youtube.com")
    title = head.meta.get("og:title") or head.title.strip() or None
    description = head.meta.get("og:description") or head.meta.get("description")
    if is_yt:
        pr = player_response(html) or {}
        vd = pr.get("videoDetails") or {}
        title = vd.get("title") or title
        # only the player's: an empty description gets YouTube's own generic og:description
        description = vd.get("shortDescription") or None
        tracks = ((pr.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get("captionTracks") or []
        asr = next((t.get("languageCode") for t in tracks if t.get("kind") == "asr"), None)
        sig["yt_caption_asr"] = _sig(asr, lang_from_code(asr))
        uploaded = sorted({t.get("languageCode") for t in tracks if t.get("kind") != "asr" and t.get("languageCode")})
        implied = {lang_from_code(c) for c in uploaded}
        sig["yt_caption_tracks"] = _sig(uploaded or None, implied.pop() if len(implied) == 1 else None)
        audio = [f.get("audioTrack") for f in (pr.get("streamingData") or {}).get("adaptiveFormats") or []
                 if f.get("audioTrack")]
        default = next((a.get("id") for a in audio if a.get("audioIsDefault")), None)
        sig["yt_audio_track"] = _sig(default, lang_from_code(default))
        m = re.search(r'"defaultAudioLanguage"\s*:\s*"([^"]+)"', html)
        sig["yt_default_audio_language"] = _sig(m.group(1) if m else None, lang_from_code(m.group(1)) if m else None)
        sig["channel_script"] = _sig(vd.get("author"), script_language(vd.get("author")))
    sig["title_script"] = _sig(title, script_language(title))
    sig["title_script_majority"] = _sig(title, script_language(title, rule="majority"))
    sig["description_script"] = _sig((description or "")[:200] or None, script_language(description))
    # the three texts come from the same uploader, so they are one vote: the first non-Latin
    # script among title, channel, description; else the title's (Latin -> en)
    texts = [title, (sig.get("channel_script") or {}).get("value"), description]
    non_latin = next((script_language(t) for t in texts if script_language(t) in ("zh", "bn")), None)
    sig["page_script"] = _sig(non_latin or (sig["title_script"]["lang"] and "latin"),
                              non_latin or sig["title_script"]["lang"])
    sig["html_lang"] = _sig(head.html_lang, lang_from_code(head.html_lang))
    og = head.meta.get("og:locale")
    sig["og_locale"] = _sig(og, lang_from_code(og))
    return sig


def tally(rows: List[dict]) -> Dict[str, dict]:
    """Per signal: pages seen, pages where it implied a language, right and wrong ones."""
    out: Dict[str, dict] = {}
    for row in rows:
        for name, s in row["signals"].items():
            t = out.setdefault(name, {"pages": 0, "present": 0, "right": 0, "wrong": 0})
            t["pages"] += 1
            if s.get("lang"):
                t["present"] += 1
                t["right" if s["lang"] == row["spoken"] else "wrong"] += 1
    return out


def fetch(url: str, cache: Optional[Path]) -> str:
    if cache:
        f = cache / (re.sub(r"[^A-Za-z0-9]+", "_", url)[-120:] + ".html")
        if f.exists():
            return f.read_text(encoding="utf-8")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(html, encoding="utf-8")
    return html


def _pages(args) -> List[tuple]:
    pages = []
    if args.pages:
        for line in args.pages.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                parts = line.split("\t")
                pages.append((parts[0].strip(), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    for a in args.page:
        lang, _, url = a.partition("=")
        pages.append((lang, url, ""))
    return pages


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page", nargs="*", help="spoken=URL (spoken: en, zh or bn)")
    ap.add_argument("--pages", type=Path, help="a file of spoken<TAB>URL[<TAB>label] lines")
    ap.add_argument("--json", type=Path, help="write every page's signals here")
    ap.add_argument("--cache", type=Path, help="keep the fetched HTML here and reuse it")
    args = ap.parse_args()
    rows = []
    for spoken, url, label in _pages(args):
        try:
            html = fetch(url, args.cache)
        except Exception as e:  # one unreachable page does not end the evaluation
            print(f"## {label or url}: fetch failed ({type(e).__name__}: {e})")
            continue
        sig = extract_signals(html, url)
        rows.append({"spoken": spoken, "url": url, "label": label, "signals": sig})
        print(f"## {label or url} (spoken: {spoken})")
        for name in SIGNALS:
            if name in sig:
                s = sig[name]
                mark = "" if not s["lang"] else (" ok" if s["lang"] == spoken else " WRONG")
                print(f"  {name:26} -> {s['lang'] or '-':3}{mark:6} {json.dumps(s['value'], ensure_ascii=False)[:110]}")
        if args.cache is None:
            time.sleep(1.0)
    t = tally(rows)
    print("\n| Signal | Pages | Present | Names the spoken language | Names another | Right when present |")
    print("|---|---|---|---|---|---|")
    for name in SIGNALS:
        if name in t:
            c = t[name]
            rate = f"{c['right'] / c['present']:.0%}" if c["present"] else "-"
            print(f"| `{name}` | {c['pages']} | {c['present']} | {c['right']} | {c['wrong']} | {rate} |")
    if args.json:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
