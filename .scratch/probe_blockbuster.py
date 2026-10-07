import subprocess, sys
r = subprocess.run(
    [r"C:\Users\PREM KUMAR\Videos\alpha\backend\.venv\Scripts\python.exe", "-c",
     "import blockbuster; print('blockbuster installed')"],
    capture_output=True, text=True)
print("STDOUT:", r.stdout)
print("STDERR:", r.stderr)
print("RC:", r.returncode)
