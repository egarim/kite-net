---
title: "Compiling a made-up language to .NET"
series: "Kite.NET: teaching a small model a language that didn't exist"
part: 3
date: 2026-10-11
---

# Compiling a made-up language to .NET

Kite.NET needed a compiler before any model could learn it: the compiler is what grades every answer. This
article is about one decision: how do you get a new language onto .NET?

## Three routes

A .NET program is IL (intermediate language) inside an assembly. There are three ways to get a new language
there:

| Route | How | Who does it this way |
|---|---|---|
| 1. Write C# text | Translate the new language to C# source and compile that | Many code generators |
| 2. Build Roslyn's code in memory | Translate to Roslyn's syntax, with markers that point every line back to the original file; Roslyn writes the IL | Razor (`.cshtml` pages) |
| 3. Write IL yourself | Your own parser, type checker and IL writer | F# |

It helps to see how the existing languages do it:

- **C# and VB.NET** both live inside Roslyn. VB.NET has its own parser and type checker, but the part that
  writes the IL into the assembly is shared with C#. VB never passes through C#, but it reuses everything after
  type checking.
- **F#** has its own compiler, separate from Roslyn, and writes IL itself. That means its own type checker, its
  own handling of generics, closures and everything else. It's years of work.
- **Razor** is the closest to what I needed. A `.cshtml` page is converted to C# with `#line` markers, so when
  you make a mistake in the page, the error shows up on the page's line, not in some generated file.

## Why route 2

The deciding factor was the **.NET static analyzers**. These are the rules that warn about wasted LINQ calls,
dead code and possible null references. They only run on a Roslyn compilation of C# or VB code. If Kite wrote
its own IL (route 3), I'd lose all of them, plus spend weeks on a type checker. And the experiment would look
exactly the same from the outside.

Route 2 gets everything Roslyn has, for the cost of a translator.

## What the compiler actually does

`kite` reads a `.kite` file and turns it into C#, with a `#line` marker before every statement. Then Roslyn
parses that C# in memory, next to Kite's small standard library, and compiles it. No C# file is ever written to
disk.

Here's a small Kite program:

```
type Item = record(name: str, price: dec, qty: int)

fn total(items: list<Item>) -> dec =
    items |> sum(i => i.price * i.qty)
```

And here's what Roslyn gets (`kite emit` shows it, for debugging the compiler):

```csharp
#nullable enable
#line hidden

namespace KiteApp
{
#line 1 "small.kite"
    public sealed record Item(string name, decimal price, int qty);
#line hidden
    public static partial class Main
    {
#line 3 "small.kite"
        public static decimal total(List<Item> items)
#line 3 "small.kite"
            => sum(items, i => i.price * i.qty);
#line hidden
    }
}
```

The `#line 3 "small.kite"` markers are the whole trick. When Roslyn or an analyzer reports a problem, it reports
it against `small.kite`, line 3. Code I add around the program (the class, the namespace) is marked
`#line hidden`, so nothing ever points at it.

Most of Kite maps directly:

| Kite | Becomes |
|---|---|
| `xs \|> map(x => x * 2)` | `map(xs, x => x * 2)` |
| `Item("tea", 2.5, 3)` | `new Item("tea", 2.5m, 3)` |
| `[1, 2, 3]` | `list(1, 2, 3)` |
| `"total: {t}"` | `$"total: {t}"` |
| `a and not b` | `a && !b` |
| `when n` + arms | a C# `switch` expression |
| `let mut n = 0` | `var n = 0;` |

`let` versus `let mut` doesn't exist in C#, so Kite checks it itself before Roslyn sees the code (more on that in
part 4).

## Two small traps

**C# keywords.** A Kite program can call a variable `out`, but `out` is a C# keyword. The translator escapes
those (`@out`), or the model's perfectly valid Kite would fail with a C# error it can't understand.

**Cascading errors.** When a Kite line has a syntax error, the C# it turns into is broken too, and Roslyn reports
fifteen confusing C# errors for one Kite mistake. So when Kite's own parser finds a syntax error, `kite` reports
only that and skips Roslyn. One clear message beats fifteen noisy ones, for a person and even more for a
model trying to fix its code.

## Running it

```bash
kite run  tour.kite      # compile in memory and run main()
kite test tour.kite      # run the test blocks
kite check tour.kite     # errors and warnings only
kite build tour.kite -o tour.dll
```

`kite test --json` prints a machine-readable result: every error, warning and test outcome. That's what the
training pipeline reads.

## Projects, NuGet and solutions

A real .NET language needs more than single files: project files, NuGet packages, solutions mixing languages.
The plan for that is an **MSBuild project SDK**, shipped as a NuGet package, the same way F# plugs into
`dotnet build`:

```xml
<Project Sdk="KiteNet.Sdk/0.1.0">
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net9.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
  </ItemGroup>
</Project>
```

The SDK builds on the normal .NET SDK and swaps in Kite's compiler, so `dotnet build`, `dotnet run`, NuGet
restore, project references to and from C# projects, and `dotnet sln add` all come for free. NuGet packages
that ship analyzers would run on Kite code too, because the code goes through Roslyn. What you don't get for
free is IntelliSense in Visual Studio or Rider; that needs a language server, which is separate work.

The SDK isn't built yet. The single-file compiler was enough for the experiment.

Next: the checks every answer goes through, and two traps that silently turned them off.
