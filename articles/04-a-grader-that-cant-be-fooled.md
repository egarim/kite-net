---
title: "A grader that can't be fooled"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 4
date: 2026-10-11
---

# A grader that can't be fooled

The whole experiment depends on one thing: every answer the model writes is graded by a machine, the same way
every time. If the grader is weak, the numbers mean nothing. This article is about what the grader checks, and
two traps that quietly switched parts of it off.

## Four layers

Every answer goes through four checks, in order:

| Layer | Catches | Ids |
|---|---|---|
| Kite's parser and analyzer | syntax mistakes, C# written into Kite, changing a `let`, naming | KT000-KT006 |
| The C# compiler (Roslyn) | type errors, unknown names, possible null references, unused variables | CSxxxx |
| The .NET analyzers | wasted work, dead code, lost results | CAxxxx |
| Hidden tests | wrong answers | |

An answer **compiles** if it has no errors. It's **clean** if it also has no warnings. It **passes** if it
compiles and every hidden test passes.

## Kite's own rules

Some mistakes only make sense in Kite, so Roslyn can't see them. Kite checks these itself:

| Id | Rule |
|---|---|
| KT000 | Syntax: bad indentation, a statement Kite doesn't have |
| KT001 | Changing a `let` or a parameter (error) |
| KT002 | A `let mut` that is never changed (warning) |
| KT003 | Naming: functions and variables snake_case, types PascalCase |
| KT004 | Another language written into Kite: `;`, braces, `var`, `new`, `&&`, `!`, `$"` (error) |
| KT006 | A result thrown away: `upper(name)` on a line by itself |

Here's a program with four mistakes:

```
module Mistakes

fn average(xs: list<dec>) -> dec
    let total = sum(xs)
    total = total + 0.0
    let mut count = len(xs)
    upper("unused")
    return total / count

fn first_name(name: str?) -> str
    return name.Split(' ')[0]
```

```
$ kite check mistakes.kite
mistakes.kite(5): error KT001: 'total' cannot be changed: declare it with let mut
mistakes.kite(6): warning KT002: 'count' is never changed: declare it with let, not let mut
mistakes.kite(7): warning KT006: the result of upper() is not used
mistakes.kite(11): warning CS8602: Dereference of a possibly null reference.
failed: 1 errors, 3 warnings
```

Three come from Kite's analyzer. The last one comes from the C# compiler: `name` may be `none`, and the code uses
it without checking. It points at line 11 of the Kite file, thanks to the `#line` markers from part 3.

And here's what happens when someone writes C# habits into Kite:

```
module Leak

fn add(a: int, b: int) -> int
    var sum = a + b;
    return sum
```

```
leak.kite(4): error KT004: 'var' is C#, not Kite
leak.kite(4): error KT004: ';' is C#, not Kite
```

KT004 is the most important rule for the experiment. It's how I measure whether the model is really writing
Kite or falling back to a language it already knows.

## The .NET analyzers

Roslyn ships around 270 analyzer rules (`Microsoft.CodeAnalysis.NetAnalyzers`). By default the .NET SDK turns
on only the ones about outright bugs. For Kite I turned on a few more that a reviewer would flag: dead code
(CA1508), lost results (CA1806), and wasted LINQ (CA1829, CA1860 and friends):

```
module Ca

fn some(xs: list<int>) -> bool
    if xs.Count() > 0
        return true
    return xs.Any()
```

```
ca.kite(4): warning CA1829: Use the "Count" property instead of Enumerable.Count()
ca.kite(6): warning CA1860: Prefer comparing 'Count' to 0 rather than using 'Any()'
```

A few rules are switched off because they fight Kite's own style: CA1707 complains about underscores in names,
and Kite *requires* snake_case.

## Trap 1: the analyzers were silently skipping everything

The first version loaded all 270 analyzers, and they reported... nothing. Not even on code that was obviously
wasteful.

The cause: I had named the in-memory C# file `shop.kite.g.cs`. Roslyn treats any file ending in `.g.cs` as
generated code, and most analyzers skip generated code on purpose. Nothing was wrong except the file name.
Renaming it to `shop.kite.cs` turned every rule back on.

That one is worth remembering: **an analyzer that finds nothing may not be running at all.** Test your grader on
code that should fail.

## Trap 2: the standard library vanished

Kite's standard library is a C# file compiled into every program. To keep it out of the compiler itself, I
embedded it as a resource named `Std.cs.txt`. At run time it wasn't there.

MSBuild had read `Std.cs.txt` as "a resource for the culture `cs`", which is Czech, and put it in a separate
satellite assembly for Czech users. Renaming it to `KiteStd.txt` fixed it. It took longer to find than to fix.

## Hidden tests

The last layer is the simplest and the most important. Each task has tests the model never sees:

```
test "word_count counts words"
    expect word_count("hello world") == 2
    expect word_count("  one   two three  ") == 3
    expect word_count("") == 0
    expect word_count("single") == 1
```

`kite test` runs each test with a 5-second limit, so an infinite loop in the model's code counts as a failure
instead of hanging the run.

## Why all four layers

Each layer catches things the others can't. Tests alone would accept code that's correct but sloppy:
`let mut` everywhere, results thrown away. The compiler alone would accept code that's tidy but wrong. Together
they grade the way a strict reviewer would, except they never get tired and never grade the same code
differently twice.

The grader also checks the training data, which is the next article: before a task could teach the model
anything, it had to pass all four layers itself.
