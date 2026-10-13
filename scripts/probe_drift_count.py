#!/usr/bin/env python3
"""Probe: does check_generated_drift.py detect a CHANGED COUNT in the manifest?"""
import shutil
import subprocess
import sys

ROOT = "/c/Users/PREM KUMAR/Videos/alpha"
MANIFEST = ROOT + "/contracts/feature_manifest.json"
BAK = ROOT + "/contracts/feature_manifest.json.probe.bak"

def run_gate():
    r = subprocess.run(
        ["python", "scripts/check_generated_drift.py", "--line-endings", "exact"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")

def main():
    shutil.copy2(MANIFEST, BAK)
    try:
        txt = open(MANIFEST, encoding="utf-8").read()
        # Flip ONE linked entry from wired->unwired. Count how many entries were originally wired.
        import re
        # count wired:true occurrences
        wired_before = txt.count('"wired": true')
        new_txt = txt.replace('"wired": true', '"wired": false', 1)
        open(MANIFEST, "w", encoding="utf-8").write(new_txt)
        rc, out = run_gate()
        print("=== probe: flipped one 'wired':true -> 'wired':false ===")
        print("wired count before:", wired_before, "after:", wired_before-1)
        print("gate rc:", rc)
        print("gate tail:", " | ".join(out.strip().splitlines()[-3:]))
        print("gate reports content drift:", "changed line" in out and "DRIFT" in out)
    finally:
        shutil.move(BAK, MANIFEST)
    # also prove an unchanged manifest yields rc 0 (already measured: normalized=0; do exact again
    r = run_gate()
    print("=== probe: committed manifest untouched (re-run) ===")
    print("gate rc:", r[0], "| header:", " | ".join(r[1].strip().splitlines()[:1]))
    return 0

if __name__ == "__main__":
    sys.exit(main())
