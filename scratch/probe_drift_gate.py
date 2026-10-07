import json, subprocess, sys, pathlib, tempfile, os

root = pathlib.Path(r"C:/Users/PREM KUMAR/Videos/alpha")
manifest = root / "contracts" / "feature_manifest.json"
generator = root / "backend" / "scripts" / "generate_feature_manifest.py"

tmp = pathlib.Path(tempfile.mkdtemp(prefix="drift_gate_probe_"))
copy = tmp / "copy"
copy.mkdir(parents=True)

for rel, src in [("contracts/feature_manifest.json", manifest),
                 ("backend/scripts/generate_feature_manifest.py", generator),
                 ("scripts/check_generated_drift.py", root / "scripts" / "check_generated_drift.py")]:
    dst = copy / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

print("temp copy at", copy)

data = json.loads((copy / "contracts" / "feature_manifest.json").read_text())
print("BEFORE engines:", len(data["engines"]))
# Mutate engines from 117 down to 116 (off-by-one removal)
data["engines"] = data["engines"][:116] + data["engines"][117:]
print("AFTER  engines:", len(data["engines"]))
(copy / "contracts" / "feature_manifest.json").write_text(json.dumps(data, indent=2) + "\n")

env = dict(os.environ, ALPHA_ROOT=str(tmp))
res = subprocess.run(
    [sys.executable, "scripts/check_generated_drift.py", "--line-endings", "exact"],
    cwd=copy, env=env, capture_output=True, text=True,
)
print("=== GATE FAILED-SIDE OBSERVATION (mutated copy, engines 117->116) ===")
print(res.stdout)
print("STDERR:", res.stderr[-1200:])
print("EXIT:", res.returncode)
