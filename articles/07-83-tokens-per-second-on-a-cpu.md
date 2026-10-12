---
title: "83 tokens per second on a CPU"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 7
date: 2026-10-11
---

# 83 tokens per second on a CPU

A model that only runs on a big GPU isn't much use for a narrow job like "write code in our language". I wanted
Kite's model to run on an ordinary laptop, a basic GPU, or a CPU-only server. This article is about the steps
that took it from "trained" to "fast and portable", with the numbers.

Every speed here is **tokens generated per second** (↑ higher is better), measured on a Mac Studio with an
M1 Max, nothing else running.

## Step 1: merge the adapter

Training produced a LoRA adapter: a few megabytes of extra weights that sit next to the original model. Running
the model with the adapter attached means extra work on every token:

| | Tokens/s ↑ |
|---|---|
| Untrained model | 176 |
| Same model with the adapter attached | **74** |

Less than half the speed. The fix is to **merge** the adapter into the model's weights once. The merged model has
exactly the same size and shape as the original, so it runs at the same speed:

```bash
python -m mlx_lm fuse --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
  --adapter-path adapters/r3-coder1.5b-i750 --save-path fused/kite-coder
```

| | Size | Tokens/s ↑ |
|---|---|---|
| Merged, MLX 4-bit | 0.84 GB | **161** |

Merging rounds the weights slightly differently, so I checked quality on the same 60 test tasks: 70.0% passing
merged, against 71.7% with the adapter attached. One task apart.

## Step 2: GGUF, for everything that isn't a Mac

MLX only runs on Apple Silicon. For everything else (Ollama, LM Studio, llama.cpp, NVIDIA GPUs, plain CPUs), the
format is **GGUF**. The export merges the adapter at full precision, converts it with llama.cpp, and quantizes
it:

| File | Size | What for |
|---|---|---|
| `kite-coder-q8_0.gguf` | 1.65 GB | closest to the trained model |
| `kite-coder-q4_k_m.gguf` | 0.99 GB | smaller and faster, for CPUs and basic GPUs |

Four-bit rounding can hurt quality, so I scored the Q4 file through Ollama on the same 60 test tasks: **75.0%**
passing, against 71.7% for the adapter. No loss, within noise.

## The speed table

| Runtime | File | GPU ↑ | CPU only ↑ |
|---|---|---|---|
| MLX, adapter attached | 4-bit + adapter | 74 | |
| MLX, adapter merged | 4-bit, 0.84 GB | 161 | |
| Ollama | Q4 GGUF, 0.99 GB | 149 | |
| llama.cpp (`llama-bench`) | Q4 GGUF, 0.99 GB | 110 | **83** |
| llama.cpp (`llama-bench`) | Q8 GGUF, 1.65 GB | 84 | 54 |

**83 tokens per second on CPU only** is the number I care about. The model's answers on the exam are short: half
are under 61 tokens and 9 in 10 are under 112. On CPU alone, that's about a second for most answers, plus a
moment to read the prompt (llama.cpp reads about 270 prompt tokens per second on this CPU). No GPU needed.

The MacBook Air M1: [number to add].

## Running it yourself

In **Ollama**, with the GGUF and the Modelfile from the release:

```bash
ollama create kite-coder -f kite-coder.Modelfile
ollama run kite-coder "Write fn average(xs: list<dec>) -> dec that returns 0 for an empty list."
```

The Modelfile is four lines. The system prompt matters: it's the one the model was trained with.

```
FROM ./kite-coder-q4_k_m.gguf
SYSTEM """You write Kite.NET code. Answer with the code only."""
PARAMETER temperature 0
PARAMETER num_ctx 4096
```

In **LM Studio**: put the `.gguf` file in a folder under `~/.lmstudio/models/` (for example
`~/.lmstudio/models/kite-net/kite-coder-GGUF/`), or drag it into the app, and set the same system prompt.

Then check the answer with the compiler:

```bash
kite test answer.kite
```

## Why this matters

The useful version of this experiment isn't "a model that writes Kite". It's "a small model that's very good at
one narrow, strict thing, runs anywhere, and is graded automatically". One company's framework, an internal
language, a configuration format. A one-gigabyte file on a CPU-only server is cheap enough to deploy for each
of those.

Next, and last: what didn't work, and what's next.
