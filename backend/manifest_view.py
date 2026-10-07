import json

WORK = "/c/Users/PREM KUMAR/Videos/alpha/backend/contracts/feature_manifest.json"


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


try:
    work = load(WORK)
except Exception as e:
    print("work load error:", e)
    work = None
    # try the git sandbox version
    try:
        with open("/tmp/head_manifest.json", encoding="utf-8") as f:
            work = json.load(f)
    except Exception as e2:
        print("head load error:", e2)

if work is not None:

    def label(m):
        return {
            "tools": len(m.get("tools", [])),
            "routers": len(m.get("routers", [])),
            "middlewares": len(m.get("middlewares", [])),
            "loops": len(m.get("loops", [])),
            "engines": len(m.get("engines", [])),
        }

    print("WORKING manifest label:")
    for k, v in label(work).items():
        print(f"  {k} = {v}")

    # git HEAD versions
    for tag, path in [("HEAD", "/tmp/head_manifest.json"), ("HEAD~1", "/tmp/head1_manifest.json")]:
        try:
            m = load(path)
            print(f"{tag} manifest label:")
            for k, v in label(m).items():
                print(f"  {k} = {v}")
        except Exception as e:
            print(tag, "load error:", e)
