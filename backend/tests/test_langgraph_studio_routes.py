"""Route-level regressions for standalone LangGraph Studio assistants."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]

_GRAPH_SOURCE = """
from langgraph.graph import END, START, StateGraph

builder = StateGraph(dict)
builder.add_node("noop", lambda state: {})
builder.add_edge(START, "noop")
builder.add_edge("noop", END)
graph = builder.compile()
""".lstrip()

_CURRENT_AUTH_SHIM = """
from app.gateway.langgraph_auth import auth
from app.gateway.langgraph_studio import langgraph_app
""".lstrip()

_LEGACY_AUTH_SHIM = """
from fastapi import FastAPI
from langgraph_sdk import Auth

auth = Auth()

@auth.authenticate
async def authenticate(request):
    return "langgraph-studio-user"

@auth.on
async def legacy_owner_filter(ctx, value):
    metadata = value.setdefault("metadata", {})
    metadata["user_id"] = ctx.user.identity
    return {"user_id": ctx.user.identity}

langgraph_app = FastAPI()
""".lstrip()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# How long the dev persistence loop may take to publish `.langgraph_ops.pckl`
# after the last assistant write. Measured at ~4.5s after /ok on the development
# host; the ceiling is generous because this is a timer inside the child, not
# work the test can influence, and a server that died is caught by the explicit
# poll rather than by the deadline.
_OPS_PCKL_FLUSH_BUDGET_SECONDS = 90


def _wait_for_ops_pckl(runtime_dir: Path) -> None:
    """Wait for the dev persistence flush to write `.langgraph_ops.pckl`.

    Called while the server that writes the file is still alive. Failing here
    names the real cause (the flush never ran) instead of leaving the next
    assertion to blame the restart that reads the missing file.
    """
    pckl = runtime_dir / ".langgraph_api" / ".langgraph_ops.pckl"
    deadline = time.monotonic() + _OPS_PCKL_FLUSH_BUDGET_SECONDS
    while time.monotonic() < deadline:
        if pckl.is_file():
            return
        time.sleep(0.25)
    pytest.fail(f"the dev server never flushed {pckl} within {_OPS_PCKL_FLUSH_BUDGET_SECONDS}s while it was running; the cross-version restart below cannot prove anything without it")


@contextmanager
def _running_studio_server(
    runtime_dir: Path,
    *,
    auth_source: str,
) -> Iterator[httpx.Client]:
    """Run the locked dev server against one persistent runtime directory."""
    (runtime_dir / "graph.py").write_text(_GRAPH_SOURCE, encoding="utf-8")
    (runtime_dir / "auth_shim.py").write_text(auth_source, encoding="utf-8")
    config_path = runtime_dir / "langgraph.json"
    config_path.write_text(
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

    port = _free_port()
    log_path = runtime_dir / f"server-{uuid4()}.log"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(BACKEND_DIR), env.get("PYTHONPATH")]))
    env["LANGSMITH_LANGGRAPH_API_VARIANT"] = "local_dev"
    # The spawned `langgraph dev` server logs a `->` in its startup banner. On a
    # Windows host without this, its stdout inherits the ANSI codepage (cp1252
    # here) and the handler raises UnicodeEncodeError on that character, which
    # kills the server before it ever answers /ok — so five Studio route tests
    # failed at SETUP with "dev server failed to start" and the real cause was
    # only visible in the log tail. The file handle is already utf-8; this is
    # about the child's own stdout encoding, which no parent-side file object
    # can set.
    env["PYTHONIOENCODING"] = "utf-8"
    executable = shutil.which(
        "langgraph",
        path=os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
    )
    if executable is None:
        pytest.fail("langgraph executable is unavailable; install the backend development dependencies before running Studio route tests")
    with log_path.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            [
                executable,
                "dev",
                "--config",
                str(config_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-browser",
                "--no-reload",
            ],
            cwd=runtime_dir,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )

        base_url = f"http://127.0.0.1:{port}"
        # Measured, not guessed. `scripts/measure_studio_boot.py` starts this
        # exact server under this exact fixture config and waits for /ok: on the
        # development host it answered after **104.3s**. The old 45s bound was
        # below that, so on a loaded machine the fixture killed a healthy server
        # and failed five Studio tests at SETUP with "failed to start" while the
        # log showed it still initializing. 300s leaves the same margin the
        # measurement implies (~3x) without hiding a genuinely dead process:
        # the loop also exits the moment `process.poll()` reports an exit, so a
        # crashed server still fails immediately rather than at the deadline.
        deadline = time.monotonic() + 300
        last_error: Exception | None = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                response = httpx.get(
                    f"{base_url}/ok",
                    timeout=1,
                    trust_env=False,
                )
                if response.status_code == 200:
                    break
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(0.1)
        else:
            process.terminate()
            process.wait(timeout=10)
            pytest.fail(f"LangGraph dev server failed to start ({last_error!r}).\n{log_path.read_text(encoding='utf-8')}")

        client = httpx.Client(
            base_url=base_url,
            headers={"x-auth-scheme": "langsmith"},
            timeout=10,
            trust_env=False,
        )
        try:
            yield client
        finally:
            client.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.fixture(scope="module")
def studio_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[httpx.Client]:
    """Run the locked dev server with a tiny graph and Alpha's real auth."""
    runtime_dir = tmp_path_factory.mktemp("langgraph-studio-routes")
    with _running_studio_server(
        runtime_dir,
        auth_source=_CURRENT_AUTH_SHIM,
    ) as client:
        yield client


