---
title: "Training data that grades itself"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 5
date: 2026-10-11
---

# Training data that grades itself

To teach a model Kite, I needed examples: a request in English, a correct Kite solution, and tests. About 1,500
of them, with nobody reading them by hand. This article is about how they were made, and why the compiler, not a
person, decided which ones were good enough.

## One task

Each task looks like this:

> **Request:** Write fn word_count(s: str) -> int that returns the number of words in s. Words are separated by
> spaces. Empty string has zero words.

```
fn word_count(s: str) -> int =
    len(words(s))
```

```
test "word_count counts words"
    expect word_count("hello world") == 2
    expect word_count("  one   two three  ") == 3
    expect word_count("") == 0
    expect word_count("single") == 1
```

The model trains on the request and the solution. The tests stay hidden: they're how the task is graded.

## An LLM drafts, the compiler decides

A big model can't write Kite from memory either; nobody has seen Kite. But it can follow a written definition.
I gave a large, cheap model (DeepSeek V4 Flash) the one-page language reference, the list of standard library
functions, one complete example program, and rules like "no semicolons, `let` unless reassigned, at least three
`expect` lines". Then I asked for batches of eight tasks at a time, one topic per batch.

Every draft then went through the grader from part 4. A draft was **kept** only if:

- it compiled with **0 errors and 0 warnings** (Kite's analyzer, the C# compiler and the .NET analyzers);
- **every test passed**;
- it had **at least 3 `expect` lines**.

If a draft failed, the drafting model got one chance to fix it: it saw kite's error messages and sent a new
version, which went through the same checks.

The numbers:

| | |
|---|---|
| Drafts that came back | about 2,300 |
| Kept (clean and passing) | 2,027, about 88% |
| Kept only after one fix | 200 |
| Unique after removing duplicate solutions | 1,869 |

Nothing that failed the grader taught the model anything. That rule matters: a model trained on code that
"mostly works" learns to write code that mostly works.

## Held-out kinds of problems

The tasks are grouped into 44 families, one topic each: text basics, money, sorting, records for an inventory,
enums for traffic lights, a bank account, parsing CSV lines, and so on.

The split is by family, not by task:

| Split | Families | Tasks |
|---|---|---|
| Train | 36 | 1,506 |
| Validation (to choose a model) | 2: weather logs, parking | 98 |
| Test (the exam) | 6: appointments, warehouse, elections, recipes, transit, timesheets | 265 |

So the exam isn't "more problems like the ones you practiced". It's **new kinds of problems**. A model that
memorized its training tasks would fail it.

## Keeping C# in the mix

The first trained model forgot how to write C# (part 6 tells that story). The fix was to mix plain C# tasks into
training: 748 of them, drafted and checked the same way, on topics different from the C# exam tasks. `kite`
grades C# files too, with the same compiler, analyzers and test runner.

## Repair lessons

I also wanted the model to fix its own mistakes when the compiler complains. So I built 256 **repair lessons**,
without any outside model:

1. Take a correct training solution.
2. Break it the way a C# habit would: add a semicolon, braces, `var`, `&&`, `$"`, `null`, or reassign a `let`.
3. Run kite on it and record the real error message.
4. The lesson is: here's the broken code, here's what kite says, here's the fix.

A real one:

```
fn transpose(m: list<list<int>>) -> list<list<int>>
    if len(m) == 0
        return list<list<int>>()
    var cols = len(first(m))
    return range(0, cols) |> map(c => m |> map(row => first(skip(row, c))))
```

```
line 4: error KT004: 'var' is C#, not Kite
```

The fix is `let cols = len(first(m))`. A lesson is kept only if kite really reports an error for the broken
version.

## What I'd do differently

- **The validation set was small**: 98 tasks from only 2 families. Choosing between checkpoints on so few tasks
  is noisy. More held-out families would make the choice more reliable.
- **The repair lessons only cover syntax slips.** Part 8 shows that this mattered: they didn't teach the model
  to fix logic errors, which is most of what's left.

Every kept task, every C# task and every repair lesson is in the repo as plain JSON lines, so the whole dataset
can be inspected and rebuilt.

Next: what happened when the model trained on it.
