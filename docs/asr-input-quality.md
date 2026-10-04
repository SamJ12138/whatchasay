# Does the translator need punctuation and casing on ASR text?

Phase 3, Batch A. Question: the streaming recognizers emit no punctuation, and the English one emits UPPERCASE.
Does that text translate worse with OPUS-MT than the same words cased and punctuated? If so, restore it before
translation on the audio path.

**Answer: yes for English, no for Mandarin and Bengali.** The audio path now restores English with sherpa-onnx's
online punctuation model (`asr/punctuation.py`; p50 4.4 ms, p95 6.2 ms per sentence on one CPU thread, against a
50 ms budget). Mandarin and Bengali text goes to the translator as the recognizer produced it.

## Method

`backend/scripts/asr_input_quality.py` (reproduces the table below):

1. The three languages' sample WAVs (`test_wavs/0.wav` and `1.wav` from the en, zh and bn Zipformer archives; the
   "three sample WAVs" of the e2e runs are the `0.wav` files), streamed through the real Zipformer recognizer in
   40 ms frames with the configured endpoint rules, the way `/ws/asr` does it. Each final, after
   `TextNormalizer` (what `pipeline.translate_cue` gives the engine), is the **raw ASR** row: what reached the
   translator before this batch. Mandarin used the model that was the default then
   (`zipformer-zh-int8-2025-06-30`).
