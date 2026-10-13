import json
import sys

p = "contracts/feature_manifest.json"
try:
    d = json.load(open(p, encoding="utf-8"))
except Exception as e:
    print("ERROR:", e)
    sys.exit(1)
print("TOP KEYS:", list(d.keys()))
print("version:", d.get("version"), "| generated_at:", d.get("generated_at"), "| generator:", d.get("generator"))
for k, v in d.items():
    if isinstance(v, list):
        print(f"{k}: list[{len(v)}]")
        if v:
            print("  sample:", json.dumps(v[0])[:500])
    elif isinstance(v, dict):
        print(f"{k}: dict keys={list(v.keys())[:20]}")
    else:
        print(f"{k}: {type(v).__name__} = {str(v)[:200]}")
