// Kite.NET front end: Kite source -> C# text with #line directives (the way Razor compiles .cshtml), so Roslyn
// compiles it straight to IL and every compiler error and analyzer warning points at a Kite line.
// Also the Kite analyzer (KT rules), which checks what Roslyn cannot see: mutability, naming, C# leaking in.
using System.Text;
using System.Text.RegularExpressions;

namespace Kite;

public sealed record Diag(string Id, string Severity, int Line, string Message);

public sealed record Line(int No, int Indent, string Text);

public sealed partial class Front
{
    readonly string file;
    readonly List<Diag> diags = [];
    readonly StringBuilder cs = new();
    readonly List<string> usings = [];
    readonly StringBuilder types = new();
    readonly StringBuilder members = new();
    string module = "Main";
    bool hasMain;
    int testNo;
    readonly HashSet<string> fnNames = [];
    // Std functions that only return a value: calling one as a statement loses its result
    static readonly HashSet<string> Pure = ["map", "filter", "fold", "sum", "count", "sort", "sort_by", "sort_desc",
        "sort_by_desc", "reverse", "take", "skip", "distinct", "first", "last", "find", "any", "all", "has", "len",
        "join", "split", "words", "upper", "lower", "trim", "replace", "to_str", "to_int", "to_dec", "abs", "min",
        "max", "round", "sqrt", "keys", "values", "get", "range", "min_of", "max_of", "min_by", "max_by", "group_by"];
    // the current function's names: mutable or not, where declared, changed since
    Dictionary<string, (bool Mut, bool Param, int Line, bool Changed)> scope = [];

    public IReadOnlyList<Diag> Diags => diags;
    public bool HasMain => hasMain;

    Front(string file) { this.file = file; }

    public static (string CSharp, Front Front) Lower(string source, string file)
    {
        var f = new Front(file);
        var cs = f.Run(source);
        return (cs, f);
    }

    void Error(string id, int line, string msg) => diags.Add(new Diag(id, "error", line, msg));
    void Warn(string id, int line, string msg) => diags.Add(new Diag(id, "warning", line, msg));
    string Pos(int line) => $"#line {line} \"{file}\"\n";

    // ---- lines -------------------------------------------------------------------------------------------

    static string StripComment(string s)
    {
        bool str = false;
        for (int i = 0; i < s.Length; i++)
        {
            if (s[i] == '"' && (i == 0 || s[i - 1] != '\\')) str = !str;
            else if (s[i] == '#' && !str) return s[..i];
        }
        return s;
    }

    List<Line> Lines(string source)
    {
        var raw = source.Replace("\r\n", "\n").Split('\n');
        var lines = new List<Line>();
        for (int i = 0; i < raw.Length; i++)
        {
            var text = StripComment(raw[i]).TrimEnd();
            if (text.Trim().Length == 0) continue;
            if (text.Contains('\t')) Error("KT000", i + 1, "use spaces, not tabs, to indent");
            int indent = text.Length - text.TrimStart().Length;
            var body = text.Trim();
            // a line starting with |> or . continues the previous one; so does an open bracket
            if (lines.Count > 0 && (body.StartsWith("|>") || body.StartsWith('.') || Open(lines[^1].Text) > 0))
            {
                var prev = lines[^1];
                lines[^1] = prev with { Text = prev.Text + " " + body };
                continue;
            }
            lines.Add(new Line(i + 1, indent, body));
        }
        return lines;
    }

    static int Open(string s)
    {
        int depth = 0; bool str = false;
        for (int i = 0; i < s.Length; i++)
        {
            if (s[i] == '"' && (i == 0 || s[i - 1] != '\\')) str = !str;
            else if (!str && "([".Contains(s[i])) depth++;
            else if (!str && ")]".Contains(s[i])) depth--;
        }
        return depth;
    }

    // the lines indented under lines[i], and the index after them
    static (List<Line> Body, int Next) BlockAfter(List<Line> lines, int i)
    {
        var body = new List<Line>();
        int j = i + 1;
        while (j < lines.Count && lines[j].Indent > lines[i].Indent) body.Add(lines[j++]);
        return (body, j);
    }

    // ---- top level ---------------------------------------------------------------------------------------

