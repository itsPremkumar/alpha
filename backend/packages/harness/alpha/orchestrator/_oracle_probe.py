import inspect
import os
import sys

ROOT = os.path.abspath("C:/Users/PREM KUMAR/Videos/alpha/backend")
sys.path.insert(0, ROOT)

import alpha.orchestrator.replay as replay_mod
print("REPLAY_FILE =", replay_mod.__file__)

path = os.path.join(ROOT, "packages/harness/alpha/orchestrator/replay.py")
print("WORKING_TREE_PATH = ", path)
print("WORKING_TREE_HAS_FIX = ", "external_wait_parked" in open(path).read())

src = inspect.getsource(replay_mod.replay_run)
print("IMPORTED_MODULE_HAS_FIX = ", "external_wait_parked" in src)
