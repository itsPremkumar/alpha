import json, shutil, subprocess, sys

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
        committed = open(MANIFEST, encoding="utf-8").read()
        wired_before = committed.count('"wired": true')
        open(MANIFEST, "w", encoding="utf-8").write(committed.replace('"wired": true', '"wired": false', 1))
        rc, out = run_gate()
        lines = out.strip().splitlines()
        print("=== PROBE: flipped one 'wired':true -> 'wired':false ===")
        print("wired count before:", wired_before, "after:", wired_before - 1)
        print("gate rc:", rc)
        print("gate first line:", lines[0] if lines else "(empty)")
        print("gate reports DRIFT:", "DRIFT" in out)
        print("diff lines present:", any(("+++ b/contracts/feature_manifest.json" in l or "+  \"wired\": false" in l) for l in lines))
    finally:
        shutil.move(BAK, MANIFEST)
    rc, out = run_gate()
    print("=== probe: committed manifest restored, re-run ===")
    print("gate rc:", rc, "| first line:", out.strip().splitlines()[0] if out.strip() else "(empty)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
