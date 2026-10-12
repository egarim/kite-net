# The Kite.NET compiler

A small made-up .NET language: no model has seen it, so a model that writes it
after training has learned it. `kite` compiles it with Roslyn straight to a .NET assembly, and every error and
warning points at the Kite line.

```bash
compiler/bin/kite run   examples/shop.kite      # compile and run main()
compiler/bin/kite test  examples/shop.kite      # run the test blocks (--json for machines)
compiler/bin/kite check examples/shop.kite      # errors and warnings only (--json)
compiler/bin/kite emit  examples/shop.kite      # the C# Roslyn compiles (compiler debugging)
compiler/tests/run.sh                           # the compiler's own checks
```

Needs the .NET 9 SDK. The first `kite` call builds the compiler.

## The language in one page

```
module Shop                                   # optional; one module per file
use System.Text                               # any .NET namespace

type Item = record(name: str, price: dec, qty: int)
type Color = enum(Red, Green, Blue)

fn total(items: list<Item>) -> dec =          # one expression
    items |> sum(i => i.price * i.qty)

fn size(n: int) -> str = when n               # pattern match: pattern -> value
    0 -> "empty"
    < 5 -> "a few"
    else -> "a lot"

fn countdown(from: int) -> list<int>          # a block: indented, return gives the value
    let out: list<int> = []                   # let: never reassigned (contents may change)
    let mut n = from                          # let mut: may be reassigned
    while n > 0
        push(out, n)
        n -= 1
    return out

fn main()
    let cart = [Item("tea", 2.5, 3)]          # calling a type creates it; [..] is a list
    print("total: {total(cart)}")             # every string interpolates; {{ for a literal {
    for i in 1..=3                            # 1..3 excludes 3, 1..=3 includes it
        if i == 2 and not (total(cart) > 10.0)
            print("two")
        elif i == 3
            print(if i > 2 then "big" else "small")
        else
            pass

test "total adds price times quantity"        # kite test runs these
    expect total([Item("a", 1.5, 2)]) == 3.0
```

| Kite | Meaning |
|---|---|
| `int` `dec` `float` `bool` `str` `char` | int, decimal, double, bool, string, char; `2.5` is a dec |
| `list<T>` `map<K, V>` `set<T>` `T?` | List, Dictionary, HashSet, nullable; `[]` needs a declared type; `map<str, int>()` makes one |
| `and` `or` `not` `none` | `&&` `||` `!` `null` (writing the C# ones is an error) |
| `x \|> f(y)` | `f(x, y)`; binds tighter than `==` and `and` |
| `if c then a else b` | an expression |
| `fail "message"` / `expect cond` | throw / a test check |
| `x.Method()` | any .NET member works |

Standard library (snake_case): `print len map filter fold sum count sort sort_by sort_desc sort_by_desc reverse
take skip distinct first last find any all has min_of max_of min_by max_by group_by range push get keys values
join split words upper lower trim starts ends replace chars repeat to_str to_int to_dec abs min max round sqrt
dec floor`. Source: `Kite/KiteStd.txt`.

## What `kite check` reports

| Id | From | Examples |
|---|---|---|
| KT000 | Kite parser | bad indentation, a statement Kite does not have |
| KT001 | Kite analyzer | reassigning a `let` or a parameter (error) |
| KT002 | Kite analyzer | `let mut` that is never reassigned |
| KT003 | Kite analyzer | names: functions and variables snake_case, types PascalCase |
| KT004 | Kite analyzer | C# written into Kite: `;` `{ }` `var` `new` `&&` `!` `$"` (error) |
| KT006 | Kite analyzer | a result thrown away: `upper(name)` as a statement |
| CSxxxx | C# compiler | type errors, unused variables, possible null dereference |
| CAxxxx | .NET analyzers | the SDK's default rules plus dead code, wasted LINQ, lost results (`Program.cs`, `On`) |

A program is clean when it has 0 errors and 0 warnings; the training data only keeps clean programs.

## How it works

`Front.cs` turns Kite into C# text with `#line` directives (the way Razor compiles .cshtml), `Program.cs`
parses it with Roslyn next to the standard library, compiles it in memory, runs the analyzers and either
reports, runs `main`, runs the tests or writes the .dll. No C# file is written.
