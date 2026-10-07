import json, urllib.request

def get(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8001" + path, timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"_error": str(e)}

d = get("/openapi.json")
if "_error" in d:
    print(d)
    raise SystemExit

for p in sorted(d["paths"]):
    for m in sorted(d["paths"][p]):
        route = d["paths"][p][m]
        deps = [str(x) for x in route.get("dependencies", [])]
        print(f"{m.upper():6} {p:55} deps={deps}")