    string Run(string source)
    {
        var lines = Lines(source);
        foreach (var l in lines)
            if (l.Indent == 0 && FnRx().Match(l.Text) is { Success: true } f) fnNames.Add(f.Groups[1].Value);
        for (int i = 0; i < lines.Count;)
        {
            var l = lines[i];
            if (l.Indent != 0) { Error("KT000", l.No, "unexpected indentation"); i++; continue; }
            var (body, next) = BlockAfter(lines, i);
            TopLevel(l, body);
            i = next;
        }
        cs.Append("// kite output for ").Append(file).Append("\n#nullable enable\n#line hidden\n");
        foreach (var u in new[] { "System", "System.Collections.Generic", "System.Linq" }.Concat(usings).Distinct())
            cs.Append("using ").Append(u).Append(";\n");
        cs.Append("using static KiteStd.Std;\n\nnamespace KiteApp\n{\n");
        cs.Append(types);
        cs.Append("#line hidden\n    public static partial class ").Append(module).Append("\n    {\n");
        cs.Append(members);
        cs.Append("#line hidden\n    }\n}\n");
        return cs.ToString();
    }

    void TopLevel(Line l, List<Line> body)
    {
        var t = l.Text;
        Match m;
        if ((m = ModuleRx().Match(t)).Success) { module = m.Groups[1].Value; Pascal(m.Groups[1].Value, l.No, "a module"); }
        else if ((m = UseRx().Match(t)).Success) usings.Add(m.Groups[1].Value);
        else if ((m = RecordRx().Match(t)).Success)
        {
            Pascal(m.Groups[1].Value, l.No, "a type");
            var fields = Params(m.Groups[2].Value, l.No, record: true);
            types.Append(Pos(l.No)).Append("    public sealed record ").Append(m.Groups[1].Value)
                 .Append('(').Append(fields).Append(");\n");
        }
        else if ((m = EnumRx().Match(t)).Success)
        {
            Pascal(m.Groups[1].Value, l.No, "a type");
            types.Append(Pos(l.No)).Append("    public enum ").Append(m.Groups[1].Value)
                 .Append(" { ").Append(m.Groups[2].Value).Append(" }\n");
        }
        else if ((m = FnRx().Match(t)).Success) Function(l, m, body);
        else if ((m = TestRx().Match(t)).Success)
        {
            scope = [];
            members.Append(Pos(l.No)).Append("        [KiteStd.KiteTest(").Append(m.Groups[1].Value)
                   .Append(")] public static void __test_").Append(++testNo).Append("()\n        {\n");
            Block(body, 3);
            members.Append("        }\n");
            Unchanged();
        }
        else { Leak(t, l.No); Error("KT000", l.No, $"expected module, use, type, fn or test, got: {Short(t)}"); }
    }

    void Function(Line l, Match m, List<Line> body)
    {
        var name = m.Groups[1].Value;
        var generic = m.Groups[2].Value;
        var ret = m.Groups[4].Success ? Type(m.Groups[4].Value.Trim()) : "void";
        var rest = m.Groups[5].Value.Trim();
        Snake(name, l.No, "a function");
        scope = [];
        var ps = Params(m.Groups[3].Value, l.No, record: false);
        var csName = name == "main" ? "Main" : Id(name);
        if (name == "main") hasMain = true;
        members.Append(Pos(l.No)).Append("        public static ").Append(ret).Append(' ').Append(csName)
               .Append(generic).Append('(').Append(ps).Append(')');
        if (rest.StartsWith('='))
        {
            var e = rest[1..].Trim();
            if (e.Length == 0 && body.Count > 0) { e = string.Join(" ", body.Select(x => x.Text)); body = []; }
            if (e.Length == 0) { Error("KT000", l.No, "missing expression after ="); e = "default!"; }
            members.Append("\n").Append(Pos(l.No)).Append("            => ").Append(WhenOrExpr(e, body, l.No)).Append(";\n");
        }
        else if (rest.Length > 0) { Leak(rest, l.No); Error("KT000", l.No, $"expected = or a new line after the signature, got: {Short(rest)}"); }
        else
        {
            members.Append("\n        {\n");
            Block(body, 3);
            members.Append("        }\n");
        }
        Unchanged();
    }

    void Unchanged()
    {
        foreach (var (n, v) in scope)
            if (v.Mut && !v.Changed) Warn("KT002", v.Line, $"'{n}' is never changed: declare it with let, not let mut");
    }

