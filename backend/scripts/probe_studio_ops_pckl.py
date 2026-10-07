# When does the langgraph dev server actually write .langgraph_ops.pckl?
#
# test_langgraph_studio_routes.py::test_persisted_legacy_assistants_survive_cross_version_restart
# asserts the file exists after the first (legacy-auth) server has stopped, then
# starts a second server on the same runtime dir to prove the repair survives a
# restart. On this host the file is absent at that point.
#
# Two candidate causes, and this measures which:
#   (a) the flush is periodic and the server was terminated before its first
#       tick, so waiting longer while the server is UP would produce the file;
#   (b) Windows `Popen.terminate()` is TerminateProcess -- a hard kill with no
#       shutdown path -- so a file that only a graceful stop writes can never
#       appear, and no amount of waiting changes that.
#
# Method: poll for the file while the server is up (records first appearance),
# then terminate and poll again, so (a) and (b) are distinguishable.
from __future__ import annotations

import importlib.util
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

_spec = importlib.util.spec_from_file_location(
    "_studio_routes_for_probe", BACKEND_DIR / "tests" / "test_langgraph_studio_routes.py"
)
assert _spec and _spec.loader
_studio = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_studio)


def main() -> int:
    runtime_dir = Path(tempfile.mkdtemp(prefix="studio-pckl-"))
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
    (runtime_dir / "graph.py").write_text(_studio._GRAPH_SOURCE, encoding="utf-8")
    (runtime_dir / "auth_shim.py").write_text(_studio._LEGACY_AUTH_SHIM, encoding="utf-8")

    executable = shutil.which(
        "langgraph", path=os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    )
    assert executable, "langgraph executable unavailable"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), env.get("PYTHONPATH")]))
    env["LANGSMITH_LANGGRAPH_API_VARIANT"] = "local_dev"
    env["PYTHONIOENCODING"] = "utf-8"

    port = 8124
    log_path = runtime_dir / f"server-{uuid4()}.log"
    pckl = runtime_dir / ".langgraph_api" / ".langgraph_ops.pckl"

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
            pckl_while_up = None
            while time.monotonic() - started < 240:
                if process.poll() is not None:
                    print(f"server exited after {time.monotonic() - started:.1f}s (code {process.returncode})")
                    break
                if ready_at is None:
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ok", timeout=2) as resp:
                            if resp.status == 200:
                                ready_at = time.monotonic() - started
                    except (urllib.error.URLError, OSError):
                        pass
                if pckl_while_up is None and pckl.is_file():
                    pckl_while_up = time.monotonic() - started
                time.sleep(0.25)

            print(f"/ok answered at        : {ready_at if ready_at is None else f'{ready_at:.1f}s'}")
            print(f"pckl appeared while up : {pckl_while_up if pckl_while_up is None else f'{pckl_while_up:.1f}s'}")
            print(f"pckl exists now (up)   : {pckl.is_file()}")

            # Now stop it the way the fixture does and see whether the file lands.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            for _ in range(80):  # up to 20s after the stop
                if pckl.is_file():
                    break
                time.sleep(0.25)
            after = pckl.is_file()
            print(f"pckl exists after stop : {after}")
            log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-600:]
            if not after:
                print("\n  => (b) VERIFIED: the file is never written, even though the server ran")
                print("     and was stopped. It is not a timing race; on Windows")
                print("     Popen.terminate() is a hard kill, so no flush-on-stop can run.")
                print("\nlast log lines:\n" + log_tail)
                return 1
            print("\n  => (a) was right: the file lands if you wait; the fixture stops too early.")
            return 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())