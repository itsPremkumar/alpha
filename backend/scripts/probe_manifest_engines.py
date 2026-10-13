import json

d = json.load(open("contracts/feature_manifest.json", encoding="utf-8"))
engs = d.get("engines", [])
print("total engines:", len(engs))
print("sample engine:", json.dumps(engs[0], indent=2))
print()
# collect ids
ids = [e.get("id") for e in engs]
print("count:", len(ids))
print("first 10 ids:", ids[:10])
# look for height in any engine
for e in engs:
    blob = json.dumps(e).lower()
    if "height" in blob:
        print("ENGINE WITH HEIGHT:", json.dumps(e, indent=2))
        break
else:
    print("no engine contains 'height'")
