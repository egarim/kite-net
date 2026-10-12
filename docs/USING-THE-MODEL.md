# Using the Kite.NET model

The trained model, `kite-coder`, writes [Kite.NET](../README.md) code. This is how to **download it, run it, ask
it for a program in plain English, and check what it wrote** with the compiler.

The model is a LoRA adapter on Qwen2.5-Coder-1.5B-Instruct, exported to GGUF. The Q4 file is 0.99 GB and runs on
a laptop CPU.

## 1. Get the model

The weights are not in git — they're attached to the release
[`kite-coder-2026-10-11`](https://github.com/egarim/kite-net/releases/tag/kite-coder-2026-10-11):

```bash
gh release download kite-coder-2026-10-11 -R egarim/kite-net \
  -p 'kite-coder-q4_k_m.gguf' -p 'kite-coder.Modelfile'
```

(No `gh`? Download the files from the release page in a browser.)

| Asset | Size | Use |
|---|---|---|
| `kite-coder-q4_k_m.gguf` | 0.99 GB | smaller and faster — CPUs and basic GPUs (recommended) |
| `kite-coder-q8_0.gguf` | 1.65 GB | closest to the trained model |
| `kite-coder.Modelfile` | tiny | Ollama wrapper — sets the trained system prompt |
| `kite-coder-adapter.safetensors` + `_config.json` | small | the raw LoRA adapter, if you want to merge it yourself with MLX |

## 2. Load it

The **system prompt matters** — it is the exact one the model trained with, and the temperature should be `0`:

```
You write Kite.NET code. Answer with the code only.
```

### Ollama (easiest)

The Modelfile already sets the system prompt and temperature. From the folder that holds the `.gguf` and the
Modelfile:

```bash
ollama create kite-coder -f kite-coder.Modelfile
```

The Modelfile is four lines:

```
FROM ./kite-coder-q4_k_m.gguf
SYSTEM """You write Kite.NET code. Answer with the code only."""
PARAMETER temperature 0
PARAMETER num_ctx 4096
```

### LM Studio

Put `kite-coder-q4_k_m.gguf` in a folder under `~/.lmstudio/models/` (for example
`~/.lmstudio/models/kite-net/kite-coder-GGUF/`), or drag it into the app. Set the system prompt above and
temperature 0.

### llama.cpp (advanced)

Run `llama-cli` with the GGUF directly; set the same system prompt your build's way (recent builds take
`-sys "..."`), and `--temp 0`. Ollama is less fiddly because the Modelfile carries the prompt for you.

## 3. Ask it — "write me a program that does X"

Describe what you want in plain English. The model answers in Kite, code only:

```bash
ollama run kite-coder "Write a program with a record Product(name: str, price: dec, qty: int), a function total(items: list<Product>) -> dec that sums price times qty, and a test."
```

A smaller request works just as well:

```bash
ollama run kite-coder "Write fn average(xs: list<dec>) -> dec that returns 0 for an empty list."
```

Tips for a 1.5B model:

- **Be concrete** about types and names (`record(name: str, price: dec, qty: int)`, `fn total(...) -> dec`). A
  precise request gets a precise answer.
- It replies with **code only** — that's the system prompt. One `fn`, or a small `module` with a `fn main()`,
  works best.
- For the language itself — records, `fn`, pipes `|>`, `when`, `let` vs `let mut`, tests — see
  [part 2 of the write-up (Designing Kite.NET)](https://jocheojeda.com/2026/10/11/designing-kite-net/) and
  [`compiler/README.md`](../compiler/README.md).

## 4. Check what it wrote

The model is graded by the compiler, and so can you. Save the answer to a `.kite` file and run it (needs the
.NET 9 SDK; the first call builds the compiler):

```bash
compiler/bin/kite run   answer.kite    # compile in memory and run main()
compiler/bin/kite test  answer.kite    # run its test blocks
compiler/bin/kite check answer.kite    # errors and warnings only
```

If `kite check` is clean and `kite test` passes, the model got it right — the same bar used to score it in the
experiment.

## 5. Score it on the held-out tasks (the grading harness)

To reproduce the numbers instead of eyeballing one answer, run the eval harness against the Ollama model over
the 265 held-out test tasks. It generates an answer per task, grades each with the compiler + analyzers + hidden
tests, and writes every scored answer to `results/`:

```bash
ollama create kite-coder -f kite-coder.Modelfile          # if not already
python -m kitegym.eval --model kite-coder --ollama --split test --name mine
```

Useful flags:

| Flag | What |
|---|---|
| `--csharp` | score the C# guard set instead (the "not dumber" check) |
| `--repairs 2` | the fix-it loop: feed kite's error messages back, up to N times |
| `--limit N` | only the first N tasks (a quick smoke test) |
| `--split valid` | the validation tasks instead of the test set |

See the repo [`README.md`](../README.md) for the full reproduce path (training, export) and the results tables.
