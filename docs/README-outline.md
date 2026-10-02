# README outline (for Phase 3)

The README itself is written in Phase 3. This file fixes its structure, the product statement, and the commands as
they work at the end of Phase 2b (commit of this file), so Phase 3 does not have to rediscover them. Run in Phase 2b:
`download_models.py` (license gate, and a real OPUS-MT download into a scratch dir), `run.py` through the browser
harness, the fast / slow / node test commands, every harness path. The venv / pip steps are the ones
`backend/scripts/clean_install_check.py` runs (Phase 2a); the CPU-torch and launcher lines were read from the code,
not re-run. Re-check anything a later phase changes.

## Product statement (one sentence, verbatim)

> Live local subtitles for any tab's audio: nothing you hear leaves your machine unless you turn that on.

## Sections

1. **Title + product statement** (above) and a 2-3 line description: Chrome extension + local Python backend;
   tab audio → streaming speech recognition → translation → overlay, in under a second; English, Mandarin, Bengali
   first-class (speech and text, every direction), other languages translation-only.
2. **What stays on your machine** (D1-D4 in user words): default engines are local (Zipformer ASR, OPUS-MT on CPU);
   no site access at install; tab audio capture asked for on first *Start Live Captions*; caption mode opt-in with
   its site list; live-caption text never stored; translation memory contents, retention and *Clear translation
   memory*; cloud off unless enabled in `backend/.env`, and how to see which provider receives text (startup log
   line, Options).
3. **Requirements**: Windows 10/11, macOS or Linux; Python 3.10+ (CI runs 3.12); Chrome (desktop) with MV3
   offscreen documents and `tabCapture` from the service worker; ~1 GB disk for the default models; optional NVIDIA
   GPU (4 GB+) only for the optional HY-MT engine; Node 22 only for the extension tests.
4. **Quickstart** (commands below).
5. **Using it**: popup (*Start Live Captions*, spoken-language picker, *Translate subtitles on this tab*), first-use
   permission prompt, caption mode in Options, keyboard shortcuts as the manifest defines them (Alt+L live captions,
   Alt+T overlay, Alt+S swap languages, Alt+Period larger font; Alt+E edit is a page key handled by the content
   script), corrections. Note: the Options page's shortcut list is out of date (it shows Alt+] / Alt+[, and there
   is no decrease-font command); fix it with the README.
6. **Configuration**: Options page (languages, line limits, refiner, caption mode, data management); backend
   settings via `SUBTITLE_*` environment variables or `backend/.env` (engine, TM policy, cloud), with the defaults
   table (`mt.engine=opus`, `tm.persist_audio_sessions=false`, `tm.persist_captions=true`, `tm.retention_days=30`,
   `cloud.enabled=false`).
7. **Engines and models**: OPUS-MT default (directions, pivot through English), HY-MT optional (GPU, license gate),
   Zipformer models + whisper-tiny LID, Chrome on-device Translator (Tier 0); link to `NOTICE.md` for every license
   (and the Mandarin model's undeclared license).
8. **Performance**: measured numbers from DEVLOG §4 and Phase 2b (OPUS-MT p50 30 ms per call over the six en/zh/bn
   directions, first caption ≈1 s, final ≈0.6-0.8 s after the speaker pauses); the machine they were measured on.
9. **How it works**: the pipeline diagram (DEVLOG §2), links to `docs/pipeline-stages.md`,
   `docs/architecture-notes.md` (service-worker lifetime, site access, memory, cloud keys), `docs/observations.md`.
10. **Development**: tests (fast / slow / node), the browser harness paths, CI, logs (`backend/logs/run_*.jsonl`,
    `scripts/failure_report.py`), DEVLOG convention.
11. **Troubleshooting**: from `docs/SETUP.md` §6 plus: "Tab audio capture is not allowed yet", "Caption mode is off",
    "HY-MT is configured but ..." lines and what they mean.
12. **Licenses and credits**: the project's own license (not chosen yet: Phase 3 decision), `NOTICE.md`.

## Quickstart commands (as they stand now)

Windows (PowerShell or cmd), from the repository root:

```
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu
venv\Scripts\python ..\scripts\download_models.py
venv\Scripts\python run.py
```

- `torch` (CPU wheel) is needed once to convert the OPUS-MT checkpoints to CTranslate2. On an NVIDIA machine use
  `venv\Scripts\pip install -r requirements-gpu.txt --extra-index-url https://download.pytorch.org/whl/cu128`
  instead (it includes CUDA torch).
- `download_models.py` is optional (the backend fetches the ASR and OPUS-MT models on first use); it shows progress
  and does it ahead of time. Optional GPU engine:
  `venv\Scripts\python ..\scripts\download_models.py --hymt --accept-hymt-license` (shows the Tencent license
  summary first; without the flag it downloads nothing and exits 2), then set `SUBTITLE_MT__ENGINE=hymt`.
- `run.py` serves on `127.0.0.1:8765` (`--port N`; loopback only, `--host` refuses anything else). Check
  `http://127.0.0.1:8765/health/json` (`mt_engines` lists `opus`).
- One-click alternative: `Start_Subtitle_Translator.bat` (Windows) / `Start_Subtitle_Translator.command` (macOS);
  `Stop_Subtitle_Translator.bat` stops it.

macOS / Linux: the same with `python3 -m venv venv`, `venv/bin/pip`, `venv/bin/python`, `../scripts/download_models.py`.
HY-MT's llama-server download is Windows-only; elsewhere install `llama-server` into `backend/bin/llama/` by hand.

Extension:

1. `chrome://extensions` → Developer mode → **Load unpacked** → the `extension/` folder (no site access is requested).
2. Open a tab with a video → extension icon → **Start Live Captions** → allow tab audio capture once.
3. Optional, videos with subtitles: Options → **Caption Mode** → allow the listed sites → popup → **Translate
   subtitles on this tab**.

Cloud (optional), `backend/.env`, then restart the backend:

```
SUBTITLE_CLOUD__ENABLED=true
SUBTITLE_CLOUD__GOOGLE_API_KEY=...
```

Tests:

```
cd backend
venv\Scripts\pip install -r requirements-dev.txt
venv\Scripts\python -m pytest                      # fast suite: fakes only, no models, no network (~30 s)
venv\Scripts\python -m pytest -m slow              # real models + browser (needs: pip install playwright; playwright install chromium;
                                                   # set ST_PLAYWRIGHT_PYTHON to a Python that has playwright)
cd ..\extension
node --test                                        # extension pure modules (Node 22)
```

Browser harness directly: `python backend/scripts/e2e_extension.py --start-backend --path audio|captions|security|sw-idle|permissions|clear-memory|cloud-keys`.
