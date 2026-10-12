"""Score a model on Kite.NET tasks it has never seen: it writes the code, the Kite compiler and the
task's hidden tests decide.

    python -m kitegym.eval --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit --name stock
    python -m kitegym.eval --model ... --adapter adapters/<run> --name <run> --repairs 2

Per task: compiles (0 errors) ↑, passes (compiles and every hidden test passes) ↑, clean (0 errors and
0 warnings) ↑, C# leakage (the Kite analyzer's KT004: C# written into Kite) ↓, warnings per compiling program ↓,
and with --repairs N the same after the harness feeds kite's messages back up to N times. Also tokens/s.
Writes results/kite/<name>-<split>.json with every answer.
"""

import argparse
import json
import re
import time

from kitegym.common import DATASETS, ROOT, read_jsonl
from kitegym.tasks import KITE, REPAIR_ASK

OUT = ROOT / "results"
CS_VALID = 60


def code_of(reply):
    """The code in a reply: inside a ``` fence if there is one, else all of it."""
    m = re.search(r"```[a-zA-Z#]*\n(.*?)```", reply, re.S)
    return (m.group(1) if m else reply).strip("\n")


def kite_report(solution, tests, suffix=".kite"):
    import os
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as f:
        f.write(solution.rstrip() + "\n\n" + tests.rstrip() + "\n")
        path = f.name
    try:
        p = subprocess.run([str(KITE / "bin" / "kite"), "test", path, "--json"], capture_output=True, text=True,
                           timeout=60)
        r = json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:                                   # noqa: BLE001 - a crash or hang counts as a failure
        r = {"errors": 1, "warnings": 0, "diagnostics": [{"id": "KITE", "line": 0, "severity": "error",
                                                          "message": "kite crashed or timed out"}], "tests": []}
    finally:
        os.unlink(path)
    # diagnostics in the hidden tests are the tests' business: count only the solution's lines
    n = solution.rstrip().count("\n") + 1
    mine = [d for d in r["diagnostics"] if d["line"] <= n]
    tests_ok = bool(r.get("tests")) and all(t["pass"] for t in r["tests"])
    return {"errors": sum(d["severity"] == "error" for d in mine) + (r["errors"] > 0 and not mine),
            "warnings": sum(d["severity"] == "warning" for d in mine),
            "leak": any(d["id"] == "KT004" for d in mine),
            "passes": r["errors"] == 0 and tests_ok,
            "messages": [f"line {d['line']}: {d['severity']} {d['id']}: {d['message']}" for d in r["diagnostics"]][:12]
            + [f"test {t['name']!r} failed: {t['error']}" for t in r.get("tests") or [] if not t["pass"]][:5]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--split", default="test", choices=["test", "valid"])
    ap.add_argument("--csharp", action="store_true", help="the plain C# set instead (not dumber)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--repairs", type=int, default=0, help="harness: feed kite's messages back up to N times")
    ap.add_argument("--max-tokens", type=int, default=768)
    ap.add_argument("--name", required=True)
    ap.add_argument("--ollama", action="store_true", help="--model is an Ollama model name (the exported GGUF)")
    a = ap.parse_args()

    if a.ollama:
        model = tok = None
    else:
        from mlx_lm import load, stream_generate
        from mlx_lm.sample_utils import make_sampler
        model, tok = load(a.model, adapter_path=a.adapter) if a.adapter else load(a.model)
        greedy = make_sampler(temp=0.0)
    rows = read_jsonl(DATASETS / "kite" / ("csharp-test.jsonl" if a.csharp else f"{a.split}.jsonl"))
    if a.csharp:                      # the first 60 C# tasks choose checkpoints (valid), the rest is the guard (test)
        rows = rows[:CS_VALID] if a.split == "valid" else rows[CS_VALID:]
    rows = rows[:a.limit]
    suffix = ".cs" if a.csharp else ".kite"
    gen_tokens, gen_seconds, results = 0, 0.0, []

    def ask(messages):
        nonlocal gen_tokens, gen_seconds
        if a.ollama:                       # the exported GGUF, served by Ollama on this machine (127.0.0.1)
            import urllib.request
            body = json.dumps({"model": a.model, "messages": messages, "stream": False,
                               "options": {"temperature": 0, "num_predict": a.max_tokens}}).encode()
            with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/chat", body), timeout=600) as r:
                d = json.load(r)
            gen_tokens += d.get("eval_count", 0)
            gen_seconds += d.get("eval_duration", 0) / 1e9
            return d["message"]["content"]
        prompt = tok.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
        text, last, t = "", None, time.time()
        for last in stream_generate(model, tok, prompt, max_tokens=a.max_tokens, sampler=greedy):
            text += last.text
        gen_seconds += time.time() - t
        gen_tokens += last.generation_tokens if last else 0
        return text

    for k, r in enumerate(rows):
        messages = r["messages"][:2]
        reply = ask(messages)
        first = kite_report(code_of(reply), r["tests"], suffix)
        final, tries = first, 0
        while not final["passes"] and tries < a.repairs:
            tries += 1
            messages = messages + [{"role": "assistant", "content": reply},
                                   {"role": "user", "content": REPAIR_ASK.format(report="\n".join(final["messages"]))}]
            reply = ask(messages)
            final = kite_report(code_of(reply), r["tests"], suffix)
        results.append({"family": r["family"], "prompt": r["messages"][1]["content"], "first": first,
                        "final": final, "repairs": tries, "answer": code_of(reply)})
        print(f"  {k + 1}/{len(rows)} {'pass' if first['passes'] else 'compiles' if not first['errors'] else 'fails'}"
              f"{' -> pass after ' + str(tries) if tries and final['passes'] else ''}", flush=True)

    n = len(results)
    rate = lambda f: sum(map(f, results)) / n
    compiled = [x["first"] for x in results if not x["first"]["errors"]]
    metrics = {
        "compiles": rate(lambda x: not x["first"]["errors"]),
        "passes": rate(lambda x: x["first"]["passes"]),
        "clean": rate(lambda x: not x["first"]["errors"] and not x["first"]["warnings"]),
        "csharp_leak": rate(lambda x: x["first"]["leak"]),
        "warnings_per_compiled": sum(c["warnings"] for c in compiled) / max(1, len(compiled)),
        "tokens_per_s": gen_tokens / max(gen_seconds, 1e-9),
    }
    if a.repairs:
        metrics["passes_after_repairs"] = rate(lambda x: x["final"]["passes"])
    direction = {"compiles": "↑", "passes": "↑", "clean": "↑", "csharp_leak": "↓", "warnings_per_compiled": "↓",
                 "tokens_per_s": "↑", "passes_after_repairs": "↑"}
    print(f"\n{a.name} on {'csharp ' if a.csharp else 'kite '}{a.split} ({n} tasks): {a.model}{' + ' + a.adapter if a.adapter else ''}")
    for m, v in metrics.items():
        print(f"  {direction[m]} {m:24s} {v:.1%}" if m not in ("warnings_per_compiled", "tokens_per_s")
              else f"  {direction[m]} {m:24s} {v:.2f}")
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{a.name}-{'csharp-' if a.csharp else ''}{a.split}.json"
    out.write_text(json.dumps({"model": a.model, "adapter": a.adapter, "split": a.split, "repairs": a.repairs,
                               "metrics": metrics, "results": results}, indent=1), encoding="utf-8")
    print(f"  -> {out}")


if __name__ == "__main__":
    main()
