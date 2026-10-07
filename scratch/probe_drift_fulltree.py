import json, subprocess, sys, pathlib, tempfile, shutil, os

root = pathlib.Path(r"C:/Users/PREM KUMAR/Videos/alpha")
manifest = root / "contracts" / "feature_manifest.json"

# Copy the whole checkout to a temp dir so the generator's imports resolve.
tmp = pathlib.Path(tempfile.mkdtemp(prefix="drift_fulltree_"))
shutil.copytree(root, tmp / "alpha", ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", "*.pyc", "logs", "tmp", "build"))
copy = tmp / "alpha"
print("full-tree copy at", copy)

data = json.loads((copy / "contracts" / "feature_manifest.json").read_text())
print("BEFORE engines:", len(data["engines"]))
data["engines"] = data["engines"][:116] + data["engines"][117:]
print("AFTER  engines:", len(data["engines"]))
(copy / "contracts" / "feature_manifest.json").write_text(json.dumps(data, indent=2) + "\n")

env = dict(os.environ, ALPHA_ROOT=str(copy))
res = subprocess.run(
    [sys.executable, "scripts/check_generated_drift.py", "--line-endings", "exact"],
    cwd=copy, env=env, capture_output=True, text=True,
)
print("=== GATE FAILED-SIDE OBSERVATION (full-tree copy, engines 117->116) ===")
print(res.stdout[:4000])
print("STDERR:", res.stderr[-1200:])
print("EXIT:", res.returncode)
