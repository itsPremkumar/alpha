import os, subprocess, sys

demo = sys.argv[1:] if len(sys.argv) > 1 else ["ls -l", "echo ok"]
for cmd in demo:
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(timeout=3)
        print("CMD", repr(cmd), "rc", p.returncode, "out", repr(out[:120]), "err", repr(err[:120]))
    except Exception as e:
        print("ERR", type(e).__name__, repr(str(e)[:300]))
