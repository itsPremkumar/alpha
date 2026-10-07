import time, sys
sys.path.insert(0, "C:/Users/PREM KUMAR/Videos/alpha/backend/packages/harness/alpha")
t0=time.monotonic()
import alpha.workflow as w
print("alpha.workflow:", round(time.monotonic()-t0,3), flush=True)
# list submodules
print("dir:", [x for x in dir(w) if not x.startswith('_')], flush=True)
