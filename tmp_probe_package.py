import time, sys
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")
print("start", flush=True)
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
t0=time.monotonic()
import alpha.workflow as w
print("alpha.workflow full import: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.executors
print("executors: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.runtime
print("runtime: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.runtime as rt
print("runtime as: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.dynamic_perception
print("dynamic_perception: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.dynamic_service
print("dynamic_service: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.dynamic_assembler
print("dynamic_assembler: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.branch_assembler
print("branch_assembler: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.branch_router
print("branch_router: ", _fmt(time.monotonic()-t0), flush=True)
t0=time.monotonic()
import alpha.workflow.workflow_kernel
print("workflow_kernel: ", _fmt(time.monotonic()-t0), flush=True)
print("DONE", flush=True)
