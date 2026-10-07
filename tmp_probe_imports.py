import time
def t(label):
    def deco(fn):
        def wrapper(*a, **k):
            t0 = time.monotonic()
            r = fn(*a, **k)
            print(f"{label}: {_fmt(time.monotonic()-t0)}", flush=True)
            return r
        return wrapper
    return deco
def _fmt(x):
    return f"{x:.3f}s"

import importlib
print("start", flush=True)
t0=time.monotonic()
import alpha
print("alpha: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow
print("alpha.workflow: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.models as m
print("alpha.workflow.models: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.events as e
print("alpha.workflow.events: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.leases as l
print("alpha.workflow.leases: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.patch as p
print("alpha.workflow.patch: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.router as r
print("alpha.workflow.router: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.scheduler as s
print("alpha.workflow.scheduler: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.orchestrator.replay
print("alpha.orchestrator.replay: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.orchestrator.loop
print("alpha.orchestrator.loop: ", _fmt(time.monotonic()-t0), flush=True)
print("DONE", flush=True)
