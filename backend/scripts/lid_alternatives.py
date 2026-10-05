"""Ways to decide the spoken language earlier than whisper-tiny, measured offline
(docs/lid-alternatives.md). Nothing here is used by the app.

For every clip and start offset (the starts of docs/page-prior.md: 0, 0.3, 0.7, 1.5, 3, 6, 10 s
where the file is long enough) and every window (0.5 / 0.75 / 1.0 s of voiced audio, the buffer
up to that point, silence included, as the session's language ID sees it):

  whisper-<size>      whisper's language token probabilities renormalised over en/zh/bn (as
                      asr/lid.py does for tiny), for every whisper export given
  sherpa-slid-<size>  sherpa-onnx's SpokenLanguageIdentification on the same audio: its answer
                      is whisper's unconstrained top language (no probabilities)
  agreement           the three streaming recognizers fed the same audio from the start, in
                      40 ms frames, decoding what is ready (no flush: what the session's
                      parallel recognizers have at that moment); each scored by its own output:
                      tokens, mean token log-probability (sherpa's ys_probs), and the share of
                      its words that are real words of its language (a word list per language)

    python scripts/lid_alternatives.py --clips clips.json --models-root DIR [--whisper DIR ...]
        [--wordlists DIR] --out results.json
    python scripts/lid_alternatives.py --table results.json

clips.json: {"name": ["en"|"zh"|"bn", "path/to/16k-mono.wav"], ...}. --wordlists: a folder with
en_50k.txt, zh_cn_50k.txt, bn_50k.txt ("word count" lines, e.g. hermitdave/FrequencyWords).
Writes only --out.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LANGS = ("en", "zh", "bn")
OFFSETS = (0.0, 0.3, 0.7, 1.5, 3.0, 6.0, 10.0)
WINDOWS = (0.5, 0.75, 1.0)
SR, FRAME, MIN_RMS = 16000, 640, 0.004

HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
BENGALI_WORD = re.compile(r"[ঀ-৿]+")
LATIN_WORD = re.compile(r"[A-Za-z']+")


def load_wav(path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def window_ends(audio: np.ndarray, windows=WINDOWS) -> dict:
    """{window: number of samples} at which `window` seconds of voiced audio have been heard
    (frames of 40 ms at or above the session's lid_min_rms)."""
    out, voiced, want = {}, 0, list(windows)
    for i in range(0, len(audio) - FRAME + 1, FRAME):
        f = audio[i:i + FRAME]
        if float(np.sqrt(np.mean(f * f))) >= MIN_RMS:
            voiced += FRAME
        while want and voiced / SR >= want[0] - 1e-6:
            out[want.pop(0)] = i + FRAME
        if not want:
            break
    return out


def load_wordlist(path: Path, n: int = 50000) -> set:
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines()[:n]:
        w = line.split(" ")[0].strip().lower()
        if w:
            words.add(w)
    return words


def real_word_share(text: str, lang: str, lexicon: set) -> tuple:
    """(real words, words) in the recognizer's output, for its own language: English and
    Bengali words against the word list; Mandarin: the Han text segmented by longest match
    against the list, a 'word' being a match of 2+ characters (a lone character is not counted
    as real, any Han string is made of real characters); Latin words out of the bilingual
    Mandarin model count as not Mandarin."""
    if lang == "en":
        words = [w.lower() for w in LATIN_WORD.findall(text)]
        return sum(1 for w in words if w in lexicon), len(words)
    if lang == "bn":
        words = BENGALI_WORD.findall(text)
        return sum(1 for w in words if w in lexicon), len(words)
    han = "".join(HAN.findall(text))
    latin = LATIN_WORD.findall(text)
    real, n, i = 0, len(latin), 0
    while i < len(han):
        for j in range(min(len(han), i + 4), i, -1):
            if j - i >= 2 and han[i:j] in lexicon:
                real, n, i = real + 1, n + 1, j
                break
        else:
            n, i = n + 1, i + 1
    return real, n


def agreement_decision(scores: dict, rule: str) -> tuple:
    """(language or None, confidence) from the three recognizers' scores.
    logprob: the highest mean token log-probability among recognizers with tokens; confidence =
    its margin over the next one (nats; the only one with tokens: its margin over nothing = 9.0).
    words: the highest real-word share among recognizers with words, ties to more words;
    confidence = that share minus the next one's."""
    if rule == "logprob":
        cand = {l: s["mean_lp"] for l, s in scores.items() if s["tokens"] > 0 and s["mean_lp"] is not None}
    else:
        cand = {l: s["real"] / s["words"] + 1e-3 * s["words"] for l, s in scores.items() if s["words"] > 0}
    if not cand:
        return None, 0.0
    ranked = sorted(cand, key=cand.get, reverse=True)
    best = ranked[0]
    margin = cand[best] - cand[ranked[1]] if len(ranked) > 1 else 9.0
    return best, round(float(margin), 4)


class Whisper:
    """whisper's language probabilities via onnxruntime (asr/lid.py's code), any size."""

    def __init__(self, model_dir: Path, threads: int = 1):
        from app.asr import lid
        size = model_dir.name.rsplit("-", 1)[-1]
        self.name = f"whisper-{size}"
        self.impl = lid.SpokenLanguageId(model_dir.parent, num_threads=threads)
        enc = model_dir / f"{size}-encoder.int8.onnx"
        dec = model_dir / f"{size}-decoder.int8.onnx"
        dec = dec if dec.exists() and size != "tiny" else model_dir / f"{size}-decoder.onnx"  # tiny: as the app
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads, opts.inter_op_num_threads = threads, 1
        self.impl._enc = ort.InferenceSession(str(enc), opts, providers=["CPUExecutionProvider"])
        self.impl._dec = ort.InferenceSession(str(dec), opts, providers=["CPUExecutionProvider"])
        self.impl._lang_tokens, self.impl._sot = lid.language_tokens(self.impl._enc.get_modelmeta().custom_metadata_map)
        self.size_mb = round((enc.stat().st_size + dec.stat().st_size) / 2 ** 20, 1)

    def __call__(self, audio):
        from app.asr.lid import choose_language
        t0 = time.perf_counter()
        _, info = choose_language(self.impl.probabilities(audio), LANGS, 0.0)
        return {"lang": info["best"], "conf": info["confidence"], "top3": info["top3"],
                "ms": round((time.perf_counter() - t0) * 1000, 1)}


class SherpaSlid:
    """sherpa-onnx's own SpokenLanguageIdentification: whisper's unconstrained top language."""

    def __init__(self, model_dir: Path, threads: int = 1):
        import sherpa_onnx
        size = model_dir.name.rsplit("-", 1)[-1]
        self.name = f"sherpa-slid-{size}"
        cfg = sherpa_onnx.SpokenLanguageIdentificationConfig(
            whisper=sherpa_onnx.SpokenLanguageIdentificationWhisperConfig(
                encoder=str(model_dir / f"{size}-encoder.int8.onnx"), decoder=str(model_dir / f"{size}-decoder.int8.onnx")),
            num_threads=threads)
        self.slid = sherpa_onnx.SpokenLanguageIdentification(cfg)

    def __call__(self, audio):
        t0 = time.perf_counter()
        s = self.slid.create_stream()
        s.accept_waveform(SR, audio)
        lang = self.slid.compute(s)
        return {"lang": lang, "ms": round((time.perf_counter() - t0) * 1000, 1)}


class Agreement:
    """The three streaming recognizers on the same audio, scored at each window end."""

    def __init__(self, models_root: Path, lexicons: dict, threads: int = 1):
        from app.asr.sherpa_engine import SherpaZipformerEngine
        self.engine = SherpaZipformerEngine(models_root, num_threads=threads)
        self.recs = {l: self.engine.get_recognizer(l) for l in LANGS}
        self.lexicons = lexicons

    def run(self, audio: np.ndarray, ends: dict) -> dict:
        streams = {l: r.create_stream() for l, r in self.recs.items()}
        out, todo = {}, sorted(ends.items(), key=lambda kv: kv[1])
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            for l, s in streams.items():
                s.accept_waveform(SR, audio[i:i + FRAME])
                while self.recs[l].is_ready(s):
                    self.recs[l].decode_stream(s)
            while todo and i + FRAME >= todo[0][1]:
                w, _ = todo.pop(0)
                out[w] = {l: self._score(l, s) for l, s in streams.items()}
            if not todo:
                break
        return out

    def _score(self, lang, stream) -> dict:
        r = self.recs[lang].get_result_all(stream)
        probs = list(r.ys_probs)
        text = "".join(r.tokens).replace("▁", " ").strip()
        real, words = real_word_share(text, lang, self.lexicons.get(lang, set()))
        return {"tokens": len(r.tokens), "mean_lp": round(float(np.mean(probs)), 4) if probs else None,
                "real": real, "words": words, "text": text[:80]}


def evaluate(clips: dict, models_root: Path, whisper_dirs, wordlists: Path | None) -> dict:
    lexicons = {}
    if wordlists:
        for lang, fname in (("en", "en_50k.txt"), ("zh", "zh_cn_50k.txt"), ("bn", "bn_50k.txt")):
            if (wordlists / fname).exists():
                lexicons[lang] = load_wordlist(wordlists / fname)
    whispers = [Whisper(d) for d in whisper_dirs]
    slids = []
    for d in whisper_dirs:
        if (d / f"{d.name.rsplit('-', 1)[-1]}-decoder.int8.onnx").exists():
            slids.append(SherpaSlid(d))
    agree = Agreement(models_root, lexicons)
    res = {"models": {w.name: {"size_mb": w.size_mb} for w in whispers}, "starts": {}}
    for name, (spoken, path) in clips.items():
        audio = load_wav(path)
        for off in OFFSETS:
            if off * SR >= len(audio) - 3 * SR:
                continue
            a = audio[int(off * SR):]
            ends = window_ends(a)
            row = {"spoken": spoken, "windows": {}}
            scores = agree.run(a, ends)
            for w, end in ends.items():
                cell = {m.name: m(a[:end]) for m in whispers + slids}
                cell["agreement"] = scores.get(w)
                cell["buffered_s"] = round(end / SR, 2)
                row["windows"][str(w)] = cell
            res["starts"][f"{name}@{off}"] = row
            print(name, off, " ".join(f"{w}:{c['whisper-tiny']['lang'] if 'whisper-tiny' in c else '-'}" for w, c in row["windows"].items()),
                  flush=True)
    return res


# ---------------------------------------------------------------------------- table

def decide(cell: dict, method: str):
    """(language or None, confidence) of `method` on one window."""
    if method.startswith("agreement:"):
        if not cell.get("agreement"):
            return None, 0.0
        return agreement_decision(cell["agreement"], method.split(":", 1)[1])
    c = cell.get(method)
    if not c:
        return None, 0.0
    lang = c["lang"] if c["lang"] in LANGS else None
    return lang, c.get("conf", 1.0 if lang else 0.0)


def accepted(lang, conf, method: str, floors: dict) -> bool:
    """Would the session accept this answer at the first attempt? whisper: today's floors
    (English 0.97, else 0.6); others: the floor given for the method."""
    if lang is None:
        return False
    if method.startswith("whisper-"):
        return conf >= (0.97 if lang == "en" else 0.6)
    return conf >= floors.get(method, 0.0)


def table(res: dict, floors: dict | None = None) -> dict:
    """Per method and window: right / wrong / undecided top answers over all starts, and at the
    method's floor: right accepted, wrong accepted (misfires), split by spoken language group."""
    floors = floors or {}
    methods = sorted({k for r in res["starts"].values() for c in r["windows"].values() for k in c
                      if k not in ("agreement", "buffered_s")}) + ["agreement:logprob", "agreement:words"]
    out = {}
    for m in methods:
        for w in (str(x) for x in WINDOWS):
            t = {"n": 0, "right": 0, "wrong": 0, "undecided": 0, "acc_right": 0, "misfires": 0,
                 "misfires_zhbn_as_en": 0, "n_en": 0, "n_zhbn": 0, "acc_right_en": 0, "acc_right_zhbn": 0}
            for r in res["starts"].values():
                cell = r["windows"].get(w)
                if cell is None:
                    continue
                lang, conf = decide(cell, m)
                spoken = r["spoken"]
                t["n"] += 1
                t["n_en" if spoken == "en" else "n_zhbn"] += 1
                if lang is None:
                    t["undecided"] += 1
                elif lang == spoken:
                    t["right"] += 1
                else:
                    t["wrong"] += 1
                if accepted(lang, conf, m, floors):
                    if lang == spoken:
                        t["acc_right"] += 1
                        t["acc_right_en" if spoken == "en" else "acc_right_zhbn"] += 1
                    else:
                        t["misfires"] += 1
                        if lang == "en":
                            t["misfires_zhbn_as_en"] += 1
            out[f"{m}@{w}"] = t
    return out


def zero_misfire_floor(res: dict, method: str, window: str) -> float:
    """The lowest confidence floor at which `method` accepts no wrong answer on these starts."""
    worst = 0.0
    for r in res["starts"].values():
        cell = r["windows"].get(window)
        if cell is None:
            continue
        lang, conf = decide(cell, method)
        if lang is not None and lang != r["spoken"]:
            worst = max(worst, conf)
    return round(worst + 1e-4, 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=Path)
    ap.add_argument("--models-root", type=Path, help="the asr models folder (Zipformers, sherpa-onnx-whisper-tiny)")
    ap.add_argument("--whisper", type=Path, action="append", default=[],
                    help="a sherpa-onnx-whisper-<size> folder (repeatable); tiny from --models-root is always in")
    ap.add_argument("--wordlists", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--table", type=Path, help="print the summary of a results file")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.table:
        res = json.loads(args.table.read_text(encoding="utf-8"))
        print(json.dumps(table(res), indent=1))
        return 0
    clips = {k: (v[0], Path(v[1])) for k, v in json.loads(args.clips.read_text(encoding="utf-8")).items()}
    dirs = [args.models_root / "sherpa-onnx-whisper-tiny"] + list(args.whisper)
    res = evaluate(clips, args.models_root, dirs, args.wordlists)
    args.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
