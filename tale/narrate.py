"""narrate: tell a Kite program as a Kite Tale, and check the round trip.

    python narrate.py story     shop.kite    # print the tale for a Kite program
    python narrate.py roundtrip shop.tale    # tale -> Kite -> tale -> Kite: is it the same program?
    python narrate.py corpus [--fly]         # narrate every task in ../sources/kite-tasks.jsonl

The round trip is the self-check: a story is faithful when telling its program again gives back the same
program. "Same" means the same parsed program: brackets, layout, lambda variable names and test names may differ.
No AI here either.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import tale

HERE = Path(__file__).resolve().parent


class NarrateError(Exception):
    """This Kite has no tale phrase yet; the message names what is missing."""


# ---------------------------------------------------------------- reading Kite

KITE_TOKEN = re.compile(r"""
    (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<chr>'(?:[^'\\]|\\.)')
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<id>[A-Za-z_]\w*)
  | (?P<op>\|>|=>|==|!=|<=|>=|->|\.\.=|\.\.|[-+*/%<>()\[\],.:?=])
  | (?P<sp>\s+)
  | (?P<bad>.)
""", re.X)


def kite_tokens(text: str) -> list[tuple[str, str]]:
    out = []
    for m in KITE_TOKEN.finditer(text):
        k = m.lastgroup
        if k == "sp":
            continue
        if k == "bad":
            raise NarrateError(f'Kite character "{m.group()}"')
        out.append((k, m.group()))
    return out


class KiteExpr:
    """Kite expressions as trees: pipes become calls, brackets disappear."""

    def __init__(self, text: str):
        self.t = kite_tokens(text)
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j][1] if j < len(self.t) else None

    def take(self, s):
        if self.peek() == s:
            self.i += 1
            return True
        return False

    def need(self, s):
        if not self.take(s):
            raise NarrateError(f'Kite I cannot read near "{self.peek()}"')

    def run(self):
        e = self.expr()
        if self.i != len(self.t):
            raise NarrateError(f'Kite I cannot read near "{self.peek()}"')
        return e

    def expr(self):
        if self.t[self.i:self.i + 2] and len(self.t) > self.i + 1 and self.t[self.i][0] == "id" \
                and self.peek(1) == "=>":
            v = self.peek()
            self.i += 2
            return ("lam", v, self.expr())
        if self.peek() == "(" and self.peek(2) == "," :
            raise NarrateError("a lambda with two parameters")
        if self.take("if"):
            c = self.expr()
            self.need("then")
            a = self.expr()
            self.need("else")
            return ("if", c, a, self.expr())
        if self.peek() == "when":
            raise NarrateError("when (pattern matching)")
        return self.orx()

    def orx(self):
        a = self.andx()
        while self.take("or"):
            a = ("bin", "or", a, self.andx())
        return a

    def andx(self):
        a = self.cmp()
        while self.take("and"):
            a = ("bin", "and", a, self.cmp())
        return a

    def cmp(self):
        a = self.pipe()
        if self.peek() in ("==", "!=", "<", ">", "<=", ">="):
            op = self.peek()
            self.i += 1
            return ("bin", op, a, self.pipe())
        return a

    def pipe(self):
        a = self.add()
        while self.take("|>"):
            if self.i >= len(self.t) or self.t[self.i][0] != "id":
                raise NarrateError("a pipe into something that is not a function")
            f = self.peek()
            self.i += 1
            args = self.args("(", ")") if self.peek() == "(" else []
            a = ("call", f, [a] + args)
        return a

    def add(self):
        a = self.mul()
        while self.peek() in ("+", "-"):
            op = self.peek()
            self.i += 1
            a = ("bin", op, a, self.mul())
        return a

    def mul(self):
        a = self.unary()
        while self.peek() in ("*", "/", "%"):
            op = self.peek()
            self.i += 1
            a = ("bin", op, a, self.unary())
        return a

    def unary(self):
        if self.take("not"):
            return ("not", self.unary())
        if self.take("-"):
            x = self.unary()
            return ("num", "-" + x[1]) if x[0] == "num" and not x[1].startswith("-") else ("neg", x)
        return self.postfix()

    def postfix(self):
        a = self.primary()
        while True:
            if self.take("."):
                name = self.peek()
                self.i += 1
                if self.peek() == "(":
                    raise NarrateError(f".NET method calls (.{name}())")
                a = ("field", a, name)
            elif self.peek() == "(" and a[0] == "var":
                a = ("call", a[1], self.args("(", ")"))
            elif self.peek() in ("[", "..", "..=", "?"):
                raise NarrateError(f'Kite "{self.peek()}" (indexing, ranges or ?)')
            else:
                return a

    def args(self, open_, close):
        self.need(open_)
        out = []
        if self.take(close):
            return out
        while True:
            out.append(self.expr())
            if self.take(close):
                return out
            self.need(",")

    def primary(self):
        if self.i >= len(self.t):
            raise NarrateError("Kite that ends too early")
        k, s = self.t[self.i]
        if s == "(":
            self.i += 1
            e = self.expr()
            self.need(")")
            return e
        if s == "[":
            return ("list", self.args("[", "]"))
        self.i += 1
        if k == "num":
            return ("num", s)
        if k == "str":
            return ("str", s)
        if k == "chr":
            return ("chr", s)
        if s in ("true", "false"):
            return ("bool", s)
        if s == "none":
            return ("none",)
        if k == "id":
            return ("var", s)
        raise NarrateError(f'Kite "{s}"')


@dataclass
class Fn:
    name: str
    params: list[tuple[str, str]]
    returns: str
    body: tuple


@dataclass
class Program:
    module: str | None = None
    enums: list[tuple[str, list[str]]] = field(default_factory=list)
    records: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    fns: list[Fn] = field(default_factory=list)
    tests: list[tuple[list[tuple[str, tuple]], list[tuple]]] = field(default_factory=list)
    let_types: dict[str, str] = field(default_factory=dict)


def resolve_names(e, prog: Program):
    """Calls to record types are constructions; Type.Member on an enum is a choice."""
    enums = {n for n, _ in prog.enums}
    records = {n for n, _ in prog.records}

    def go(x):
        if not isinstance(x, tuple) or not x:
            return x
        if x[0] == "call" and x[1] in records:
            return ("new", x[1], [go(a) for a in x[2]])
        if x[0] == "field" and x[1][0] == "var" and x[1][1] in enums:
            return ("enum", x[1][1], x[2])
        return tuple(go(a) if isinstance(a, tuple) else [go(b) for b in a] if isinstance(a, list) else a for a in x)

    return go(e)


def read_kite(text: str) -> Program:
    prog = Program()
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]
    i = 0
    raw_fns, raw_tests = [], []
    while i < len(lines):
        line = lines[i].rstrip()
        s = line.strip()
        i += 1
        body = []
        while i < len(lines) and lines[i].startswith((" ", "\t")):
            body.append(lines[i].strip())
            i += 1
        if s.startswith("module "):
            prog.module = s.split()[1]
        elif s.startswith("use "):
            raise NarrateError("use (.NET namespaces)")
        elif m := re.match(r"type\s+(\w+)\s*=\s*enum\s*\((.*)\)$", s):
            prog.enums.append((m.group(1), [x.strip() for x in m.group(2).split(",") if x.strip()]))
        elif m := re.match(r"type\s+(\w+)\s*=\s*record\s*\((.*)\)$", s):
            fields = []
            for part in split_params(m.group(2)):
                n, t = part.split(":", 1)
                fields.append((n.strip(), t.strip()))
            prog.records.append((m.group(1), fields))
        elif m := re.match(r"fn\s+(\w+)\s*\((.*)\)\s*(?:->\s*(.+?))?\s*(=\s*(.*))?$", s):
            if m.group(1) == "main":
                continue
            if not m.group(4):
                raise NarrateError("a step-by-step function body")
            if not m.group(3):
                raise NarrateError("a function that returns nothing")
            params = []
            for part in split_params(m.group(2)):
                n, t = part.split(":", 1)
                params.append((n.strip(), t.strip()))
            expr = " ".join([m.group(5) or ""] + body).strip()
            raw_fns.append((m.group(1), params, m.group(3).strip(), expr))
        elif m := re.match(r'test\s+"', s):
            lets, checks = [], []
            for b in body:
                if mm := re.match(r"let\s+(\w+)\s*(?::([^=]+))?=\s*(.+)$", b):
                    lets.append((mm.group(1), mm.group(3)))
                    if mm.group(2):
                        prog.let_types[mm.group(1)] = mm.group(2).strip()
                elif b.startswith("expect "):
                    checks.append(b[len("expect "):])
                else:
                    raise NarrateError("a test step other than let and expect")
            raw_tests.append((lets, checks))
        else:
            raise NarrateError(f'Kite line "{s[:40]}"')
    for name, params, ret, expr in raw_fns:
        if expr.startswith("when ") or " when" in expr.split("=")[0]:
            raise NarrateError("when (pattern matching)")
        prog.fns.append(Fn(name, params, ret, resolve_names(KiteExpr(expr).run(), prog)))
    for lets, checks in raw_tests:
        prog.tests.append(([(n, resolve_names(KiteExpr(e).run(), prog)) for n, e in lets],
                           [resolve_names(KiteExpr(c).run(), prog) for c in checks]))
    return prog


def split_params(s: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in s:
        depth += ch == "<"
        depth -= ch == ">"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return [p for p in parts if p.strip()]


# ---------------------------------------------------------------- the same program?

def canonical(prog: Program):
    """What must survive the round trip. Lambda variables are renamed by depth; test names are ignored."""

    def norm(x, depth=0, env=None):
        env = env or {}
        if not isinstance(x, tuple):
            return x
        if x[0] == "lam":
            v = f"λ{depth}"
            return ("lam", v, norm(x[2], depth + 1, {**env, x[1]: v}))
        if x[0] == "var" and x[1] in env:
            return ("var", env[x[1]])
        return tuple(norm(a, depth, env) if isinstance(a, tuple) else
                     [norm(b, depth, env) for b in a] if isinstance(a, list) else a for a in x)

    return {
        "enums": prog.enums,
        "records": prog.records,
        "fns": [(f.name, f.params, f.returns, norm(f.body)) for f in prog.fns],
        "tests": [(sorted((n, norm(e)) for n, e in lets), [norm(c) for c in checks]) for lets, checks in prog.tests],
    }


# ---------------------------------------------------------------- telling it as a tale

IF, COMP, OR, AND, NOT, CMP, FIN, ADD, MUL, POST, PRIM = range(11)
# "the sum of E for each x in L": fine before "is", "and" or the end, but its L would swallow "plus" or "rounded"
AGG = 5.5
SMALL_WORDS = {"a", "an", "the", "some", "of", "and", "or", "not", "is", "it", "its", "each", "every", "no",
               "nothing", "true", "false", "where", "whose", "if", "otherwise", "for", "in", "with", "when"}
PREPS = {"for", "in", "on", "to", "at", "from", "with", "by", "among", "under", "over", "after", "before", "of",
         "per", "into", "between", "about", "against", "within", "without", "since", "until"}
SIMPLE = {"len": "length", "first": "first", "last": "last", "words": "words", "chars": "letters",
          "distinct": "distinct", "abs": "absolute"}
TEXT_POSTFIX = {"upper": "in upper case", "lower": "in lower case", "trim": "trimmed"}
CMP_WORDS = {"==": "is", "!=": "is not", ">": "is more than", "<": "is less than", ">=": "is at least",
             "<=": "is at most"}


def words_of(name: str) -> str:
    """days_late -> days late; class_ -> class"""
    if name.endswith("_") and name[:-1] in tale.RESERVED:
        name = name[:-1]
    return name.replace("_", " ")


def pascal_words(name: str) -> str:
    return " ".join(w.lower() for w in re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name))


def article(w: str) -> str:
    return "an" if w[:1].lower() in "aeiou" else "a"


def plural(w: str) -> str:
    if re.search(r"[^aeiou]y$", w):
        p = w[:-1] + "ies"
    elif re.search(r"(s|x|z|ch|sh)$", w):
        p = w + "es"
    else:
        p = w + "s"
    if tale.singular(p) != w:
        raise NarrateError(f'a plural for "{w}" that reads back the same')
    return p


class Teller:
    def __init__(self, prog: Program):
        self.prog = prog
        self.fns = {f.name: f for f in prog.fns}
        self.records = dict(prog.records)
        self.enums = dict(prog.enums)
        self.slots: list[str] = []
        self.lets: set[str] = set()
        self.loops: list[str] = []

    # -- types
    def type_words(self, t: str) -> str:
        simple = {"str": "text", "int": "a whole number", "dec": "a number", "bool": "yes or no", "char": "a letter"}
        if t in simple:
            return simple[t]
        if m := re.fullmatch(r"list<(\w+)>", t):
            inner = m.group(1)
            lists = {"str": "texts", "int": "whole numbers", "dec": "numbers", "char": "letters"}
            if inner in lists:
                return "a list of " + lists[inner]
            if inner in self.records or inner in self.enums:
                ws = pascal_words(inner).split()
                return "a list of " + " ".join(ws[:-1] + [plural(ws[-1])])
            raise NarrateError(f"a list of {inner}")
        if t in self.records or t in self.enums:
            w = pascal_words(t)
            return f"{article(w)} {w}"
        raise NarrateError(f"the type {t}")

    # -- the program
    def story(self) -> str:
        out = [(pascal_words(self.prog.module).capitalize() if self.prog.module else "A tale") + "."]
        for name, members in self.prog.enums:
            w = pascal_words(name)
            ms = [tale.snake(pascal_words(m).split()) for m in members]
            listed = ms[0] if len(ms) == 1 else ", ".join(ms[:-1]) + " or " + ms[-1]
            out.append(f"{article(w).capitalize()} {w} is one of {listed}.")
        for name, fields in self.prog.records:
            w = pascal_words(name)
            fs = [f"{words_of(f)} ({self.type_words(t)})" for f, t in fields]
            listed = fs[0] if len(fs) == 1 else ", ".join(fs[:-1]) + " and " + fs[-1]
            out.append(f"{article(w).capitalize()} {w} has {listed}.")
        for f in self.prog.fns:
            out.append(self.rule(f))
        lets: dict[str, tuple] = {}
        for ls, _ in self.prog.tests:
            for n, e in ls:
                lets.setdefault(n, e)
        if lets:
            self.slots, self.lets = [], set()
            sup = []
            for n, e in lets.items():
                if e == ("list", []):
                    t = self.prog.let_types.get(n)
                    if not t:
                        raise NarrateError("an empty list given a name without its type")
                    sup.append(f"Suppose {n} is an empty list of {self.type_words(t)[len('a list of '):]}.")
                    continue
                if n in SMALL_WORDS:
                    raise NarrateError(f'a value named "{n}", a word tale needs for itself')
                sup.append(f"Suppose {n} is {self.need(e, IF)}.")
                self.lets.add(n)
            out.append("\n".join(sup))
        self.lets = set(lets)
        for _, checks in self.prog.tests:
            out.append("\n".join(f"For example, {self.need(c, IF)}." for c in checks))
        return "\n\n".join(out) + "\n"

    def header(self, f: Fn) -> str:
        ws = f.name.split("_")
        n = len(f.params)
        predicate = f.returns == "bool"

        def slot(p, t):
            ty = self.type_words(t)
            art = "some" if ty.startswith("a list") else article(p)
            return f"{art} {p} ({ty})"

        for p, _ in f.params:
            if p in SMALL_WORDS:
                raise NarrateError(f'an input named "{p}", a word tale needs for itself')
        if f.name in ("contains", "starts_with", "ends_with", "length", "sum", "number", "largest", "smallest",
                      "first", "last", "words", "letters", "distinct", "absolute", "remainder"):
            raise NarrateError(f"a function named like a tale phrase ({f.name})")
        if set(ws) & {"a", "an", "the", "some", "of", "and"} or (f.returns != "bool" and "is" in ws):
            raise NarrateError(f"a function name with a small word in it ({f.name})")
        slots = [slot(p, t) for p, t in f.params]
        if predicate:
            if n == 1:
                return f"{slots[0][0].upper()}{slots[0][1:]} {' '.join(ws)} when:"
            if len(ws) < n - 1:
                raise NarrateError(f"too few words in {f.name} to place its inputs")
            first = ws[:len(ws) - (n - 2)]
            rest = ws[len(ws) - (n - 2):]
            parts = [slots[0], " ".join(first), slots[1]]
            for w, s in zip(rest, slots[2:]):
                parts += [w, s]
            text = " ".join(parts)
            return text[0].upper() + text[1:] + " when:"
        lead, between = self.split_name(ws, n)
        parts = ["The", " ".join(lead)] + ([] if lead[-1] in PREPS else ["of"]) + [slots[0]]
        for w, s in zip(between, slots[1:]):
            parts += [w, s]
        return " ".join(parts) + f" is {self.type_words(f.returns)}:"

    @staticmethod
    def split_name(ws: list[str], n: int) -> tuple[list[str], list[str]]:
        """share_among(bill, people): "share" leads, "among" sits between; shared_hobbies(a, b): joined by "and"."""
        if n > 1 and len(ws) >= n and all(w in PREPS for w in ws[len(ws) - n + 1:]):
            return ws[:len(ws) - n + 1], ws[len(ws) - n + 1:]
        return ws, ["and"] * (n - 1)

    def rule(self, f: Fn) -> str:
        self.slots, self.lets, self.loops = [p for p, _ in f.params], set(), []
        return self.header(f) + "\n" + self.need(f.body, IF) + "."

    def call_phrase(self, f: Fn, args: list[str]) -> str:
        ws = f.name.split("_")
        n = len(f.params)
        if f.returns == "bool":
            if n == 1:
                return f"{args[0]} {' '.join(ws)}"
            first, rest = ws[:len(ws) - (n - 2)], ws[len(ws) - (n - 2):]
            parts = [args[0], " ".join(first), args[1]]
            for w, a in zip(rest, args[2:]):
                parts += [w, a]
            return " ".join(parts)
        lead, between = self.split_name(ws, n)
        parts = ["the", " ".join(lead)] + ([] if lead[-1] in PREPS else ["of"]) + [args[0]]
        for w, a in zip(between, args[1:]):
            parts += [w, a]
        return " ".join(parts)

    # -- expressions
    def boxed(self, e, level) -> str:
        """A list or a build followed by more words: brackets keep its "and" to itself."""
        text = self.need(e, level)
        return f"({text})" if e[0] in ("list", "new") and not text.startswith("(") else text

    def need(self, e, level) -> str:
        text, lv = self.tell(e)
        return text if lv >= level else f"({text})"

    def loop(self, var: str, e, level: int) -> str:
        """Tell e with var as the innermost loop variable; a nested "for each" goes in brackets."""
        self.loops.append(var)
        try:
            text = self.need(e, level)
        finally:
            self.loops.pop()
        if re.search(r"\bfor (each|some|every)\b", strip_brackets(text)) or "," in strip_brackets(text):
            text = f"({text})"
        return text

    def lam(self, x) -> tuple[str, tuple]:
        if x[0] != "lam":
            raise NarrateError("a function passed by name, not a lambda")
        if x[1] in SMALL_WORDS:
            raise NarrateError(f'a loop variable named "{x[1]}", a word tale needs for itself')
        return x[1], x[2]

    def binder(self, src, lam, cond=None) -> str:
        v, _ = self.lam(lam)
        text = f"{v} in {self.need(src, FIN)}"
        if cond is not None:
            text += f" where {self.loop(v, self.lam(cond)[1], OR)}"
        return text

    def tell(self, e) -> tuple[str, int]:
        k = e[0]
        if k == "num":
            return e[1], PRIM
        if k == "str":
            inner = e[1][1:-1].replace("{{", "\0").replace("}}", "\1")
            if "{" in inner or "}" in inner:
                raise NarrateError("a string with {…} inside")
            return '"' + inner.replace("\0", "{").replace("\1", "}") + '"', PRIM
        if k in ("chr", "bool"):
            return e[1], PRIM
        if k == "none":
            return "nothing", PRIM
        if k == "list":
            if not e[1]:
                return "an empty list", PRIM
            items = [self.need(x, ADD) for x in e[1]]
            items = [f"({t})" if x[0] in ("list", "new") else t for t, x in zip(items, e[1])]
            return "a list of " + (items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]), PRIM
        if k == "var":
            v = e[1]
            if self.loops and v == self.loops[-1]:
                return "it", PRIM
            if v in self.loops or v in self.slots:
                return f"the {v}", PRIM
            if v in self.lets:
                return v, PRIM
            raise NarrateError(f'a name I cannot place: "{v}"')
        if k == "field":
            obj, f = e[1], e[2]
            if obj[0] != "var":
                raise NarrateError("a field of a computed value")
            if self.loops and obj[1] == self.loops[-1]:
                return f"its {words_of(f)}", PRIM
            owner, _ = self.tell(obj)
            return f"{owner}'s {words_of(f)}", PRIM
        if k == "enum":
            return tale.snake(pascal_words(e[2]).split()), PRIM
        if k == "new":
            fields = self.records[e[1]]
            w = pascal_words(e[1])
            if " " in w:
                raise NarrateError("building a kind whose name has more than one word")
            vals = [f"{words_of(f)} {self.boxed(v, ADD) if j < len(fields) - 1 else self.need(v, ADD)}"
                    for j, ((f, _), v) in enumerate(zip(fields, e[2]))]
            listed = vals[0] if len(vals) == 1 else ", ".join(vals[:-1]) + " and " + vals[-1]
            return f"{article(w)} {w} with {listed}", PRIM
        if k == "not":
            return f"not {self.need(e[1], NOT)}", NOT
        if k == "neg":
            raise NarrateError("a minus sign in front of a value")
        if k == "if":
            c, a, b = e[1], e[2], e[3]
            rest = self.need(b, IF) if b[0] == "if" else f"otherwise {self.need(b, IF)}"
            return f"{self.need(a, COMP)} if {self.need(c, COMP)}, {rest}", IF
        if k == "bin":
            op, a, b = e[1], e[2], e[3]
            if op == "or":
                return f"{self.need(a, OR)} or {self.need(b, AND)}", OR
            if op == "and":
                return f"{self.need(a, AND)} and {self.need(b, NOT)}", AND
            if op in CMP_WORDS:
                return f"{self.need(a, AGG)} {CMP_WORDS[op]} {self.need(b, AGG)}", CMP
            if op == "+":
                return f"{self.need(a, ADD)} plus {self.need(b, MUL)}", ADD
            if op == "-":
                return f"{self.need(a, ADD)} minus {self.need(b, MUL)}", ADD
            if op == "*":
                return f"{self.need(a, MUL)} times {self.need(b, POST)}", MUL
            if op == "/":
                return f"{self.need(a, MUL)} divided by {self.need(b, POST)}", MUL
            if op == "%":
                return f"the remainder of {self.need(a, POST)} divided by {self.need(b, POST)}", PRIM
        if k == "lam":
            raise NarrateError("a lambda outside a list phrase")
        if k == "call":
            return self.tell_call(e[1], e[2])
        raise NarrateError(f"Kite {k}")

    def tell_call(self, f: str, args: list) -> tuple[str, int]:
        n = len(args)
        if f in self.fns:
            fn = self.fns[f]
            if fn.returns == "bool":
                told = [self.need(args[0], AGG)] + [self.need(a, POST) for a in args[1:]]
                return self.call_phrase(fn, told), CMP
            told = [self.boxed(a, ADD) for a in args[:-1]] + [self.need(args[-1], POST)]
            return self.call_phrase(fn, told), PRIM
        if f in SIMPLE and n == 1:
            return f"the {SIMPLE[f]} of {self.need(args[0], POST)}", PRIM
        if f in TEXT_POSTFIX and n == 1:
            return f"{self.need(args[0], POST)} {TEXT_POSTFIX[f]}", POST
        if f == "join" and n == 2 and args[0][0] == "call" and args[0][1] == "reverse" \
                and args[0][2][0][0] == "call" and args[0][2][0][1] == "chars" and args[1] == ("str", '""'):
            return f"{self.need(args[0][2][0][2][0], POST)} spelled backwards", POST
        if f == "reverse" and n == 1:
            return f"{self.need(args[0], FIN)} reversed", FIN
        if f == "sort" and n == 1:
            return f"{self.need(args[0], FIN)} sorted", FIN
        if f == "sort_desc" and n == 1:
            return f"{self.need(args[0], FIN)} sorted, largest first", FIN
        if f in ("sort_by", "sort_by_desc") and n == 2:
            v, body = self.lam(args[1])
            key = self.loop(v, body, PRIM)
            return f"{self.need(args[0], FIN)} sorted by {key}" + (", largest first" if f == "sort_by_desc" else ""), FIN
        if f in ("join", "split") and n == 2:
            return f"{self.need(args[0], FIN)} {'joined with' if f == 'join' else 'split by'} {self.need(args[1], PRIM)}", FIN
        if f == "round" and n == 2:
            return f"{self.need(args[0], FIN)} rounded to {self.need(args[1], PRIM)} places", FIN
        if f == "dec" and n == 1:
            return f"{self.need(args[0], FIN)} as a number", FIN
        if f == "to_str" and n == 1:
            return f"{self.need(args[0], FIN)} as text", FIN
        if f in ("has", "starts", "ends") and n == 2:
            verb = {"has": "contains", "starts": "starts with", "ends": "ends with"}[f]
            return f"{self.need(args[0], AGG)} {verb} {self.need(args[1], AGG)}", CMP
        if f in ("sum", "max_of", "min_of"):
            word = {"sum": "sum", "max_of": "largest", "min_of": "smallest"}[f]
            if f == "sum" and n == 2:
                return self.aggregate(word, args[0], args[1])
            if n == 1 and f != "sum" and args[0][0] == "call" and args[0][1] == "map" and len(args[0][2]) == 2:
                return self.aggregate(word, args[0][2][0], args[0][2][1])
            if n == 1:
                return f"the {word} of {self.need(args[0], POST)}", PRIM
        if f == "count" and n == 2:
            v, body = self.lam(args[1])
            return (f"the number of {plural(v)} in {self.need(args[0], FIN)} "
                    f"where {self.loop(v, body, OR)}"), COMP
        if f in ("map", "any", "all") and n == 2:
            v, body = self.lam(args[1])
            src, cond = args[0], None
            if f == "map" and src[0] == "call" and src[1] == "filter" and len(src[2]) == 2 \
                    and src[2][1][0] == "lam" and src[2][1][1] == v:
                src, cond = src[2][0], src[2][1]
            q = {"map": "each", "any": "some", "all": "every"}[f]
            return f"{self.loop(v, body, OR)} for {q} {self.binder(src, args[1], cond)}", COMP
        if f == "filter" and n == 2:
            v, _ = self.lam(args[1])
            return f"each {self.binder(args[0], args[1], args[1])}", COMP
        raise NarrateError(f"{f} with {n} input(s)")

    def aggregate(self, word, src, lam) -> tuple[str, int]:
        v, body = self.lam(lam)
        cond = None
        if src[0] == "call" and src[1] == "filter" and len(src[2]) == 2 and src[2][1][0] == "lam" \
                and src[2][1][1] == v:
            src, cond = src[2][0], src[2][1]
        text = f"the {word} of {self.loop(v, body, ADD)} for each {self.binder(src, lam, cond)}"
        return text, (COMP if cond else AGG)


def strip_brackets(text: str) -> str:
    """The text outside brackets and quotes."""
    out, depth, q = [], 0, False
    for ch in text:
        if ch == '"':
            q = not q
            continue
        if q:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------- round trip

def narrate(kite_text: str) -> str:
    return Teller(read_kite(kite_text)).story()


def roundtrip_kite(kite_text: str) -> tuple[str, str, bool, str]:
    """Kite -> tale -> Kite. Returns (story, kite again, same?, why not)."""
    before = read_kite(kite_text)
    story = Teller(before).story()
    try:
        again, _ = tale.to_kite(tale.read_tale(story))
    except tale.TaleError as e:
        return story, "", False, f"the story does not frame: line {e.line}: {e}"
    after = read_kite(again)
    a, b = canonical(before), canonical(after)
    if a == b:
        return story, again, True, ""
    for key in a:
        if a[key] != b[key]:
            for x, y in zip(a[key], b[key]):
                if x != y:
                    return story, again, False, f"{key} differ: {str(x)[:300]}  vs  {str(y)[:300]}"
            return story, again, False, f"{key} differ in count"
    return story, again, False, "differ"


def roundtrip_tale(path: Path) -> tuple[bool, str, str]:
    kite1, _ = tale.compile_file(path)
    story, _, same, why = roundtrip_kite(kite1)
    return same, why, story


def corpus(fly: bool):
    src = HERE.parent / "sources" / "kite-tasks.jsonl"
    tasks = [json.loads(l) for l in src.open(encoding="utf-8")]
    out_dir = HERE / "build" / "corpus"
    out_dir.mkdir(parents=True, exist_ok=True)

    def one(i):
        t = tasks[i]
        kite_text = t["solution"] + "\n\n" + t["tests"] + "\n"
        try:
            story, again, same, why = roundtrip_kite(kite_text)
        except NarrateError as e:
            return i, "no phrase", str(e), None
        except Exception as e:  # a narrator bug: count it, never hide it
            return i, "narrator error", f"{type(e).__name__}: {e}", None
        if not same:
            return i, "not the same", why, story
        if fly:
            p = out_dir / f"task{i}.tale"
            p.write_text(story, encoding="utf-8")
            res = tale.run_kite("test", p, again, {})
            ok = res.get("ok") and all(x["pass"] for x in res.get("tests", []))
            if not ok:
                return i, "does not fly", json.dumps(res)[:300], story
        return i, "ok", "", story

    with ThreadPoolExecutor(8 if fly else 1) as ex:
        results = list(ex.map(one, range(len(tasks))))
    counts = Counter(r[1] for r in results)
    reasons = Counter(r[2] for r in results if r[1] == "no phrase")
    print(f"{len(tasks)} tasks: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print("\nmissing phrases, most common first:")
    for why, n in reasons.most_common(25):
        print(f"  {n:4}  {why}")
    for kind in ("narrator error", "not the same", "does not fly"):
        bad = [r for r in results if r[1] == kind]
        if bad:
            print(f"\n{kind} ({len(bad)}), first few:")
            for i, _, why, _ in bad[:8]:
                print(f"  task {i} ({tasks[i]['family']}): {why[:400]}")
    pairs = HERE / "build" / "tale-pairs.jsonl"
    with pairs.open("w", encoding="utf-8") as fh:
        for i, kind, _, story in results:
            if kind == "ok":
                t = tasks[i]
                fh.write(json.dumps({"family": t["family"], "split": t["split"], "prompt": t["prompt"],
                                     "tale": story, "kite": t["solution"], "tests": t["tests"]}) + "\n")
    print(f"\n{counts['ok']} story/code pairs written to {pairs.relative_to(HERE)}")
    by_split = Counter(tasks[r[0]]["split"] for r in results if r[1] == "ok")
    print("by split: " + ", ".join(f"{k} {v}" for k, v in sorted(by_split.items())))


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) >= 3 and argv[1] == "story":
        try:
            print(narrate(Path(argv[2]).read_text(encoding="utf-8")), end="")
        except NarrateError as e:
            print(f"{argv[2]}: no tale phrase yet for {e}")
            return 1
        return 0
    if len(argv) >= 3 and argv[1] == "roundtrip":
        try:
            same, why, story = roundtrip_tale(Path(argv[2]))
        except (NarrateError, tale.TaleError) as e:
            print(f"{argv[2]}: {e}")
            return 1
        print(story if "--show" in argv else "", end="")
        print("same program" if same else f"NOT the same program: {why}")
        return 0 if same else 1
    if len(argv) >= 2 and argv[1] == "corpus":
        corpus("--fly" in argv)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
