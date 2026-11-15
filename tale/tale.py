"""tale: compile a Kite Tale (a story in strict plain English) to Kite.NET. No AI: same input, same output.

    python tale.py emit  shop.tale     # print the Kite program
    python tale.py check shop.tale     # compile with kite; errors point at tale lines
    python tale.py test  shop.tale     # run the examples as tests

The rules are in TALE.md.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ARTICLES = {"a", "an", "the", "some"}


class TaleError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


# ---------------------------------------------------------------- words

@dataclass
class Tok:
    kind: str   # w word, num, str, chr, p punctuation, poss ('s)
    text: str
    line: int

    def __repr__(self):
        return self.text


TOKEN_RX = re.compile(r"""
    (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<chr>'(?:[^'\\]|\\.)')
  | (?P<num>-?\d+(?:\.\d+)?)
  | (?P<w>[A-Za-z][A-Za-z_-]*)(?P<poss>'s\b)?
  | (?P<p>[,.:()])
  | (?P<sp>\s+)
  | (?P<bad>.)
""", re.X)


def tokenize(text: str, line: int) -> list[Tok]:
    out: list[Tok] = []
    for m in TOKEN_RX.finditer(text):
        here = line + text.count("\n", 0, m.start())
        k = m.lastgroup if m.lastgroup != "poss" else "w"
        if m.group("sp"):
            continue
        if m.group("bad"):
            raise TaleError(f'I can\'t read "{m.group("bad")}" here.', here)
        if m.group("w"):
            out.append(Tok("w", m.group("w").lower(), here))
            if m.group("poss"):
                out.append(Tok("poss", "'s", here))
        elif m.group("str"):
            out.append(Tok("str", m.group("str"), here))
        elif m.group("chr"):
            out.append(Tok("chr", m.group("chr"), here))
        elif m.group("num"):
            out.append(Tok("num", m.group("num"), here))
        else:
            out.append(Tok("p", m.group("p"), here))
    return out


def sentences(tokens: list[Tok]) -> list[list[Tok]]:
    out, cur, depth = [], [], 0
    for t in tokens:
        if t.kind == "p" and t.text == "(":
            depth += 1
        elif t.kind == "p" and t.text == ")":
            depth -= 1
        if t.kind == "p" and t.text == "." and depth == 0:
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        raise TaleError("This sentence has no period at the end.", cur[-1].line)
    return out


def words(toks: list[Tok]) -> list[str]:
    return [t.text for t in toks]


def snake(ws: list[str]) -> str:
    return "_".join(w.replace("-", "_") for w in ws)


def pascal(ws: list[str]) -> str:
    return "".join(p.capitalize() for w in ws for p in w.replace("-", "_").split("_"))


def singular(w: str) -> str:
    if w.endswith("ies"):
        return w[:-3] + "y"
    if re.search(r"(ss|x|ch|sh)es$", w):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def singulars(w: str) -> list[str]:
    c = [w]
    if w.endswith("ies"):
        c.append(w[:-3] + "y")
    if w.endswith("es"):
        c.append(w[:-2])
    if w.endswith("s"):
        c.append(w[:-1])
    return c


def suggest(word: str, known) -> str:
    m = difflib.get_close_matches(word, list(known), n=1)
    return f' Did you mean "{m[0].replace("_", " ")}"?' if m else ""


# ---------------------------------------------------------------- the program

@dataclass
class Kind:
    name: str                       # Kite type name
    fields: list[tuple[str, str]]   # (kite field, kite type)
    line: int


@dataclass
class Rule:
    pattern: list                   # str literal words, or int slot index
    slots: list[tuple[str, str]]    # (kite name, kite type)
    returns: str
    name: str
    predicate: bool
    body: list[Tok]
    line: int


@dataclass
class Tale:
    module: str = "Tale"
    kinds: dict[str, Kind] = field(default_factory=dict)        # tale word -> Kind
    choices: dict[str, tuple[str, str]] = field(default_factory=dict)  # word -> (Enum, Member)
    enums: list[tuple[str, list[str], int]] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    supposes: list[tuple[str, list[Tok], int]] = field(default_factory=list)
    groups: list[list[tuple[list[Tok], int, str]]] = field(default_factory=list)

    def field_names(self) -> set[str]:
        return {f for k in self.kinds.values() for f, _ in k.fields}


def kind_for(tale: Tale, w: str) -> Kind | None:
    for c in singulars(w):
        if c in tale.kinds:
            return tale.kinds[c]
    return None


def parse_type(tale: Tale, toks: list[Tok], line: int) -> str:
    ws = [w for w in words(toks) if w not in ("a", "an")]
    s = " ".join(ws)
    simple = {"text": "str", "money": "dec", "number": "dec", "whole number": "int", "yes or no": "bool",
              "letter": "char", "texts": "str", "numbers": "dec", "whole numbers": "int", "letters": "char"}
    if ws[:2] == ["list", "of"]:
        inner = " ".join(ws[2:])
        if inner in simple:
            return f"list<{simple[inner]}>"
        k = kind_for(tale, inner)
        if k:
            return f"list<{k.name}>"
        raise TaleError(f'I don\'t know the kind "{inner}".{suggest(inner, tale.kinds)}', line)
    if s in simple:
        return simple[s]
    k = kind_for(tale, s)
    if k:
        return k.name
    raise TaleError(f'I don\'t know the type "{s}". Try text, money, a whole number, yes or no, a letter, '
                    f'a list of …, or a kind you described.{suggest(s, list(simple) + list(tale.kinds))}', line)


def split_top(toks: list[Tok], seps: set[str]) -> list[list[Tok]]:
    """Split on separators outside brackets."""
    parts, cur, depth = [], [], 0
    for t in toks:
        if t.text == "(" and t.kind == "p":
            depth += 1
        if t.text == ")" and t.kind == "p":
            depth -= 1
        if depth == 0 and t.text in seps and t.kind in ("p", "w"):
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    parts.append(cur)
    return parts


def read_tale(text: str) -> Tale:
    tale = Tale()
    paragraphs: list[tuple[str, int]] = []
    line, buf, start = 0, [], 0
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.strip().lower().startswith("note:"):
            continue
        if raw.strip():
            if not buf:
                start = i
            buf.append(raw)
        elif buf:
            paragraphs.append(("\n".join(buf), start))
            buf = []
    if buf:
        paragraphs.append(("\n".join(buf), start))

    for pi, (ptext, pline) in enumerate(paragraphs):
        group: list[tuple[list[Tok], int, str]] = []
        for si, s in enumerate(sentences(tokenize(ptext, pline))):
            ws, ln = words(s), s[0].line
            src = " ".join(t.text for t in s)
            if ws[:2] == ["for", "example"]:
                body = s[3:] if len(s) > 2 and s[2].text == "," else s[2:]
                group.append((body, ln, src))
            elif ws[0] == "suppose":
                if "is" not in ws:
                    raise TaleError('Write "Suppose <name> is <something>."', ln)
                k = ws.index("is")
                name = snake([w for w in ws[1:k] if w not in ARTICLES] or ws[1:k])
                tale.supposes.append((name, s[k + 1:], ln))
            elif any(t.text == ":" for t in s):
                read_rule(tale, s)
            elif len(ws) > 2 and ws[0] in ("a", "an") and ws[2:5] == ["is", "one", "of"]:
                members = [snake([w for w in words(p) if w not in ARTICLES]) for p in split_top(s[5:], {",", "or"})]
                ename = pascal([ws[1]])
                tale.enums.append((ename, [pascal([m]) for m in members], ln))
                tale.kinds[ws[1]] = Kind(ename, [], ln)
                for m in members:
                    tale.choices[m] = (ename, pascal([m]))
            elif len(ws) > 2 and ws[0] in ("a", "an") and "has" in ws[:4]:
                h = ws.index("has")
                kname = ws[1:h]
                kind = Kind(pascal(kname), [], ln)
                tale.kinds[" ".join(kname)] = kind
                for part in split_top(s[h + 1:], {",", "and"}):
                    if not part:
                        continue
                    if "(" not in words(part):
                        raise TaleError(f'Say what kind of value "{" ".join(words(part))}" is, '
                                        f'like "a price (money)".', ln)
                    b = words(part).index("(")
                    fname = snake([w for w in words(part[:b]) if w not in ARTICLES])
                    kind.fields.append((fname, part[b + 1:-1]))   # resolved after all kinds are known
            elif pi == 0 and si == 0:
                tale.module = pascal([w for w in ws if w not in ARTICLES]) or "Tale"
            else:
                raise TaleError(f'I don\'t know what to do with "{src}". A sentence describes a kind '
                                f'("An item has …"), a rule ("The total of … is …:"), "Suppose …" or '
                                f'"For example, …".', ln)
        if group:
            tale.groups.append(group)

    for kind in tale.kinds.values():
        kind.fields = [(f, parse_type(tale, t, kind.line) if not isinstance(t, str) else t) for f, t in kind.fields]
    for r in tale.rules:
        r.slots = [(n, parse_type(tale, t, r.line) if not isinstance(t, str) else t) for n, t in r.slots]
        if not isinstance(r.returns, str):
            r.returns = parse_type(tale, r.returns, r.line)
    return tale


def read_rule(tale: Tale, s: list[Tok]):
    ln = s[0].line
    c = next(i for i, t in enumerate(s) if t.text == ":")
    head, body = s[:c], s[c + 1:]
    hw = words(head)
    predicate = hw[-1] == "when"
    if predicate:
        head, returns = head[:-1], "bool"
    else:
        depth, cut = 0, -1
        for i, t in enumerate(head):
            depth += t.text == "("
            depth -= t.text == ")"
            if depth == 0 and t.text == "is":
                cut = i
        if cut < 0:
            raise TaleError('A rule header says what it gives: "The total of … is money:" '
                            'or "A word (text) is a palindrome when:".', ln)
        head, returns = head[:cut], head[cut + 1:]
    pattern, slots, i = [], [], 0
    while i < len(head):
        if i + 1 < len(head) and head[i + 1].text == "(":
            j = next(k for k in range(i + 1, len(head)) if head[k].text == ")")
            if pattern and pattern[-1] in ARTICLES:
                pattern.pop()
            pattern.append(len(slots))
            slots.append((snake([head[i].text]), head[i + 2:j]))
            i = j + 1
        else:
            pattern.append(head[i].text)
            i += 1
    if not slots:
        raise TaleError('A rule needs at least one input in brackets, like "some items (a list of items)".', ln)
    if not predicate and pattern and pattern[0] == "the":
        pattern = pattern[1:]
    lits = [w for w in pattern if isinstance(w, str) and w not in ARTICLES | {"of", "is"}]
    name = ("is_" if predicate else "") + snake(lits)
    tale.rules.append(Rule(pattern, slots, returns, name, predicate, body, ln))


# ---------------------------------------------------------------- expressions

AGGREGATES = {"sum": "sum", "largest": "max_of", "smallest": "min_of"}
SIMPLE_OF = {"length": "len", "first": "first", "last": "last", "words": "words", "letters": "chars",
             "distinct": "distinct", "absolute": "abs"}


class Expr:
    """Parse one tale expression into Kite text. Names resolve while parsing; `its` looks at the scope stack."""

    def __init__(self, tale: Tale, toks: list[Tok], scope: list[str], slots: list[str], line: int):
        self.tale, self.t, self.i, self.line = tale, toks, 0, line
        self.scope = list(scope)          # loop variables, innermost last
        self.slots = slots                # rule inputs and supposed names
        self.loops: list[str] = []        # loop variables bound around the current position
        self.used: set[str] = set()
        self.bare = 0                     # >0: inside a list item or rule input; postfix phrases wait for the whole
        self.near: tuple[Rule, int] | None = None

    # -- token helpers
    def peek(self, k=0) -> str | None:
        j = self.i + k
        return self.t[j].text if j < len(self.t) else None

    def at(self, *ws) -> bool:
        return all(self.peek(k) == w for k, w in enumerate(ws))

    def take(self, *ws) -> bool:
        if self.at(*ws):
            self.i += len(ws)
            return True
        return False

    def need(self, *ws):
        if not self.take(*ws):
            got = " ".join(t.text for t in self.t[self.i:self.i + 4]) or "the end"
            raise TaleError(f'I expected "{" ".join(ws)}" here but found "{got}".', self.here())

    def here(self) -> int:
        return self.t[min(self.i, len(self.t) - 1)].line if self.t else self.line

    def run(self) -> str:
        out = self.expr()
        if self.i < len(self.t):
            rest = " ".join(t.text for t in self.t[self.i:self.i + 6])
            raise TaleError(f'I understood the start, but not "{rest}".', self.here())
        return out

    # -- grammar, loosest first
    def expr(self) -> str:
        a = self.comp()
        if self.take("if"):
            c = self.comp()
            self.need(",")
            if self.take("otherwise"):
                e = self.expr()
            else:
                e = self.expr()
            return f"(if {c} then {a} else {e})"
        return a

    def comp(self) -> str:
        # the loop variable is used inside E before "for each x" names it: look ahead and bind it first
        var = self.loop_var_ahead()
        if var:
            self.loops.append(var)
        try:
            e = self.orx()
        finally:
            if var:
                self.loops.pop()
        if self.at("for") and self.peek(1) in ("each", "some", "every"):
            self.i += 1
            q = self.t[self.i].text
            self.i += 1
            v, src, cond = self.binder()
            if q == "each":
                if cond:
                    src = f"({src} |> filter({v} => {cond}))"
                return f"({src} |> map({v} => {e}))"
            fn = "any" if q == "some" else "all"
            if cond:
                return f"({src} |> {fn}({v} => not ({cond}) or ({e})))" if q == "every" else \
                    f"({src} |> {fn}({v} => ({cond}) and ({e})))"
            return f"({src} |> {fn}({v} => {e}))"
        return e

    def loop_var_ahead(self) -> str | None:
        depth = 0
        for j in range(self.i, len(self.t) - 2):
            w = self.t[j].text
            depth += w == "("
            depth -= w == ")"
            if depth < 0 or (depth == 0 and w in (",",)):
                return None
            if depth == 0 and w == "for" and self.t[j + 1].text in ("each", "some", "every"):
                return snake([self.t[j + 2].text])
        return None

    def binder(self) -> tuple[str, str, str | None]:
        """x in L [where C | whose f …]"""
        v = snake([self.t[self.i].text])
        self.i += 1
        self.need("in")
        src = self.fin()
        cond = None
        if self.at("where") or self.at("whose"):
            whose = self.peek() == "whose"
            self.i += 1
            if whose:
                self.t.insert(self.i, Tok("w", "its", self.here()))
            self.loops.append(v)
            try:
                cond = self.orx()
            finally:
                self.loops.pop()
        return v, src, cond

    def orx(self) -> str:
        a = self.andx()
        while self.take("or"):
            a = f"({a} or {self.andx()})"
        return a

    def andx(self) -> str:
        a = self.notx()
        while self.take("and"):
            a = f"({a} and {self.notx()})"
        return a

    def notx(self) -> str:
        if self.take("not"):
            return f"not ({self.notx()})"
        return self.cmp()

    def cmp(self) -> str:
        a = self.fin()
        call = self.predicate_call(a)
        if call:
            a = call    # "mine can pay 151 is false" still compares
        if self.at("is"):
            self.i += 1
            neg = self.take("not")
            ops = [(("more", "than"), ">"), (("greater", "than"), ">"), (("less", "than"), "<"),
                   (("fewer", "than"), "<"), (("at", "least"), ">="), (("at", "most"), "<="),
                   (("equal", "to"), "==")]
            op = "=="
            for ws, o in ops:
                if self.take(*ws):
                    op = o
                    break
            b = self.fin()
            if neg:
                return f"({a} != {b})" if op == "==" else f"not ({a} {op} {b})"
            return f"({a} {op} {b})"
        if self.take("contains"):
            return f"has({a}, {self.fin()})"
        if self.take("starts", "with"):
            return f"starts({a}, {self.fin()})"
        if self.take("ends", "with"):
            return f"ends({a}, {self.fin()})"
        return a

    def predicate_call(self, first: str) -> str | None:
        for r in sorted((r for r in self.tale.rules if r.predicate and r.pattern[0] == 0),
                        key=lambda r: -len(r.pattern)):
            save = self.i
            args, neg, ok = {0: first}, False, True
            for k, p in enumerate(r.pattern[1:]):
                if isinstance(p, int):
                    args[p] = self.post()
                    continue
                if not self.take(p):
                    ok = False
                    break
                if p == "is" and k == 0 and self.take("not"):
                    neg = True
            if ok:
                call = f"{r.name}({', '.join(args[n] for n in range(len(r.slots)))})"
                return f"not {call}" if neg else call
            self.i = save
        return None

    def add(self) -> str:
        a = self.mul()
        while True:
            if self.take("plus"):
                a = f"({a} + {self.mul()})"
            elif self.take("minus"):
                a = f"({a} - {self.mul()})"
            else:
                return a

    def mul(self) -> str:
        a = self.post()
        while True:
            if self.take("times"):
                a = f"({a} * {self.post()})"
            elif self.take("divided", "by"):
                a = f"({a} / {self.post()})"
            else:
                return a

    def post(self) -> str:
        return self.prim()

    def fin(self) -> str:
        """Postfix phrases apply to the whole arithmetic before them: "a divided by b rounded to 2 places"."""
        a = self.add()
        while not self.bare:
            if self.take("in", "upper", "case"):
                a = f"upper({a})"
            elif self.take("in", "lower", "case"):
                a = f"lower({a})"
            elif self.take("trimmed"):
                a = f"trim({a})"
            elif self.take("reversed"):
                a = f"reverse({a})"
            elif self.take("spelled", "backwards"):
                a = f"join(reverse(chars({a})), \"\")"
            elif self.take("sorted", "by"):
                v = "e"
                self.loops.append(v)
                try:
                    key = self.prim_post_free()
                finally:
                    self.loops.pop()
                if self.take(",", "largest", "first"):
                    a = f"({a} |> sort_by_desc({v} => {key}))"
                else:
                    a = f"({a} |> sort_by({v} => {key}))"
            elif self.take("sorted", ",", "largest", "first"):
                a = f"sort_desc({a})"
            elif self.take("sorted"):
                a = f"sort({a})"
            elif self.take("joined", "with"):
                a = f"join({a}, {self.prim()})"
            elif self.take("split", "by"):
                a = f"split({a}, {self.prim()})"
            elif self.take("rounded", "to"):
                n = self.prim()
                self.take("places") or self.take("place")
                a = f"round({a}, {n})"
            elif self.take("as", "text"):
                a = f"to_str({a})"
            else:
                return a
        return a

    def prim_post_free(self) -> str:
        return self.prim()

    def prim(self) -> str:
        t = self.t[self.i] if self.i < len(self.t) else None
        if t is None:
            raise TaleError("This sentence stops too early: something is missing at the end.", self.here())
        if t.text == "(" and t.kind == "p":
            self.i += 1
            bare, self.bare = self.bare, 0
            e = self.expr()
            self.bare = bare
            self.need(")")
            return f"({e})"
        if t.kind == "num":
            self.i += 1
            return t.text
        if t.kind == "str":
            self.i += 1
            return t.text.replace("{", "{{").replace("}", "}}")
        if t.kind == "chr":
            self.i += 1
            return t.text
        if t.text in ("true", "false"):
            self.i += 1
            return t.text
        if t.text == "nothing":
            self.i += 1
            return "none"
        if self.take("the", "remainder", "of"):
            a = self.post()
            self.need("divided", "by")
            return f"({a} % {self.post()})"
        if self.at("the", "number", "of") and self.peek(4) in ("in",):
            self.i += 3
            v = singular(self.t[self.i].text)
            self.t[self.i] = Tok("w", v, self.t[self.i].line)
            v, src, cond = self.binder()
            return f"({src} |> count({v} => {cond}))" if cond else f"len({src})"
        for word, fn in AGGREGATES.items():
            if self.take("the", word, "of"):
                arg, lam = self.aggregate_arg()
                if lam is None:
                    return f"{fn}({arg})"
                return f"({arg} |> sum({lam}))" if fn == "sum" else f"{fn}({arg} |> map({lam}))"
        for word, fn in SIMPLE_OF.items():
            if self.take("the", word, "of") or (word == "length" and self.take("the", "number", "of")):
                return f"{fn}({self.post()})"
        if self.at("each") and self.peek(2) == "in":
            self.i += 1
            v, src, cond = self.binder()
            return f"({src} |> filter({v} => {cond}))" if cond else src
        if self.take("a", "list", "of") or self.take("the", "list", "of"):
            self.bare += 1
            try:
                items = [self.add()]
                while self.take(",") or self.take("and"):
                    items.append(self.add())
            finally:
                self.bare -= 1
            return "[" + ", ".join(items) + "]"
        if self.take("an", "empty", "list") or (t.text == "no" and self.peek(1) and self.peek(2) in (None, ")", ",", "is", "and", "or")):
            if t.text == "no":
                self.i += 2
            return "[]"
        call = self.rule_call()
        if call:
            return call
        if t.text in ("a", "an") and self.peek(1) and kind_for(self.tale, self.peek(1)) and self.peek(2) == "with":
            return self.construct()
        if self.take("its"):
            return self.field_of(self.its())
        if self.take("it"):
            return self.its()
        # a name: [the] word ['s field]
        self.take("the")
        if self.i >= len(self.t) or self.t[self.i].kind != "w":
            got = self.t[self.i].text if self.i < len(self.t) else "the end"
            raise TaleError(f'I expected a value here but found "{got}".', self.here())
        w = self.t[self.i].text
        self.i += 1
        if w in self.tale.choices:
            e, m = self.tale.choices[w]
            return f"{e}.{m}"
        name = self.resolve(snake([w]))
        if self.take("'s"):
            return self.field_of(name)
        return name

    def aggregate_arg(self) -> tuple[str, str | None]:
        var = self.loop_var_ahead()
        if var:
            self.loops.append(var)
        try:
            # "the sum of a times b for each …" takes the arithmetic; "the sum of xs divided by 2" does not
            e = self.add() if var else self.post()
        finally:
            if var:
                self.loops.pop()
        if self.at("for", "each"):
            self.i += 2
            v, src, cond = self.binder()
            if cond:
                src = f"({src} |> filter({v} => {cond}))"
            return src, f"{v} => {e}"
        return e, None

    def rule_call(self) -> str | None:
        for r in sorted((r for r in self.tale.rules if not r.predicate and isinstance(r.pattern[0], str)),
                        key=lambda r: -len(r.pattern)):
            save = self.i
            self.take("the")
            args, ok, matched = {}, True, 0
            self.bare += 1
            try:
                for k, p in enumerate(r.pattern):
                    if isinstance(p, int):
                        last = k == len(r.pattern) - 1
                        try:
                            args[p] = self.post() if last else self.add()
                        except TaleError:
                            if matched >= 2:      # "the value of …" clearly meant this rule: say what went wrong
                                raise
                            ok = False
                            break
                    elif self.take(p):
                        matched += 1
                    else:
                        ok = False
                        break
            finally:
                self.bare -= 1
            if not ok and matched >= 2 and (self.near is None or matched > self.near[1]):
                self.near = (r, matched)
            if ok:
                return f"{r.name}({', '.join(args[n] for n in range(len(r.slots)))})"
            self.i = save
        return None

    def construct(self) -> str:
        self.i += 1
        kind = kind_for(self.tale, self.t[self.i].text)
        self.i += 1
        self.need("with")
        given: dict[str, str] = {}
        names = sorted(kind.fields, key=lambda f: -len(f[0]))
        while len(given) < len(kind.fields):
            for fname, _ in names:
                fw = fname.split("_")
                if self.take(*fw):
                    given[fname] = self.add()
                    break
            else:
                got = " ".join(t.text for t in self.t[self.i:self.i + 3])
                missing = [f for f, _ in kind.fields if f not in given]
                raise TaleError(f'"{self.article(kind.name)} with …" is missing {", ".join(missing)} '
                                f'(I found "{got}").', self.here())
            if len(given) < len(kind.fields) and not (self.take(",") or self.take("and")):
                missing = [f for f, _ in kind.fields if f not in given]
                raise TaleError(f'"{self.article(kind.name)} with …" is missing {", ".join(missing)}.', self.here())
        return f"{kind.name}({', '.join(given[f] for f, _ in kind.fields)})"

    @staticmethod
    def article(name: str) -> str:
        n = name.lower()
        return ("an " if n[0] in "aeiou" else "a ") + n

    def field_of(self, target: str) -> str:
        known = sorted(self.tale.field_names(), key=lambda f: -len(f))
        for f in known:
            if self.take(*f.split("_")):
                return f"{target}.{f}"
        got = self.peek() or "the end"
        raise TaleError(f'"{got}" is not a field of any kind I know.{suggest(got, self.tale.field_names())}',
                        self.here())

    def its(self) -> str:
        if self.loops:
            return self.loops[-1]
        if self.scope:
            return self.scope[-1]
        if len(self.slots) == 1:
            self.used.add(self.slots[0])
            return self.slots[0]
        raise TaleError('"its" has nothing to point to here: there is no "for each" and more than one input. '
                        'Name it, like "the item\'s price".', self.here())

    def resolve(self, name: str) -> str:
        for cand in [name] + [snake([s]) for s in singulars(name)]:
            if cand in self.loops or cand in self.scope or cand in self.slots:
                self.used.add(cand)
                return cand
        known = set(self.loops) | set(self.scope) | set(self.slots) | set(self.tale.choices)
        hint = ""
        if self.near:
            shape = " ".join("…" if isinstance(p, int) else p for p in self.near[0].pattern)
            hint = f' It looks like "the {shape}", but the rest did not fit that rule.'
        raise TaleError(f'I don\'t know "{name.replace("_", " ")}".{suggest(name, known)}{hint}', self.here())


# ---------------------------------------------------------------- to Kite

def to_kite(tale: Tale) -> tuple[str, dict[int, int]]:
    out: list[str] = []
    where: dict[int, int] = {}

    def emit(s: str, line: int):
        out.append(s)
        where[len(out)] = line

    emit(f"module {tale.module}", 1)
    emit("", 1)
    for ename, members, ln in tale.enums:
        emit(f"type {ename} = enum({', '.join(members)})", ln)
    for kind in tale.kinds.values():
        if kind.fields:
            emit(f"type {kind.name} = record({', '.join(f'{f}: {t}' for f, t in kind.fields)})", kind.line)
    for r in tale.rules:
        names = [n for n, _ in r.slots]
        body = Expr(tale, list(r.body), [], names, r.line).run()
        emit("", r.line)
        emit(f"fn {r.name}({', '.join(f'{n}: {t}' for n, t in r.slots)}) -> {r.returns} =", r.line)
        emit(f"    {body}", r.body[0].line if r.body else r.line)

    supposed = [n for n, _, _ in tale.supposes]
    lets: dict[str, tuple[str, set[str], int]] = {}
    for n, toks, ln in tale.supposes:
        e = Expr(tale, list(toks), [], [m for m in supposed if m in lets], ln)
        lets[n] = (e.run(), e.used, ln)
    for gi, group in enumerate(tale.groups, 1):
        checks, used = [], set()
        for toks, ln, src in group:
            e = Expr(tale, list(toks), [], list(lets), ln)
            checks.append((e.run(), ln))
            used |= e.used
        need, todo = set(), list(used)
        while todo:
            n = todo.pop()
            if n in lets and n not in need:
                need.add(n)
                todo.extend(lets[n][1])
        title = " ".join(t.text for t in group[0][0])[:60].replace('"', "'").replace("{", "(").replace("}", ")")
        emit("", group[0][1])
        emit(f'test "example {gi}: {title}"', group[0][1])
        for n in lets:
            if n in need:
                emit(f"    let {n} = {lets[n][0]}", lets[n][2])
        for c, ln in checks:
            emit(f"    expect {c}", ln)
    return "\n".join(out) + "\n", where


# ---------------------------------------------------------------- command line

def kite_cmd() -> list[str]:
    """This repo's Kite compiler; like compiler/bin/kite, it is built on first use."""
    if os.environ.get("KITE_DLL"):
        return ["dotnet", os.environ["KITE_DLL"]]
    project = Path(__file__).resolve().parent.parent / "compiler" / "Kite"
    dll = project / "bin" / "Release" / "net9.0" / "kite.dll"
    if not dll.exists():
        subprocess.run(["dotnet", "build", str(project), "-c", "Release", "-v", "q", "-nologo"], check=True,
                       capture_output=True)
    return ["dotnet", str(dll)]


def run_kite(verb: str, tale_path: Path, kite_text: str, where: dict[int, int]) -> dict:
    build = tale_path.parent / "build"
    build.mkdir(exist_ok=True)
    kpath = build / (tale_path.stem + ".kite")
    kpath.write_text(kite_text, encoding="utf-8")
    env = dict(os.environ, DOTNET_ROLL_FORWARD="Major")
    p = subprocess.run(kite_cmd() + [verb, str(kpath), "--json"], capture_output=True, text=True, env=env)
    try:
        res = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "crash": (p.stdout + p.stderr).strip()[-800:]}
    for d in res.get("diagnostics", []):
        d["tale_line"] = where.get(d.get("line", 0), 0)
    return res


