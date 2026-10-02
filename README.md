# Subtitle Translator — live captions and sub-second translation

A Chrome extension plus a local Python backend that puts translated subtitles on any video:

- **Videos with subtitles** — the existing captions are detected in the page and translated in ~100 ms.
- **Videos without subtitles** — *Live Captions* listens to the tab's audio, transcribes it with streaming
  speech models and translates each sentence, all in **under one second** end to end.

First-class languages: **English, Mandarin Chinese, Bengali** (speech and text, every direction).
Translation-only support for Vietnamese, Japanese, Korean, Spanish, French, German, Russian, Portuguese, Italian.

Everything runs on your machine by default. Cloud providers (Gladia/ElevenLabs speech, Google/Azure translation,
Groq/Gemini refinement) are optional and only used if you paste an API key.

## What runs where

```
Chrome tab audio ──tabCapture──▶ offscreen document ──40 ms PCM──▶ /ws/asr (FastAPI)
                                   │ AudioWorklet @16 kHz              │ sherpa-onnx Zipformer (streaming, CPU)
                                   │ Chrome on-device Translator (opt) │   en / zh / bn + spoken-language-ID
                                   ▼                                   ▼
                              overlay on the video ◀── partial / final / translation ── HY-MT1.5-1.8B (llama.cpp, GPU)
                                                                                   or OPUS-MT (CTranslate2, CPU)
```

| Stage | Engine | Typical latency (RTX 4060 laptop) |
|---|---|---|
| Streaming speech → partial caption | sherpa-onnx Zipformer transducers (en/zh/bn), CPU | ~0.3–0.4 s after the word is spoken |
| Sentence end → final caption | built-in endpointing (0.6 s trailing silence) | ~0.7 s after the speaker pauses |
| Translation of a sentence | HY-MT1.5-1.8B via llama-server on GPU | en→zh 90–130 ms, zh/bn→en ~175 ms, →bn 300–500 ms |
| Translation fallback | OPUS-MT int8 on CPU (en↔bn direct, zh↔bn via English pivot) | ~20–45 ms warm |
| Optional refinement | Ollama / Groq / Gemini, async with a 0.8 s deadline | never blocks the first render |

Measured on this machine: the source-language caption appears at ~0.4 s, the translated line at ~0.6–0.9 s
(Bengali output is the slowest because its script tokenizes into many pieces). See `DEVLOG.md` for the numbers.

## System requirements

- Windows 10/11 (macOS/Linux: CPU path works; llama.cpp binary must be installed manually)
- Python 3.10–3.12
- Chrome 116+ (Chrome 138+ for optional on-device translation)
- 8 GB RAM (16 GB recommended). NVIDIA GPU with 4+ GB VRAM optional: enables HY-MT (all bn/zh directions) and accuracy mode
- ~1 GB disk for speech models, +2.5 GB with the GPU translation model

## Quick start

1. Double-click `Start_Subtitle_Translator.bat` (macOS: `Start_Subtitle_Translator.command`).
   First run installs dependencies and downloads models (several minutes); later runs take ~10 s.
2. Open `chrome://extensions`, enable **Developer mode**, **Load unpacked** → select the `extension` folder.
3. Open a video:
   - with subtitles: click the extension icon → turn on **Translate subtitles on this tab**; translations then appear
     above the subtitles (the extension contacts the backend only for tabs you switched on);
   - without subtitles: click the extension icon → **Start Live Captions** (or press **Alt+L**).
     Pick the spoken language or leave *Auto-detect* (captions start immediately and switch language if needed).

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Alt+L` | Start/stop live captions for the current tab |
| `Alt+T` | Toggle translation overlay |
| `Alt+S` | Swap primary/secondary language order |
| `Alt+.` | Increase font size |
| `Alt+E` | Edit current translation (double-click a line also works) |

## Configuration

Extension **Options** page: target languages (incl. Bengali), live-caption language and engine, translation engine
(auto / local backend / Chrome on-device), partial captions on/off, AI refinement, cloud API keys.

Backend: `backend/app/config.py` or environment variables with prefix `SUBTITLE_` (nested with `__`), e.g.

```bash
SUBTITLE_ASR__LANGUAGES='["en","zh","bn"]'
SUBTITLE_MT__ENGINE_ORDER='["hymt","opus","cloud"]'
SUBTITLE_MT__HYMT_FILE=HY-MT1.5-1.8B-Q4_K_M.gguf     # smaller/faster quant
SUBTITLE_REFINER__ENABLED=true SUBTITLE_REFINER__PROVIDER=groq SUBTITLE_REFINER__GROQ_API_KEY=...
SUBTITLE_CLOUD__GOOGLE_API_KEY=...                    # cloud translation tier
```

Useful URLs while the backend runs: `/health`, `/health/json`, `/debug` (test translation and a microphone live-caption
test), `/metrics` (p50/p95 latency), `/config`.

## Project layout

```
backend/app/asr/          streaming ASR: engine protocol, sherpa-onnx Zipformer engine, spoken-language-ID, session
backend/app/translation/  router (base_translator), HY-MT via llama-server, OPUS-MT CTranslate2, cloud, async refiner, pipeline
backend/app/main.py       FastAPI app: /ws (subtitle cues), /ws/asr (live captions), HTTP endpoints
backend/scripts/          spikes and the end-to-end WebSocket test client (e2e_ws_asr.py)
backend/tests/            pytest suite (unit + integration when the server is up)
extension/                MV3 extension: background.js (tabCapture), offscreen.js (audio + WebSocket + Translator API),
                          pcm-worklet.js, content-scripts/ (detector, overlay, main), popup/, options/
```

## Running tests

```bash
cd backend
venv\Scripts\pip install -r requirements-dev.txt   # pytest, pytest-asyncio, pytest-timeout, pytest-cov
venv\Scripts\python -m pytest -v             # fast suite: no models, no server, no network
venv\Scripts\python -m pytest -m slow -v     # real models + Chromium harness (scripts/e2e_extension.py)
cd ..\extension && node --test               # extension modules
venv\Scripts\python scripts\e2e_ws_asr.py data\models\asr\sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09\test_wavs\0.wav auto en,zh
```

## Limitations

- DRM-protected sites (Netflix, Disney+, Prime Video) usually block tab-audio capture; the overlay shows a "No audio" notice.
- Streaming models output no punctuation; sentences are split by pauses (0.6 s) or every 10 s of continuous speech.
- Auto-detect starts with English and switches after ~2.5 s of speech; choose the language explicitly for the fastest start.
- Bengali speech recognition is the weakest of the three (~18–21% word error on benchmarks); "Accurate (delayed)" mode
  with a fine-tuned Whisper is available on GPU when accuracy matters more than latency.

## Privacy

Audio and text stay on your machine unless you add a cloud API key. Keys are stored in the extension's local storage and
sent only to the local backend. Corrections you make are stored in `backend/data/translation_memory.db`.

## Acknowledgments

[k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) and the Vosk Bengali Zipformer, [Tencent HY-MT1.5](https://huggingface.co/tencent/HY-MT1.5-1.8B),
[ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp), [Helsinki-NLP OPUS-MT](https://github.com/Helsinki-NLP/Opus-MT),
[CTranslate2](https://github.com/OpenNMT/CTranslate2), [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

MIT License.