    string Params(string text, int line, bool record)
    {
        var parts = SplitTop(text, ',');
        var outp = new List<string>();
        foreach (var p0 in parts)
        {
            var p = p0.Trim();
            if (p.Length == 0) continue;
            var m = ParamRx().Match(p);
            if (!m.Success) { Error("KT000", line, $"a parameter is name: type, got: {Short(p)}"); continue; }
            var n = m.Groups[1].Value;
            Snake(n, line, record ? "a field" : "a parameter");
            if (!record) scope[n] = (false, true, line, false);
            var def = m.Groups[3].Success ? " = " + Expr(m.Groups[3].Value, line) : "";
            outp.Add(Type(m.Groups[2].Value.Trim()) + " " + Id(n) + def);
        }
        return string.Join(", ", outp);
    }

    // ---- statements --------------------------------------------------------------------------------------

    void Block(List<Line> lines, int depth)
    {
        if (lines.Count == 0) { members.Append(new string(' ', depth * 4)).Append("{ }\n"); return; }
        int baseIndent = lines[0].Indent;
        for (int i = 0; i < lines.Count;)
        {
            var l = lines[i];
            if (l.Indent != baseIndent) { Error("KT000", l.No, "indentation does not match the lines above"); i++; continue; }
            var (body, next) = BlockAfter(lines, i);
            i = next;
            // an if/elif/else chain: gather it
            if (l.Text.StartsWith("if ") || l.Text == "if")
            {
                Statement(l, body, depth);
                while (i < lines.Count && lines[i].Indent == baseIndent &&
                       (lines[i].Text.StartsWith("elif ") || lines[i].Text == "else"))
                {
                    var l2 = lines[i];
                    var (b2, n2) = BlockAfter(lines, i);
                    i = n2;
                    Statement(l2, b2, depth);
                }
            }
            else Statement(l, body, depth);
        }
    }

