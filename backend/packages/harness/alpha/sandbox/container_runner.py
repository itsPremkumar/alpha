"""Containerized Sandbox Execution Runner.

Executes subagent and specialist bot terminal commands inside isolated
ephemeral Docker or Podman containers mounting only the designated worktree.
Falls back safely to host isolation if container runtimes are unconfigured.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ContainerExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    sandbox_type: str  # "docker", "podman", or "host_fallback"
    isolated: bool
    command: str
    worktree_path: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContainerSandboxRunner:
    """Executes arbitrary code and test commands within ephemeral container boundaries."""

    def __init__(self, default_image: str = "python:3.12-slim", timeout_seconds: int = 120):
        self.default_image = os.environ.get("AGENT_WORKSPACE_SANDBOX_IMAGE", default_image)
        self.timeout_seconds = timeout_seconds
        self._runtime_cache: str | None = None

    def detect_runtime(self) -> str | None:
        """Detect if docker or podman is available and its daemon is actively running."""
        if self._runtime_cache is not None:
            return self._runtime_cache if self._runtime_cache != "none" else None

        for cmd in ("docker", "podman"):
            if shutil.which(cmd):
                try:
                    # Check if daemon is responsive, not just CLI installed
                    proc = subprocess.run([cmd, "info"], capture_output=True, text=True, timeout=2)
                    if proc.returncode == 0:
                        self._runtime_cache = cmd
                        return cmd
                except Exception:
                    pass

        self._runtime_cache = "none"
        return None

    def execute(
        self,
        worktree_path: Path | str,
        command: str | list[str],
        image: str | None = None,
        env: dict[str, str] | None = None,
        memory_limit: str = "2g",
        cpu_limit: float = 2.0,
        network_disabled: bool = False,
    ) -> ContainerExecutionResult:
        """Run command in container or fallback to local host execution."""
        wt_path = Path(worktree_path).resolve()
        wt_path.mkdir(parents=True, exist_ok=True)

        cmd_str = command if isinstance(command, str) else " ".join(command)
        runtime = self.detect_runtime()
        start_time = time.perf_counter()

        # 1. Containerized Execution (Docker / Podman)
        if runtime:
            img = image or self.default_image
            docker_cmd = [
                runtime,
                "run",
                "--rm",
                "-v",
                f"{wt_path}:/workspace",
                "-w",
                "/workspace",
                f"--memory={memory_limit}",
                f"--cpus={cpu_limit}",
            ]

            if network_disabled:
                docker_cmd.append("--network=none")

            if env:
                for k, v in env.items():
                    docker_cmd.extend(["-e", f"{k}={v}"])

            docker_cmd.extend([img, "sh", "-c", cmd_str])

            try:
                proc = subprocess.run(
                    docker_cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
                if proc.returncode == 0 or (
                    "cannot find the file specified" not in proc.stderr
                    and "daemon is running" not in proc.stderr
                    and "failed to connect" not in proc.stderr.lower()
                ):
                    duration_ms = (time.perf_counter() - start_time) * 1000
                    return ContainerExecutionResult(
                        exit_code=proc.returncode,
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                        duration_ms=round(duration_ms, 2),
                        sandbox_type=runtime,
                        isolated=True,
                        command=cmd_str,
                        worktree_path=str(wt_path),
                        details={"image": img, "runtime": runtime},
                    )
                else:
                    logger.warning("Docker daemon unreachable during execution; falling back to host execution.")
            except Exception as e:
                logger.warning(f"Container run failed, falling back to host execution: {e}")

        # 2. Host Fallback Execution
        try:
            full_env = dict(os.environ)
            if env:
                full_env.update(env)

            # Root cause parity with the container branch: the container image
            # ships its own `python`, but the host shell resolves bare `python`
            # from PATH. On hosts where only a virtualenv interpreter exists
            # (no system Python on PATH) the command fails with
            # "'python' is not recognized as an internal or external command".
            # Prepend the directory of the interpreter running this process so
            # `python` in the command string resolves to sys.executable —
            # equivalent to activating the harness environment — without
            # rewriting the caller's command.
            interpreter_dir = str(Path(sys.executable).resolve().parent)
            host_path = full_env.get("PATH") or ""
            full_env["PATH"] = interpreter_dir + (os.pathsep + host_path if host_path else "")

            proc = subprocess.run(
                cmd_str,
                shell=True,
                cwd=str(wt_path),
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=full_env,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000
            return ContainerExecutionResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=round(duration_ms, 2),
                sandbox_type="host_fallback",
                isolated=False,
                command=cmd_str,
                worktree_path=str(wt_path),
                details={"host_cwd": str(wt_path)},
            )
        except subprocess.TimeoutExpired:
            return ContainerExecutionResult(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {self.timeout_seconds}s",
                duration_ms=(time.perf_counter() - start_time) * 1000,
                sandbox_type="host_fallback",
                isolated=False,
                command=cmd_str,
                worktree_path=str(wt_path),
                details={"timed_out": True},
            )
        except Exception as e:
            return ContainerExecutionResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000,
                sandbox_type="host_fallback",
                isolated=False,
                command=cmd_str,
                worktree_path=str(wt_path),
                details={"error": str(e)},
            )


_RUNNER_INSTANCE: ContainerSandboxRunner | None = None


def get_container_sandbox_runner() -> ContainerSandboxRunner:
    global _RUNNER_INSTANCE
    if _RUNNER_INSTANCE is None:
        _RUNNER_INSTANCE = ContainerSandboxRunner()
    return _RUNNER_INSTANCE
