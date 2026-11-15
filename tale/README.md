# Kite Tale

**Can a specification be a story that compiles?** Kite Tale is plain English with strict sentence patterns.
You write what you want as a short story. `tale` turns it into [Kite.NET](../README.md)
without any AI, and Kite compiles it to .NET. Every "For example" in the story becomes a test.

```
A shop.

An item has a name (text), a price (money) and a quantity (a whole number).

The total of some items (a list of items) is money:
the sum of its price times its quantity for each item in the items.

Suppose tea is an item with name "tea", price 2.5 and quantity 2.
For example, the total of a list of tea and tea is 10.
For example, the total of no items is 0.
```

becomes

```
module Shop

type Item = record(name: str, price: dec, quantity: int)

fn total(items: list<Item>) -> dec =
    (items |> sum(item => (item.price * item.quantity)))

test "example 1: the total of a list of tea and tea is 10"
    let tea = Item("tea", 2.5, 2)
    expect (total([tea, tea]) == 10)
    expect (total([]) == 0)
```

When a sentence is unclear, `tale` asks instead of guessing:

```
colour.tale:6: "colour" is not a field of any kind I know. Did you mean "color"?
its.tale:6: "its" has nothing to point to here: there is no "for each" and more than one input. Name it, like "the item's price".
missing-field.tale:8: "an item with …" is missing quantity.
wrong-type.tale:3: error CS0029: this gives 'text', but the rule says it gives 'a whole number'
```

## The plan

```
Free narration  --(AI, next)-->  .tale  --(tale, no AI)-->  .kite  --(kite)-->  .NET + tests
```

1. **Done (v0):** the language ([TALE.md](TALE.md)), the compiler ([tale.py](tale.py)), 20 sample tales, all
   compiling clean (0 errors, 0 warnings) with every example passing, plus 5 broken tales the compiler refuses.
2. **Next:** a narrator that goes the other way, Kite → tale, run over kite-net's 1,506 checked tasks to get
   story/code pairs for free.
3. **Then:** train a small model (same gym as Kite.NET) to turn loose narration into a strict tale. It is graded
   by the machine: the tale compiles, and the hidden tests pass.

## Run it

Needs Python 3.10+ and the .NET 9 SDK (.NET 10 works too: `tale` sets `DOTNET_ROLL_FORWARD=Major`). It uses this
repo's Kite compiler and builds it on first use; set `KITE_DLL` to use another one. Run from this folder (`tale/`).

```bash
python tale.py emit  samples/01-shop.tale    # print the Kite
python tale.py frame samples/01-shop.tale    # compile and check: clean, or a question about what you meant
python tale.py fly   samples/01-shop.tale    # compile, then run the examples
python run_samples.py                        # everything
```

## What v0 does not do yet

- Rule bodies are one expression: no step-by-step blocks, loops with state, maps/dictionaries or recursion.
- Only one rule can start with an input (`A word (text) is a palindrome when:`) per phrase shape.
- The grammar is small on purpose. It grows when the narrator meets Kite it cannot retell.
