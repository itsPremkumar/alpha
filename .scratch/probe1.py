"""Probe 1: does deleting DynamicContextMiddleware._inject break any test?"""
import subprocess, sys, pathlib

# Read the middleware module source
mw = pathlib.Path("backend/packages/harness/alpha/agents/middlewares/dynamic_context_middleware.py")
src = mw.read_text(encoding="utf-8")
lines = src.splitlines()

# Find the _inject function
print("=== _inject in dynamic_context_middleware.py ===")
in_func = False
for i, ln in enumerate(lines, 1):
    if ln.startswith("def _inject"):
        in_func = True
    if in_func:
        print(f"{i}: {ln}")
        if in_func and ln.strip() == "" and i > 0:
            pass
