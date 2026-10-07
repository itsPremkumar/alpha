import json

HEAD = "/tmp/head_manifest.json"
WORK = "/c/Users/PREM KUMAR/Videos/alpha/backend/contracts/feature_manifest.json"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


head = load(HEAD)
work = load(WORK)


def label(m):
    return {
        "tools": len(m.get("tools", [])),
        "routers": len(m.get("routers", [])),
        "middlewares": len(m.get("middlewares", [])),
        "loops": len(m.get("loops", [])),
        "engines": len(m.get("engines", [])),
    }


print("HEAD   :", label(head))
print("WORKING:", label(work))

h_tools = set(str(x) for x in head.get("tools", []))
w_tools = set(str(x) for x in work.get("tools", []))
print("New tools:", sorted(w_tools - h_tools)[:10])
print("Removed tools:", sorted(h_tools - w_tools)[:10])

h_r = set(str(x) for x in head.get("routers", []))
w_r = set(str(x) for x in work.get("routers", []))
print("New routers:", sorted(w_r - h_r))
print("Removed routers:", sorted(h_r - w_r))

h_l = set(str(x) for x in head.get("loops", []))
w_l = set(str(x) for x in work.get("loops", []))
print("New loops:", sorted(w_l - h_l))
print("Removed loops:", sorted(h_l - w_l))