    void Statement(Line l, List<Line> body, int depth)
    {
        var pad = new string(' ', depth * 4);
        var t = l.Text;
        var o = members;
        o.Append(Pos(l.No));
        Match m;
        if ((m = LetRx().Match(t)).Success)
        {
            var mut = m.Groups[1].Success;
            var name = m.Groups[2].Value;
            Snake(name, l.No, "a variable");
            var type = m.Groups[3].Success ? Type(m.Groups[3].Value.Trim()) : "var";
            var value = WhenOrExpr(m.Groups[4].Value.Trim(), body, l.No);
            if (type == "var" && value == "new()") Error("KT000", l.No, $"an empty list needs a type: let {name}: list<int> = []");
            scope[name] = (mut, false, l.No, false);
            o.Append(pad).Append(type).Append(' ').Append(Id(name)).Append(" = ").Append(value).Append(";\n");
            return;
        }
        if (t.StartsWith("if ") || t.StartsWith("elif ") || t == "else")
        {
            var kw = t.StartsWith("if ") ? "if" : t.StartsWith("elif ") ? "else if" : "else";
            o.Append(pad).Append(kw);
            if (kw != "else") o.Append(" (").Append(Expr(t[(t.IndexOf(' ') + 1)..], l.No)).Append(')');
            o.Append('\n').Append(pad).Append("{\n");
            Block(body, depth + 1);
            o.Append(pad).Append("}\n");
            return;
        }
        if (t.StartsWith("while "))
        {
            o.Append(pad).Append("while (").Append(Expr(t[6..], l.No)).Append(")\n").Append(pad).Append("{\n");
            Block(body, depth + 1);
            o.Append(pad).Append("}\n");
            return;
        }
        if ((m = ForRx().Match(t)).Success)
        {
            var v = m.Groups[1].Value;
            Snake(v, l.No, "a variable");
            scope[v] = (false, false, l.No, false);
            var src = m.Groups[2].Value.Trim();
            var range = SplitTop(src, "..=", "..");
            if (range is { } r)
                o.Append(pad).Append("for (var ").Append(Id(v)).Append(" = ").Append(Expr(r.A, l.No)).Append("; ").Append(Id(v))
                 .Append(r.Op == "..=" ? " <= " : " < ").Append(Expr(r.B, l.No)).Append("; ").Append(Id(v)).Append("++)\n");
            else o.Append(pad).Append("foreach (var ").Append(Id(v)).Append(" in ").Append(Expr(src, l.No)).Append(")\n");
            o.Append(pad).Append("{\n");
            Block(body, depth + 1);
            o.Append(pad).Append("}\n");
            return;
        }
        if (t == "return") { o.Append(pad).Append("return;\n"); return; }
        if (t.StartsWith("return ")) { o.Append(pad).Append("return ").Append(WhenOrExpr(t[7..], body, l.No)).Append(";\n"); return; }
        if (t is "break" or "continue" or "pass") { o.Append(pad).Append(t == "pass" ? ";" : t + ";").Append('\n'); return; }
        if (t.StartsWith("fail ")) { o.Append(pad).Append("throw fail(").Append(Expr(t[5..], l.No)).Append(");\n"); return; }
        if (t.StartsWith("expect "))
        {
            var what = $"line {l.No}: {t[7..]}".Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("{", "{{").Replace("}", "}}");
            o.Append(pad).Append("expect(").Append(Expr(t[7..], l.No)).Append(", \"").Append(what).Append("\");\n");
            return;
        }
        if (body.Count > 0 && !WhenAnywhere().IsMatch(t))
            Error("KT000", body[0].No, "unexpected indentation");
        // assignment or an expression statement (a call)
        var asg = Assignment(t);
        if (asg is { } a)
        {
            var target = a.Lhs.Trim();
            if (IdentRx().IsMatch(target))
            {
                if (scope.TryGetValue(target, out var v))
                {
                    if (!v.Mut)
                        Error("KT001", l.No, v.Param
                            ? $"'{target}' is a parameter and cannot be changed: copy it with let mut"
                            : $"'{target}' cannot be changed: declare it with let mut");
                    scope[target] = v with { Changed = true };
                }
            }
            o.Append(pad).Append(Expr(target, l.No)).Append(' ').Append(a.Op).Append(' ')
             .Append(WhenOrExpr(a.Rhs, body, l.No)).Append(";\n");
            return;
        }
        if (t.StartsWith("print ")) Error("KT000", l.No, "print is a function: print(x)");
        var head = Lex.Tokens(t);
        var called = TopLast(head, 0, head.Count, "|>") is var pp and >= 0 && pp + 1 < head.Count ? head[pp + 1].Text
                   : head.Count > 1 && head[1].Text == "(" ? head[0].Text : "";
        if (Pure.Contains(called)) Warn("KT006", l.No, $"the result of {called}() is not used");
        o.Append(pad).Append(Expr(t, l.No)).Append(";\n");
    }

    // `x when` + indented arms `pattern -> value`: a C# switch expression
    string WhenOrExpr(string e, List<Line> body, int line)
    {
        var m = WhenTail().Match(e);
        if (!m.Success) return Expr(e, line);
        var arms = new List<string>();
        foreach (var a in body)
        {
            var parts = a.Text.Split("->", 2);
            if (parts.Length != 2) { Error("KT000", a.No, "a when arm is: pattern -> value"); continue; }
            var pat = parts[0].Trim();
            if (pat == "else") pat = "_";
            arms.Add($"\n{Pos(a.No)}                {Pattern(pat)} => {Expr(parts[1].Trim(), a.No)}");
        }
        if (arms.Count == 0) Error("KT000", line, "when needs arms on the next lines: pattern -> value");
        return $"({Expr(m.Groups[1].Value, line)} switch {{{string.Join(",", arms)}\n{Pos(line)}            }})";
    }

    // patterns keep and/or/not (C# patterns use them too); literals only need the decimal suffix
    static string Pattern(string p) => DecimalRx().Replace(p, "$0m");

    static (string Lhs, string Op, string Rhs)? Assignment(string t)
    {
        var toks = Lex.Tokens(t);
        int depth = 0;
        for (int i = 0; i < toks.Count; i++)
        {
            var k = toks[i].Text;
            if (k is "(" or "[") depth++;
            else if (k is ")" or "]") depth--;
            else if (depth == 0 && k is "=" or "+=" or "-=" or "*=" or "/=" or "%=")
                return (t[..toks[i].Start], k, t[(toks[i].Start + k.Length)..].Trim());
            else if (depth == 0 && k == "=>") return null;
        }
        return null;
    }

    // ---- expressions -------------------------------------------------------------------------------------

