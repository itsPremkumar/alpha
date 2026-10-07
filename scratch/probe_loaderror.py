import json, os, sys, tempfile, traceback
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend")

def show(label, obj):
    print(f"--- {label}")
    print("  type:", type(obj).__name__)
    print("  load_error:", getattr(obj, "load_error", "<n/a>"))
    print("  is_degraded:", getattr(obj, "is_degraded", "<n/a>"))

td = tempfile.mkdtemp(prefix="memprobe_")

# --- GoalStore ---
from alpha.harness.continuous.store import GoalStore
g = GoalStore(storage_path=os.path.join(td, "goals.json"))
print("GoalStore created; storage exists?", os.path.exists(g.storage_path))
open(g.storage_path, "w").write("{not valid json !!!")
g2 = GoalStore(storage_path=os.path.join(td, "goals2.json"))
show("GoalStore(corrupt file)", g2)
print("  goals list:", g2.list_goals() if hasattr(g2, "list_goals") else "n/a")

# --- HarnessState ---
from alpha.harness.continual.state import HarnessState
sf = os.path.join(td, "harness_state.json")
open(sf, "w").write("{oops")
hs = HarnessState(file_path=sf)
show("HarnessState(corrupt)", hs)
print("  load_error:", hs.load_error)
print("  entries:", hs.entries)

# partial-parse: valid JSON array with malformed entry partway -> must not destroy parsed ones
partial = os.path.join(td, "partial.json")
with open(partial, "w") as f:
    json.dump({"entries": {"prompt": [{"id":"a","kind":"prompt","title":"A","content":"1"}, {"id":"b"}]}}, f)
hs2 = HarnessState(file_path=partial)
print("HarnessState(partial): load_error:", hs2.load_error)
print("  entries prompt:", hs2.entries["prompt"])
print("  has b:", "b" in hs2.entries["prompt"])

# --- HarnessSnapshotManager ---
from alpha.harness.continual.snapshots import HarnessSnapshotManager
# needs a state whose file_path exists; build with a valid file then corrupt manifest
mdir = os.path.join(td, "snaps")
os.makedirs(mdir, exist_ok=True)
msf = os.path.join(mdir, "manifest.json")
open(msf, "w").write("{oops")
mgr = HarnessSnapshotManager(hs2)
show("HarnessSnapshotManager instance", mgr)
print("  snapshot_dir:", mgr.snapshot_dir)
print("  list_snapshots():", mgr.list_snapshots())
print("  has is_degraded:", hasattr(mgr, "is_degraded"))
print("  has load_error:", hasattr(mgr, "load_error"))
