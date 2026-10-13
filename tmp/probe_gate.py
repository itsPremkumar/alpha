import subprocess, sys, os
cmd = [r"C:\Users\PREM KUMAR\Videos\alpha\backend\.venv\Scripts\python.exe", "-m", "pytest", "backend/tests/blocking_io/test_gate_smoke.py::test_gate_catches_unoffloaded_blocking_io_in_alpha_module",
       "-q", "-p", "no:cacheprovider", "--tb=short", "-s"]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
print("RC", r.returncode)
print("---STDOUT---")
print(r.stdout[-4000:])
print("---STDERR---")
print(r.stderr[-2000:])