    string Expr(string e, int line)
    {
        var toks = Lex.Tokens(e);
        foreach (var bad in toks.Where(x => x.Kind == Lex.Bad)) Error("KT000", line, $"unexpected character '{bad.Text}'");
        return Rw(toks, 0, toks.Count, line);
    }

    string Rw(List<Lex.Tok> ts, int a, int b, int line)
    {
        if (a >= b) return "";
        // lambda: params => body (the body is rewritten on its own)
        int arrow = Top(ts, a, b, "=>");
        if (arrow >= 0) return Raw(ts, a, arrow) + " => " + Rw(ts, arrow + 1, b, line);
        // pipes bind tighter than comparisons and and/or: xs |> len() == 3
        int pipe = TopLast(ts, a, b, "|>");
        if (pipe >= 0)
            foreach (var ops in new[] { new[] { "or", "||" }, new[] { "and", "&&" }, new[] { "==", "!=", "<", ">", "<=", ">=" } })
            {
                int k = ops.Select(op => TopLast(ts, a, b, op)).Max();
                if (k > a) return $"{Rw(ts, a, k, line)} {ts[k].Text switch { "or" => "||", "and" => "&&", var x => x }} {Rw(ts, k + 1, b, line)}";
            }
        // pipes: x |> f(y) is f(x, y)
        if (pipe >= 0)
        {
            var left = Rw(ts, a, pipe, line);
            int f = pipe + 1;
            if (f >= b || ts[f].Kind != Lex.Ident) { Error("KT000", line, "after |> comes a function call, like |> map(x => x * 2)"); return left; }
            if (f + 1 == b) return $"{ts[f].Text}({left})";
            if (ts[f + 1].Text != "(" || Match(ts, f + 1, b) != b - 1)
            { Error("KT000", line, "after |> comes a function call, like |> map(x => x * 2)"); return left; }
            var args = Rw(ts, f + 2, b - 1, line);
            return $"{ts[f].Text}({left}{(args.Length > 0 ? ", " + args : "")})";
        }
        // inline if: if c then x else y
        if (ts[a].Text == "if")
        {
            int th = Top(ts, a + 1, b, "then"), el = th < 0 ? -1 : TopLast(ts, th + 1, b, "else");
            if (th < 0 || el < 0) { Error("KT000", line, "an if inside an expression is: if c then x else y"); return "default!"; }
            return $"({Rw(ts, a + 1, th, line)} ? {Rw(ts, th + 1, el, line)} : {Rw(ts, el + 1, b, line)})";
        }
        var sb = new StringBuilder();
        for (int i = a; i < b; i++)
        {
            var t = ts[i];
            string prev = i > a ? ts[i - 1].Text : "";
            switch (t.Kind)
            {
                case Lex.Str:
                    if (prev == "$") { Error("KT004", line, "Kite strings need no $: \"total: {x}\""); }
                    Emit(sb, Str(t.Text, line));
                    continue;
                case Lex.Num:
                    Emit(sb, t.Text.Contains('.') ? t.Text + "m" : t.Text);
                    continue;
                case Lex.Ident:
                    switch (t.Text)
                    {
                        case "and": Emit(sb, "&&"); continue;
                        case "or": Emit(sb, "||"); continue;
                        case "not": Emit(sb, "!"); continue;
                        case "none": Emit(sb, "null"); continue;
                        case "var" or "new" or "public" or "static" or "void" or "class" or "null" or "string" or "this":
                            Error("KT004", line, $"'{t.Text}' is C#, not Kite"); Emit(sb, t.Text); continue;
                    }
                    // list<int>() / map<str, int>() / set<str>(): create one
                    if (t.Text is "list" or "map" or "set" && i + 1 < b && ts[i + 1].Text == "<")
                    {
                        int close = Angle(ts, i + 1, b);
                        if (close > 0 && close + 1 < b && ts[close + 1].Text == "(")
                        {
                            Emit(sb, "new " + Type(Raw(ts, i, close + 1)));
                            i = close;
                            continue;
                        }
                    }
                    // calling a type creates it: Item("tea", 2.5, 3)
                    if (char.IsUpper(t.Text[0]) && i + 1 < b && ts[i + 1].Text == "(" && prev != "." && !fnNames.Contains(t.Text))
                    {
                        Emit(sb, "new " + t.Text);
                        continue;
                    }
                    Emit(sb, prev == "." ? t.Text : Id(t.Text));
                    continue;
            }
            switch (t.Text)
            {
                case "(":
                {
                    int close = Match(ts, i, b);
                    if (close < 0) { Error("KT000", line, "missing )"); close = b; }
                    sb.Append('(').Append(Rw(ts, i + 1, close, line)).Append(')');
                    i = close;
                    continue;
                }
                case "[":
                {
                    int close = Match(ts, i, b);
                    if (close < 0) { Error("KT000", line, "missing ]"); close = b; }
                    var inner = Rw(ts, i + 1, close, line);
                    bool index = i > a && (ts[i - 1].Kind is Lex.Ident or Lex.Str || ts[i - 1].Text is ")" or "]");
                    if (index) sb.Append('[').Append(inner).Append(']');
                    else Emit(sb, inner.Length == 0 ? "new()" : $"list({inner})");
                    i = close;
                    continue;
                }
                case "{" or "}" or ";" or "&&" or "||":
                    Error("KT004", line, $"'{t.Text}' is C#, not Kite{(t.Text is "&&" or "||" ? ": use and / or" : "")}");
                    Emit(sb, t.Text);
                    continue;
                case "!":
                    Error("KT004", line, "'!' is C#, not Kite: use not");
                    Emit(sb, "!");
                    continue;
                case "$":
                    continue;
                case ".":
                    sb.Append('.');
                    continue;
                case ",":
                    sb.Append(", ");
                    continue;
            }
            Emit(sb, t.Text);
        }
        return sb.ToString().Trim();
    }

