import sys
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")
from alpha.workflow.events import _new_event_id
ids = [_new_event_id() for _ in range(5000)]
print("count:", len(ids), "unique:", len(set(ids)))
print("first:", ids[0])
print("last:", ids[-1])
print("sorted==insertion:", ids == sorted(ids))
