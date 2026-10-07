import importlib.util, sys, time
p = "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha/workflow/events.py"
spec = importlib.util.spec_from_file_location("events_mod", p)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import datetime
print("module file:", p, flush=True)
t0 = time.monotonic()
ids = [m._new_event_id() for _ in range(500)]
t1 = time.monotonic()
print("import+500 elapsed: %.3fs" % (t1-t0), flush=True)
print("count:", len(ids), "unique:", len(set(ids)), flush=True)
print("sorted==insertion:", ids == sorted(ids), flush=True)
print("v0:", ids[0], flush=True)
print("v1:", ids[1], flush=True)
print("v2:", ids[2], flush=True)
# two threads
import threading
def gen(g):
    for _ in range(100):
        g.append(m._new_event_id())
g1, g2 = [], []
tA = threading.Thread(target=gen, args=(g1,))
tB = threading.Thread(target=gen, args=(g2,))
tA.start(); tB.start()
tA.join(); tB.join()
all_ids = g1 + g2
print("THREADED count:", len(all_ids), "unique:", len(set(all_ids)), flush=True)
print("THREADED_DUP:", len(all_ids) != len(set(all_ids)), flush=True)
# also check the 'next()' counter monotonic per process: last counter value
counter = ids[-1].split("-")[1]
print("LAST_COUNTER:", counter, flush=True)
