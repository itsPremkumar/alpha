import json, os, sys, tempfile
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend")

print("=== Test 1: partial-parse data loss (HarnessState) ===")
td = tempfile.mkdtemp(prefix="pp_")
from alpha.harness.continual.state import HarnessState

# Build a valid file with two entries, then corrupt the middle of 'memory' entries.
p1 = os.path.join(td, "valid.json")
with open(p1, "w") as f:
    json.dump({"entries": {"prompt": [{"id":"p1","kind":"prompt","title":"T1","content":"A"}, {"id":"p2","kind":"prompt","title":"T2","content":"B"}], "memory": []} }, f)
hs = HarnessState(file_path=p1)
print("valid -> entries p1,p2 present:", "p1" in hs.entries["prompt"] and "p2" in hs.entries["prompt"], "load_error:", hs.load_error)

# Now corrupt the file partway through the memory array (valid entries before, malformed after).
p2 = os.path.join(td, "corrupt.json")
with open(p2, "w") as f:
    f.write(json.dumps({"entries": {"prompt": [{"id":"x1","kind":"prompt","title":"X1","content":"X1"}, {"id":"x2","kind":"prompt","title":"X2","content":"X2"}], "memory": [{"id":"m1","kind":"memory","title":"M1","content":"OK"},{invalid}] }})
try:
    hs2 = HarnessState(file_path=p2)
    print("corrupt -> load_error:", hs2.load_error)
    print("  entries preserved after partial parse:", list(hs2.entries["prompt"].keys()), "load_error set:", hs2.load_error is not None)
    # the defect hypothesis: if entries were adopted in-place (self.entries=staged only on full success),
    # this file would still show preserved. The real defect is a DIFFERENT pattern: entries adopted in-place
    # then save() writes back the half-populated state.
    # To prove destructive-write: check the saved file content.
    print("  written back file:", open(p2).read()[:400])
except Exception as e:
    print("  exception:", type(e).__name__, e)

print()
print("=== Test 2: GoalStore is_degraded vs empty on corrupt file ===")
from alpha.harness.continuous.store import GoalStore
gs = os.path.join(td, "g.json")
open(gs, "w").write("{oops")
gs2 = GoalStore(storage_path=gs)
print("corrupt -> load_error:", gs2.load_error, "| is_degraded:", gs2.is_degraded)
print("  list_goals():", gs2.list_goals(), "(empty list is the FAIL-OPEN read)")

print()
print("=== Test 3: HarnessSnapshotManager rollback manifest corruption ===")
from alpha.harness.continual.snapshots import HarnessSnapshotManager
sd = os.path.join(td, "snaps")
ms = os.path.join(sd, "manifest.json")
open(ms, "w").write("{oops; the snapshot disk file sits unreadable, restore returns []")
hs3 = HarnessState(file_path=os.path.join(td, "s.json"))
mgr = HarnessSnapshotManager(hs3)
print("list_snapshots() due to corrupt manifest:", mgr.list_snapshots())
print("  expected: [] but files exist on disk => DORMANT DISCLOSURE / silent-rollback-impossible")
