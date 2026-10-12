"""Kite.NET training tasks: an outside LLM drafts them, the Kite compiler decides what is kept.

    python -m kitegym.tasks generate --per-family 40          # drafts -> real/sources/kite-tasks.jsonl
    python -m kitegym.tasks export                            # -> datasets/kite/{train,valid,test}.jsonl

A task is an English request, a Kite solution and Kite tests. A draft is kept only if `kite test` compiles it
with 0 errors and 0 warnings (Kite analyzer, C# compiler, .NET analyzers) and every test passes, with at least
3 expects. One repair round: the drafting model sees the compiler's messages once. Synthetic text only; nothing
private goes to the outside model. Split by family, so valid and test are new kinds of problems.
The training prompt teaches nothing about Kite: the model has to learn the language from the examples.
"""

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

from kitegym.common import DATASETS, ROOT

KITE = ROOT / "compiler"
SOURCE = ROOT / "sources" / "kite-tasks.jsonl"
GATEWAY = os.environ.get("KITE_LLM_URL", "http://100.64.0.4:4000/v1/chat/completions")
MODEL = os.environ.get("KITE_LLM_MODEL", "deepseek-v4-flash")
SYSTEM = "You write Kite.NET code. Answer with the code only."
REPAIR_ASK = "kite says:\n{report}\nFix the code. Answer with the code only."   # the harness's words (eval_kite)

# (family, what its tasks are about); the last VALID + TEST families are held out of training
FAMILIES = [
    ("text-basics", "string manipulation: case, trimming, reversing, counting characters, padding"),
    ("word-stats", "words in a sentence: counting, longest word, capitalizing, acronyms"),
    ("number-math", "integer arithmetic: digits, sums of digits, divisors, primes, gcd, factorial"),
    ("money", "prices, totals, discounts, tax and change with dec values, rounding to 2 places"),
    ("list-filter", "filtering and mapping lists of numbers with pipes"),
    ("list-aggregate", "sums, averages, min and max of lists, counting matches"),
    ("records-inventory", "a record type for products in stock: totals, low-stock lists, restocking"),
    ("records-people", "a record type for people: ages, grouping, oldest, names by city"),
    ("grades", "student scores: averages, letter grades with when, pass or fail, ranking"),
    ("temperature", "unit conversions: celsius, fahrenheit, kilometers, miles with dec"),
    ("loops-counting", "while and for loops: counting down, summing ranges, multiplication tables as lists"),
    ("fizzbuzz-like", "rules per number with when and if: fizzbuzz variants, labels for ranges"),
    ("maps-counting", "counting occurrences with map<str, int>, word frequencies, most common item"),
    ("sets", "unique items with set<T> or distinct: duplicates, common items, differences"),
    ("sorting", "sorting lists and records by keys, top-n, ascending and descending"),
    ("validation", "checking input strings: passwords, emails, codes, returning true/false or a message"),
    ("enums", "an enum type and when over it: traffic lights, days, order states, next state"),
    ("bank-account", "a record for an account with deposits and withdrawals, balance rules, fail on overdraft"),
    ("recursion", "recursive functions: fibonacci, power, sum of list, depth counting"),
    ("matrix", "lists of lists of int: row sums, transpose, diagonal, grid lookups"),
    ("dates-simple", "days, hours and minutes as ints: durations, formatting hh:mm, adding minutes"),
    ("shopping-cart", "a cart of item records: totals, quantities, applying coupons"),
    ("text-format", "building strings with interpolation: receipts, tables, labels, plural words"),
    ("search", "finding items: first match, index of, binary search on sorted lists, contains"),
    ("statistics", "median, mode, range, variance-like computations on lists of dec"),
    ("queues", "simulating queues and stacks with lists: push, pop, peek, processing in order"),
    ("games-score", "scoring games: bowling-like frames, dice totals, tennis points as when"),
    ("parsing", "parsing simple text: csv lines into records, key=value pairs, numbers in a string"),
    ("geometry", "shapes as records: areas, perimeters, distances with dec and sqrt"),
    ("scheduling", "time slots as records: overlaps, total busy time, free gaps"),
    ("library", "books and loans as records: available books, overdue loans, counts per author"),
    ("pipelines", "multi-step pipes: filter, map, sort_by, take, join in one expression"),
    ("optional", "nullable values T? and none: safe lookups with get, defaults, find returning none"),
    ("strings-encode", "simple encodings: caesar shift, run-length encoding, vowel removal"),
    ("tests-rich", "small functions with many edge cases: empty lists, zero, negative numbers"),
    ("restaurant", "orders and menu items as records: bills, splitting, tips"),
    # held out: valid
    ("weather-log", "daily weather readings as records: hottest day, rainy days, averages per week"),
    ("parking", "a parking lot: fees by hours with when, occupancy, which spots are free"),
    # held out: test
    ("clinic", "patients and appointments as records: waiting times, today's list, counts per doctor"),
    ("warehouse", "packages with weights as records: shipping cost bands, heaviest, grouping by city"),
    ("elections", "votes as a list of names: counting, winner, percentages, ties"),
    ("recipes", "recipes and ingredients: scaling quantities, shopping lists, total calories"),
    ("transit", "bus stops and trips: travel time, transfers, fares by zone with when"),
    ("timesheets", "work hours per person: overtime, weekly totals, pay with dec"),
]
VALID, TEST = 2, 6
# plain C#, test only: the "not dumber" check (learning Kite must not cost the model its C#)
CS_SOURCE = ROOT / "sources" / "csharp-tasks.jsonl"
CS_SYSTEM = "You write C# code. Answer with the code only."
CS_FAMILIES = [
    ("cs-strings", "string manipulation and counting in C#"),
    ("cs-linq", "LINQ over lists of numbers and records"),
    ("cs-records", "C# records for products, people or orders, with totals and lookups"),
    ("cs-math", "integer and decimal arithmetic: primes, gcd, rounding money"),
    ("cs-collections", "Dictionary and HashSet: counting, grouping, unique items"),
    ("cs-switch", "switch expressions and pattern matching for categories and states"),
    ("cs-loops", "loops and recursion: sequences, accumulations, searching"),
    ("cs-parsing", "parsing simple text into numbers and records"),
]
SPLIT = {f: ("test" if k >= len(FAMILIES) - TEST else "valid" if k >= len(FAMILIES) - TEST - VALID else "train")
         for k, (f, _) in enumerate(FAMILIES)}

