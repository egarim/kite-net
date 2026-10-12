"""The champion as one GGUF file for Ollama, LM Studio and llama.cpp.

    python -m kitegym.export --quant Q4_K_M --llama-cpp ~/src/llama.cpp     # 0.99 GB, fast on CPU
    python -m kitegym.export --quant Q8_0 --llama-cpp ~/src/llama.cpp       # 1.65 GB, closest to the adapter

Steps: mlx-lm fuse --dequantize (the adapter merged into full weights) -> llama.cpp convert_hf_to_gguf ->
llama-quantize -> fused/kite-coder-<quant>.gguf + fused/kite-coder.Modelfile -> `ollama create kite-coder` and a
copy into LM Studio's models folder (skip both with --no-install).
Needs llama.cpp: `brew install llama.cpp` (llama-quantize) and its source for the converter
(git clone --depth 1 https://github.com/ggml-org/llama.cpp).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kitegym.common import ROOT
from kitegym.tasks import SYSTEM

FUSED = ROOT / "fused"


def sh(*cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="kite-coder")
    ap.add_argument("--quant", default="Q4_K_M", help="Q4_K_M (smaller, faster on CPU) or Q8_0 (closest to the adapter)")
    ap.add_argument("--llama-cpp", default=os.environ.get("LLAMA_CPP"), help="llama.cpp source checkout")
    ap.add_argument("--no-install", action="store_true", help="write the GGUF and Modelfile only")
    a = ap.parse_args()

    champ = json.loads((ROOT / "results" / "champion.json").read_text(encoding="utf-8"))
    convert = Path(a.llama_cpp or "") / "convert_hf_to_gguf.py"
    if not convert.exists() or not shutil.which("llama-quantize"):
        sys.exit("needs llama.cpp: --llama-cpp <source checkout> and llama-quantize on PATH (see above)")
    adapter = ROOT / "adapters" / champ["candidate"]
    hf, f16 = FUSED / f"{a.name}-hf", FUSED / f"{a.name}-f16.gguf"
    gguf = FUSED / f"{a.name}-{a.quant.lower()}.gguf"
    sh(sys.executable, "-m", "mlx_lm", "fuse", "--model", champ["model"], "--adapter-path", adapter,
       "--save-path", hf, "--dequantize")
    sh(sys.executable, convert, hf, "--outfile", f16, "--outtype", "f16",
       env={**os.environ, "PYTHONPATH": str(Path(a.llama_cpp) / "gguf-py")})
    sh("llama-quantize", f16, gguf, a.quant)
    shutil.rmtree(hf)
    f16.unlink()                                      # intermediates: about 3 GB, rebuilt in a minute
    mf = FUSED / f"{a.name}.Modelfile"                # trained with this one-line system prompt
    mf.write_text(f'FROM ./{gguf.name}\nSYSTEM """{SYSTEM}"""\nPARAMETER temperature 0\nPARAMETER num_ctx 4096\n',
                  encoding="utf-8")
    if not a.no_install:
        sh("ollama", "create", a.name, "-f", mf.name, cwd=FUSED)
        lmstudio = Path.home() / ".lmstudio" / "models"
        if lmstudio.exists():                         # LM Studio lists any GGUF under models/<user>/<repo>/
            dst = lmstudio / "kite-net" / f"{a.name}-GGUF"
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(gguf, dst / gguf.name)
            print(f"LM Studio: {dst / gguf.name}")
    print(f"{a.name}: {gguf} ({gguf.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
