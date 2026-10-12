# Kite.NET article series

Eight articles about the experiment, one concern each, in publishing order. Drafted 11 October 2026 from the
results in this repo; every Kite example compiles (`examples/tour.kite` runs: `compiler/bin/kite run
articles/examples/tour.kite`).

| # | File | Concern |
|---|---|---|
| 1 | [01-can-a-small-model-learn-a-new-language.md](01-can-a-small-model-learn-a-new-language.md) | the question, why a made-up language is a clean test, the result |
| 2 | [02-designing-kite-net.md](02-designing-kite-net.md) | the language, with examples |
| 3 | [03-compiling-a-made-up-language-to-dotnet.md](03-compiling-a-made-up-language-to-dotnet.md) | the compiler: Roslyn, `#line`, projects and NuGet |
| 4 | [04-a-grader-that-cant-be-fooled.md](04-a-grader-that-cant-be-fooled.md) | the checks every answer goes through, two traps |
| 5 | [05-training-data-that-grades-itself.md](05-training-data-that-grades-itself.md) | how the tasks were made and kept |
| 6 | [06-it-learned-kite-and-forgot-csharp.md](06-it-learned-kite-and-forgot-csharp.md) | three training rounds, the C# guard |
| 7 | [07-83-tokens-per-second-on-a-cpu.md](07-83-tokens-per-second-on-a-cpu.md) | merging, GGUF, speed, Ollama and LM Studio |
| 8 | [08-what-didnt-work-and-whats-next.md](08-what-didnt-work-and-whats-next.md) | the fix-it loop, limits, next steps |

## Before publishing

- Article 7: fill in the MacBook Air M1 speed (`[number to add]`).
- Article 8: the repo link (`[link when public]`); the repo is private until made public.
- Every number has a direction (↑ higher is better, ↓ lower is better) and comes from `results/`.