2. **hand-restored**: the same words, cased and punctuated by hand (English: the punctuation of the source text,
   Hawthorne's *The Scarlet Letter*; Chinese: full-width commas and stops; Bengali: comma and danda). No word
   changed.
3. **model-restored** (English only): `sherpa-onnx-online-punct-en-2024-08-06` (int8, lower-cased input).
4. Each row translated with OPUS-MT (CTranslate2 int8, CPU, the D1 decoding settings) into the other two
   languages; zh->bn and bn->zh pivot through English.

Run 2026-10-02, i9-13900H, Windows 11, Python 3.10. Translation quality is judged by reading; there is no
reference translation for these clips, so no BLEU.

## Results

| WAV | input | text given to OPUS-MT | translations |
|---|---|---|---|
| en/0.wav | raw ASR | AFTER EARLY NIGHTFALL THE YELLOW LAMPS WOULD LIGHT UP HERE AND THERE THE SQUALID QUARTER OF THE BROTHELS | zh: 夜幕降临后,黄灯会升起 这里和有质量的胶片<br>bn: ছোট্ট নজর দেয়ালে ইস্ল্যাপস এখানে চলে যাবে এবং সেখানে ব্রোথের স্কোয়াড কোয়ার্টার |
| en/0.wav | hand-restored | After early nightfall the yellow lamps would light up, here and there, the squalid quarter of the brothels. | zh: 夜幕降临后,黄灯会照亮 这里和那里 妓院的肮脏街区<br>bn: রাতের শুরুতে হলদে আলো জ্বালিয়ে দিত, এখানে এবং সেখানেই বর্তোলদের স্কোয়ালিড কোয়ার্টার। |
| en/0.wav | model-restored | After early nightfall, the yellow lamps would light up here and there the squalid quarter of the brothels | zh: 黄灯在夜幕降临后,会点亮这里 还有妓院的肮脏街区<br>bn: রাতের শুরুর দিকে, হলুদ আলো এখানে আলোকিত হবে এবং সেখানে ভ্রাতৃসমাজের বর্গাঞ্চলীয় চতুর্থাংশ। |
| en/1.wav | raw ASR | GOD AS A DIRECT CONSEQUENCE OF THE SIN WHICH MAN THUS PUNISHED HAD GIVEN HER A LOVELY CHILD WHOSE PLACE WAS ON THAT SAME DISHONORED BOSOM TO CONNECT HER | zh: 上帝是直接的原因 一个人打倒她 一个可爱的孩子 谁在同一个地方<br>bn: ঈশ্বর যখন সেই পুরুষের মৃত্যুর এক অদ্বিতীয় ঘটনার কথা বলেছিলেন, যাঁরা থোসাসকে হত্যা করেছিল তাদের ভালবাসাপূর্ণ বালিকা দিয়েছিল |
| en/1.wav | hand-restored | God, as a direct consequence of the sin which man thus punished, had given her a lovely child, whose place was on that same dishonored bosom, to connect her | zh: 上帝,作为罪孽的直接后果 男人这样惩罚, 给了她一个可爱的孩子<br>bn: ঈশ্বর, যে পাপের কারণে মানুষকে শাস্তি দেওয়া হয়েছিল তার সরাসরি পরিণতি হিসেবে তাকে একটা সুন্দর শিশু প্রদান করেছিলেন, যার অবস্থান ছিল একই অসম্মানজনক বুকের ওপর, যাতে তিনি তাকে সংযুক্ত করতে পারেন। |
| en/1.wav | model-restored | God as a direct consequence of the sin, which man thus punished, had given her a lovely child whose place was on that same dishonored bosom to connect her | zh: 上帝是罪孽的直接后果 男人这样惩罚她 给了她一个可爱的孩子 她的家在同一个污秽的怀里<br>bn: ঈশ্বর সেই পাপের সরাসরি পরিণতি হিসাবে, যা মানুষ শাস্তি দেয়, তাকে এক সুন্দর শিশু প্রদান করেছিলেন যার অবস্থান ছিল একই অসম্মানজনক বুকে তার সাথে সংযুক্ত করা। |
| en/1.wav | raw ASR | PARENT FOR EVER WITH THE RACE AND DESCENT OF MORTALS AND TO BE FINALLY A BLESSED SOUL IN HEAVEN | zh: 永远与暴徒和嗜好并最终在高山中成为<br>bn: মোহনা ও যন্ত্রণার সঙ্গে লড়াই করার জন্য প্রস্তুত থাকুন এবং হিমবাহের মধ্যে এক লজ্জাজনক মনোভাব গড়ে তুলুন |
| en/1.wav | hand-restored | parent for ever with the race and descent of mortals, and to be finally a blessed soul in heaven. | zh: 永远是人类的祖先和后裔 最终成为天堂里一个幸福的灵魂<br>bn: চিরদিনের জন্য পিতামাতা, মানুষদের বংশ এবং অবশেষে স্বর্গে এক আশীর্বাদজনক প্রাণ হতে পারবেন। |
| en/1.wav | model-restored | Parent for ever with the race and descent of mortals, and to be finally a blessed soul in heaven. | zh: 永远是人类的父子关系 最终成为天堂里一个幸福的灵魂<br>bn: চিরদিনের জন্য পিতামাতা, মানুষের বংশ এবং অবশেষে স্বর্গে এক পরমদেশী প্রাণ হতে হবে। |
| zh/0.wav | raw ASR | 对我做了介绍啊那么我想说的是呢大家如果对我的研究感兴趣呢 | en: What I'm trying to say is, if you're interested in my research,<br>bn: আমি যা বলতে চাইছি তা হল, যদি তুমি আমার গবেষণার ব্যাপারে আগ্রহী হও, |
| zh/0.wav | hand-restored | 对我做了介绍啊。那么我想说的是呢，大家如果对我的研究感兴趣呢， | en: I was introduced. So what I'm saying is, if you're interested in my research,<br>bn: আমাকে চালু করা হয়েছিল, তাই আমি যা বলছি তা হল, যদি আপনি আমার গবেষণা সম্পর্কে আগ্রহী হন, |
| zh/1.wav | raw ASR | 重点呢想谈三个问题 | en: The point is to talk about three issues.<br>bn: মূল বিষয়টা হল তিনটি বিষয় নিয়ে কথা বলা। |
| zh/1.wav | hand-restored | 重点呢，想谈三个问题。 | en: The focus is on three issues.<br>bn: তিনটি বিষয় নিয়ে মনোযোগ কেন্দ্রীভূত করা হচ্ছে। |
| zh/1.wav | raw ASR | 首先呢就是这一轮全球金融动荡的表现 | en: The first is this round of global financial turmoil.<br>bn: প্রথমটি হল বিশ্ব অর্থনৈতিক অস্থিরতা। |
| zh/1.wav | hand-restored | 首先呢，就是这一轮全球金融动荡的表现。 | en: First of all, this round of global financial turmoil.<br>bn: সর্বপ্রথম, বিশ্ব অর্থনৈতিক অস্থিরতার এই গোল। |
| bn/0.wav | raw ASR | এদিকে মিঃ তুগলক অরবিন কেজরিওয়াল এবং তার দল আপনায় ঘণ্টায় টুইট করছেন ক্রেডিটের জন্য | en: Meanwhile, Mr. Tugkol Orbin Kejriwal and his team are tweeting in their hours for credit.<br>zh: Tugkol Orbin Kejriwal先生和他的团队在微博上发推特, |
| bn/0.wav | hand-restored | এদিকে, মিঃ তুগলক অরবিন কেজরিওয়াল এবং তার দল আপনায় ঘণ্টায় টুইট করছেন ক্রেডিটের জন্য। | en: Meanwhile, Mr. Tugkok Orbin Kejriwal and his team are tweeting for the hour in their own country.<br>zh: Tugkok Orbin Kejriwal先生和他的团队在他们自己的国家里发了一小时的推特。 |
| bn/1.wav | raw ASR | তোমার দেশ তোমার জন্য কি করতে পারে তা জিজ্ঞেস করো না বরং তুমি তোমার দেশের জন্য কি করতে পারো তা জিজ্ঞেস করুম | en: Do not ask what your country can do for you, but ask me what you can about doing for your nation.<br>zh: 不要问你们的国家能为你做什么,但请问我你能为你们的民族做些什么。 |
| bn/1.wav | hand-restored | তোমার দেশ তোমার জন্য কি করতে পারে তা জিজ্ঞেস করো না, বরং তুমি তোমার দেশের জন্য কি করতে পারো তা জিজ্ঞেস করুম। | en: Don't ask what your country can do for you, but I'll ask you what to do about your land.<br>zh: 别问国家能为你做什么 但我会问你怎么处理你的土地 |


## Reading the table

**English: raw is clearly worse.** OPUS-MT reads UPPERCASE words as names or acronyms. en->zh turned "the squalid
quarter of the brothels" into 有质量的胶片 ("quality film") and "a blessed soul in heaven" into 在高山中 ("in the high
mountains"); en->bn produced unrelated sentences for all three finals (the first one starts "a small look at the
wall"). With the hand-restored text both directions keep the meaning. The model-restored text recovers nearly all
of that: its punctuation is not the author's (it misses "here and there," as an aside), but the casing does most of
the work. A lower-cased copy alone (no punctuation) also recovers most of it in a side run, so casing matters more
than punctuation for OPUS-MT.

**Mandarin: no consistent gain.** zh->en is about as good either way: hand punctuation recovers the dropped
"对我做了介绍啊" ("I was introduced") in 0.wav, and the other two finals mean the same either way; zh->bn changed
wording without becoming better. The Mandarin recognizers already split sentences at pauses, which is where most of the
punctuation would go. The bilingual zh-en model (the new default, below) splits 0.wav into "对我做了介绍" / "那么我想
说的是..." on its own, and zh->en gives "He introduced me." for the first part.

**Bengali: no gain, sometimes worse.** bn->en and bn->zh with a danda and comma changed the clause after the comma
("for credit" became "in their own country"; "ask what you can do" became "I'll ask you what to do"). There is no
Bengali punctuation model in sherpa-onnx either.

## Decision

- English finals and partials are restored before they are sent (`StreamingASRSession` `restore_text` hook,
  `asr/punctuation.py`); the final keeps the recognizer's text as `raw_text`. Partials are restored too so the
  caption does not change case when it becomes final; only finals are logged (`punctuate` stage).
- Model: `sherpa-onnx-online-punct-en-2024-08-06`, a CNN-BiLSTM from
  [Edge-Punct-Casing](https://github.com/frankyoujian/Edge-Punct-Casing) (Apache-2.0, repository and Hugging Face
  model card), 7.5 MB int8 (the archive also holds a 28 MB fp32 copy, deleted after download). Latency on one CPU
  thread: p50 4.4 ms, p95 6.2 ms over 100 calls on 5 sentences (fp32: p50 13.5 ms). Downloaded by
  `scripts/download_models.py`; the backend never downloads it, and without it English passes through unchanged
  with one WARNING line.
- `SUBTITLE_ASR__PUNCTUATION=false` turns it off.
- Mandarin and Bengali: no step. A Chinese punctuation model exists (CT-Transformer zh-en, 72 MB int8, converted
  from FunASR); it was not evaluated because hand punctuation, its upper bound, showed nothing to gain.

## Limits this leaves

- The bilingual Mandarin model writes English words inside Mandarin speech in UPPERCASE, and a whole English
  sentence spoken in a Mandarin session is labelled `zh`. On its own sample (`test_wavs/0.wav` of the bilingual
  archive), "昨天是 MONDAY" became "Yesterday, it was MONDAY." and "IS LIBR THE DAY AFTER TOMORROW" came back from
  zh->en unchanged. The restorer only runs on `en` text.
- Utterances over 10 s are still cut by the endpoint rule (en/1.wav: "...to connect her" / "parent for ever..."),
  and each piece is translated alone.
- These are six short read-speech clips; they show the size of the casing effect, not translation quality in
  general. Per-direction quality notes are in `docs/limitations.md`.
