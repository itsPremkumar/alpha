import sys
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")
from pathlib import Path
from alpha.workflow.leases import LeaseManager, ResultVerdict
m = LeaseManager(store_dir=Path("C:/tmp/lease_probe"))
print("verdict for fresh key:", m.check_result("r1", "n1", worker_id="w1", fence_token=1, graph_version=1))
print("verdict for released key:", end=" ")
m.acquire_lease("r1", "n1", "w1")
m.release_lease("r1", "n1", "w1")
print(m.check_result("r1", "n1", worker_id="w1", fence_token=1, graph_version=1))
m.acquire_lease("r1", "n1", "w1")
m.acquire_lease("r1", "n1", "w2")
print("concurrent holder refused:", m.acquire_lease("r1", "n1", "w2"))
