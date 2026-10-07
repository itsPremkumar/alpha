import json, os, sys, tempfile
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend")

td = tempfile.mkdtemp(prefix="goal_")
from alpha.harness.continuous.store import GoalStore

gs = os.path.join(td, "goals.json")
open(gs, "w").write("{oops")
print("file bytes:", repr(open(gs).read()))
g = GoalStore(storage_path=gs)
print("load_error:", repr(g.load_error))
print("is_degraded:", g.is_degraded)
print("list_goals():", g.list_goals())
print("is_durable:", g.is_durable)
