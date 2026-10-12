# Kite.NET

**Can a small model learn a programming language nobody has ever seen?** Kite.NET is a made-up .NET language,
invented for this experiment, so no model met it in training: anything a model writes in it after training,
it learned. A 1.5B-parameter model learned it in an afternoon, keeps its C#, and runs on a laptop CPU.

```
type Item = record(name: str, price: dec, qty: int)

fn total(items: list<Item>) -> dec =
    items |> sum(i => i.price * i.qty)

test "total adds price times quantity"
    expect total([Item("tea", 2.5, 2)]) == 5.0
```

Every answer is graded by a machine, not a person: the **Kite compiler** (Roslyn, straight to a .NET
assembly), the **.NET static analyzers**, and **hidden tests**.

## The write-up

The experiment is written up as an eight-part series on jocheojeda.com:

1. [Can a small model learn a programming language nobody has ever seen?](https://jocheojeda.com/2026/10/11/can-a-small-model-learn-a-new-language/)
2. [Designing Kite.NET](https://jocheojeda.com/2026/10/11/designing-kite-net/)
3. [Compiling a made-up language to .NET](https://jocheojeda.com/2026/10/11/compiling-a-made-up-language-to-dotnet/)
4. [A grader that can't be fooled](https://jocheojeda.com/2026/10/11/a-grader-that-cant-be-fooled/)
5. [Training data that grades itself](https://jocheojeda.com/2026/10/11/training-data-that-grades-itself/)
6. [It learned Kite and forgot C#](https://jocheojeda.com/2026/10/11/it-learned-kite-and-forgot-csharp/)
7. [83 tokens per second on a CPU](https://jocheojeda.com/2026/10/11/83-tokens-per-second-on-a-cpu/)
8. What didn't work, and what's next *(coming soon)*

## Results (11 October 2026)

Qwen2.5-Coder-1.5B-Instruct (Apache 2.0) + a LoRA adapter, scored on 265 tasks from 6 task families it never
trained on. ↑ higher is better, ↓ lower is better.

| | Untrained | Round 1 (Kite only) | Round 2 | **Round 3: champion** |
|---|---|---|---|---|
| Kite compiles ↑ | 0% | 80.0% | (not tested) | **83.4%** |
| Kite passes hidden tests ↑ | 0% | 65.7% | (not tested) | **69.4%** |
| Writes another language instead of Kite ↓ | 98.9% (mostly Kotlin) | 0% | | **0%** |
| C# guard passes ↑ (117 C# tasks) | 64.1% | 0% | | **64.1%** |
| Verdict | | rejected: forgot C# | rejected on valid: C# 12 points down | **champion** |

- **Round 1** learned Kite and forgot C#: asked for C#, it wrote Kite. The "not dumber" guard caught it.
- **Round 2** mixed in 328 C# tasks and 256 repair lessons (kite's real error messages and the fix): C# came
  back, not far enough.
- The fix-it loop (feed kite's messages back, up to 2 tries) fixed none of the 44 answers that didn't compile; the
  only 2 "fixes" were retries after kite crashed or timed out. The repair lessons covered syntax slips the model no
  longer makes.
- **Round 3**: 748 C# tasks and half the learning rate; chosen on valid as the best Kite score whose C# stays
  within 2 points of the untrained model, then one look at test.

Speed on an M1 Max (Mac Studio), tokens generated per second ↑:

| Runtime | File | GPU | CPU only |
|---|---|---|---|
| MLX, adapter attached | 4-bit + adapter | 74 | |
| MLX, adapter merged | 4-bit, 0.84 GB | 161 | |
| Ollama / llama.cpp | `kite-coder-q4_k_m.gguf`, 0.99 GB | 149 (Ollama), 110 (llama-bench) | 83 |
| llama.cpp | `kite-coder-q8_0.gguf`, 1.65 GB | 84 | 54 |

The Q4 GGUF keeps the quality (75.0% passing on the first 60 test tasks vs 71.7% for the adapter).
MacBook Air M1: not measured yet. Every number's raw answers are in `results/`.

## Try it

The compiler (needs the .NET 9 SDK; the first call builds it):

```bash
compiler/bin/kite run  compiler/examples/shop.kite
compiler/bin/kite test compiler/examples/shop.kite
```

The model, in Ollama (the GGUF and Modelfile come from this repo's GitHub Releases):

```bash
ollama create kite-coder -f kite-coder.Modelfile
ollama run kite-coder "Write fn average(xs: list<dec>) -> dec that returns 0 for an empty list."
```

In LM Studio: put `kite-coder-q4_k_m.gguf` under `~/.lmstudio/models/kite-net/kite-coder-GGUF/`, or drag it in.
Use the system prompt `You write Kite.NET code. Answer with the code only.`

## Reproduce

On an Apple Silicon Mac with Python 3.12 and the .NET 9 SDK:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && source .venv/bin/activate
python -m kitegym.tasks export                     # sources/ -> datasets/kite (train, valid, test, C# guard)
python -m mlx_lm lora --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit --train --data datasets/kite \
  --iters 1000 --batch-size 4 --num-layers 16 --learning-rate 5e-5 --mask-prompt --save-every 250 \
  --max-seq-length 2048 --adapter-path adapters/r3-coder1.5b
python -m kitegym.eval --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit --adapter adapters/r3-coder1.5b \
  --split valid --name mine                          # then --csharp --split valid; one look at test at the end
python -m kitegym.export --quant Q4_K_M --llama-cpp <llama.cpp checkout>
```

Making new tasks (`python -m kitegym.tasks generate`) calls an outside LLM to draft them (DeepSeek V4 Flash was
used); a draft is kept only when kite compiles it with 0 errors and 0 warnings and its tests pass.

## Layout

| Path | What |
|---|---|
| `compiler/` | the Kite.NET compiler and runner (C#), the language reference, examples and its own tests |
| `kitegym/` | task drafting and checking (`tasks.py`), scoring with hidden tests and the repair harness (`eval.py`), GGUF export (`export.py`) |
| `sources/` | every kept task: 1,506 Kite (36 training families, 2 valid, 6 test), 748 C# training, 177 C# guard, 256 repair lessons |
| `results/` | every scored answer, round by round, and the champion pointer |

Weights are not in git: the adapter and GGUF files go to GitHub Releases.

## The rules this experiment follows

- Choose on the validation tasks, confirm on the test tasks with one look; a rejected round stays rejected.
- Test families are held out entirely: test tasks are new kinds of problems, not near-copies of training ones.
- The C# guard ("not dumber") must not drop more than 2 points; the C# tasks that choose checkpoints are kept apart
  from the C# tasks that judge the result.
