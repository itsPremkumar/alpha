# Measure how long the locked `langgraph dev` server needs before /ok answers.
#
# Why: test_langgraph_studio_routes.py waits only 45s and then declares the
# server dead. On this host the server logs "Using custom authentication" and
# keeps going, so the reader needs to know the real number before anyone widens
# the bound -- a bound is only honest with a measurement behind it.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Reuse the test module's own constants so this measures the real thing. The
# tests directory is not a package (no __init__.py), so load it by path.
import importlib.util  # noqa: E402

_SOURCE_PATH = BACKEND_DIR / "tests" / "test_langgraph_studio_routes.py"
_spec = importlib.util.spec_from_file_location("_studio_routes_for_timing", _SOURCE_PATH)
assert _spec and _spec.loader
_studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_studio)
_CURRENT_AUTH_SHIM = _studio._CURRENT_AUTH_SHIM
_GRAPH_SOURCE = _studio._GRAPH_SOURCE


def main() -> int:
    runtime_dir = Path(tempfile.mkdtemp(prefix="studio-timing-"))
    # The config must match the fixture's own shape exactly. A config that lost
    # `http.app` still serves /ok, so a wrong one here would measure a lighter
    # server than the tests start and under-report the boot time.
    (runtime_dir / "langgraph.json").write_text(
        json.dumps(
            {
                "python_version": "3.12",
                "dependencies": [str(BACKEND_DIR)],
                "graphs": {"test_graph": "./graph.py:graph"},
                "auth": {"path": "./auth_shim.py:auth"},
                "http": {"app": "./auth_shim.py:langgraph_app"},
                "env": {
                    "AUTH_JWT_SECRET": "test-secret-key-for-langgraph-route-tests-min-32",
                    "ALPHA_AUTH_DISABLED": "1",
                    "LANGSMITH_TRACING": "false",
                },
            }
        ),
        encoding="utf-8",
    )
    (runtime_dir / "graph.py").write_text(_GRAPH_SOURCE, encoding="utf-8")
    (runtime_dir / "auth_shim.py").write_text(_CURRENT_AUTH_SHIM, encoding="utf-8")

    executable = shutil.which(
        "langgraph",
        path=os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
    )
    assert executable, "langgraph executable unavailable"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), env.get("PYTHONPATH")]))
    env["LANGSMITH_LANGGRAPH_API_VARIANT"] = "local_dev"
    env["PYTHONIOENCODING"] = "utf-8"

    port = 8123
    log_path = runtime_dir / f"server-{uuid4()}.log"
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [executable, "dev", "--config", str(runtime_dir / "langgraph.json"), "--host", "127.0.0.1", "--port", str(port), "--no-browser", "--no-reload"],
            cwd=runtime_dir,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            ready_at = None
            while time.monotonic() - started < 300:
                if process.poll() is not None:
                    print(f"server EXITED after {time.monotonic() - started:.1f}s with code {process.returncode}")
                    break
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ok", timeout=2) as resp:
                        if resp.status == 200:
                            ready_at = time.monotonic() - started
                            break
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.25)

            if ready_at is None:
                print("NOT READY within 300s")
                return 1
            print(f"VERIFIED  /ok answered 200 after {ready_at:.1f}s")
            print(f"          (the test's current deadline is 45s -> {'SUFFICIENT' if ready_at < 45 else 'TOO TIGHT'})")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())