# Training Your Own Post-Editor Model

This guide explains how to use your accumulated corrections to fine-tune a custom post-editor model for better subtitle translations.

## Overview

The translation pipeline logs all user corrections to `data/corrections.jsonl`. Over time, this creates a training dataset of:
- Original source text
- Machine translation output
- Your human-corrected version

This data can be used to fine-tune the Ollama post-editor model using QLoRA (Quantized Low-Rank Adaptation), making it learn your preferred style and terminology.

## Prerequisites

- At least 100 corrections accumulated (500+ recommended)
- NVIDIA GPU with 8GB+ VRAM (for training)
- Python 3.10+ with CUDA support
- ~20GB free disk space

## Data Format

Corrections are stored in JSONL format:

```jsonl
{"source": "Bonjour le monde", "source_lang": "fr", "mt_output": {"en": "Hello world", "zh": "你好世界"}, "correction": {"en": "Hello, world!", "zh": "你好，世界！"}, "timestamp": "2024-01-15T10:30:00Z"}
{"source": "Comment ça va?", "source_lang": "fr", "mt_output": {"en": "How is it going?", "zh": "怎么样？"}, "correction": {"en": "How are you?", "zh": "你好吗？"}, "timestamp": "2024-01-15T10:31:00Z"}
```

## Step 1: Export Training Data

```bash
cd subtitle-translator/backend

# Export corrections from database
python -c "
from app.cache.translation_memory import TranslationMemory
import json

tm = TranslationMemory('data/translation_memory.db')
corrections = tm.export_corrections()

with open('data/corrections_export.jsonl', 'w', encoding='utf-8') as f:
    for c in corrections:
        f.write(json.dumps(c, ensure_ascii=False) + '\n')

print(f'Exported {len(corrections)} corrections')
"
```

## Step 2: Prepare Training Dataset

Convert corrections to training format:

```python
# scripts/prepare_training_data.py

import json
from pathlib import Path

def prepare_training_data(input_path: str, output_path: str):
    """Convert corrections to Ollama fine-tuning format."""
    
    training_examples = []
    
    with open(input_path, 'r', encoding='utf-8') as f:
        for line in f:
            data = json.loads(line)
            
            # Create training example for each correction
            source = data['source']
            source_lang = data.get('source_lang', 'unknown')
            mt_en = data['mt_output'].get('en', '')
            mt_zh = data['mt_output'].get('zh', '')
            corr_en = data['correction'].get('en', mt_en)
            corr_zh = data['correction'].get('zh', mt_zh)
            
            # System prompt (same as production)
            system_prompt = """You are a professional subtitle post-editor. Your task is to refine machine translations for natural, readable subtitles.

Rules:
1. PRESERVE the exact meaning - never add or remove information
2. Use natural, conversational language appropriate for subtitles
3. Keep translations concise (max 42 chars for English, 22 for Chinese per line)
4. Output valid JSON only"""

            # User message
            user_message = f"""Source ({source_lang}): {source}

Machine Translation:
EN: {mt_en}
ZH: {mt_zh}

Refine these translations for natural subtitles. Output JSON:
{{"en": {{"lines": [...], "single_line": "..."}}, "zh": {{"lines": [...], "single_line": "..."}}}}"""

            # Assistant response (the correction)
            assistant_response = json.dumps({
                "en": {"lines": [corr_en], "single_line": corr_en},
                "zh": {"lines": [corr_zh], "single_line": corr_zh}
            }, ensure_ascii=False)
            
            training_examples.append({
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_response}
                ]
            })
    
    # Write training data
    with open(output_path, 'w', encoding='utf-8') as f:
        for example in training_examples:
            f.write(json.dumps(example, ensure_ascii=False) + '\n')
    
    print(f"Prepared {len(training_examples)} training examples")
    return len(training_examples)

if __name__ == "__main__":
    prepare_training_data(
        'data/corrections_export.jsonl',
        'data/training_data.jsonl'
    )
```

## Step 3: Install Training Dependencies

```bash
pip install transformers datasets peft bitsandbytes accelerate trl
```

## Step 4: Fine-tune with QLoRA

