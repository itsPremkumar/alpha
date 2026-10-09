"""Managed browser-use runtime: install-on-demand, subprocess-isolated control.

`browser-use <https://github.com/browser-use/browser-use>`_ is the leading
open-source browser automation library. Alpha deliberately does **not** nest
browser-use's autonomous ``Agent(task, llm).run()`` planner inside its own
LangGraph planner — that is two planners, two context windows, two sets of
stopping conditions, and a completion signal Alpha cannot verify (the analysis
behind ``AGENT_TOOLING_IMPLEMENTATION_PLAN.md`` §1.4, which is also why
Alpha's own browser tools are a host-driven ``[ref]``-indexed loop rather than
a delegated agent).

What this module does instead keeps browser-use usable without adopting its
loop shape:

1. **Install** the *latest* browser-use from PyPI into a dedicated virtualenv
   under the Alpha runtime home — on first use, or explicitly through
   ``browser_use_setup``. The venv is isolated from the Gateway environment
   because browser-use pins its own ``playwright``/``langchain`` versions and
   installing it in place would fight ``uv sync``'s lockfile.
2. **Drive** it as a **subprocess** of that venv: one bounded task per call,
   with a hard step budget and wall-clock timeout, so the inner loop's
   context, token spend and crashes stay in the child process. Only a small
   JSON envelope crosses back into the conversation.

The cost is a cold first run (pip resolution plus a Chromium download, minutes
on a fresh host); the marker file makes every later call skip the install by
probing the installed version instead.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config import get_app_config
from alpha.config.runtime_paths import project_root, runtime_home

logger = logging.getLogger(__name__)

#: Directory name of the managed virtualenv, relative to ``runtime_home()``.
DEFAULT_VENV_DIRNAME = "browser-use"
#: Install-state marker written after a successful install/upgrade.
MARKER_FILENAME = "alpha_browser_use_state.json"
#: Per-install subprocess ceiling. Installing browser-use resolves its own
#: dependency tree and then downloads a Chromium build, so this is generous.
DEFAULT_INSTALL_TIMEOUT_SECONDS = 900
#: Per-run step budget handed to browser-use's own loop.
DEFAULT_MAX_STEPS = 10
#: Hard ceiling for the step budget — refused above, never silently clamped,
#: because a caller asking for 500 steps is describing a task this surface is
#: not shaped for.
MAX_MAX_STEPS = 50
DEFAULT_RUN_TIMEOUT_SECONDS = 300
MAX_RUN_TIMEOUT_SECONDS = 900
#: Bounded inputs/outputs. The task is the only model-authored text that
#: crosses into the child process, and the result is the only text that crosses
#: back, so both are capped rather than trusted.
MAX_TASK_CHARS = 4000
MAX_RESULT_CHARS = 8000
MAX_HISTORY_ROWS = 20
#: Tail of the child's stderr kept for a failure report.
_STDERR_TAIL_CHARS = 2000
#: browser-use launches its browser through an event handler with a hard 30s
#: timeout. On a loaded host — or with leftover Chrome state — that handler trips
#: before the browser is up, and the whole run dies with a `TimeoutError` naming
#: `BrowserStartEvent` even though nothing about the task was wrong. Vision adds
#: startup work, so this bites more with `use_vision: true`. These markers are the
#: signature of *that* specific transient, which is worth one retry, as opposed to a
#: genuine task failure, which must never be retried behind the caller's back.
_TRANSIENT_BROWSER_START_MARKERS: tuple[str, ...] = ("BrowserStartEvent", "on_BrowserStartEvent")
#: How many times a run is re-attempted after that signature. One: a second
#: identical failure is a real condition, and repeating it would only burn time.
_BROWSER_START_RETRIES = 1
_BROWSER_START_RETRY_DELAY_SECONDS = 5.0
#: Packages installed alongside browser-use itself, beyond the operator's
#: ``extra_packages``. ``litellm`` is here for a reason found by running the real
#: thing: browser-use ships no provider adapter for OpenAI-compatible endpoints
#: except ``ChatLiteLLM``, which imports ``litellm`` **lazily at call time**. So
#: without it every model call raises ``No module named 'litellm'`` from inside
#: the agent loop — after the browser has already navigated and burned the whole
#: step budget, surfacing only as "Result failed 1/6 times". It is a runtime
#: requirement of the supported path, not an optional extra.
DEFAULT_EXTRA_PACKAGES: tuple[str, ...] = ("litellm",)

#: Keys that belong to Alpha's config schema rather than to a LangChain model
#: constructor. Forwarding them would raise a confusing ``TypeError`` inside the
#: child process instead of a clear one here.
_ALPHA_ONLY_MODEL_KEYS = frozenset(
    {
        "name",
        "display_name",
        "description",
        "provider",
        "use",
        "fallbacks",
        "supports_thinking",
        "supports_reasoning_effort",
        "reasoning_efforts",
        "default_reasoning_effort",
        "reasoning_effort_style",
        "when_thinking_enabled",
        "when_thinking_disabled",
        "supports_vision",
        "capabilities",
        "pricing",
        "use_responses_api",
        "output_version",
    }
)

#: Probes the installed version by **importing** browser-use in the managed venv
#: and reading its distribution metadata. Deliberately not
#: ``browser_use.__version__``: that attribute does not exist in current
#: releases, and a ``getattr(..., "unknown")`` fallback would report a successful
#: install as an unknown version — a status line asserting something nobody
#: checked. ``importlib.metadata`` reads the *installed distribution*, so it also
#: proves the package is importable, which is the property we actually need.
_VERSION_PROBE = "import importlib.metadata as m, json, sys; import browser_use; sys.stdout.write(json.dumps({'version': m.version('browser-use')}))"


class BrowserUseError(RuntimeError):
    """An operational browser-use failure the operator or model must see.

    Raised for every refusal (unknown model, missing install, a failed pip
    step) so a caller reports the reason instead of an empty result. It is
    never raised to signal "the task failed" — that is data inside a returned
    envelope.
    """


@dataclass(frozen=True)
class BrowserUseStatus:
    """What the managed runtime can prove about itself right now.

    ``installed`` is answered by *importing* browser-use in the venv, not by
    reading the marker file: a marker can survive a deleted package directory,
    and reporting "installed" from a file is how a status line starts asserting
    something nobody checked.

    ``missing_requirements`` exists because ``installed`` alone was not enough to
    answer "can this venv run a task". A venv holding browser-use but not litellm
    imports perfectly and then fails every single model call from inside the
    child — and because ``installed`` was True, auto-install never fired, so the
    run died at the first step. The two facts are kept separate rather than
    folded into one boolean: browser-use may be present while the runtime is not
    yet usable.
    """

    installed: bool
    version: str | None
    venv_path: str
    python_path: str
    installed_at: str | None = None
    log: tuple[str, ...] = field(default=())
    missing_requirements: tuple[str, ...] = field(default=())

    @property
    def ready_for_run(self) -> bool:
        """True when a task can actually be driven, not merely when a package imports."""
        return self.installed and not self.missing_requirements

    def summary(self) -> str:
        if not self.installed:
            return f"browser-use is NOT installed. Managed venv: {self.venv_path}. Run browser_use_setup to install the latest version."
        if self.missing_requirements:
            return f"browser-use {self.version} is installed in {self.venv_path}, but it cannot run a task yet: missing {', '.join(self.missing_requirements)}. Run browser_use_setup to complete the installation."
        stamp = f" (installed {self.installed_at})" if self.installed_at else ""
        return f"browser-use {self.version} is installed{stamp}. Managed venv: {self.venv_path}."


def resolve_llm_spec(model_name: str | None = None) -> dict[str, Any]:
    """Resolve a ``models[]`` entry into a LangChain constructor spec.

    browser-use takes a LangChain chat model, and the venv cannot read
    ``config.yaml``. So the Gateway resolves the model here — honouring the
    same ``provider`` profile inheritance the model factory applies — and hands
    the child only connection fields: the ``module:Class`` path, the model id,
    and any provider kwargs.

    The API key travels to the child through the request payload and is never
    echoed back into a result, a log line, or an error message.
    """
    config = get_app_config()
    chosen = model_name or config.default_model_name
    if not chosen:
        raise BrowserUseError("No model to drive browser-use: config.yaml declares no models[] and no default_model.")
    entry = next((m for m in config.models if m.name == chosen), None)
    if entry is None:
        known = ", ".join(sorted(m.name for m in config.models)) or "none"
        raise BrowserUseError(f"Model {chosen!r} is not declared in config.yaml models[] (declared: {known}).")

    merged: dict[str, Any] = {}
    if entry.provider:
        profile = config.providers.get(entry.provider)
        if profile is None:
            raise BrowserUseError(f"Model {chosen!r} references provider {entry.provider!r}, which config.yaml does not declare.")
        merged.update(profile.model_dump(exclude={"name"}))
    merged.update(entry.model_dump(exclude={"name", "provider", "fallbacks"}))

    use = merged.get("use")
    if not isinstance(use, str) or ":" not in use:
        raise BrowserUseError(f"Model {chosen!r} has no `use:` class path, so browser-use has nothing to construct (expected e.g. langchain_openai:ChatOpenAI).")

    spec: dict[str, Any] = {
        "use": use,
        "model_name": merged.get("model"),
        "api_key": merged.get("api_key"),
        "base_url": merged.get("base_url"),
        "extra": {key: value for key, value in merged.items() if key not in _ALPHA_ONLY_MODEL_KEYS},
    }
    return spec


def redact_secrets(text: str, *secrets: str | None) -> str:
    """Blank out secret values that a child process echoed back.

    Cheap because the failure mode is real: a provider SDK error can quote the
    request kwargs it was handed, and that request came from us.
    """
    cleaned = text
    for secret in secrets:
        if secret and isinstance(secret, str) and len(secret) >= 8:
            cleaned = cleaned.replace(secret, "***redacted***")
    return cleaned


class BrowserUseManager:
    """Owns one managed browser-use virtualenv and every subprocess against it.

    Single instance per process: the install is a filesystem mutation, so two
    concurrent pip runs against one venv is exactly the race this lock exists
    to prevent. It is an in-process lock — like the rest of Alpha's local state,
    it is not a cross-process coordinator, which is why browser control keeps
    the documented ``GATEWAY_WORKERS=1`` constraint.
    """

    def __init__(self, venv_path: str | Path | None = None) -> None:
        self._venv_dir = self._resolve_venv_dir(venv_path)
        self._lock = threading.Lock()

    @staticmethod
    def _resolve_venv_dir(venv_path: str | Path | None) -> Path:
        if venv_path:
            candidate = Path(str(venv_path)).expanduser()
            if not candidate.is_absolute():
                candidate = project_root() / candidate
            return candidate
        return runtime_home() / DEFAULT_VENV_DIRNAME

    @property
    def venv_dir(self) -> Path:
        return self._venv_dir

    @property
    def python_path(self) -> Path:
        """The venv interpreter — platform-correct for POSIX and Windows."""
        return self._venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    @property
    def marker_path(self) -> Path:
        return self._venv_dir / MARKER_FILENAME

    @property
    def runner_path(self) -> Path:
        """The child-side driver script shipped next to this module."""
        return Path(__file__).resolve().with_name("runner.py")

    # ------------------------------------------------------------------ status

    def probe_version(self) -> str | None:
        """Import browser-use in the venv and return its version, or ``None``.

        ``None`` means "cannot prove it is installed" — which covers both
        "never installed" and "installed but broken". Both answer the same way
        to the caller, so neither can be read as a working runtime.
        """
        if not self.python_path.exists():
            return None
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(self.python_path), "-c", _VERSION_PROBE],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("browser-use version probe failed: %s", exc)
            return None
        if completed.returncode != 0:
            logger.warning("browser-use version probe exited %s: %s", completed.returncode, redact_secrets(completed.stderr or "")[-500:])
            return None
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return None
        version = payload.get("version")
        return str(version) if isinstance(version, str) and version else None

    def status(self) -> BrowserUseStatus:
        """Report the runtime's real, probed state (never the marker's claim).

        Probes the *required runtime packages*, not just browser-use: a venv that
        imports browser-use but lacks litellm is installed and unusable, and
        reporting it as ready is what let a task die on its first model call with
        ``No module named 'litellm'`` while ``auto_install`` sat there unused.
        """
        version = self.probe_version()
        marker = self._read_marker()
        missing = tuple(pkg for pkg in DEFAULT_EXTRA_PACKAGES if version is not None and not self.probe_distribution(pkg))
        return BrowserUseStatus(
            installed=version is not None,
            version=version,
            venv_path=str(self.venv_dir),
            python_path=str(self.python_path),
            installed_at=(marker or {}).get("installed_at"),
            missing_requirements=missing,
        )

    # ----------------------------------------------------------------- install

    def ensure_installed(
        self,
        *,
        upgrade: bool = False,
        timeout_seconds: int = DEFAULT_INSTALL_TIMEOUT_SECONDS,
        extra_packages: Sequence[str] = (),
    ) -> BrowserUseStatus:
        """Make the managed venv hold the latest browser-use; return its status.

        Idempotent by default: a venv that already imports browser-use is
        left alone, so ``auto_install`` on every run costs one cheap probe
        rather than a reinstall. ``upgrade=True`` forces ``pip install -U``,
        which is what "install its latest version" means after a new upstream
        release.
        """
        # The supported provider path needs litellm (see DEFAULT_EXTRA_PACKAGES),
        # so it is part of "installed" rather than an operator's afterthought.
        wanted = list(DEFAULT_EXTRA_PACKAGES) + [str(pkg) for pkg in extra_packages if str(pkg).strip()]
        with self._lock:
            self.venv_dir.mkdir(parents=True, exist_ok=True)
            if not self.python_path.exists():
                self._create_venv(timeout_seconds=timeout_seconds)

            version = self.probe_version()
            if version is not None and not upgrade and self.probe_distribution("litellm"):
                status = self.status()
                return status

            log: list[str] = []
            self._pip_install(extra_packages=wanted, timeout_seconds=timeout_seconds, log=log)
            self._install_browser(timeout_seconds=timeout_seconds, log=log)

            version = self.probe_version()
            if version is None:
                raise BrowserUseError(f"browser-use was installed into {self.venv_dir} but still cannot be imported from that venv. Last install step output: {log[-1] if log else 'no output captured'}")
            self._write_marker(version)
            marker = self._read_marker() or {}
            return BrowserUseStatus(
                installed=True,
                version=version,
                venv_path=str(self.venv_dir),
                python_path=str(self.python_path),
                installed_at=marker.get("installed_at"),
                log=tuple(log),
            )

    def _create_venv(self, *, timeout_seconds: int) -> None:
        argv = [sys.executable, "-m", "venv", str(self.venv_dir)]
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_seconds, check=False)  # noqa: S603 - fixed argv, no shell
        except (OSError, subprocess.SubprocessError) as exc:
            raise BrowserUseError(f"Could not create the managed browser-use virtualenv at {self.venv_dir}: {exc}") from exc
        if completed.returncode != 0:
            raise BrowserUseError(f"`python -m venv` failed for {self.venv_dir} (exit {completed.returncode}): {redact_secrets(completed.stderr or completed.stdout or '')[-_STDERR_TAIL_CHARS:]}")
        if not self.python_path.exists():
            raise BrowserUseError(f"`python -m venv` reported success but {self.python_path} does not exist.")

    def _pip_install(self, *, extra_packages: Sequence[str], timeout_seconds: int, log: list[str]) -> None:
        """Install browser-use first, then any extra client packages.

        The order is load-bearing and was found by installing for real:
        browser-use 0.13.x **pins its OpenAI SDK exactly** (``openai==2.26.0``),
        while the current ``langchain-openai`` asks for a newer one. Installing
        both in a single ``pip install`` command resolved to the *newer* SDK and
        left the venv in a broken state, which pip only reports as a warning
        ("dependency conflicts") rather than an error.

        So browser-use goes in alone, and the extras are then installed against a
        constraints file written from the versions that install actually resolved.
        That keeps browser-use's pins authoritative, and the final ``pip check``
        turns "pip warned about a conflict nobody reads" into an install that
        fails loudly instead.
        """
        argv = [str(self.python_path), "-m", "pip", "install", "--upgrade", "browser-use"]
        completed = self._run_install_step(argv, timeout_seconds=timeout_seconds, label="pip install browser-use", log=log)
        if completed.returncode != 0:
            raise BrowserUseError(f"Installing browser-use failed (exit {completed.returncode}): {redact_secrets(completed.stderr or completed.stdout or '')[-_STDERR_TAIL_CHARS:]}")

        wanted = [str(pkg) for pkg in extra_packages if str(pkg).strip()]
        if not wanted:
            return

        constraints = self._write_constraints()
        extra_argv = [str(self.python_path), "-m", "pip", "install", *wanted]
        if constraints is not None:
            extra_argv += ["--constraint", str(constraints)]
        extra_completed = self._run_install_step(extra_argv, timeout_seconds=timeout_seconds, label=f"pip install {' '.join(wanted)}", log=log)
        if extra_completed.returncode != 0:
            raise BrowserUseError(f"Installing {', '.join(wanted)} into the browser-use venv failed (exit {extra_completed.returncode}): {redact_secrets(extra_completed.stderr or extra_completed.stdout or '')[-_STDERR_TAIL_CHARS:]}")

        check = self._run_install_step([str(self.python_path), "-m", "pip", "check"], timeout_seconds=timeout_seconds, label="pip check", log=log)
        if check.returncode != 0:
            raise BrowserUseError(
                f"The browser-use venv has an unsatisfiable dependency set after adding {', '.join(wanted)}, so browser-use would fail at run time rather than at install time: "
                f"{redact_secrets(check.stdout or check.stderr or '')[-_STDERR_TAIL_CHARS:]}"
            )

    def _write_constraints(self) -> Path | None:
        """Freeze the resolved venv into a pip constraints file for the extras.

        Returns ``None`` when the freeze cannot be produced, so a missing
        constraints file degrades to an unconstrained install (which pip warns
        about) rather than aborting an install that would otherwise work.
        """
        try:
            frozen = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(self.python_path), "-m", "pip", "freeze", "--exclude-editable"],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if frozen.returncode != 0 or not frozen.stdout.strip():
            return None
        target = self.venv_dir / "constraints.txt"
        try:
            target.write_text(frozen.stdout, encoding="utf-8")
        except OSError:
            return None
        return target

    def _install_browser(self, *, timeout_seconds: int, log: list[str]) -> None:
        """Make sure a browser is actually available to browser-use.

        The right command depends on the installed release and is **detected,
        not assumed**: browser-use 0.13.x drives Chrome over CDP through
        ``browser-harness``/``cdp-use`` and uses whatever Chrome the host already
        has, while earlier releases drove Playwright and needed
        ``playwright install chromium`` to download a browser binary of their
        own. Hardcoding the Playwright step (as a first draft here did) fails on
        current releases with ``No module named playwright`` and reports an
        install problem that does not exist.

        So: install the browser binary only when the venv actually has Playwright,
        and let the run itself be the real proof. Anything else is recorded in the
        install log rather than raised — a host with a working Chrome is the
        normal case here, and failing an otherwise-good install because no
        Playwright binary was downloaded would be its own false negative.
        """
        has_playwright = self.probe_distribution("playwright")
        if has_playwright:
            argv = [str(self.python_path), "-m", "playwright", "install", "chromium"]
            completed = self._run_install_step(argv, timeout_seconds=timeout_seconds, label="playwright install chromium", log=log)
            if completed.returncode != 0:
                raise BrowserUseError(f"Downloading the Chromium browser for browser-use failed (exit {completed.returncode}): {redact_secrets(completed.stderr or completed.stdout or '')[-_STDERR_TAIL_CHARS:]}")
            return
        log.append("browser: no playwright in this venv; browser-use will drive the host's Chrome over CDP")

    def probe_distribution(self, distribution: str, *, timeout_seconds: int = 60) -> bool:
        """True when a distribution is installed in the venv, via its metadata.

        Deliberately *not* an import probe. ``import litellm`` was measured at
        **127 seconds** on a loaded Windows host, so an import-based readiness
        check with a shorter budget reported a perfectly good install as missing —
        and because "missing" drives a reinstall, every single call then paid for
        a full ``pip install`` (and eventually timed out at 900s).

        Distribution metadata is an instant, authoritative statement that the
        package is installed *in that venv*. It does not prove the package loads;
        that is deliberately left to the run itself, which reports a real
        traceback if the install is broken, rather than being pre-empted here.
        """
        if not self.python_path.exists():
            return False
        code = f"import importlib.metadata as m; m.version({distribution!r})"
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(self.python_path), "-c", code],
                capture_output=True,
                encoding="utf-8",
                errors="backslashreplace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("distribution probe for %s failed: %s", distribution, exc)
            return False
        return completed.returncode == 0

    def _probe_import(self, module: str, *, timeout_seconds: int = 120) -> bool:
        """True when ``module`` imports in the managed venv.

        Kept for diagnostics, not for the readiness decision, because a heavy
        import can exceed any sane budget (see :meth:`probe_distribution`). A
        timeout is reported as a warning rather than silently meaning "missing".
        """
        if not self.python_path.exists():
            return False
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [str(self.python_path), "-c", f"import {module}"],
                capture_output=True,
                encoding="utf-8",
                errors="backslashreplace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("import probe for %s exceeded %ss; treating as unknown, not missing", module, timeout_seconds)
            return False
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("import probe for %s failed: %s", module, exc)
            return False
        return completed.returncode == 0

    def _run_install_step(self, argv: list[str], *, timeout_seconds: int, label: str, log: list[str]) -> subprocess.CompletedProcess[str]:
        logger.info("browser-use install step: %s", label)
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_seconds, check=False)  # noqa: S603 - fixed argv, no shell
        except subprocess.TimeoutExpired as exc:
            raise BrowserUseError(f"browser-use install step '{label}' exceeded {timeout_seconds}s and was terminated.") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise BrowserUseError(f"browser-use install step '{label}' could not start: {exc}") from exc
        tail = (completed.stdout or completed.stderr or "").strip().splitlines()
        if tail:
            log.append(f"{label}: {tail[-1]}")
        return completed

    # --------------------------------------------------------------------- run

    def run(
        self,
        *,
        task: str,
        llm_spec: dict[str, Any],
        start_url: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
        headless: bool = True,
        use_vision: bool = False,
        verify: bool = False,
        ground_truth: str | None = None,
    ) -> dict[str, Any]:
        """Run one bounded browser-use task and return its JSON envelope.

        Never raises for a task failure — that is data the model must read. It
        raises only when the runtime itself cannot run (no venv, missing runner
        script), because those are operator-facing faults, not task outcomes.

        A browser-launch timeout is re-attempted once (see
        ``_TRANSIENT_BROWSER_START_MARKERS``); every other outcome is returned as
        it is, so a failed task is never silently retried.
        """
        last: dict[str, Any] = {}
        for attempt in range(_BROWSER_START_RETRIES + 1):
            last = self._run_once(
                task=task,
                llm_spec=llm_spec,
                start_url=start_url,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
                headless=headless,
                use_vision=use_vision,
                verify=verify,
                ground_truth=ground_truth,
            )
            if not _is_browser_start_timeout(last):
                return last
            if attempt < _BROWSER_START_RETRIES:
                logger.warning("browser-use browser launch timed out; re-attempting once before giving up")
                time.sleep(_BROWSER_START_RETRY_DELAY_SECONDS)
        last["retried_after_browser_start_timeout"] = _BROWSER_START_RETRIES
        return last

    def _run_once(
        self,
        *,
        task: str,
        llm_spec: dict[str, Any],
        start_url: str | None,
        max_steps: int,
        timeout_seconds: int,
        headless: bool,
        use_vision: bool,
        verify: bool,
        ground_truth: str | None,
    ) -> dict[str, Any]:
        if self.probe_version() is None:
            raise BrowserUseError(f"browser-use is not installed in {self.venv_dir}. Call browser_use_setup first (or enable auto_install on browser_use_run).")
        if not self.runner_path.is_file():
            raise BrowserUseError(f"browser-use runner script is missing at {self.runner_path}.")

        payload: dict[str, Any] = {
            "task": task,
            "llm": llm_spec,
            "max_steps": max_steps,
            "use_vision": use_vision,
            "headless": headless,
            "verify": bool(verify),
        }
        if ground_truth:
            payload["ground_truth"] = ground_truth
        if start_url:
            payload["start_url"] = start_url

        scratch = self._scratch_dir()
        env = os.environ.copy()
        # Keep the child's Playwright/temp output inside the managed venv instead
        # of the Gateway's working directory.
        env["TMPDIR"] = str(scratch)
        env["TEMP"] = str(scratch)
        env["TMP"] = str(scratch)
        # browser-use prints progress glyphs that a non-UTF-8 console codec
        # cannot encode, and the resulting UnicodeEncodeError kills an otherwise
        # healthy run. Set here as well as in the runner itself: anything the
        # child launches inherits it.
        env["PYTHONIOENCODING"] = "utf-8:backslashreplace"
        env["PYTHONUTF8"] = "1"

        argv = [str(self.python_path), str(self.runner_path)]
        try:
            process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                # The child writes UTF-8 (see the runner's stream reconfiguration),
                # so decoding must not fall back to this process's console codec.
                # With text=True and no explicit encoding, a U+25B6 in the child's
                # output raised UnicodeDecodeError *in the Gateway* — the run had
                # actually completed, and the failure surfaced in the wrong
                # process entirely. backslashreplace keeps the tail readable
                # rather than losing the whole output to one bad byte.
                encoding="utf-8",
                errors="backslashreplace",
                cwd=str(scratch),
                env=env,
                **self._process_group_kwargs(),
            )
        except OSError as exc:
            raise BrowserUseError(f"Could not start the browser-use subprocess ({self.python_path}): {exc}") from exc

        try:
            stdout, stderr = process.communicate(input=json.dumps(payload), timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_tree(process)
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.communicate(timeout=30)
            logger.warning("browser-use run exceeded its %ss budget and was terminated", timeout_seconds)
            return {
                "ok": False,
                "error": f"browser-use ran past its {timeout_seconds}s budget and was terminated. It may have been mid-action; re-run with a smaller task or a higher timeout_seconds.",
                "timed_out": True,
                "steps": None,
                "result": None,
                "history": [],
            }

        return self._envelope_from(stdout, stderr, api_key=llm_spec.get("api_key"))

    def _envelope_from(self, stdout: str, stderr: str, *, api_key: str | None) -> dict[str, Any]:
        """Parse the child's last JSON line, or report an honest crash.

        browser-use, Playwright and the provider SDKs all write progress to
        stdout/stderr, so the envelope is the *last* non-empty stdout line and
        anything else is treated as log noise.
        """
        envelope: dict[str, Any] | None = None
        for line in reversed((stdout or "").splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "ok" in candidate:
                envelope = candidate
                break

        if envelope is None:
            tail = redact_secrets((stderr or stdout or "").strip(), api_key)[-_STDERR_TAIL_CHARS:]
            return {
                "ok": False,
                "error": f"browser-use produced no result envelope (the subprocess exited without answering). Output tail: {tail or '<empty>'}",
                "timed_out": False,
                "steps": None,
                "result": None,
                "history": [],
            }

        if envelope.get("error"):
            envelope["error"] = redact_secrets(str(envelope["error"]), api_key)
        return envelope

    # ---------------------------------------------------------------- internals

    @staticmethod
    def _process_group_kwargs() -> dict[str, Any]:
        """Put the child in its own process group so a timeout kills its tree.

        A run timeout must not leave an orphaned Chromium behind: on POSIX the
        group is signalled, on Windows ``taskkill /T`` walks the tree. Chromium
        outliving its deadline would keep a display, a port and a profile lock.
        """
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        return {"start_new_session": True}

    @staticmethod
    def _kill_tree(process: subprocess.Popen[str]) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if os.name == "nt":
                subprocess.run(  # noqa: S603 - fixed argv, no shell
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        with contextlib.suppress(OSError):
            process.kill()

    def _scratch_dir(self) -> Path:
        scratch = self.venv_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        return scratch

    def _read_marker(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _write_marker(self, version: str) -> None:
        payload = {
            "version": version,
            "installed_at": datetime.now(UTC).isoformat(),
            "python": str(self.python_path),
        }
        tmp = self.marker_path.with_name(f"{MARKER_FILENAME}.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.marker_path)


_MANAGER: BrowserUseManager | None = None
_MANAGER_LOCK = threading.Lock()


def _is_browser_start_timeout(envelope: dict[str, Any]) -> bool:
    """True for the one transient worth retrying: browser-use's own launch timeout.

    Matches on the event name rather than on "timed out", because our own run
    timeout produces a different, deliberately-not-retried outcome.
    """
    if envelope.get("timed_out"):
        return False
    error = str(envelope.get("error") or "")
    return any(marker in error for marker in _TRANSIENT_BROWSER_START_MARKERS)


def get_browser_use_manager(venv_path: str | Path | None = None) -> BrowserUseManager:
    """Return the process-wide manager, creating it on first use."""
    global _MANAGER
    if venv_path is not None:
        return BrowserUseManager(venv_path)
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = BrowserUseManager()
        return _MANAGER


def reset_browser_use_manager() -> None:
    """Drop the cached manager (tests, and operators switching venv paths)."""
    global _MANAGER
    with _MANAGER_LOCK:
        _MANAGER = None
