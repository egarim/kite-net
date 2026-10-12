---
title: "Designing Kite.NET"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 2
date: 2026-10-11
---

# Designing Kite.NET

For the experiment in part 1, I needed a language with three properties:

1. **New**: no model can have seen it.
2. **Visibly different from C#**: if the model falls back to C#, I want to see it at a glance, and so does the
   compiler.
3. **Small but real**: enough to write useful programs (records, loops, matching, lists, maps, tests), and it
   has to run on .NET, so it can use any .NET library.

This article is a tour of what came out. Every example here compiles and runs.

## The name

I wanted to call it **X#**. That name is taken twice: XSharp is an existing .NET language for the xBase world
(Clipper, Visual Objects, FoxPro), and the Cosmos operating system project has its own X#. So it's
**Kite.NET**: light, flies high, but tied to the ground by a string, the .NET runtime and its analyzers. Source
files end in `.kite`.

## Functions: one expression or a block

A function that is a single expression uses `=`:

```
fn total(items: list<Item>) -> dec =
    items |> sum(i => i.price * i.qty)
```

A longer function is an indented block, and `return` gives the value:

```
fn countdown(from: int) -> list<int>
    let out: list<int> = []
    let mut n = from
    while n > 0
        push(out, n)
        n -= 1
    return out
```

Blocks are indentation, like Python. No braces, no semicolons. Writing either is an error, on purpose: it's the
clearest sign that someone (or some model) is writing C# instead of Kite.

## `let` and `let mut`

`let` means "this name will never point at something else". `let mut` means it can be reassigned. In
`countdown` above, `out` is a `let` even though items are added to it: the list changes, but `out` always
refers to the same list. `n` changes, so it's `let mut`.

The compiler enforces both directions. Reassigning a `let` is an error. A `let mut` that is never reassigned is
a warning, because it says something untrue about the code.

## Pipes

`x |> f(y)` means `f(x, y)`. Pipes let you read data transformations left to right:

```
fn receipt(items: list<Item>) -> str
    let lines = items |> map(i => "{i.qty} x {i.name}: {i.price * i.qty:0.00}")
    return join(lines, "\n") + "\ntotal: {total(items):0.00}"
```

Pipes bind tighter than comparisons, so `countdown(3) |> join(",") == "3,2,1"` means what it looks like.

## Records, enums and creating things

```
type Item = record(name: str, price: dec, qty: int)
type Light = enum(Red, Yellow, Green)
```

There is no `new`. Calling a type creates it: `Item("tea", 2.5, 3)`. Lists are written with brackets:
`[Item("tea", 2.5, 3), Item("cake", 4.0, 1)]`. An empty list needs a declared type, `let out: list<int> = []`,
because there is nothing to infer the type from.

## Matching with `when`

```
fn label(n: int) -> str = when n
    0 -> "none"
    1 -> "one"
    < 5 -> "a few"
    else -> "many"

fn next_light(l: Light) -> Light = when l
    Light.Red -> Light.Green
    Light.Green -> Light.Yellow
    else -> Light.Red
```

## Strings, words and `none`

Every string interpolates, so there's no `$` prefix: `"total: {total(items)}"`. Use `{{` for a literal `{`.
Format specifiers work: `{price:0.00}`.

Logic uses words: `and`, `or`, `not`. Missing values are `none`, and `T?` marks a type that may be missing:

```
fn cheapest(items: list<Item>) -> Item? =
    items |> sort_by(i => i.price) |> find(i => i.qty > 0)
```

## The types

| Kite | .NET |
|---|---|
| `int` `dec` `float` `bool` `str` `char` | `int` `decimal` `double` `bool` `string` `char` |
| `list<T>` `map<K, V>` `set<T>` `T?` | `List<T>` `Dictionary<K, V>` `HashSet<T>` nullable |

Number literals with a decimal point are `dec`, not `double`. Most small programs are about money, prices or
averages, and decimal is the right default for those.

## Errors and tests are part of the language

`fail "message"` throws an error. `test` blocks hold `expect` checks, and `kite test` runs them:

```
fn safe_divide(a: dec, b: dec) -> dec
    if b == 0.0
        fail "cannot divide by zero"
    return a / b

test "labels and lights"
    expect label(0) == "none"
    expect label(3) == "a few"
    expect next_light(Light.Red) == Light.Green
```

Tests being built in matters for the experiment: every training task carries its own tests in the same
language.

## It's still .NET

Any .NET member works: `name.Split(' ')`, `xs.Count`, `Math.Max(a, b)`. You can `use System.Text` and call
anything in it. Kite adds a small standard library on top: `map`, `filter`, `sum`, `sort_by`, `group_by`,
`join`, `words`, `upper` and about forty more. They're all snake_case, so a Kite program reads the same whether
it uses Kite's library or .NET's.

## A complete program

```
module Tour

type Item = record(name: str, price: dec, qty: int)

fn total(items: list<Item>) -> dec =
    items |> sum(i => i.price * i.qty)

fn word_counts(text: str) -> map<str, int>
    let counts = map<str, int>()
    for w in words(lower(text))
        counts[w] = get(counts, w, 0) + 1
    return counts

fn main()
    let cart = [Item("tea", 2.5, 3), Item("cake", 4.0, 1)]
    print("total: {total(cart):0.00}")
    for i in 1..=3
        print(if i % 2 == 0 then "{i} is even" else "{i} is odd")
    print(get(word_counts("the cat the hat"), "the", 0))
```

```
$ kite run tour.kite
total: 11.50
1 is odd
2 is even
3 is odd
2
```

`1..=3` includes 3; `1..3` doesn't. `if c then a else b` is an expression.

## Design choices that were about the experiment, not taste

- **Visibly not C#.** Indentation instead of braces, `fn` instead of a return type first, `and` instead of `&&`.
  Every one of these is a place where a model falling back to C# gets caught immediately.
- **Small.** A small language means a small model can learn the whole of it from about 1,500 examples.
- **Strict about intent.** `let` versus `let mut`, and "you threw away the result of `upper()`", are warnings a
  human reviewer would raise. Making the compiler raise them means the model is graded on code quality, not just
  "does it run".

Next: how a made-up language turns into a real .NET assembly.