```python
# scripts/train_qlora.py

import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from trl import SFTTrainer

# Configuration
BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"  # Same as Ollama qwen2.5:7b
OUTPUT_DIR = "./subtitle-editor-lora"
DATA_PATH = "data/training_data.jsonl"

def train():
    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, trust_remote_code=True)
    tokenizer.pad_token = tokenizer.eos_token
    
    # Quantization config (4-bit for memory efficiency)
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True
    )
    
    # Load base model with quantization
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True
    )
    model = prepare_model_for_kbit_training(model)
    
    # LoRA configuration
    lora_config = LoraConfig(
        r=16,  # Rank
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]
    )
    
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    
    # Load dataset
    dataset = load_dataset("json", data_files=DATA_PATH, split="train")
    
    # Format function for chat template
    def format_example(example):
        messages = example["messages"]
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        return {"text": text}
    
    dataset = dataset.map(format_example)
    
    # Training arguments
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        num_train_epochs=3,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        learning_rate=2e-4,
        warmup_steps=100,
        logging_steps=10,
        save_steps=100,
        save_total_limit=3,
        fp16=True,
        optim="paged_adamw_8bit"
    )
    
    # Trainer
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        tokenizer=tokenizer,
        dataset_text_field="text",
        max_seq_length=2048
    )
    
    # Train
    print("Starting training...")
    trainer.train()
    
    # Save
    trainer.save_model(OUTPUT_DIR)
    print(f"Model saved to {OUTPUT_DIR}")

if __name__ == "__main__":
    train()
```

Run training:
```bash
python scripts/train_qlora.py
```

## Step 5: Convert to Ollama Format

After training, convert the LoRA weights for Ollama:

```bash
# Merge LoRA weights with base model
python -c "
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

base = AutoModelForCausalLM.from_pretrained('Qwen/Qwen2.5-7B-Instruct', torch_dtype=torch.float16)
model = PeftModel.from_pretrained(base, './subtitle-editor-lora')
merged = model.merge_and_unload()
merged.save_pretrained('./subtitle-editor-merged')
AutoTokenizer.from_pretrained('Qwen/Qwen2.5-7B-Instruct').save_pretrained('./subtitle-editor-merged')
"

# Convert to GGUF format for Ollama
pip install llama-cpp-python
python -m llama_cpp.convert ./subtitle-editor-merged --outfile subtitle-editor.gguf --outtype q4_k_m
```

Create Ollama Modelfile:
```
# ollama/Modelfile.custom
FROM ./subtitle-editor.gguf

PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER num_ctx 4096

SYSTEM """You are a professional subtitle post-editor..."""
```

Import to Ollama:
```bash
ollama create subedit:custom -f ollama/Modelfile.custom
```

## Step 6: Update Configuration

Update `backend/app/config.py`:
```python
class OllamaConfig:
    model: str = "subedit:custom"  # Use your custom model
    # ...
```

Or set environment variable:
```bash
export SUBTITLE_OLLAMA_MODEL="subedit:custom"
```

## Training Tips

### Data Quality
- Review corrections before training - remove errors
- Ensure consistent style across corrections
- Include diverse subtitle types (dialogue, narration, songs)

### Hyperparameters
- **Epochs**: 2-5 (more data = fewer epochs needed)
- **Learning rate**: 1e-4 to 3e-4
- **Batch size**: Limited by GPU memory
- **LoRA rank**: 8-32 (higher = more capacity)

### Avoiding Overfitting
- Use validation split (10-20% of data)
- Monitor loss - stop if validation loss increases
- Keep training data diverse

### Memory Management
- Use gradient checkpointing for large models
- Reduce batch size if OOM
- Use 4-bit quantization (QLoRA)

## Evaluation

Test your fine-tuned model:

```python
# scripts/evaluate.py
import json
from ollama import Client

client = Client()

test_cases = [
    {"source": "Guten Morgen", "expected_en": "Good morning", "expected_zh": "早上好"},
    # Add more test cases
]

correct = 0
for case in test_cases:
    response = client.chat(
        model="subedit:custom",
        messages=[
            {"role": "user", "content": f"Translate: {case['source']}"}
        ]
    )
    result = json.loads(response['message']['content'])
    
    if result['en']['single_line'] == case['expected_en']:
        correct += 1

print(f"Accuracy: {correct}/{len(test_cases)} ({100*correct/len(test_cases):.1f}%)")
```

## Continuous Improvement

1. Keep collecting corrections during normal use
2. Periodically re-train with accumulated data
3. A/B test new models against baseline
4. Track metrics: user correction rate, latency

## Troubleshooting

### Out of memory during training
- Reduce batch size
- Enable gradient checkpointing
- Use 4-bit quantization (default in QLoRA)

### Model not learning
- Increase epochs or learning rate
- Check data quality
- Ensure proper data formatting

### Poor generation quality
- More diverse training data needed
- Lower temperature during inference
- Review training data for errors

## Alternative: Cloud Training

For those without local GPU:

### Google Colab (Free)
- T4 GPU available for free
- Upload your `training_data.jsonl`
- Run training notebook

### RunPod / Lambda Labs
- Rent A100 GPU by the hour (~$1-2/hr)
- Much faster training

### Hugging Face AutoTrain
- No-code fine-tuning
- Upload dataset, select base model
- Automatic hyperparameter tuning
