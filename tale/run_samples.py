"""Run every tale in this folder and check each one does what it should:

- samples/*.tale and demo/fly/*.tale fly: they compile clean and every example passes
- demo/frame/*.tale (not drafts) frame clean
- each of those survives the round trip: narrated back and framed again, it is the same program
- mistakes/*.tale and demo/frame/*.draft.tale are refused, with a question
"""
import subprocess
import sys
from pathlib import Path

here = Path(__file__).parent
sys.stdout.reconfigure(encoding="utf-8")
frame_dir = here / "demo" / "frame"
cases = ([("fly", f, True) for f in sorted((here / "samples").glob("*.tale"))] +
         [("fly", f, True) for f in sorted((here / "demo" / "fly").glob("*.tale"))] +
         [("frame", f, True) for f in sorted(frame_dir.glob("*.tale")) if not f.name.endswith(".draft.tale")] +
         [("frame", f, False) for f in sorted(frame_dir.glob("*.draft.tale"))] +
         [("frame", f, False) for f in sorted((here / "mistakes").glob("*.tale"))])
bad = 0
for verb, f, should_pass in cases:
    p = subprocess.run([sys.executable, str(here / "tale.py"), verb, str(f)], capture_output=True, text=True,
                       encoding="utf-8")
    lines = p.stdout.strip().splitlines() or [p.stderr.strip()]
    ok = (p.returncode == 0) == should_pass
    shown = lines[-1] if should_pass else lines[0]
    if ok and should_pass:   # and telling its program again gives back the same program
        r = subprocess.run([sys.executable, str(here / "narrate.py"), "roundtrip", str(f)], capture_output=True,
                           text=True, encoding="utf-8")
        ok = r.returncode == 0
        shown += ", " + (r.stdout.strip().splitlines() or ["?"])[-1]
    bad += not ok
    print(f"{'pass' if ok else 'FAIL'}  {verb:5} {f.relative_to(here).as_posix():34} {shown}")
print("all good" if not bad else f"{bad} problem(s)")
sys.exit(1 if bad else 0)
