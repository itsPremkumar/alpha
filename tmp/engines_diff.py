import json
base = "C:/Users/PREM KUMAR/Videos/alpha"
head = json.load(open(base + "/tmp/head_manifest.json"))
new = json.load(open(base + "/contracts/feature_manifest.json"))
def ids(lst):
    return sorted(e.get("id", str(e)) for e in lst)
h, n = ids(head["engines"]), ids(new["engines"])
print("head:", len(h), "new:", len(n))
print("in new not head:", [x for x in n if x not in h])
print("in head not new:", [x for x in h if x not in n])
