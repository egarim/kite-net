---
title: "Can a small model learn a programming language nobody has ever seen?"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 1
date: 2026-10-11
---

# Can a small model learn a programming language nobody has ever seen?

Every time someone shows a small AI model writing good code, there is a question in the back of my head: did it
*learn* that, or did it *remember* it? These models were trained on most of the public code on the internet.
When one writes a clean C# LINQ query, it may be repeating something it saw a thousand times.

So I set up an experiment where remembering is impossible. I invented a programming language, gave it a
compiler, and taught a small model to write it. No model has ever seen this language, because it didn't exist
until I made it. Anything the model writes in it correctly after training, it learned.

The language is called **Kite.NET**. It runs on .NET, and it looks like this:

```
type Item = record(name: str, price: dec, qty: int)

fn total(items: list<Item>) -> dec =
    items |> sum(i => i.price * i.qty)

test "total adds price times quantity"
    expect total([Item("tea", 2.5, 2)]) == 5.0
```

That is a record type, a function written as a single expression with a pipe, and a test. It compiles to a
normal .NET assembly.

## Why a made-up language is a clean test

A made-up language removes three problems at once:

1. **No memorized answers.** The model can't have seen Kite code before, so it can't copy any.
2. **No cheating on the test set.** With a public language, test problems often have near-copies somewhere in
   the training data of the base model. Here, there is nothing to copy.
3. **No arguing about "good enough".** A program either compiles or it doesn't, and either passes its tests or
   it doesn't.

That last point matters most. I didn't want to judge the model's code by reading it. I wanted a machine to
grade every answer, the same way every time.

## Machines grade, not people

Every answer the model gives goes through three checks:

- **The Kite compiler**, which I wrote for this. It turns Kite into .NET through Roslyn, the same compiler
  that builds C#.
- **The .NET static analyzers**, the ones Visual Studio uses to warn about wasted work and possible bugs, plus a
  few Kite-specific rules ("you changed a value you said would never change").
- **Hidden tests.** Each task comes with tests the model never sees. The answer has to pass all of them.

An answer only counts as a pass if it compiles and every hidden test passes.

## The setup

- **The model:** Qwen2.5-Coder-1.5B-Instruct. That's a small coding model, 1.5 billion parameters, Apache 2.0
  licensed. Small enough to run on a laptop CPU.
- **The training:** a LoRA adapter, which is a few megabytes of extra weights trained on top of the model, on a
  Mac Studio.
- **The data:** about 1,500 Kite programming tasks, each with a solution and tests, all checked by the compiler.
- **The exam:** 265 tasks from 6 kinds of problems the model never trained on.

## The result in one line

Before training, the model passed **0%** of the exam. After training, it passed **69.4%**, on kinds of
problems it had never seen, in a language that didn't exist a day earlier.

Here is a real example from the exam. The task asked for a recipe ingredient record and a function that adds up
calories:

> Write type Ingredient = record(name: str, calories: int, quantity: int) and fn total_calories(items:
> list<Ingredient>) -> int that returns the sum of calories times quantity for each ingredient.

The untrained model answered in **Kotlin**:

```kotlin
data class Ingredient(val name: String, val calories: Int, val quantity: Int)

fun totalCalories(items: List<Ingredient>): Int {
    return items.sumOf { it.calories * it.quantity }
}
```

That's a sensible guess. Kite's `fn` and `name: type` look a bit like Kotlin, so the model reached for the closest
language it knew. But it's not Kite, and the compiler rejected it on the first line.

The trained model answered in Kite:

```
type Ingredient = record(name: str, calories: int, quantity: int)

fn total_calories(items: list<Ingredient>) -> int =
    items |> sum(i => i.calories * i.quantity)
```

It compiles with zero warnings and passes the hidden tests.

## It wasn't a straight line

The first trained model learned Kite and **forgot C#**. Ask it for C#, and it wrote Kite. A safety check I had
set up ("the model must not get worse at C#") caught it, and that model was rejected. It took three rounds to get
a model that learned Kite *and* kept its C#. That story gets its own article.

## What this series covers

Each article covers one part of the experiment:

1. **This one:** the question, and why a made-up language is a clean test.
2. **Designing Kite.NET:** the language itself, with examples.
3. **Compiling a made-up language to .NET:** how Kite becomes a .NET assembly, and why I went through Roslyn.
4. **A grader that can't be fooled:** the checks every answer goes through.
5. **Training data that grades itself:** how 1,500 tasks were made and checked without a person reading them.
6. **It learned Kite and forgot C#:** three training rounds and what each one taught me.
7. **83 tokens per second on a CPU:** making the model small and fast enough for a laptop.
8. **What didn't work, and what's next.**

The point of all this isn't Kite itself. It's what Kite proves: a small model can learn a narrow, opinionated
thing, like one company's framework, one internal language or one set of conventions, if you can grade its work
automatically. That's a much cheaper path than a big model that knows a little about everything.
