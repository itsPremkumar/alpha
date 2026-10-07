import time, sys
t0 = time.monotonic()
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")
print("importing...", flush=True)
t1 = time.monotonic()
import alpha.workflow.events as e
t2 = time.monotonic()
print("import elapsed: %.2fs" % (t2 - t1), flush=True)
print("module ok", flush=True)
t3 = time.monotonic()
ids = [e._new_event_id() for _ in range(1000)]
t4 = time.monotonic()
print("1000 ids elapsed: %.2fs" % (t4 - t3), flush=True)
print("count", len(ids), "unique", len(set(ids)), flush=True)
print("v0", ids[0], flush=True)
