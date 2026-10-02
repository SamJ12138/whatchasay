# Third-party notices

This repository contains source code only. The models and binaries below are downloaded to your machine by
`scripts/download_models.py` (or, for speech and OPUS-MT models, on first use) and are not redistributed with
this project. Each stays under its own license. Checked 2026-10-02 against the model cards and license files
linked below.

## Translation: OPUS-MT (default engine, CPU)

The OPUS-MT models are converted locally to CTranslate2 int8 (a format change only).

| Model | License | Notes |
|---|---|---|
| [Helsinki-NLP/opus-mt-zh-en](https://huggingface.co/Helsinki-NLP/opus-mt-zh-en) | **CC-BY-4.0** | Attribution: Jörg Tiedemann and Santhosh Thottingal, "OPUS-MT – Building open translation services for the World", EAMT 2020; Language Technology Research Group, University of Helsinki. Converted to CTranslate2 int8. |
| [Helsinki-NLP/opus-mt-en-zh](https://huggingface.co/Helsinki-NLP/opus-mt-en-zh) | Apache-2.0 | used with its `>>cmn_Hans<<` target token |
| [Helsinki-NLP/opus-mt-bn-en](https://huggingface.co/Helsinki-NLP/opus-mt-bn-en) | Apache-2.0 | |
| [shhossain/opus-mt-en-to-bn](https://huggingface.co/shhossain/opus-mt-en-to-bn) | Apache-2.0 | community fine-tune of [Helsinki-NLP/opus-mt-en-inc](https://huggingface.co/Helsinki-NLP/opus-mt-en-inc) (Apache-2.0) |

Other language pairs (`settings.translation.opus_models`) are fetched from the Helsinki-NLP organisation on first
use; each model card states its license (most are Apache-2.0 or CC-BY-4.0).

## Speech recognition: sherpa-onnx (Apache-2.0)

[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (k2-fsa, Apache-2.0) runs the streaming models. Models come
from its [`asr-models` release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models).

| Model | License | Notes |
|---|---|---|
| `sherpa-onnx-streaming-zipformer-en-2023-06-26` | Apache-2.0 | model card README |
| `sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09` | Apache-2.0 (upstream) | the converted repo declares none; its files come from [alphacep/vosk-model-small-streaming-bn](https://huggingface.co/alphacep/vosk-model-small-streaming-bn), Apache-2.0 |
| `sherpa-onnx-whisper-tiny` (language ID) | MIT | OpenAI Whisper weights: "Whisper's code and model weights are released under the MIT License", Copyright (c) 2022 OpenAI ([openai/whisper](https://github.com/openai/whisper)) |
| `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30` (Mandarin) | **not declared** | see below |

**The Mandarin model declares no license.** Its [model card](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30)
says it was converted from `yuekai/icefall-asr-multi-zh-hans-zipformer-large` (gated; its terms could not be
read) and was trained on the icefall multi_zh-hans corpus (14,106 h), which includes
[WenetSpeech](https://wenet-e2e.github.io/WenetSpeech/) (released for non-commercial purposes). It stays the
default because no streaming Mandarin model with a declared permissive license is comparable in size and error
rate (CER, lower is better):

| Candidate (declared license) | Size (int8) | CER aishell-1 test / WenetSpeech test_net | Verdict |
|---|---|---|---|
| current: zipformer-zh-int8-2025-06-30 (none) | ~161 MB | 1.91 / 8.54 ([icefall RESULTS.md](https://github.com/k2-fsa/icefall/blob/master/egs/multi_zh-hans/ASR/RESULTS.md)) | default |
| [zipformer-multi-zh-hans-2023-12-12](https://huggingface.co/k2-fsa/sherpa-onnx-streaming-zipformer-multi-zh-hans-2023-12-12) (Apache-2.0) | ~72 MB | 3.63 / 9.66 (unmerged [icefall PR 1369](https://github.com/k2-fsa/icefall/pull/1369), larger chunk than the export) | about 1.9x the errors on aishell-1; same WenetSpeech data |
| [zipformer-bilingual-zh-en-2023-02-20](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20) (Apache-2.0) | ~198 MB | 3.04 / 8.97 ([upstream card](https://huggingface.co/pfluo/k2fsa-zipformer-chinese-english-mixed)) | about 1.6x the errors on aishell-1; "internal" training data |
| zipformer-zh-14M-2023-02-23 (Apache-2.0) | ~22 MB encoder | not published | 14M parameters, trained on WenetSpeech |

If you need a model with a declared license, the bilingual zh-en model is the closest; do not redistribute the
default Mandarin model.

## Optional: llama.cpp (only with `--hymt`)

`llama-server` from [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) release b10909 (Windows CUDA
build), used only when the HY-MT engine is enabled.

```
MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Optional: Tencent HY-MT1.5-1.8B (only with `--hymt --accept-hymt-license`)

[tencent/HY-MT1.5-1.8B-GGUF](https://huggingface.co/tencent/HY-MT1.5-1.8B-GGUF) is licensed under the
**Tencent HY Community License Agreement** ([full text](https://huggingface.co/tencent/HY-MT1.5-1.8B/blob/main/License.txt)),
which is not an open-source license. The download script shows this summary and downloads nothing until you
pass `--accept-hymt-license`:

- The agreement does not apply in the European Union, the United Kingdom or South Korea, and the model, its output
  and results must not be used there (§1(l), §5(c)).
- The Acceptable Use Policy (Exhibit A) applies; anyone who passes the model or its output on must pass its use
  restrictions on (§5(a)).
- The model and its output must not be used to improve any other AI model (§5(b)).
- Services with more than 100 million monthly active users need a separate license from Tencent (§4).
- Redistribution requires a copy of the agreement and this notice (§3): "Tencent HY is licensed under the Tencent
  HY Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved. The trademark rights of “Tencent
  HY” are owned by Tencent or its affiliate."

This project does not redistribute the model.
