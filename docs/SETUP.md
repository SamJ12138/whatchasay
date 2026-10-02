# Setup Guide

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10/11, macOS 12+, Linux | Windows 11 |
| Python | 3.10 | 3.10–3.12 (3.14 works for the CPU path) |
| Browser | Chrome/Edge 116+ | Chrome 138+ (on-device Translator API) |
| RAM | 8 GB | 16 GB |
| GPU | none (CPU streaming captions + OPUS-MT) | NVIDIA 4 GB+ VRAM (HY-MT translation for all en/zh/bn directions, accuracy mode) |
| Disk | 1 GB | 4 GB |

## 1. One-click launcher (recommended)

Windows: double-click `Start_Subtitle_Translator.bat`. macOS: double-click `Start_Subtitle_Translator.command`.

The launcher creates `backend/venv`, installs `requirements.txt`, installs `requirements-gpu.txt` when `nvidia-smi` is
found, starts the server and opens `http://127.0.0.1:8765/health`.

On first start the server downloads:

| Asset | Size | Location |
|---|---|---|
| Zipformer streaming models en / zh / bn | 68 / 154 / 87 MB | `backend/data/models/asr/` |
| Whisper-tiny spoken-language-ID | 120 MB | `backend/data/models/asr/sherpa-onnx-whisper-tiny/` |
| OPUS-MT models (converted to CTranslate2 int8 on first use) | ~80 MB each | `backend/data/models/ct2/` |
| llama.cpp CUDA build (GPU only) | 250 MB | `backend/bin/llama/` |
| HY-MT1.5-1.8B Q8_0 GGUF (GPU only) | 1.9 GB | `backend/data/models/mt/` |

## 2. Manual install

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# NVIDIA GPU:
pip install -r requirements-gpu.txt --extra-index-url https://download.pytorch.org/whl/cu128
# CPU only (needed once to convert OPUS-MT checkpoints):
pip install torch --index-url https://download.pytorch.org/whl/cpu
python run.py
```

Check `http://127.0.0.1:8765/health/json`: `device` should be `cuda` on a GPU machine and `mt_engines` should list
`hymt` and `opus`.

### GPU notes

- No CUDA Toolkit install is needed: the CUDA runtime DLLs bundled with the torch wheel are placed on the PATH of the
  `llama-server` child process automatically.
- `llama-cpp-python` is **not** used: its prebuilt Windows wheels crash on CPUs without AVX-512 and older wheels predate
  the HunYuan architecture. The backend runs the official `llama-server.exe` instead and calls it over HTTP on localhost.
- To use a smaller/faster translation model: `set SUBTITLE_MT__HYMT_FILE=HY-MT1.5-1.8B-Q4_K_M.gguf` (downloaded on demand).

## 3. Browser extension

1. `chrome://extensions` → **Developer mode** → **Load unpacked** → `extension/`.
2. Pin the icon. Open the **Options** page to choose target languages (English/Chinese/Bengali/…).
3. Optional, Chrome 138+: in the popup click **Prepare on-device translation** to download Chrome's language packs.
   The extension then translates live captions in the browser without the backend hop.

## 4. Live captions checklist

- The video tab must be audible (tab capture takes the tab's audio; you keep hearing it).
- Click the icon → **Start Live Captions**, or press `Alt+L`. Choosing the spoken language explicitly gives the fastest
  start; *Auto-detect* starts immediately with English and switches after ~2.5 s of speech if needed.
- Grey italic text is the in-progress caption; white text is final; translated lines appear underneath within ~0.5 s.
- A "No audio" notice after 10 s means nothing reaches the capture (paused video or DRM site).

## 5. Optional cloud providers

Paste keys in the Options page (stored locally, sent only to the local backend):

| Purpose | Provider | Notes |
|---|---|---|
| Streaming speech | Gladia (`gladia_api_key`) or ElevenLabs Scribe v2 (`elevenlabs_api_key`) | both support Bengali in real time |
| Translation | Google Cloud Translation v2 key or Azure Translator key + region | ~100–300 ms, all pairs |
| Refinement | Groq or Gemini key, then enable "Refine with AI" | async second pass, ≤0.8 s deadline; skipped for Bengali locally |
| Local refinement | Ollama with a small model (`ollama pull qwen3:4b`) | optional; slower than the cloud options |

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| `device: cpu` on a GPU laptop | run the launcher again (installs CUDA torch), or `pip install -r requirements-gpu.txt --extra-index-url https://download.pytorch.org/whl/cu128` |
| `hymt` missing from `/health/json` | GPU not detected, or first download still running; check the server window. OPUS-MT still handles en↔zh and bn→en |
| Popup says "Failed: … tabCapture" | click the icon on a normal web page (not `chrome://` pages), and reload the page after installing the extension |
| Captions appear but no translation | target language equals the spoken language, or the backend is not running |
| Bengali translation slow (>0.6 s) | use Chrome on-device translation (popup → Prepare), or the Q4_K_M model |
| Port 8765 busy | `Stop_Subtitle_Translator.bat`, or `python run.py --port 8766` and update the server URL in Options |

## Who can reach the backend

The backend binds `127.0.0.1` only (`run.py` refuses any other `--host`). Every HTTP request and WebSocket handshake
that carries an `Origin` header must come from the extension (`chrome-extension://<id>`) or the backend's own page
(`http://127.0.0.1:<port>`, the `/debug` console); anything else gets 403 and CORS never reflects it. The id of the
unpacked extension in `../extension` is derived from its folder path automatically. If you load the extension from
another folder or install it from a store, add its id (shown on `chrome://extensions`):

```
SUBTITLE_SERVER__EXTENSION_IDS='["abcdefghijklmnopabcdefghijklmnop"]'
```

Translation of page subtitles is off on every tab until you turn on **Translate subtitles on this tab** in the popup;
only then does the extension open a connection for that tab. Live captions (Alt+L / popup) work independently.
