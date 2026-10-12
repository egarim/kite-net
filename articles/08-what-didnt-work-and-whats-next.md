---
title: "What didn't work, and what's next"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 8
date: 2026-10-11
---

# What didn't work, and what's next

The headline result is good: a 1.5B model went from 0% to 69.4% on new kinds of problems in a language that
didn't exist, kept its C#, and runs at 83 tokens per second on a CPU. This last article is about what didn't
work, what I'd change, and what comes next.

## The fix-it loop fixed nothing real

The plan was a harness around the model: if its code doesn't compile or fails a test, give it kite's messages
and let it try again, up to two times. Round 2 and round 3 even trained on 256 repair lessons built from real
kite error messages (part 5).

The numbers looked like a small win at first: 69.4% passing on the first try, 70.2% after up to two fixes. Then
I looked at the two tasks that "got fixed". In both, the first attempt hadn't failed because of the model:
kite crashed or timed out on the first run, and the retry simply worked. **The loop fixed none of the 44
answers that didn't compile.**

Why, I think:

- **The repair lessons only covered syntax slips** (semicolons, braces, `var`, `&&`), the C# habits. The trained
  model had stopped making those. Its real mistakes were different.
- **Its real compile errors look like this**, from the exam:

  ```
  type Appointment = record(patient: Patient, start: datetime, end: datetime)
  ```

  ```
  line 2: error CS0246: The type or namespace name 'datetime' could not be found
  ```

  Kite has no `datetime` type; the model invented one. Other common ones: piping into something that isn't a
  function call, and trying to change a field of a record (records are read-only). Fixing those needs
  understanding, not a syntax swap.
- **37 more answers compiled and failed tests**: logic errors. A test failure message ("expected 20") says
  what's wrong, not how to fix it.

What would probably work: repair lessons made from the model's *own* failures. Run the model on the training
tasks, collect what it gets wrong, and teach it the fixes for those.

## Other things I'd change

- **Bigger validation sets.** Choosing checkpoints on 98 tasks from 2 families is noisy: one family that happens
  to suit a checkpoint can decide the choice. Four or five held-out validation families would be safer.
- **More than one seed.** Every round was trained once. Two or three runs per setting would show how much of the
  difference between rounds is real.
- **The "C# leakage" metric was misnamed.** It counts any foreign syntax. The untrained model mostly wrote
  Kotlin, so "writes another language instead of Kite" is the honest name.

## What's next

1. **Projects, NuGet and solutions.** The MSBuild project SDK from part 3, and a `dotnet new kite` template, so
   a Kite project builds with `dotnet build` next to C# projects and uses NuGet packages.
2. **Repair lessons from the model's own mistakes**, and a measured second try at the fix-it loop.
3. **A real target.** Kite was the clean experiment. The useful version is the same recipe for a framework that
   already exists and is very opinionated, where a small specialist could beat a big generalist.
4. **Publishing.** The repo, with the compiler, every task, every scored answer and the model files, will be
   public: [link when public].

## The recipe, in one place

For anyone who wants to try this with their own narrow domain:

1. **Get a grader first.** A compiler, analyzers and hidden tests. If you can't grade an answer automatically,
   you can't make data or measure a model.
2. **Let a big model draft, let the grader decide.** Keep only drafts that pass everything. Never train on
   "mostly right".
3. **Hold out whole kinds of problems**, not just individual tasks.
4. **Guard what you don't want to lose.** Here it was C#. Split the guard into a part that chooses and a part
   that judges.
5. **Choose on validation, look at test once, and keep the rejected rounds.** They're where the lessons are.
6. **Merge and quantize before measuring speed**, and check that quality survived.

A small model, a strict grader and a few thousand checked examples went a long way. The model is 0.99 GB and
runs on a CPU. What it can't do yet is fix its own mistakes, and that's the next experiment.
