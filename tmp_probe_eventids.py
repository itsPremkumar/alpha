import time
t0 = time.monotonic()

# Standalone replication of _new_event_id (self-contained: datetime + itertools.count)
import datetime
import itertools
_SEQ = itertools.count()
def _new_event_id():
    return f"{datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S%f')}-{next(_SEQ):012d}"

ids = [_new_event_id() for _ in range(1000)]
t1 = time.monotonic()
print("elapsed_1000: %.3fs" % (t1 - t0))
print("count:", len(ids), "unique:", len(set(ids)))
print("sorted==insertion:", ids == sorted(ids))
print("v0:", ids[0])
print("v1:", ids[1])
print("v2:", ids[2])
print("DUP_SEQUENTIAL:", any(a == b for a, b in zip(ids, ids[1:])))

# Two threads each calling simultaneously (the TOCTOU the docstring claims is safe)
import threading
def gen(g):
    for _ in range(200):
        g.append(_new_event_id())
g1, g2 = [], []
tA = threading.Thread(target=gen, args=(g1,))
tB = threading.Thread(target=gen, args=(g2,))
tA.start(); tB.start()
tA.join(); tB.join()
all_ids = g1 + g2
print("THREADED count:", len(all_ids), "unique:", len(set(all_ids)))
print("THREADED_DUP:", len(all_ids) != len(set(all_ids)))