lock = threading.Lock()


def llm(messages, temperature=0.9):
    key = (Path.home() / ".sivar" / "llm-gateway-key").read_text().strip()
    body = json.dumps({"model": MODEL, "messages": messages, "temperature": temperature}).encode()
    req = urllib.request.Request(GATEWAY, body, {"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    if "choices" not in d:                       # an empty account answers 200 with an error body
        raise RuntimeError(f"LLM error: {str(d)[:200]}")
    return d["choices"][0]["message"]["content"], d.get("usage", {})


def spec():
    readme = (KITE / "README.md").read_text(encoding="utf-8")
    lang = readme[readme.index("## The language in one page"):readme.index("## What `kite check` reports")]
    std = (KITE / "Kite" / "KiteStd.txt").read_text(encoding="utf-8")
    sigs = "\n".join(l.strip() for l in std.splitlines() if l.strip().startswith("public static") and "Std" not in l)
    return lang, sigs


DRAFT = """You are writing training tasks for Kite.NET, a new programming language. Here is its definition:

{lang}

Standard library signatures (C#, as Kite calls them, snake_case):
{sigs}

A complete example:
{example}

Rules for the code you write:
- Kite only, exactly as defined above. No semicolons, no braces, no var, no new, no && || !, no $ before strings.
- Indent with 4 spaces. Functions, variables and fields are snake_case, types PascalCase.
- Use `let` unless a variable is reassigned; then `let mut`. Never reassign a parameter.
- Never call a function and throw its result away. No print in solutions.
- `[]` only where the type is declared, e.g. `let out: list<int> = []`.
- Calling a type creates it: Item("a", 1.5, 2). Decimal literals look like 2.5.
- Prefer pipes and the standard library over loops when it reads better, but use both styles across tasks.

Write {n} different tasks for this family: {family}: {about}.
Vary difficulty from one small function to 2-3 functions plus a record or enum type.
Each task is a JSON object:
  "prompt": the request in plain English, as a user would ask it, naming the function(s) and their parameter and
            return types in Kite syntax, e.g. "Write fn average(xs: list<dec>) -> dec that ...". If the task needs
            a record or enum type, describe it exactly (name and fields) so the tests can use it.
  "solution": the Kite code: the type(s) and function(s), no module line, no main, no tests.
  "tests": one or more Kite `test "..."` blocks with at least 3 `expect` lines in total, covering edge cases.
Answer with a JSON array of {n} objects and nothing else."""

REPAIR = """This Kite.NET task does not compile cleanly or fails its tests. Fix the solution and/or the tests (tests
must stay meaningful: at least 3 expects, and they must match the prompt). Kite rules as before.

prompt: {prompt}

solution:
{solution}

tests:
{tests}

kite says:
{report}

Answer with one JSON object with "prompt", "solution" and "tests" and nothing else."""


def check(solution, tests, suffix=".kite"):
    """Compile and test with kite (.kite, or plain C# for .cs); (clean and passing, short report)."""
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(solution.rstrip() + "\n\n" + tests.rstrip() + "\n")
        path = f.name
    try:
        p = subprocess.run([str(KITE / "bin" / "kite"), "test", path, "--json"], capture_output=True, text=True,
                           timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        r = None
    finally:
        os.unlink(path)
    if r is None:
        return False, "kite crashed or timed out"
    expects = len(re.findall(r"^\s+expect[ (]", tests, re.M))
    ok = r["errors"] == 0 and r["warnings"] == 0 and r.get("tests") and all(t["pass"] for t in r["tests"]) and expects >= 3
    lines = [f"line {d['line']}: {d['severity']} {d['id']}: {d['message']}" for d in r["diagnostics"]]
    lines += [f"test {t['name']!r} failed: {t['error']}" for t in r.get("tests") or [] if not t["pass"]]
    if expects < 3:
        lines.append("fewer than 3 expect lines")
    return bool(ok), "\n".join(lines[:15])


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def family_batch(family, about, n, example, lang, sigs, seed):
    out, usage = [], {"prompt_tokens": 0, "completion_tokens": 0}
    prompt = DRAFT.format(lang=lang, sigs=sigs, example=example, n=n, family=family, about=about)
    try:
        text, u = llm([{"role": "user", "content": prompt + f"\n(batch {seed})"}])
        drafts = parse_json(text)
    except Exception as e:                          # noqa: BLE001 - a bad batch is skipped, the run goes on
        print(f"  {family}: draft failed: {e}", flush=True)
        return out, usage
    for k in usage:
        usage[k] += u.get(k, 0)
    for d in drafts if isinstance(drafts, list) else []:
        if not all(isinstance(d.get(k), str) for k in ("prompt", "solution", "tests")):
            continue
        ok, report = check(d["solution"], d["tests"])
        repaired = False
        if not ok:
            try:
                text, u = llm([{"role": "user", "content": REPAIR.format(report=report, **d)}], temperature=0.2)
                for k in usage:
                    usage[k] += u.get(k, 0)
                d2 = parse_json(text)
                if all(isinstance(d2.get(k), str) for k in ("prompt", "solution", "tests")):
                    ok, report = check(d2["solution"], d2["tests"])
                    d, repaired = d2, True
            except Exception:                       # noqa: BLE001
                pass
        if ok:
            out.append({"family": family, "split": SPLIT[family], "prompt": d["prompt"].strip(),
                        "solution": d["solution"].strip(), "tests": d["tests"].strip(), "repaired": repaired,
                        "drafted_by": MODEL})
    return out, usage


def generate(per_family, batch, workers, families=None):
    lang, sigs = spec()
    example = (KITE / "examples" / "shop.kite").read_text(encoding="utf-8")
    SOURCE.parent.mkdir(parents=True, exist_ok=True)
    have = {}
    if SOURCE.exists():
        for l in SOURCE.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            have[r["family"]] = have.get(r["family"], 0) + 1
    jobs = []
    for family, about in FAMILIES:
        if families and family not in families:
            continue
        need = per_family - have.get(family, 0)
        for s in range(max(0, -(-need // batch)) + (1 if need > 0 else 0)):   # one extra batch: drafts get dropped
            jobs.append((family, about, s))
    print(f"{len(jobs)} batches of {batch} with {MODEL}", flush=True)
    total = {"prompt_tokens": 0, "completion_tokens": 0}
    kept = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(family_batch, f, a, batch, example, lang, sigs, s) for f, a, s in jobs]
        for fut in cf.as_completed(futs):
            rows, usage = fut.result()
            with lock:
                for k in total:
                    total[k] += usage[k]
                with SOURCE.open("a", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r) + "\n")
                kept += len(rows)
            print(f"  kept {kept} so far; tokens in {total['prompt_tokens']:,} out {total['completion_tokens']:,}",
                  flush=True)


def export():
    rows = [json.loads(l) for l in SOURCE.read_text(encoding="utf-8").splitlines()]
    seen, out = set(), {"train": [], "valid": [], "test": []}
    for r in rows:
        key = re.sub(r"\s+", " ", r["solution"])
        if key in seen:                              # the same solution twice teaches nothing new
            continue
        seen.add(key)
        out[r["split"]].append({"messages": [{"role": "system", "content": SYSTEM},
                                             {"role": "user", "content": r["prompt"]},
                                             {"role": "assistant", "content": r["solution"]}],
                                "tests": r["tests"], "family": r["family"]})
    if CS_TRAIN_SOURCE.exists():
        for l in CS_TRAIN_SOURCE.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            out["train"].append({"messages": [{"role": "system", "content": CS_SYSTEM},
                                              {"role": "user", "content": r["prompt"]},
                                              {"role": "assistant", "content": r["solution"]}],
                                 "tests": r["tests"], "family": r["family"]})
    if REPAIRS.exists():               # the harness turn: kite's messages in, the fixed code out
        for l in REPAIRS.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            out["train"].append({"messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": r["prompt"]},
                                              {"role": "assistant", "content": r["broken"]},
                                              {"role": "user", "content": REPAIR_ASK.format(report=r["report"])},
                                              {"role": "assistant", "content": r["solution"]}],
                                 "tests": "", "family": r["family"]})
    folder = DATASETS / "kite"
    folder.mkdir(parents=True, exist_ok=True)
    random.Random(0).shuffle(out["train"])
    for split, rs in out.items():
        (folder / f"{split}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs), encoding="utf-8")
        print(f"{split}: {len(rs)} tasks, {len({r['family'] for r in rs})} families")
    if CS_SOURCE.exists():
        cs = [json.loads(l) for l in CS_SOURCE.read_text(encoding="utf-8").splitlines()]
        rows = [{"messages": [{"role": "system", "content": CS_SYSTEM}, {"role": "user", "content": r["prompt"]},
                              {"role": "assistant", "content": r["solution"]}], "tests": r["tests"], "family": r["family"]}
                for r in cs]
        (folder / "csharp-test.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        print(f"csharp-test: {len(rows)} tasks (not dumber)")


CS_DRAFT = """Write {n} different C# programming tasks for this family: {family}: {about}.
Each task is a JSON object:
  "prompt": the request in plain English naming the static method(s) and their C# signatures, e.g.
            "Write a static method `public static int CountVowels(string s)` in a static class Solution that ...".
            If a record type is needed, describe it exactly.
  "solution": C# code: `public static class Solution` with the method(s), plus any record types at top level.
            No usings (System, System.Collections.Generic and System.Linq are already imported), no Main, no Console.
            Nullable reference types are enabled; the code must compile with no warnings.
  "tests": C# code: `public static class Tests` with one or more methods marked `[KiteTest("what it checks")]`,
            each `public static void`, using `expect(bool condition, "what")` at least 3 times in total,
            each expect on its own line, covering edge cases.
Answer with a JSON array of {n} objects and nothing else."""


# C# mixed into training so learning Kite does not cost the model its C# (round 1 forgot it: 68% -> 0%);
# different topics from the guard set above, so the guard stays a test of things not trained on
CS_TRAIN_SOURCE = ROOT / "sources" / "csharp-train-tasks.jsonl"
CS_TRAIN_FAMILIES = [
    ("cs-text-format", "building strings: receipts, padding, joining, StringBuilder"),
    ("cs-inventory", "classes or records for stock items with totals and restocking"),
    ("cs-grades", "student scores: averages, letter grades, ranking"),
    ("cs-dates", "minutes and hours as ints: durations and hh:mm formatting"),
    ("cs-queues", "Queue and Stack: processing orders, undo, bracket matching"),
    ("cs-geometry", "shapes as records with areas, perimeters and distances"),
    ("cs-bank", "an account with deposits and withdrawals, exceptions on overdraft"),
    ("cs-search", "searching sorted and unsorted lists, binary search, index of"),
    ("cs-validation", "validating passwords, emails and codes"),
    ("cs-matrix", "int[,] or List<List<int>>: sums, transpose, lookups"),
]


def cs_batch(family, about, n, seed, split="test"):
    out, usage = [], {"prompt_tokens": 0, "completion_tokens": 0}
    try:
        text, u = llm([{"role": "user", "content": CS_DRAFT.format(n=n, family=family, about=about) + f"\n(batch {seed})"}])
        drafts = parse_json(text)
        for k in usage:
            usage[k] += u.get(k, 0)
    except Exception as e:                          # noqa: BLE001
        print(f"  {family}: draft failed: {e}", flush=True)
        return out, usage
    for d in drafts if isinstance(drafts, list) else []:
        if all(isinstance(d.get(k), str) for k in ("prompt", "solution", "tests")) and check(d["solution"], d["tests"], ".cs")[0]:
            out.append({"family": family, "split": split, "prompt": d["prompt"].strip(), "solution": d["solution"].strip(),
                        "tests": d["tests"].strip(), "drafted_by": MODEL})
    return out, usage


def generate_cs(per_family, batch, workers, train=False):
    families, target, split = (CS_TRAIN_FAMILIES, CS_TRAIN_SOURCE, "train") if train else (CS_FAMILIES, CS_SOURCE, "test")
    jobs = [(f, a, s) for f, a in families for s in range(-(-per_family // batch) + 1)]
    kept = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for fut in cf.as_completed([ex.submit(cs_batch, f, a, batch, s, split) for f, a, s in jobs]):
            rows, _ = fut.result()
            with lock, target.open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
                kept += len(rows)
            print(f"  C# kept {kept}", flush=True)


REPAIRS = ROOT / "sources" / "kite-repairs.jsonl"
# typical slips, most of them C# habits; a lesson is kept only if kite really reports an error for it
BREAKS = [
    ("semicolon", lambda c: re.sub(r"^(\s+(?:let|return) .+)$", r"\1;", c, count=1, flags=re.M)),
    ("and", lambda c: c.replace(" and ", " && ", 1)),
    ("or", lambda c: c.replace(" or ", " || ", 1)),
    ("not", lambda c: re.sub(r"\bnot ", "!", c, count=1)),
    ("dollar", lambda c: re.sub(r'(?<![$\w])"([^"]*\{)', r'$"\1', c, count=1)),
    ("none", lambda c: re.sub(r"\bnone\b", "null", c, count=1)),
    ("mut", lambda c: re.sub(r"\blet mut ", "let ", c, count=1)),
    ("var", lambda c: re.sub(r"^(\s+)let (?!mut)", r"\1var ", c, count=1, flags=re.M)),
    ("braces", lambda c: re.sub(r"^(fn .+[^=])\n", r"\1 {\n", c, count=1, flags=re.M)),
    ("str-type", lambda c: re.sub(r": str\b", ": string", c, count=1)),
]


def make_repairs(n, workers):
    """Repair lessons for the harness: broken code -> kite's real messages -> the fixed code (train families only)."""
    rows = [json.loads(l) for l in SOURCE.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if r["split"] == "train"]
    rnd = random.Random(1)
    jobs = []
    for r in rnd.sample(rows, min(len(rows), n * 4)):
        name, brk = rnd.choice(BREAKS)
        broken = brk(r["solution"])
        if broken != r["solution"]:
            jobs.append((r, name, broken))

    def one(job):
        r, name, broken = job
        ok, report = check(broken, r["tests"])
        return None if ok or "error" not in report else {"family": r["family"], "break": name, "prompt": r["prompt"],
                                                         "broken": broken, "report": report, "solution": r["solution"]}
    with cf.ThreadPoolExecutor(workers) as ex:
        kept = [x for x in ex.map(one, jobs) if x][:n]
    REPAIRS.write_text("".join(json.dumps(x) + "\n" for x in kept), encoding="utf-8")
    print(f"{len(kept)} repair lessons from {len(jobs)} broken programs -> {REPAIRS}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["generate", "generate-cs", "generate-cs-train", "repairs", "export"])
    ap.add_argument("--per-family", type=int, default=40)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--families", nargs="*")
    a = ap.parse_args()
    if a.action == "generate":
        generate(a.per_family, a.batch, a.workers, a.families)
    elif a.action == "generate-cs":
        generate_cs(a.per_family, a.batch, a.workers)
    elif a.action == "repairs":
        make_repairs(a.per_family, a.workers)
    elif a.action == "generate-cs-train":
        generate_cs(a.per_family, a.batch, a.workers, train=True)
    else:
        export()


if __name__ == "__main__":
    main()
