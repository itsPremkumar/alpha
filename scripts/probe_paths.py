import os, sys
ROOT = "/c/Users/PREM KUMAR/Videos/alpha"
print("python:", sys.executable)
print("ROOT exists:", os.path.exists(ROOT))
print("ROOT listdir head:", os.listdir(ROOT)[:5])
M = ROOT + "/contracts/feature_manifest.json"
print("MANIFEST exists:", os.path.exists(M))
print("MANIFEST size:", os.path.getsize(M) if os.path.exists(M) else None)
BAK = M + ".probe.bak"
print("BAK exists:", os.path.exists(BAK))
print("copy2 would dest dir exist:", os.path.isdir(os.path.dirname(V if False else BAK)))