    static void Emit(StringBuilder sb, string s)
    {
        if (sb.Length > 0 && sb[^1] != '(' && sb[^1] != '.' && sb[^1] != ' ' && sb[^1] != '[' && sb[^1] != '!') sb.Append(' ');
        sb.Append(s);
    }

    // "total: {total(cart)}" -> $"total: {total(cart)}", each hole rewritten as Kite
    string Str(string s, int line)
    {
        var inner = s[1..^1];
        if (!inner.Contains('{')) return s;
        var sb = new StringBuilder("$\"");
        for (int i = 0; i < inner.Length; i++)
        {
            char c = inner[i];
            if (c == '{' && i + 1 < inner.Length && inner[i + 1] == '{') { sb.Append("{{"); i++; continue; }
            if (c == '}' && i + 1 < inner.Length && inner[i + 1] == '}') { sb.Append("}}"); i++; continue; }
            if (c == '{')
            {
                int end = inner.IndexOf('}', i);
                if (end < 0) { Error("KT000", line, "a { in a string needs its }; write {{ for a literal {"); return s; }
                var hole = inner[(i + 1)..end];
                var fmt = "";
                int colon = TopColon(hole);
                if (colon >= 0) { fmt = hole[colon..]; hole = hole[..colon]; }
                sb.Append('{').Append(Expr(hole, line)).Append(fmt).Append('}');
                i = end;
                continue;
            }
            sb.Append(c);
        }
        return sb.Append('"').ToString();
    }

    static int TopColon(string s)
    {
        int depth = 0;
        for (int i = 0; i < s.Length; i++)
        {
            if ("([".Contains(s[i])) depth++;
            else if (")]".Contains(s[i])) depth--;
            else if (s[i] == ':' && depth == 0) return i;
        }
        return -1;
    }

    // ---- types and names ---------------------------------------------------------------------------------

    static string Type(string t)
    {
        var toks = Lex.Tokens(t);
        var sb = new StringBuilder();
        foreach (var k in toks)
            sb.Append(k.Text switch
            {
                "str" => "string",
                "dec" => "decimal",
                "float" => "double",
                "list" => "List",
                "map" => "Dictionary",
                "set" => "HashSet",
                "," => ", ",
                _ => k.Text,
            });
        return sb.ToString();
    }

    void Snake(string n, int line, string what)
    {
        if (!SnakeRx().IsMatch(n)) Warn("KT003", line, $"{what} name is snake_case in Kite: '{n}'");
    }

    void Pascal(string n, int line, string what)
    {
        if (!PascalRx().IsMatch(n)) Warn("KT003", line, $"{what} name is PascalCase in Kite: '{n}'");
    }

