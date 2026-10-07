import json

ROOT = "/c/Users/PREM KUMAR/Videos/alpha/backend"


def count_manifest(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    return {k: len(v) if isinstance(v, list) else v for k, v in m.items()}


# HEAD manifest
with open(ROOT + "/contracts/feature_manifest.json", encoding="utf-8") as f:
    head = json.load(f)
print("WORKING ARTIFACT HEAD counts:")
for k, v in head.items():
    print(f"  {k} = {len(v) if isinstance(v, list) else v}")

# Generated manifest (just regenerated)
print("\nGENERATED counts:")
with open(ROOT + "/contracts/feature_manifest.json", encoding="utf-8") as f:
    gen = json.load(f)
for k, v in gen.items():
    print(f"  {k} = {len(v) if isinstance(v, list) else v}")

# diff of tools/routers
print("\ntools head:", head.get("tools", [])[:2] if isinstance(head.get("tools"), list) else head.get("tools"))
print("GEN tools head:", gen.get("tools", [])[:2] if isinstance(gen.get("tools"), list) else gen.get("tools"))
print("GEN routers:", gen.get("routers"))
