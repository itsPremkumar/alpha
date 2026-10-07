import subprocess, sys

root = "C:/Users/PREM KUMAR/Videos/alpha"
py = root + "/backend/.venv/Scripts/python.exe"

# 1. Inspect committed manifest bytes
p = root + "/contracts/feature_manifest.json"
data = open(p, "rb").read()
print("=== COMMITTED MANIFEST ===")
print("bytes:", len(data))
print("CRLF count:", data.count(b"\r\n"))
print("LF count:", data.count(b"\n"))
print("CR count:", data.count(b"\r"))

# 2. Generate fresh
r = subprocess.run(
    [py, root + "/backend/scripts/generate_feature_manifest.py"],
    cwd=root + "/backend", capture_output=True, text=True, timeout=300)
print("\n=== GENERATOR ===")
print("exit:", r.returncode)
print("stdout:", r.stdout)
print("stderr:", r.stderr)

# 3. Compare committed vs generated
r2 = subprocess.run(
    [py, root + "/scripts/check_generated_drift.py", "--line-endings", "exact"],
    cwd=root, capture_output=True, text=True, timeout=900)
print("\n=== DRIFT GATE (exact) ===")
print("exit:", r2.returncode)
print(r2.stdout[-4000:])
print(r2.stderr[-1000:])