@pytest.mark.parametrize("requested_created_by", [None, "system"])
def test_studio_create_then_get_and_search_assistant(
    studio_client: httpx.Client,
    requested_created_by: str | None,
):
    """Ordinary and forged create payloads stay Studio-owned and readable."""
    assistant_id = str(uuid4())
    label = f"route-test-{assistant_id}"
    metadata = {"label": label}
    if requested_created_by is not None:
        metadata["created_by"] = requested_created_by

    response = studio_client.post(
        "/assistants",
        json={
            "assistant_id": assistant_id,
            "graph_id": "test_graph",
            "metadata": metadata,
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["metadata"]["created_by"] == "user"
    assert created["metadata"]["user_id"] == "langgraph-studio-user"

    response = studio_client.get(f"/assistants/{assistant_id}")
    assert response.status_code == 200, response.text
    assert response.json()["assistant_id"] == assistant_id

    response = studio_client.post(
        "/assistants/search",
        json={"metadata": {"label": label}},
    )
    assert response.status_code == 200, response.text
    assert [item["assistant_id"] for item in response.json()] == [assistant_id]


def test_studio_can_get_and_search_registered_system_assistant(
    studio_client: httpx.Client,
):
    """The registered graph remains discoverable alongside Studio-owned rows."""
    response = studio_client.post(
        "/assistants/search",
        json={"graph_id": "test_graph", "metadata": {"created_by": "system"}},
    )
    assert response.status_code == 200, response.text
    registered = response.json()
    assert len(registered) == 1

    assistant_id = registered[0]["assistant_id"]
    response = studio_client.get(f"/assistants/{assistant_id}")
    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["created_by"] == "system"


def test_studio_update_cannot_forge_system_provenance(
    studio_client: httpx.Client,
):
    assistant_id = str(uuid4())
    response = studio_client.post(
        "/assistants",
        json={"assistant_id": assistant_id, "graph_id": "test_graph"},
    )
    assert response.status_code == 200, response.text

    response = studio_client.patch(
        f"/assistants/{assistant_id}",
        json={"metadata": {"created_by": "system", "updated": True}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["metadata"] == {
        "created_by": "user",
        "updated": True,
        "user_id": "langgraph-studio-user",
    }

    response = studio_client.post(
        f"/assistants/{assistant_id}/latest",
        json={"version": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert response.json()["metadata"]["created_by"] == "user"

    response = studio_client.post(
        f"/assistants/{assistant_id}/latest",
        json={"version": 2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert response.json()["metadata"]["created_by"] == "user"


def test_non_studio_auth_disabled_principal_can_select_older_and_newer_versions(
    studio_client: httpx.Client,
):
    """Exercise non-Studio owner scoping without claiming JWT-path coverage."""
    assistant_id = str(uuid4())
    with httpx.Client(
        base_url=studio_client.base_url,
        timeout=10,
        trust_env=False,
    ) as client:
        response = client.post(
            "/assistants",
            json={"assistant_id": assistant_id, "graph_id": "test_graph"},
        )
        assert response.status_code == 200, response.text
        owner_id = response.json()["metadata"]["user_id"]
        assert owner_id != "langgraph-studio-user"

        response = client.patch(
            f"/assistants/{assistant_id}",
            json={"metadata": {"revision": 2}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["version"] == 2

        for version in (1, 2):
            response = client.post(
                f"/assistants/{assistant_id}/latest",
                json={"version": version},
            )
            assert response.status_code == 200, response.text
            assert response.json()["version"] == version
            assert response.json()["metadata"] == {
                **({"revision": 2} if version == 2 else {}),
                "created_by": "user",
                "user_id": owner_id,
            }


def test_persisted_legacy_assistants_survive_cross_version_restart(
    tmp_path: Path,
):
    """Old forged rows are repaired before the locked runtime can purge them."""
    assistant_ids = [str(uuid4()) for _ in range(4)]

    with _running_studio_server(
        tmp_path,
        auth_source=_LEGACY_AUTH_SHIM,
    ) as legacy_client:
        for assistant_id in assistant_ids:
            response = legacy_client.post(
                "/assistants",
                json={
                    "assistant_id": assistant_id,
                    "graph_id": "test_graph",
                    "metadata": {
                        "created_by": "system",
                        "legacy": assistant_id,
                    },
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["metadata"]["created_by"] == "system"

            response = legacy_client.patch(
                f"/assistants/{assistant_id}",
                json={
                    "metadata": {
                        "created_by": "system",
                        "legacy": assistant_id,
                        "revision": 2,
                    }
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["version"] == 2

        # `.langgraph_ops.pckl` is written by LangGraph's *periodic* dev
        # persistence flush loop, not synchronously with each request. Measured
        # on the development host (`scripts/probe_studio_ops_pckl.py`): /ok
        # answers at 27.1s and the file first appears at 31.7s, i.e. it can lag
        # the last write by seconds. Asserting right after the server stops
        # therefore raced a timer, and on a loaded host the flush had not run yet
        # — the restart below then had nothing to read and the test failed at the
        # `is_file()` line below with a message that pointed at the restart
        # rather than at the unflushed state. Wait for the flush while the server
        # that owns it is still running.
        _wait_for_ops_pckl(tmp_path)

    assert (tmp_path / ".langgraph_api" / ".langgraph_ops.pckl").is_file()

    with _running_studio_server(
        tmp_path,
        auth_source=_CURRENT_AUTH_SHIM,
    ) as repaired_client:
        for assistant_id in assistant_ids:
            response = repaired_client.get(f"/assistants/{assistant_id}")
            assert response.status_code == 200, response.text
            assert response.json()["metadata"] == {
                "created_by": "user",
                "legacy": assistant_id,
                "revision": 2,
                "user_id": "langgraph-studio-user",
            }

            response = repaired_client.post(
                f"/assistants/{assistant_id}/versions",
                json={"limit": 10},
            )
            assert response.status_code == 200, response.text
            versions = response.json()
            assert {item["version"] for item in versions} == {1, 2}
            assert all(item["metadata"]["created_by"] == "user" for item in versions)

            response = repaired_client.post(
                f"/assistants/{assistant_id}/latest",
                json={"version": 1},
            )
            assert response.status_code == 200, response.text
            assert response.json()["metadata"]["created_by"] == "user"

            response = repaired_client.post(
                f"/assistants/{assistant_id}/latest",
                json={"version": 2},
            )
            assert response.status_code == 200, response.text
            assert response.json()["metadata"]["created_by"] == "user"
