---
title: "It learned Kite and forgot C#"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 6
date: 2026-10-11
---

# It learned Kite and forgot C#

This is the part where the model actually learns, and where the first result looked great and was wrong.

## The rules of the game

Three rules decided everything in this article:

1. **Choose on validation, confirm on test, once.** Each training run saves checkpoints. I pick the best
   checkpoint on the validation tasks. That one checkpoint gets one look at the test tasks. If it fails there,
   it's rejected; I don't go back and try the runner-up until something passes.
2. **Not dumber.** Learning Kite must not cost the model its C#. A separate set of C# tasks, the **C# guard**,
   must not drop by more than 2 points against the untrained model.
3. **Keep the guard honest.** The C# guard has 177 tasks. The first 60 are used to choose checkpoints; the other
   117 only judge the final result. A set that helps you choose can't also be the proof.

Everything below is scored on the 265 test tasks from 6 kinds of problems the model never trained on (part 5).
↑ means higher is better, ↓ means lower is better.

## Before training

The untrained model, Qwen2.5-Coder-1.5B-Instruct, given the same one-line instruction ("You write Kite.NET code.
Answer with the code only."):

| | Untrained |
|---|---|
| Compiles ↑ | 0% |
| Passes hidden tests ↑ | 0% |
| Writes another language instead of Kite ↓ | 98.9% |

About two thirds of its answers were Kotlin (`fun`, `data class`, `it`), most of the rest C#. It saw `fn` and
`name: type`, and reached for the closest language it knew.

## Round 1: Kite only

Training on the 1,506 Kite tasks, 800 steps. The best checkpoint on validation passed 72.4% of the validation
tasks, so it got its look at the test set:

| | Untrained | Round 1 |
|---|---|---|
| Kite compiles ↑ | 0% | 80.0% |
| Kite passes hidden tests ↑ | 0% | 65.7% |
| Writes another language ↓ | 98.9% | 0% |
| **C# passes ↑** (all 177 C# tasks) | **67.8%** | **0%** |

A small model went from never having seen a language to passing two thirds of new kinds of problems in it.
And it forgot C# completely.

Asked to write a C# method that counts vowels, round 1 answered:

```
static class Solution
    public static int CountVowels(string s)
        return chars(s) |> sum(c => has("aeiouAEIOU", to_str(c)))
```

C# names and types, Kite's indentation and pipes. It's no longer a C# model with Kite added; it's a Kite model
that remembers some C# words. The guard rejected it.

This is called **catastrophic forgetting**, and it's the most common way fine-tuning goes wrong. Without the
guard, I would have published a great Kite number and a broken model.

## Round 2: mix C# back in

The standard fix is to keep training on some of what you don't want to lose. Round 2 added 328 C# tasks (on
topics different from the guard) and the 256 repair lessons from part 5, for 1,000 steps:

| Checkpoint | Kite passes, valid ↑ | C# passes, valid ↑ (untrained: 75.0%) |
|---|---|---|
| step 250 | 63.3% | 43.3% |
| step 500 | 49.0% | 43.3% |
| step 750 | 54.1% | 55.0% |
| step 1000 | 58.2% | 63.3% |

The C# came back, and it was real C# again: the remaining losses were logic bugs, not Kite leaking in. But the
best checkpoint was still 12 points below the untrained model, and the rule allows 2. So **no checkpoint
qualified, and round 2 never got a look at the test set.**

## Round 3: more C#, gentler learning

Two changes: twice as much C# (748 tasks), and half the learning rate (5e-5 instead of 1e-4), so each step moves
the model less. The choice rule: the best Kite score among checkpoints whose C# stays within 2 points of the
untrained model.

| Checkpoint | Kite passes, valid ↑ | C# passes, valid ↑ (bar: 73%) |
|---|---|---|
| step 250 | 65.3% | 85.0% ✓ |
| step 500 | 64.3% | 71.7% ✗ |
| **step 750** | **70.4%** | **75.0%** ✓ |
| step 1000 | 68.4% | 71.7% ✗ |

Step 750 won and got its one look at the test set:

| | Untrained | **Round 3** |
|---|---|---|
| Kite compiles ↑ | 0% | **83.4%** |
| Kite passes hidden tests ↑ | 0% | **69.4%** |
| Kite clean (0 warnings) ↑ | 0% | **82.6%** |
| Writes another language ↓ | 98.9% | **0%** |
| C# guard passes ↑ (117 tasks) | 64.1% | **64.1%** |
| C# guard compiles ↑ | 74.4% | **84.6%** |
| C# guard clean ↑ | 64.1% | **82.9%** |

It learned Kite better than round 1, and it **kept its C# exactly**. It even writes cleaner C# than before
training: more of its C# compiles, and with fewer warnings. My guess is that grading every training example for
warnings taught it some tidiness in both languages.

## What the model writes now

A task from the exam, about scaling a recipe:

> Write type Ingredient = record(name: str, quantity: dec, unit: str) and fn scale_all(items: list<Ingredient>,
> factor: dec) -> list<Ingredient> that multiplies every ingredient's quantity by factor.

Untrained:

```kotlin
data class Ingredient(val name: String, val quantity: Double, val unit: String)

fun scaleAll(items: List<Ingredient>, factor: Double): List<Ingredient> {
    return items.map { it.copy(quantity = it.quantity * factor) }
}
```

Round 3:

```
type Ingredient = record(name: str, quantity: dec, unit: str)

fn scale_all(items: list<Ingredient>, factor: dec) -> list<Ingredient> =
    items |> map(i => Ingredient(i.name, i.quantity * factor, i.unit))
```

Records are created by calling the type, the pipe reads left to right, names are snake_case, and the whole
function is one expression. It passes the hidden tests with zero warnings.

## What I took from this

- **A guard is not optional.** Round 1's Kite number was excellent. Without the C# guard I'd never have known
  the model was broken.
- **Rejected rounds are results.** Round 1 showed what was missing, and round 2 showed it wasn't enough. Both are
  in the repo with every answer they gave.
- **Small changes, measured.** Twice the C# data and half the learning rate were the only differences between
  round 2 and round 3.

Next: making the champion small and fast enough for a laptop.
