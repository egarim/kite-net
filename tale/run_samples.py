"""Fly every samples/*.tale; frame every mistakes/*.tale and check that each one is refused."""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).parent
sys.stdout.reconfigure(encoding="utf-8")
bad = 0
for f in sorted((here / "samples").glob("*.tale")):
    p = subprocess.run([sys.executable, str(here / "tale.py"), "fly", str(f)], capture_output=True, text=True,
                       encoding="utf-8")
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr.strip()
    ok = p.returncode == 0
    bad += not ok
    print(f"{'pass' if ok else 'FAIL'}  {f.name:28} {last}")
for f in sorted((here / "mistakes").glob("*.tale")):
    p = subprocess.run([sys.executable, str(here / "tale.py"), "frame", str(f)], capture_output=True, text=True,
                       encoding="utf-8")
    ok = p.returncode != 0
    bad += not ok
    print(f"{'pass' if ok else 'FAIL'}  {f.name:28} {p.stdout.strip().splitlines()[0]}")
print("all good" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)
