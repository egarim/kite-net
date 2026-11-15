# Kite Tale, version 0

A Kite Tale is a short story about what a program should do. It is written in plain English with a small
set of sentence patterns. The `tale` compiler turns it into [Kite.NET](https://github.com/egarim/kite-net)
without any AI. If a sentence is unclear, the compiler asks a question instead of guessing.

```
Free narration  --(AI, later)-->  .tale (this file's rules)  --(tale, deterministic)-->  .kite  -->  .NET
```

## A whole tale

```
A shop.

An item has a name (text), a price (money) and a quantity (a whole number).

The total of some items (a list of items) is money:
the sum of its price times its quantity for each item in the items.

Suppose tea is an item with name "tea", price 2.5 and quantity 2.
For example, the total of a list of tea and tea is 10.
For example, the total of no items is 0.
```

- The first line is the title (it becomes the module name).
- Paragraphs are separated by blank lines. Sentences end with a period. Lines starting with `Note:` are ignored.

## Kinds of thing

| Tale | Kite |
|---|---|
| `An item has a name (text), a price (money) and a quantity (a whole number).` | `type Item = record(name: str, price: dec, quantity: int)` |
| `A color is one of red, green or blue.` | `type Color = enum(Red, Green, Blue)` |

Types: `text`, `money` / `a number` (dec), `a whole number` (int), `yes or no` (bool), `a letter` (char),
`an item` (a kind you declared), `a list of items`, `a list of whole numbers`, `a list of texts`,
`a list of letters`.

## Rules (functions)

| Tale header | Kite |
|---|---|
| `The total of some items (a list of items) is money:` | `fn total(items: list<Item>) -> dec` |
| `The share of a bill (money) among some people (a whole number) is money:` | `fn share_among(bill: dec, people: int) -> dec` |
| `A word (text) is a palindrome when:` | `fn is_palindrome(word: str) -> bool` |
| `An account (an account) can pay an amount (money) when:` | `fn can_pay(account: Account, amount: dec) -> bool` |

- A slot is `<a/an/some/the> <name> (<type>)`. The name is the word just before the brackets.
- The function name is every word outside the slots, minus `the`, `a`, `an`, `some` and `of` (and `is`,
  except in a yes-or-no rule). For example,
  `the share of … among …` becomes `share_among`, and `the price after tax of …` becomes `price_after_tax`,
  `… is a palindrome` becomes `is_palindrome` and `… can pay …` becomes `can_pay`.
- The words around the slots form the phrase you use to call it: `the share of 90 among 3`.
- A `… is a palindrome when:` rule is a yes-or-no rule. You call it as `"level" is a palindrome`, and
  `is not a palindrome` negates it.
- The body is one expression and ends with a period.

## Expressions, loosest first

| Tale | Kite |
|---|---|
| `A if C, B if D, otherwise E` | `if C then A else if D then B else E` |
| `E for each x in L` | `L \|> map(x => E)` |
| `each x in L where C` / `… whose price is …` | `L \|> filter(x => C)` (`whose f` = `where its f`) |
| `E for each x in L where C` | filter, then map |
| `C for some x in L` / `C for every x in L` | `any` / `all` |
| `A or B`, `A and B`, `not A` | `or`, `and`, `not` |
| `A is B`, `A is not B` | `==`, `!=` |
| `A is more than B` / `less than` / `at least` / `at most` | `>` `<` `>=` `<=` |
| `A contains B`, `A starts with B`, `A ends with B` | `has` `starts` `ends` |
| `A plus B`, `A minus B` | `+` `-` |
| `A times B`, `A divided by B`, `the remainder of A divided by B` | `*` `/` `%` |
| `X in upper case`, `in lower case`, `trimmed`, `spelled backwards` | stick to the word right before them (`the letters of the word in lower case`) |
| `X rounded to 2 places`, `joined with`, `sorted`, `reversed`, `as text` | apply to the whole phrase before them (`a divided by b rounded to 2 places`) |
| `( … )` | grouping, whenever English is not clear enough |

`and` joins items in a list (`a list of tea, cake and milk`) and fields in a `with` (see below) before it
means logical *and*. Use brackets when you need both.

## Things you can name

| Tale | Kite |
|---|---|
| `the items`, `items`, `the item` | a slot or loop variable |
| `its price`, `the item's price` | `item.price` (`its` = the closest `for each` variable, or the only slot) |
| `42`, `2.5`, `"tea"`, `'a'`, `true`, `false`, `nothing` | literals (`2.5` is money) |
| `red` (a declared choice) | `Color.Red` |
| `an item with name "tea", price 2.5 and quantity 2` | `Item("tea", 2.5, 2)` (any order) |
| `a list of A, B and C`, `no items` / `an empty list` | `[A, B, C]`, `[]` |

Built-in phrases (`X` is any expression):

| Tale | Kite | Tale | Kite |
|---|---|---|---|
| `the length of X` / `the number of X` | `len(X)` | `the number of x in L where C` | `count` |
| `the sum of X` / `the sum of E for each …` | `sum` | `the largest of X` / `the smallest of X` | `max_of` / `min_of` |
| `the first of X` / `the last of X` | `first` / `last` | `the words of X` / `the letters of X` | `words` / `chars` |
| `X in upper case` / `X in lower case` | `upper` / `lower` | `X trimmed` / `X reversed` / `X sorted` | `trim` / `reverse` / `sort` |
| `X joined with S` / `X split by S` | `join` / `split` | `the distinct of X` | `distinct` |
| `X sorted by E` / `X sorted by E, largest first` | `sort_by` / `sort_by_desc` | `the absolute of X` | `abs` |
| `X rounded to N places` | `round(X, N)` | `X as text` | `to_str(X)` |

## Examples are the tests

- `For example, <yes-or-no expression>.` becomes `expect …`. `the total of … is 10` is just a comparison.
- `Suppose tea is <expression>.` names a value for the examples after it. A test only includes the values it uses.
- All examples in one paragraph form one `test` block, named after the paragraph's first rule or `examples N`.

## When the compiler is not sure, it asks

- `I don't know "colour". Did you mean "color"?`
- `"its" has nothing to point to here: there is no "for each" and more than one slot. Say "the item's price".`
- `"an item with name …" is missing quantity.`
- Kite errors are reported against the tale line that produced them.
