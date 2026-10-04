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
| `sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20` (Mandarin, default) | Apache-2.0 | [model card](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20); converted from [pfluo/k2fsa-zipformer-chinese-english-mixed](https://huggingface.co/pfluo/k2fsa-zipformer-chinese-english-mixed) (Apache-2.0). Mandarin with English words mixed in |
| `sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09` | Apache-2.0 (upstream) | the converted repo declares none; its files come from [alphacep/vosk-model-small-streaming-bn](https://huggingface.co/alphacep/vosk-model-small-streaming-bn), Apache-2.0 |
| `sherpa-onnx-whisper-tiny` (language ID) | MIT | OpenAI Whisper weights: "Whisper's code and model weights are released under the MIT License", Copyright (c) 2022 OpenAI ([openai/whisper](https://github.com/openai/whisper)) |
| `sherpa-onnx-online-punct-en-2024-08-06` (English punctuation and casing) | Apache-2.0 | from [frankyoujian/Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) (repository and [Hugging Face model](https://huggingface.co/frankyoujian/Edge-Punct-Casing) both Apache-2.0); paper: "A lightweight and efficient punctuation and word casing prediction model for on-device streaming ASR" ([arXiv:2407.13142](https://arxiv.org/abs/2407.13142)); [punctuation-models release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/punctuation-models) |
| `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30` (Mandarin, optional) | **not declared** | only with `--mandarin-large --accept-mandarin-large-terms`; see below |

### The optional large Mandarin model declares no license

Its [model card](https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30) says it was
converted from `yuekai/icefall-asr-multi-zh-hans-zipformer-large` (gated; its terms could not be read) and was
trained on the icefall multi_zh-hans corpus (14,106 h), which includes
[WenetSpeech](https://wenet-e2e.github.io/WenetSpeech/) (released for non-commercial purposes). It was the default
before Phase 3; it is now opt-in. `scripts/download_models.py --mandarin-large` prints a notice and downloads
nothing unless `--accept-mandarin-large-terms` is also given; the backend never downloads it; it is used only with
`SUBTITLE_ASR__ZH_MODEL=large`. Do not redistribute it. Why someone might want it (CER, lower is better):

| Model (declared license) | Size (int8) | CER aishell-1 test / WenetSpeech test_net | |
|---|---|---|---|
| zipformer-bilingual-zh-en-2023-02-20 (Apache-2.0) | ~198 MB | 3.04 / 8.97 ([upstream card](https://huggingface.co/pfluo/k2fsa-zipformer-chinese-english-mixed)) | **default**; also handles English words inside Mandarin speech |
| zipformer-zh-int8-2025-06-30 (none) | ~161 MB | 1.91 / 8.54 ([icefall RESULTS.md](https://github.com/k2-fsa/icefall/blob/master/egs/multi_zh-hans/ASR/RESULTS.md)) | optional, `--mandarin-large` |
| [zipformer-multi-zh-hans-2023-12-12](https://huggingface.co/k2-fsa/sherpa-onnx-streaming-zipformer-multi-zh-hans-2023-12-12) (Apache-2.0) | ~72 MB | 3.63 / 9.66 (unmerged [icefall PR 1369](https://github.com/k2-fsa/icefall/pull/1369), larger chunk than the export) | not used: higher error rate than the bilingual model, trained on the same WenetSpeech data |
| zipformer-zh-14M-2023-02-23 (Apache-2.0) | ~22 MB encoder | not published | not used: 14M parameters, trained on WenetSpeech |

On the two Mandarin sample clips the bilingual and the large model produce the same words except filler particles
(啊, 呢); on the bilingual model's own code-switched samples the large model garbles the English words
(`docs/asr-input-quality.md`).

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

## Media in this repository

`docs/screenshot.png` (and the demo GIF) show a 30-second excerpt (0:09-0:39) of **"ScienceCasts: The Zero Gravity
Coffee Cup"** by ScienceAtNASA (NASA, 12 July 2013), from
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:ScienceCasts-_The_Zero_Gravity_Coffee_Cup.webm). Public
domain in the United States: "This file is in the public domain in the United States because it was solely created
by NASA." NASA's logo, visible in the video's corner, is not covered by that and its use is restricted
(14 CFR 1221); this project is not endorsed by NASA. The subtitles drawn over it are this project's own output.

`docs/demo-youtube.gif` shows 9 seconds of **"10,000 রাজভোগের Order | Ora Char Jon | Movie Scene"** as played on
YouTube by the channel Bengali Movies with English Subtitle (https://www.youtube.com/watch?v=-tpVpbIxFmI), with
the YouTube page around it. The film and the video belong to their rights holders; they are not covered by this
repository's MIT License and this project is not endorsed by them or by YouTube. The excerpt is shown only to
demonstrate the software; the subtitles drawn over it are this project's own output. Its audio is not in the
repository.

### Clips of the latency benchmark (`docs/latency.md`, 2026-10-03)

Six clips, two per spoken language, each from a Wikimedia Commons file page whose license was read there on
2026-10-03 (the license template on the page; for the two YouTube imports also the "Creative Commons Attribution
license (reuse allowed)" mark on the YouTube page). None of the clips is in the repository; the harness played
local copies. SHA-256 is of the Commons file as downloaded. Only the fastest clip's picture appears here, as
`docs/demo-fastest.gif`; the subtitles drawn over it are this project's own output, and the people and
organisations in these clips have nothing to do with this project and do not endorse it.

| Clip | Source | Author / channel | License (on the file page) | Excerpt used |
|---|---|---|---|---|
| English, NASA ScienceCasts | [ScienceCasts: The Zero Gravity Coffee Cup](https://commons.wikimedia.org/wiki/File:ScienceCasts-_The_Zero_Gravity_Coffee_Cup.webm) (12 July 2013; SHA-256 `6f36a182…e00a76`) | ScienceAtNASA (NASA) | `{{PD-NASA}}`: public domain in the United States, solely created by NASA. NASA's logo is not covered (see above) | 0:09-0:39.5 (30.5 s) |
| English, VOA Helix report | [VOA's Matt Dibble reports on Helix electric aircraft – VOA News](https://commons.wikimedia.org/wiki/File:VOA%E2%80%99s_Matt_Dibble_reports_on_Helix_electric_aircraft_%E2%80%93_VOA_News.webm) (5 July 2024; SHA-256 `3c945f9a…7a7515`) | Voice of America | `{{PD-USGov-VOA}}`: a work of the U.S. federal government (Voice of America), public domain in the United States | whole file (89.2 s) |
| Mandarin, VOA Norway news | [2011-07-26 美国之音新闻: 挪威部长称警方反应精彩](https://commons.wikimedia.org/wiki/File:2011-07-26_%E7%BE%8E%E5%9B%BD%E4%B9%8B%E9%9F%B3%E6%96%B0%E9%97%BB-_%E6%8C%AA%E5%A8%81%E9%83%A8%E9%95%BF%E7%A7%B0%E8%AD%A6%E6%96%B9%E5%8F%8D%E5%BA%94%E7%B2%BE%E5%BD%A9.webm) (26 July 2011; SHA-256 `46d7da78…b6196`) | 美国之音中文网 (Voice of America, Mandarin service) | `{{PD-USGov-VOA}}`, as above | whole file (72.0 s) |
| Mandarin, VOA Taiwan typhoon | [台湾准备迎战强台风](https://commons.wikimedia.org/wiki/File:%E5%8F%B0%E6%B9%BE%E5%87%86%E5%A4%87%E8%BF%8E%E6%88%98%E5%BC%BA%E5%8F%B0%E9%A3%8E.webm) (12 July 2013; SHA-256 `e474f72c…d93ca1`) | 美国之音中文网 (Voice of America, Mandarin service) | `{{PD-USGov-VOA}}`, as above; license reviewed on Commons 2019-01-14 | whole file (40.1 s) |
| Bengali, Maasranga News | [চিকিৎসার জন্য ঢাকায় তরিকুল](https://commons.wikimedia.org/wiki/File:%E0%A6%9A%E0%A6%BF%E0%A6%95%E0%A6%BF%E0%A7%8E%E0%A6%B8%E0%A6%BE%E0%A6%B0_%E0%A6%9C%E0%A6%A8%E0%A7%8D%E0%A6%AF_%E0%A6%A2%E0%A6%BE%E0%A6%95%E0%A6%BE%E0%A6%AF%E0%A6%BC_%E0%A6%A4%E0%A6%B0%E0%A6%BF%E0%A6%95%E0%A7%81%E0%A6%B2.webm) (9 July 2018, from https://www.youtube.com/watch?v=DP7BoVgOJXI; SHA-256 `dccb6bc4…240375`) | Maasranga News (Maasranga Television, Bangladesh) | **CC BY 3.0** ([license](https://creativecommons.org/licenses/by/3.0/)): `{{YouTube CC-BY|Maasranga News}}` on the file page and the Creative Commons Attribution mark on the YouTube video | 0:00-1:00 (60 s) |
| Bengali, Wikitongues Sanjoy | [WIKITONGUES: Sanjoy speaking Bengali](https://commons.wikimedia.org/wiki/File:WIKITONGUES-_Sanjoy_speaking_Bengali.webm) (13 May 2016, from https://www.youtube.com/watch?v=5bYNuCOdd_Q; SHA-256 `aaca51ea…95e60a`) | Wikitongues | **CC BY 3.0** ([license](https://creativecommons.org/licenses/by/3.0/)): `{{YouTube CC-BY|Wikitongues}}`, license reviewed on Commons 2016-06-24 | 0:00-1:15 (75 s) |

Attribution for the two CC BY 3.0 clips: "চিকিৎসার জন্য ঢাকায় তরিকুল" by Maasranga News and "WIKITONGUES: Sanjoy
speaking Bengali" by Wikitongues, both licensed under CC BY 3.0; used as excerpts, transcoded to 16 kHz mono
audio for the measurement, no other change; the subtitles over them are this project's output.

