import subprocess, sys
root = "C:/Users/PREM KUMAR/Videos/alpha"
py = root + "/backend/.venv/Scripts/python.exe"
cmd = [py, root + "/scripts/check_generated_drift.py", "--line-endings", "exact"]
print("CMD:", " ".join(cmd))
r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=900)
print("EXIT:", r.returncode)
print("---STDOUT---")
print(r.stdout)
print("---STDERR---")
print(r.stderr)
