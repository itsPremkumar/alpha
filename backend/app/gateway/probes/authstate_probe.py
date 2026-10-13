"""Check whether the running gateway enforces authentication at all."""

import subprocess
import sys

GATE = "http://127.0.0.1:8001"


def curl(args):
    r = subprocess.run(["curl", "-s", "-m", "6", "-o", "/tmp/curl_body"] + args, capture_output=True, text=True, timeout=10)
    try:
        body = open("/tmp/curl_body", encoding="utf-8", errors="replace").read()
    except Exception:
        body = ""
    return r.returncode, body


def show(label, args):
    code, body = curl(args)
    print(f"{label:60s} -> HTTP {code}  {body[:160]!r}")


print("### running app.py listener check")
_, out = curl(["-s", "-m", "6", "http://127.0.0.1:8001/health"])
print("GET /health (current process):", out[:120])

print("\n### Bearer token probes (no auth header otherwise)")
show("GET /api/threads  + garbage Bearer", ["-H", "Authorization: Bearer garbage-not-a-token", "http://127.0.0.1:8001/api/threads"])
show("POST /api/groups + garbage Bearer", ["-H", "Authorization: Bearer garbage-not-a-token", "-X", "POST", "-H", "Content-Type: application/json", "-d", "{}", "http://127.0.0.1:8001/api/groups"])
show("GET /api/threads  + no header at all", ["http://127.0.0.1:8001/api/threads"])
show("POST /api/threads + no header at all", ["-X", "POST", "-H", "Content-Type: application/json", "-d", "{}", "http://127.0.0.1:8001/api/threads"])

print("\n### env check: is ALPHA_AUTH_DISABLED set in the running shell?")
# Ask the child process env (the probe subprocess shares our env, not the server's)
env_probe = subprocess.run([sys.executable, "-c", "import os; print('AUTH_DISABLED=' + str(os.environ.get('ALPHA_AUTH_DISABLED','<unset>')) )"], capture_output=True, text=True, timeout=10)
print(env_probe.stdout.strip())
