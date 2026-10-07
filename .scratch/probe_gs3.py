import json, sys, pathlib

sys.path.insert(0, "backend/packages/harness/alpha")
from alpha.harness.continuous.store import GoalStore
from alpha.harness.continual.state import HarnessState

TMP = pathlib.Path("C:/Users/PREM KUMAR/Videos/alpha/.scratch")
TMP.mkdir(exist_ok=True)

# ---- GoalStore ----
d = TMP / "goals"
d.mkdir(exist_ok=True)
p = d / "goals.json"
p.write_text("{not valid json", encoding="utf-8")
gs = GoalStore(storage_path=str(p))
print("GOALSTORE degraded:", gs.is_degraded)
print("GOALSTORE load_error:", repr(gs.load_error))
print("GOALSTORE list (not empty-as-no-work):", gs.list_goals)

p.write_text(json.dumps({"goals": []}), encoding="utf-8")
gs2 = GoalStore(storage_path=str(p))
print("GOALSTORE degraded after valid:", gs2.is_degraded)
print("GOALSTORE list:", gs2.list_goals)

# ---- HarnessState ----
d2 = TMP / "hl"
d2.mkdir(exist_ok=True)
h = d2 / "harness_state.json"
h.write_text("{not valid json", encoding="utf-8")
hs = HarnessState(file_path=str(h))
print("HARNESSSTATE degraded:", hs.is_degraded)
print("HARNESSSTATE load_error:", repr(hs.load_error))
print("HARNESSSTATE entries (not empty-as-none):", list(hs.entries.values()))

h2 = d2 / "harness_state.json"
h2.write_text(json.dumps({"entries": {}}), encoding="utf-8")
hs2 = HarnessState(file_path=str(h2))
print("HARNESSSTATE degraded after valid:", hs2.is_degraded)
print("HARNESSSTATE entries:", hs2.entries)