    void Leak(string t, int line)
    {
        if (CSharpRx().IsMatch(t)) Error("KT004", line, "this line is C#, not Kite");
    }

    // a Kite name that is a C# keyword gets C#'s @ escape: let out = [] is fine in Kite
    static readonly HashSet<string> CsKeywords = ["abstract", "as", "base", "byte", "case", "catch", "checked", "const",
        "decimal", "default", "delegate", "do", "double", "enum", "event", "explicit", "extern", "finally", "fixed",
        "goto", "implicit", "interface", "internal", "lock", "namespace", "object", "operator", "out", "override",
        "params", "private", "protected", "readonly", "ref", "sbyte", "sealed", "short", "sizeof", "stackalloc",
        "struct", "switch", "throw", "try", "typeof", "uint", "ulong", "unchecked", "unsafe", "ushort", "using",
        "virtual", "volatile", "operator", "in"];
    static string Id(string n) => CsKeywords.Contains(n) ? "@" + n : n;

    static string Short(string s) => s.Length > 40 ? s[..40] + "..." : s;

    // ---- token helpers -----------------------------------------------------------------------------------

    static string Raw(List<Lex.Tok> ts, int a, int b) => string.Join(" ", ts.Skip(a).Take(b - a).Select(x => x.Text));

    static int Match(List<Lex.Tok> ts, int open, int b)
    {
        int depth = 0;
        for (int i = open; i < b; i++)
        {
            if (ts[i].Text is "(" or "[") depth++;
            else if (ts[i].Text is ")" or "]" && --depth == 0) return i;
        }
        return -1;
    }

    static int Angle(List<Lex.Tok> ts, int open, int b)
    {
        int depth = 0;
        for (int i = open; i < b; i++)
        {
            if (ts[i].Text == "<") depth++;
            else if (ts[i].Text == ">" && --depth == 0) return i;
            else if (ts[i].Text == ">>") { depth -= 2; if (depth <= 0) return depth == 0 ? i : -1; }
            else if (ts[i].Kind != Lex.Ident && ts[i].Text is not ("," or "?")) return -1;
        }
        return -1;
    }

    static int Top(List<Lex.Tok> ts, int a, int b, string what)
    {
        int depth = 0;
        for (int i = a; i < b; i++)
        {
            if (ts[i].Text is "(" or "[") depth++;
            else if (ts[i].Text is ")" or "]") depth--;
            else if (depth == 0 && ts[i].Text == what) return i;
        }
        return -1;
    }

    static int TopLast(List<Lex.Tok> ts, int a, int b, string what)
    {
        int depth = 0, found = -1;
        for (int i = a; i < b; i++)
        {
            if (ts[i].Text is "(" or "[") depth++;
            else if (ts[i].Text is ")" or "]") depth--;
            else if (depth == 0 && ts[i].Text == what) found = i;
            else if (depth == 0 && ts[i].Text == "=>") break;   // a pipe inside a lambda body belongs to it
        }
        return found;
    }

    static List<string> SplitTop(string s, char sep)
    {
        var parts = new List<string>();
        int depth = 0, start = 0; bool str = false;
        for (int i = 0; i < s.Length; i++)
        {
            if (s[i] == '"') str = !str;
            else if (!str && "([<".Contains(s[i])) depth++;
            else if (!str && ")]>".Contains(s[i])) depth--;
            else if (!str && depth == 0 && s[i] == sep) { parts.Add(s[start..i]); start = i + 1; }
        }
        parts.Add(s[start..]);
        return parts;
    }

    static (string A, string Op, string B)? SplitTop(string s, params string[] ops)
    {
        var toks = Lex.Tokens(s);
        int depth = 0;
        for (int i = 0; i < toks.Count; i++)
        {
            if (toks[i].Text is "(" or "[") depth++;
            else if (toks[i].Text is ")" or "]") depth--;
            else if (depth == 0 && ops.Contains(toks[i].Text))
                return (s[..toks[i].Start], toks[i].Text, s[(toks[i].Start + toks[i].Text.Length)..]);
        }
        return null;
    }