CS_TYPES = [(r"List<(\w+)>", r"a list of \1"), (r"\bstring\b", "text"), (r"\bint\b", "a whole number"),
            (r"\bdecimal\b", "money"), (r"\bbool\b", "yes or no"), (r"\bchar\b", "a letter")]


def in_tale_words(msg: str) -> str:
    """Say Kite/C# type names the way a tale does."""
    m = re.match(r"Cannot implicitly convert type '(.+)' to '(.+)'", msg)
    if m:
        msg = f"this gives '{m.group(1)}', but the rule says it gives '{m.group(2)}'"
    for rx, to in CS_TYPES:
        msg = re.sub(rx, to, msg)
    return msg


def compile_file(path: Path) -> tuple[str, dict[int, int]]:
    return to_kite(read_tale(path.read_text(encoding="utf-8")))


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(argv) < 3 or argv[1] not in ("emit", "check", "test"):
        print(__doc__)
        return 2
    verb, path = argv[1], Path(argv[2])
    try:
        kite_text, where = compile_file(path)
    except TaleError as e:
        print(f"{path.name}:{e.line}: {e}")
        return 1
    if verb == "emit":
        print(kite_text, end="")
        return 0
    res = run_kite(verb, path, kite_text, where)
    if "crash" in res:
        print(f"{path.name}: kite failed:\n{res['crash']}")
        return 1
    for d in res.get("diagnostics", []):
        print(f"{path.name}:{d['tale_line']}: {d['severity']} {d['id']}: {in_tale_words(d['message'])}")
    for t in res.get("tests", []):
        print(f"  {'pass' if t['pass'] else 'FAIL'}  {t['name']}" + (f"  ({t['error']})" if t.get("error") else ""))
    ok = res.get("ok") and all(t["pass"] for t in res.get("tests", []))
    print("ok" if ok else "not ok", f"({res.get('errors', 0)} errors, {res.get('warnings', 0)} warnings)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