    [GeneratedRegex(@"^module\s+([A-Za-z_]\w*)$")] private static partial Regex ModuleRx();
    [GeneratedRegex(@"^use\s+([A-Za-z_][\w.]*)$")] private static partial Regex UseRx();
    [GeneratedRegex(@"^type\s+([A-Za-z_]\w*)\s*=\s*record\s*\((.*)\)$")] private static partial Regex RecordRx();
    [GeneratedRegex(@"^type\s+([A-Za-z_]\w*)\s*=\s*enum\s*\(([\w\s,]*)\)$")] private static partial Regex EnumRx();
    [GeneratedRegex(@"^fn\s+([A-Za-z_]\w*)\s*(<[\w\s,]+>)?\s*\((.*?)\)\s*(?:->\s*([^=]+?))?\s*(=.*)?$")] private static partial Regex FnRx();
    [GeneratedRegex(@"^test\s+(""[^""]*"")$")] private static partial Regex TestRx();
    [GeneratedRegex(@"^\s*([A-Za-z_]\w*)\s*:\s*([^=]+?)\s*(?:=\s*(.+))?$")] private static partial Regex ParamRx();
    [GeneratedRegex(@"^let\s+(mut\s+)?([A-Za-z_]\w*)\s*(?::\s*([^=]+?))?\s*=\s*(.+)$")] private static partial Regex LetRx();
    [GeneratedRegex(@"^for\s+([A-Za-z_]\w*)\s+in\s+(.+)$")] private static partial Regex ForRx();
    [GeneratedRegex(@"^when\s+(.+)$")] private static partial Regex WhenTail();
    [GeneratedRegex(@"^[A-Za-z_]\w*$")] private static partial Regex IdentRx();
    [GeneratedRegex(@"(^|=\s*|return\s+)when\s")] private static partial Regex WhenAnywhere();
    [GeneratedRegex(@"^_?[a-z][a-z0-9_]*$")] private static partial Regex SnakeRx();
    [GeneratedRegex(@"^[A-Z][A-Za-z0-9]*$")] private static partial Regex PascalRx();
    [GeneratedRegex(@"(?<![\w.])\d+\.\d+(?![\w.])")] private static partial Regex DecimalRx();
    [GeneratedRegex(@"[;{}]\s*$|^\s*(public|private|static|class|var|using|namespace|void|int|string)\s")] private static partial Regex CSharpRx();
}

public static class Lex
{
    public const int Ident = 1, Num = 2, Str = 3, Op = 4, Bad = 5;
    public sealed record Tok(int Kind, string Text, int Start);

    static readonly string[] Ops = ["|>", "=>", "->", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "..=", "..",
        "&&", "||", "?.", "??", ">>"];

    public static List<Tok> Tokens(string s)
    {
        var ts = new List<Tok>();
        int i = 0;
        while (i < s.Length)
        {
            char c = s[i];
            if (char.IsWhiteSpace(c)) { i++; continue; }
            int st = i;
            if (c == '"')
            {
                i++;
                while (i < s.Length && s[i] != '"') i += s[i] == '\\' ? 2 : 1;
                i = Math.Min(i + 1, s.Length);
                ts.Add(new Tok(Str, s[st..i], st));
            }
            else if (c == '\'')
            {
                i++;
                while (i < s.Length && s[i] != '\'') i += s[i] == '\\' ? 2 : 1;
                i = Math.Min(i + 1, s.Length);
                ts.Add(new Tok(Str, s[st..i], st));
            }
            else if (char.IsDigit(c))
            {
                while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '_')) i++;
                if (i + 1 < s.Length && s[i] == '.' && char.IsDigit(s[i + 1]))
                {
                    i++;
                    while (i < s.Length && char.IsDigit(s[i])) i++;
                }
                ts.Add(new Tok(Num, s[st..i], st));
            }
            else if (char.IsLetter(c) || c == '_')
            {
                while (i < s.Length && (char.IsLetterOrDigit(s[i]) || s[i] == '_')) i++;
                ts.Add(new Tok(Ident, s[st..i], st));
            }
            else
            {
                var op = Ops.FirstOrDefault(o => string.CompareOrdinal(s, i, o, 0, o.Length) == 0);
                if (op != null) { ts.Add(new Tok(Op, op, st)); i += op.Length; }
                else if ("+-*/%<>=().,[]{}:;?!$@&|".Contains(c)) { ts.Add(new Tok(Op, c.ToString(), st)); i++; }
                else { ts.Add(new Tok(Bad, c.ToString(), st)); i++; }
            }
        }
        return ts;
    }
}
