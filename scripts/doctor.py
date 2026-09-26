#!/usr/bin/env python3
"""Alpha Health Check (make doctor).

Checks system requirements, configuration, LLM provider, and optional
components, then prints an actionable report.

Every check is a **tri-state** verdict and the summary keeps all three apart:

* ``ok``     — checked, fine.
* ``fail``   — checked, broken. Never rendered as healthy.
* ``warn``   — checked, works, with a caveat an operator should read.
* ``skip``   — **unknown**: the check could not be performed (absence of
  evidence). Unknown is not success and is never folded into "Ready".

A checkout that reports ``Status: Ready`` must therefore have had every check
actually performed and passed. When any check is unknown the summary says
``Status: Indeterminate`` and names the checks that were not determined,
because a stack that is not running cannot be proven to serve - and neither can
a checkout whose status file outlived the process that wrote it. See
:mod:`deploy_status` for the staleness rules behind the Runtime Readiness
section and the separate :mod:`test_deploy_status` suite.

Exit codes:
  0 — all required checks passed, none unknown (warnings allowed)
  1 — one or more required checks failed
  2 — nothing failed, but at least one check was unknown (indeterminate)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import Literal

import deploy_status

#: Exit codes. The third one is the point of the tri-state: a caller that only
#: tests ``== 0`` still treats "indeterminate" as success, so it is spelled out
#: and exercised by tests rather than left implicit.
EXIT_READY = 0
EXIT_NOT_READY = 1
EXIT_INDETERMINATE = 2

#: Loopback ports the launchers bind, overridable so a custom-port deployment
#: is actually probed instead of silently skipped.
GATEWAY_PORT = int(os.environ.get("ALPHA_DOCTOR_GATEWAY_PORT") or deploy_status.DEFAULT_GATEWAY_PORT)
FRONTEND_PORT = int(os.environ.get("ALPHA_DOCTOR_FRONTEND_PORT") or deploy_status.DEFAULT_FRONTEND_PORT)
PROBE_TIMEOUT_SECONDS = float(os.environ.get("ALPHA_DOCTOR_PROBE_TIMEOUT") or deploy_status.DEFAULT_PROBE_TIMEOUT_SECONDS)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def configure_stdio() -> None:
    """Prefer UTF-8 output so Unicode status markers and box characters render on Windows."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue


configure_stdio()

Status = Literal["ok", "warn", "fail", "skip"]
PNPM_SCRIPT_PATH = Path(__file__).resolve().with_name("pnpm.py")
FRONTEND_DIR = PNPM_SCRIPT_PATH.parent.parent / "frontend"


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    if _supports_color():
        return f"\033[{code}m{text}\033[0m"
    return text


def green(t: str) -> str:
    return _c(t, "32")


def red(t: str) -> str:
    return _c(t, "31")


def yellow(t: str) -> str:
    return _c(t, "33")


def cyan(t: str) -> str:
    return _c(t, "36")


def bold(t: str) -> str:
    return _c(t, "1")


def _icon(status: Status) -> str:
    icons = {"ok": green("✓"), "warn": yellow("!"), "fail": red("✗"), "skip": "—"}
    return icons[status]


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return (r.stdout or r.stderr).strip()
    except Exception:
        return None


def _truthy(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_major(version_text: str) -> int | None:
    v = version_text.lstrip("v").split(".", 1)[0]
    return int(v) if v.isdigit() else None


def _load_yaml_file(path: Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("top-level config must be a YAML mapping")
    return data


def _load_app_config(config_path: Path) -> object:
    from alpha.config.app_config import AppConfig

    return AppConfig.from_file(str(config_path))


def _split_use_path(use: str) -> tuple[str, str] | None:
    if ":" not in use:
        return None
    module_name, attr_name = use.split(":", 1)
    if not module_name or not attr_name:
        return None
    return module_name, attr_name


# --- Check result container -------------------------------------------------
# RESTORED 2026-09-26: a concurrent edit to this shared file dropped the class
# while leaving ~40 `CheckResult(...)` call sites, so the module raised
# NameError at the first check and `make doctor` could not run at all. Restored
# verbatim; every caller below depends on this exact shape (label, status,
# detail, fix).
class CheckResult:
    def __init__(
        self,
        label: str,
        status: Status,
        detail: str = "",
        fix: str | None = None,
    ) -> None:
        self.label = label
        self.status = status
        self.detail = detail
        self.fix = fix

    def print(self) -> None:
        icon = _icon(self.status)
        detail_str = f"  ({self.detail})" if self.detail else ""
        print(f"  {icon} {self.label}{detail_str}")
        if self.fix:
            for line in self.fix.splitlines():
                print(f"      {cyan('→')} {line}")


# --- model construction / provider reachability ---------------------------
#
def check_python() -> CheckResult:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 12):
        return CheckResult("Python", "ok", version_str)
    return CheckResult(
        "Python",
        "fail",
        version_str,
        fix="Python 3.12+ required. Install from https://www.python.org/",
    )


def check_node() -> CheckResult:
    node = shutil.which("node")
    if not node:
        return CheckResult(
            "Node.js",
            "fail",
            fix="Install Node.js 22+: https://nodejs.org/",
        )
    out = _run(["node", "-v"]) or ""
    major = _parse_major(out)
    if major is None or major < 22:
        return CheckResult(
            "Node.js",
            "fail",
            out or "unknown version",
            fix="Node.js 22+ required. Install from https://nodejs.org/",
        )
    return CheckResult("Node.js", "ok", out.lstrip("v"))


def check_pnpm() -> CheckResult:
    try:
        result = subprocess.run(
            [sys.executable, str(PNPM_SCRIPT_PATH), "-v"],
            cwd=FRONTEND_DIR,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
    except OSError as exc:
        return CheckResult(
            "pnpm",
            "fail",
            f"Unable to run pnpm resolver: {exc}",
            fix="Install pnpm, or install Corepack and ensure it is on PATH",
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = "\n".join(part for part in (stderr, stdout) if part)
        return CheckResult(
            "pnpm",
            "fail",
            detail or f"pnpm resolver exited with status {result.returncode}",
            fix="Install pnpm, or install Corepack and ensure it is on PATH",
        )
    if not stdout:
        return CheckResult(
            "pnpm",
            "fail",
            stderr or "pnpm resolver returned no version",
            fix="Install pnpm, or install Corepack and ensure it is on PATH",
        )
    return CheckResult("pnpm", "ok", stdout)


def check_uv() -> CheckResult:
    if not shutil.which("uv"):
        return CheckResult(
            "uv",
            "fail",
            fix="curl -LsSf https://astral.sh/uv/install.sh | sh",
        )
    out = _run(["uv", "--version"]) or ""
    parts = out.split()
    version = parts[1] if len(parts) > 1 else out
    return CheckResult("uv", "ok", version)


def check_nginx() -> CheckResult:
    if shutil.which("nginx"):
        out = _run(["nginx", "-v"]) or ""
        version = out.split("/", 1)[-1] if "/" in out else out
        return CheckResult("nginx", "ok", version)
    if sys.platform.startswith("win"):
        return CheckResult(
            "nginx",
            "warn",
            "not installed (direct dev mode uses Next.js proxy on :3000)",
            fix=("Optional on Windows. Direct dev mode (start.ps1) routes through Next.js.\nFor port 2026 unified proxy, use Docker mode or install nginx."),
        )
    return CheckResult(
        "nginx",
        "fail",
        fix=("macOS:   brew install nginx\nUbuntu:  sudo apt install nginx\nWindows: use WSL or Docker mode"),
    )


def check_config_exists(config_path: Path) -> CheckResult:
    if config_path.exists():
        return CheckResult("config.yaml found", "ok")
    return CheckResult(
        "config.yaml found",
        "fail",
        fix="Run 'make setup' to create it",
    )


def check_config_version(config_path: Path, project_root: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("config.yaml version", "skip")

    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            user_data = yaml.safe_load(f) or {}
        user_ver = int(user_data.get("config_version", 0))
    except Exception as exc:
        return CheckResult("config.yaml version", "fail", str(exc))

    example_path = project_root / "config.example.yaml"
    if not example_path.exists():
        return CheckResult("config.yaml version", "skip", "config.example.yaml not found")

    try:
        import yaml

        with open(example_path, encoding="utf-8") as f:
            example_data = yaml.safe_load(f) or {}
        example_ver = int(example_data.get("config_version", 0))
    except Exception:
        return CheckResult("config.yaml version", "skip")

    if user_ver < example_ver:
        return CheckResult(
            "config.yaml version",
            "warn",
            f"v{user_ver} < v{example_ver} (latest)",
            fix="make config-upgrade",
        )
    return CheckResult("config.yaml version", "ok", f"v{user_ver}")


def check_models_configured(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("models configured", "skip")
    try:
        data = _load_yaml_file(config_path)
        models = data.get("models") or []
        if models:
            return CheckResult("models configured", "ok", f"{len(models)} model(s)")
        return CheckResult(
            "models configured",
            "fail",
            "no models found",
            fix="Run 'make setup' to configure an LLM provider",
        )
    except Exception as exc:
        return CheckResult("models configured", "fail", str(exc))


def check_config_loadable(config_path: Path) -> CheckResult:
    if not config_path.exists():
        return CheckResult("config.yaml loadable", "skip")

    try:
        _load_app_config(config_path)
        return CheckResult("config.yaml loadable", "ok")
    except Exception as exc:
        return CheckResult(
            "config.yaml loadable",
            "fail",
            str(exc),
            fix="Run 'make setup' again, or compare with config.example.yaml",
        )


def check_llm_api_key(config_path: Path) -> list[CheckResult]:
    """Check that each model's env var is set in the environment."""
    if not config_path.exists():
        return []

    results: list[CheckResult] = []
    try:
        import yaml
        from dotenv import load_dotenv

        env_path = config_path.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        for model in data.get("models") or []:
            # Collect all values that look like $ENV_VAR references
            def _collect_env_refs(obj: object) -> list[str]:
                refs: list[str] = []
                if isinstance(obj, str) and obj.startswith("$"):
                    refs.append(obj[1:])
                elif isinstance(obj, dict):
                    for v in obj.values():
                        refs.extend(_collect_env_refs(v))
                elif isinstance(obj, list):
                    for item in obj:
                        refs.extend(_collect_env_refs(item))
                return refs

            env_refs = _collect_env_refs(model)
            model_name = model.get("name", "default")
            for var in env_refs:
                label = f"{var} set (model: {model_name})"
                if os.environ.get(var):
                    results.append(CheckResult(label, "ok"))
                else:
                    results.append(
                        CheckResult(
                            label,
                            "fail",
                            fix=f"Add {var}=<your-key> to your .env file",
                        )
                    )
    except Exception as exc:
        results.append(CheckResult("LLM API key check", "fail", str(exc)))

    return results


def check_llm_package(config_path: Path) -> list[CheckResult]:
    """Check that the LangChain provider package is installed."""
    if not config_path.exists():
        return []

    results: list[CheckResult] = []
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        seen_packages: set[str] = set()
        for model in data.get("models") or []:
            use = model.get("use", "")
            if ":" in use:
                package_path = use.split(":")[0]
                # e.g. langchain_openai → langchain-openai
                top_level = package_path.split(".")[0]
                pip_name = top_level.replace("_", "-")
                if pip_name in seen_packages:
                    continue
                seen_packages.add(pip_name)
                label = f"{pip_name} installed"
                try:
                    __import__(top_level)
                    results.append(CheckResult(label, "ok"))
                except ImportError:
                    results.append(
                        CheckResult(
                            label,
                            "fail",
                            fix=f"cd backend && uv add {pip_name}",
                        )
                    )
    except Exception as exc:
        results.append(CheckResult("LLM package check", "fail", str(exc)))

    return results


def check_llm_auth(config_path: Path) -> list[CheckResult]:
    if not config_path.exists():
        return []

    results: list[CheckResult] = []
    try:
        data = _load_yaml_file(config_path)
        for model in data.get("models") or []:
            use = model.get("use", "")
            model_name = model.get("name", "default")

            if use == "alpha.models.openai_codex_provider:CodexChatModel":
                auth_path = Path(os.environ.get("CODEX_AUTH_PATH", "~/.codex/auth.json")).expanduser()
                if auth_path.exists():
                    results.append(CheckResult(f"Codex CLI auth available (model: {model_name})", "ok", str(auth_path)))
                else:
                    results.append(
                        CheckResult(
                            f"Codex CLI auth available (model: {model_name})",
                            "fail",
                            str(auth_path),
                            fix="Run `codex login`, or set CODEX_AUTH_PATH to a valid auth.json",
                        )
                    )

            if use == "alpha.models.claude_provider:ClaudeChatModel":
                credential_paths = [Path(os.environ["CLAUDE_CODE_CREDENTIALS_PATH"]).expanduser() for env_name in ("CLAUDE_CODE_CREDENTIALS_PATH",) if os.environ.get(env_name)]
                credential_paths.append(Path("~/.claude/.credentials.json").expanduser())
                has_oauth_env = any(
                    os.environ.get(name)
                    for name in (
                        "ANTHROPIC_API_KEY",
                        "CLAUDE_CODE_OAUTH_TOKEN",
                        "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
                    )
                )
                existing_path = next((path for path in credential_paths if path.exists()), None)
                if has_oauth_env or existing_path is not None:
                    detail = "env var set" if has_oauth_env else str(existing_path)
                    results.append(CheckResult(f"Claude auth available (model: {model_name})", "ok", detail))
                else:
                    results.append(
                        CheckResult(
                            f"Claude auth available (model: {model_name})",
                            "fail",
                            fix=("Set ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN, or place credentials at ~/.claude/.credentials.json"),
                        )
                    )
    except Exception as exc:
        results.append(CheckResult("LLM auth check", "fail", str(exc)))
    return results


def check_web_search(config_path: Path) -> CheckResult:
    return check_web_tool(config_path, tool_name="web_search", label="web search configured")


def check_web_tool(config_path: Path, *, tool_name: str, label: str) -> CheckResult:
    """Warn (not fail) if a web capability is not configured."""
    if not config_path.exists():
        return CheckResult(label, "skip")

    try:
        from dotenv import load_dotenv

        env_path = config_path.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

        data = _load_yaml_file(config_path)

        tool_entries = [t for t in (data.get("tools") or []) if isinstance(t, dict) and t.get("name") == tool_name]
        if not tool_entries:
            return CheckResult(
                label,
                "warn",
                f"no {tool_name} tool in config",
                fix=f"Run 'make setup' to configure {tool_name}",
            )

        free_providers = {
            "web_search": {"ddg_search": "DuckDuckGo (no key needed)"},
            "web_fetch": {"jina_ai": "Jina AI Reader (no key needed)", "crawl4ai": "Crawl4AI (self-hosted, no key needed)"},
            "image_search": {"alpha.community.image_search.tools": "DuckDuckGo Images (no key needed)"},
        }
        key_providers = {
            "web_search": {
                "tavily": "TAVILY_API_KEY",
                "infoquest": "INFOQUEST_API_KEY",
                "exa": "EXA_API_KEY",
                "firecrawl": "FIRECRAWL_API_KEY",
                "fastcrw": "CRW_API_KEY",
                "brave": "BRAVE_SEARCH_API_KEY",
                "serper": "SERPER_API_KEY",
                "serply": "SERPLY_API_KEY",
                "sofya": "SOFYA_API_KEY",
                "tencent_wsa": "TENCENTCLOUD_WSA_APIKEY",
            },
            "web_fetch": {
                "infoquest": "INFOQUEST_API_KEY",
                "exa": "EXA_API_KEY",
                "firecrawl": "FIRECRAWL_API_KEY",
                "fastcrw": "CRW_API_KEY",
                "sofya": "SOFYA_API_KEY",
            },
            "image_search": {
                "brave": "BRAVE_SEARCH_API_KEY",
                "infoquest": "INFOQUEST_API_KEY",
                "serper": "SERPER_API_KEY",
            },
            "web_capture": {
                "browserless": "BROWSERLESS_TOKEN",
            },
        }
        key_fields = {
            "web_capture": {
                "browserless": "token",
            },
        }

        def _configured_key_detail(tool: dict, default_var: str, key_field: str = "api_key") -> tuple[Status, str] | None:
            configured_key = tool.get(key_field)
            if isinstance(configured_key, str) and configured_key.strip():
                key = configured_key.strip()
                if key.startswith("$"):
                    env_name = key[1:]
                    val = os.environ.get(env_name)
                    if val and val.strip():
                        return ("ok", f"{env_name} set from config")
                    # The referenced var is unset; fall through to the default
                    # env var below, which tools use as a runtime fallback.
                else:
                    return ("warn", f"literal {key_field} set in config")

            val = os.environ.get(default_var)
            return ("ok", f"{default_var} set") if val and val.strip() else None

        def _browserless_self_hosted(tool: dict) -> bool:
            base_url = str(tool.get("base_url") or "http://localhost:3032").lower()
            return "browserless.io" not in base_url

        for tool in tool_entries:
            use = tool.get("use", "")
            for provider, detail in free_providers.get(tool_name, {}).items():
                if provider in use:
                    return CheckResult(label, "ok", detail)

        for tool in tool_entries:
            use = tool.get("use", "")
            for provider, var in key_providers.get(tool_name, {}).items():
                if provider in use:
                    key_field = key_fields.get(tool_name, {}).get(provider, "api_key")
                    key_status = _configured_key_detail(tool, var, key_field=key_field)
                    if key_status:
                        status, detail = key_status
                        if status == "warn":
                            return CheckResult(
                                label,
                                "warn",
                                f"{provider} ({detail})",
                                fix=f"Move the {key_field} to .env as {var}=<your-key> and reference it as ${var}",
                            )
                        return CheckResult(label, "ok", f"{provider} ({detail})")
                    if tool_name == "web_capture" and provider == "browserless" and _browserless_self_hosted(tool):
                        return CheckResult(label, "ok", "browserless (self-hosted, token optional)")
                    return CheckResult(
                        label,
                        "warn",
                        f"{provider} configured but {var} not set",
                        fix=f"Add {var}=<your-key> to .env, or run 'make setup'",
                    )

        for tool in tool_entries:
            use = tool.get("use", "")
            split = _split_use_path(use)
            if split is None:
                return CheckResult(
                    label,
                    "fail",
                    f"invalid use path: {use}",
                    fix="Use a valid module:path provider from config.example.yaml",
                )
            module_name, attr_name = split
            try:
                module = import_module(module_name)
                getattr(module, attr_name)
            except Exception as exc:
                return CheckResult(
                    label,
                    "fail",
                    f"provider import failed: {use} ({exc})",
                    fix="Install the provider dependency or pick a valid provider in `make setup`",
                )

        return CheckResult(label, "ok")
    except Exception as exc:
        return CheckResult(label, "warn", str(exc))


def check_web_fetch(config_path: Path) -> CheckResult:
    return check_web_tool(config_path, tool_name="web_fetch", label="web fetch configured")


def check_web_capture(config_path: Path) -> CheckResult:
    return check_web_tool(config_path, tool_name="web_capture", label="web capture configured")


def check_image_search(config_path: Path) -> CheckResult:
    return check_web_tool(config_path, tool_name="image_search", label="image search configured")


def check_frontend_env(project_root: Path) -> CheckResult:
    env_path = project_root / "frontend" / ".env"
    if env_path.exists():
        return CheckResult("frontend/.env found", "ok")
    return CheckResult(
        "frontend/.env found",
        "warn",
        fix="Run 'make setup' or copy frontend/.env.example to frontend/.env",
    )


def check_sandbox(config_path: Path) -> list[CheckResult]:
    if not config_path.exists():
        return [CheckResult("sandbox configured", "skip")]

    try:
        data = _load_yaml_file(config_path)
        sandbox = data.get("sandbox")
        if not isinstance(sandbox, dict):
            return [
                CheckResult(
                    "sandbox configured",
                    "fail",
                    "missing sandbox section",
                    fix="Run 'make setup' to choose an execution mode",
                )
            ]

        sandbox_use = sandbox.get("use", "")
        tools = data.get("tools") or []
        tool_names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
        results: list[CheckResult] = []

        if "LocalSandboxProvider" in sandbox_use:
            results.append(CheckResult("sandbox configured", "ok", "Local sandbox"))
            has_bash_tool = "bash" in tool_names
            allow_host_bash = bool(sandbox.get("allow_host_bash", False))
            if has_bash_tool and not allow_host_bash:
                results.append(
                    CheckResult(
                        "bash compatibility",
                        "warn",
                        "bash tool configured but host bash is disabled",
                        fix="Enable host bash only in a fully trusted environment, or switch to container sandbox",
                    )
                )
            elif allow_host_bash:
                results.append(
                    CheckResult(
                        "bash compatibility",
                        "warn",
                        "host bash enabled on LocalSandboxProvider",
                        fix="Use container sandbox for stronger isolation when bash is required",
                    )
                )
        elif "AioSandboxProvider" in sandbox_use:
            results.append(CheckResult("sandbox configured", "ok", "Container sandbox"))
            if not sandbox.get("provisioner_url") and not (shutil.which("docker") or shutil.which("container")):
                results.append(
                    CheckResult(
                        "container runtime available",
                        "warn",
                        "no Docker/Apple Container runtime detected",
                        fix="Install Docker Desktop / Apple Container, or switch to local sandbox",
                    )
                )
        elif sandbox_use:
            results.append(CheckResult("sandbox configured", "ok", sandbox_use))
        else:
            results.append(
                CheckResult(
                    "sandbox configured",
                    "fail",
                    "sandbox.use is empty",
                    fix="Run 'make setup' to choose an execution mode",
                )
            )
        return results
    except Exception as exc:
        return [CheckResult("sandbox configured", "fail", str(exc))]


def check_env_file(project_root: Path) -> CheckResult:
    env_path = project_root / ".env"
    if env_path.exists():
        return CheckResult(".env found", "ok")
    return CheckResult(
        ".env found",
        "warn",
        fix="Run 'make setup' or copy .env.example to .env",
    )


# ---------------------------------------------------------------------------
# Runtime readiness: can the *running* deployment actually serve?
# ---------------------------------------------------------------------------
#
# Every check above is a property of the checkout. None of them can notice the
# two failure modes that cost the most time in the field:
#
#   * ``logs/alpha_health.json`` still says ``status: "starting"`` (or
#     "healthy") from a launcher that was killed or that aborted on a missing
#     dependency. That file outliving its process is a monitoring lie: every
#     diagnostic that trusts it then reports the lie forward.
#   * the Gateway process is up on its port but cannot serve, because the
#     persistence backends behind ``/health/ready`` are unreachable. A 200 from
#     ``/health`` (liveness) says nothing about this, so the probe here uses the
#     readiness route.
#
# Both are fail-closed. The tri-state is preserved end to end: "not running" and
# "nothing answered" are *unknown*, and unknown is reported as Indeterminate
# with its own exit code - never as Ready.


def _describe(verdict: deploy_status.Verdict) -> str:
    """Render a verdict for a human, keeping the observed cause attached.

    ``Verdict.reason`` names the *kind* of problem ("launcher recorded
    status='failed'"); ``Verdict.detail`` carries what was actually observed
    ("dependency missing: uv"). Printing only the first throws away the reason
    the launcher failed - which is the one thing the operator needs, and the
    reason a report is worth reading at all.
    """
    if verdict.detail:
        return f"{verdict.reason}: {verdict.detail}"
    return verdict.reason

def check_launcher_status_file(project_root: Path) -> CheckResult:
    """Is the launcher status file currently telling the truth?"""
    verdict = deploy_status.audit_status_file(deploy_status.default_logs_dir(project_root))
    if verdict.state == deploy_status.OK:
        return CheckResult("launcher status file", "ok", _describe(verdict))
    if verdict.state == deploy_status.UNKNOWN:
        return CheckResult(
            "launcher status file",
            "skip",
            _describe(verdict),
            fix="Start Alpha (./start.ps1 or ./start.sh) to produce one; nothing is asserted until then.",
        )
    return CheckResult(
        "launcher status file",
        "fail",
        _describe(verdict),
        fix=("The status file does not describe a live launcher, so it cannot be trusted.\nStop the stale launcher (./stop.ps1) and start again, or delete logs/alpha_health.json\nand logs/alpha.pid if no launcher is running."),
    )


def check_launcher_pid_agreement(project_root: Path) -> CheckResult:
    """Do alpha.pid and alpha_health.json agree on which PID owns the stack?"""
    verdict = deploy_status.pid_file_agrees_with_status(deploy_status.default_logs_dir(project_root))
    if verdict.state == deploy_status.OK:
        return CheckResult("launcher pid agreement", "ok", _describe(verdict))
    if verdict.state == deploy_status.UNKNOWN:
        return CheckResult("launcher pid agreement", "skip", _describe(verdict))
    return CheckResult(
        "launcher pid agreement",
        "fail",
        _describe(verdict),
        fix="Two launchers are fighting over the same ports. Run ./stop.ps1, then start exactly one.",
    )


def check_gateway_readiness() -> CheckResult:
    """Ask the Gateway, over /health/ready, whether it can actually serve."""
    verdict = deploy_status.probe_readiness(
        deploy_status.gateway_base_url(GATEWAY_PORT),
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    if verdict.state == deploy_status.OK:
        return CheckResult("gateway readiness", "ok", _describe(verdict))
    if verdict.state == deploy_status.UNKNOWN:
        return CheckResult(
            "gateway readiness",
            "skip",
            _describe(verdict),
            fix=(f"Start the Gateway on port {GATEWAY_PORT} (./start.ps1) and re-run, or set\nALPHA_DOCTOR_GATEWAY_PORT to the port it really uses."),
        )
    return CheckResult(
        "gateway readiness",
        "fail",
        _describe(verdict),
        fix=("The Gateway answered but reports it cannot serve. Fix the named subsystem\n(database / checkpointer) - see logs/gateway.err.log and config.yaml database:."),
    )


def check_frontend_http() -> CheckResult:
    """Liveness probe for the web UI (it exposes no readiness route)."""
    verdict = deploy_status.probe_http_ok(
        deploy_status.frontend_base_url(FRONTEND_PORT),
        timeout=PROBE_TIMEOUT_SECONDS,
    )
    if verdict.state == deploy_status.OK:
        return CheckResult("frontend http", "ok", _describe(verdict))
    if verdict.state == deploy_status.UNKNOWN:
        return CheckResult(
            "frontend http",
            "skip",
            _describe(verdict),
            fix=(f"Start the web UI on port {FRONTEND_PORT}, or set ALPHA_DOCTOR_FRONTEND_PORT\nto the port it really uses."),
        )
    return CheckResult(
        "frontend http",
        "fail",
        _describe(verdict),
        fix="The frontend is listening but not serving HTTP 200. Check logs/frontend.err.log.",
    )


# ---------------------------------------------------------------------------
# Can this checkout actually complete a run?
# ---------------------------------------------------------------------------
#
# Everything above is static: it reads config.yaml, checks that env vars are set,
# and imports provider packages. None of it builds a model client or talks to a
# provider, so a checkout in which *every* model fails to construct -- which is
# exactly the state all four P0 defects shipped in -- still reported
# ``Status: Ready``. That check is what let the defects ship.
#
# The two checks below close that gap. Both are fail-closed and both are real:
# they run the actual factory and the actual client.

#: Paid in latency by ``make doctor``. Kept small on purpose: this is a health
#: check, not a benchmark. Enough to prove the endpoint answers, not enough to
#: slow a developer's first command. Reads the shared, env-overridable value so
#: ``ALPHA_DOCTOR_PROBE_TIMEOUT`` shortens this probe too.
_PROBE_TIMEOUT_SECONDS = PROBE_TIMEOUT_SECONDS

#: Cap on the total time spent probing, so a config with many models (and long
#: fallback chains) cannot stall the report. Whatever is left over is reported as
#: ``skip`` with the reason, never silently dropped.
_PROBE_TOTAL_BUDGET_SECONDS = float(os.environ.get("ALPHA_DOCTOR_PROBE_TOTAL_BUDGET") or 180.0)

#: Escape hatch for an air-gapped or metered host. Construction still runs (it
#: makes no request); only the live probe is skipped, and the report says so
#: rather than quietly reporting Ready for something it never tried.
SKIP_NETWORK_ENV = "AGENT_WORKSPACE_DOCTOR_NO_NETWORK"


def _iter_reachable_models(app_config) -> list:
    """Models the operator can actually reach: the primary of every entry, plus every fallback.

    A fallback member is a real deployment target (``fallbacks:`` exists precisely
    so a retryable failure can fail over), so each reachable entry is probed in
    turn. Probing them all means doctor cannot report Ready while any model the
    product will actually try is broken.

    Order is the order a run would try them: each entry's own chain, primary
    first. Note that the primary must NOT be pre-seeded into ``seen`` -- every
    chain contains its own primary, so doing that silently drops every primary
    from the result and leaves only the fallbacks to check, which is the exact
    opposite of what this function is for.
    """
    from alpha.models.factory import _resolve_chain_configs

    seen: set[str] = set()
    ordered: list = []
    for model in app_config.models:
        try:
            chain = _resolve_chain_configs(model.name, app_config)
        except Exception:
            # An unresolvable chain is itself a finding; report the primary so
            # the construction check surfaces the real error against it.
            chain = [model]
        for member in chain:
            if member.name in seen:
                continue
            seen.add(member.name)
            ordered.append(member)
    return ordered


def check_models_construct(config_path: Path) -> list[CheckResult]:
    """Build every reachable model through the real factory.

    This is the check the four P0 defects slipped past. It is the *same* code
    path a run takes (``alpha.models.factory.create_chat_model``), so a metadata
    key that leaks into the provider constructor, or a chain that resolves to
    nothing, fails here instead of 86 seconds into a user's first run.
    """
    if not config_path.exists():
        return [CheckResult("model client construction", "skip")]

    results: list[CheckResult] = []
    try:
        from dotenv import load_dotenv

        env_path = config_path.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

        # Import order is load-bearing, not incidental. ``alpha.config.app_config``
        # pulls in the ``alpha.models`` package, and resolving
        # ``alpha.models.factory`` *through* that partially-initialised package
        # raises "cannot import name 'create_chat_model' from partially
        # initialized module 'alpha.models'". Importing the factory submodule
        # first completes the package, so the later attribute access is safe.
        # Verified: the reversed order made this check report a false FAIL on a
        # healthy checkout, which is worse than no check at all.
        import alpha.models.factory as model_factory
        from alpha.config.app_config import AppConfig

        app_config = AppConfig.from_file(str(config_path))
    except Exception as exc:
        return [
            CheckResult(
                "model client construction",
                "fail",
                str(exc),
                fix="Fix config.yaml so it loads (see 'config.yaml loadable' above)",
            )
        ]

    create_chat_model = model_factory.create_chat_model

    for member in _iter_reachable_models(app_config):
        label = f"model client builds ({member.name})"
        try:
            model = create_chat_model(name=member.name, app_config=app_config, attach_tracing=False)
        except Exception as exc:
            results.append(
                CheckResult(
                    label,
                    "fail",
                    f"{type(exc).__name__}: {exc}",
                    fix=(f"Alpha cannot start a run on model '{member.name}'. This is the exact error a run would hit at agent assembly."),
                )
            )
            continue
        # Belt and braces: an unknown kwarg is not rejected by the OpenAI client,
        # it is diverted into model_kwargs and then spread into the completion
        # request, where the SDK rejects it as an unexpected keyword argument.
        # Catch that class of defect without spending a request.
        leaked = sorted(key for key in dict(getattr(model, "model_kwargs", None) or {}) if key in {"capabilities", "supports_thinking", "supports_vision", "pricing", "context_window"})
        if leaked:
            results.append(
                CheckResult(
                    label,
                    "fail",
                    f"metadata key(s) {leaked} would be sent to the provider and rejected at request time",
                    fix="These are Alpha metadata, not provider arguments; they must be stripped before construction.",
                )
            )
            continue
        results.append(CheckResult(label, "ok", type(model).__name__))

    return results


def check_provider_reachable(config_path: Path) -> list[CheckResult]:
    """Send one minimal real request per configured model.

    Static checks cannot distinguish "the key is set" from "the key works" and
    "the endpoint resolves" from "the endpoint answers". Both distinctions have
    shipped as false positives. One tiny non-streaming call is the cheapest
    honest test, and its latency is bounded so a dead endpoint reports rather
    than hangs.
    """
    if not config_path.exists():
        return [CheckResult("provider reachable", "skip")]

    if _truthy(os.environ.get(SKIP_NETWORK_ENV)):
        return [
            CheckResult(
                "provider reachable",
                "skip",
                f"{SKIP_NETWORK_ENV} is set; no request was made",
                fix="Unset it to have doctor send one minimal request per reachable model.",
            )
        ]

    try:
        from dotenv import load_dotenv

        env_path = config_path.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

        # Same load-bearing import order as ``check_models_construct``: the
        # factory submodule must be resolved before ``alpha.config.app_config``
        # pulls in the partially-initialised ``alpha.models`` package.
        import alpha.models.factory as model_factory
        from alpha.config.app_config import AppConfig
        from langchain_core.messages import HumanMessage
    except Exception as exc:
        return [CheckResult("provider reachable", "fail", str(exc))]

    create_chat_model = model_factory.create_chat_model

    try:
        app_config = AppConfig.from_file(str(config_path))
    except Exception as exc:
        return [CheckResult("provider reachable", "fail", str(exc))]

    results: list[CheckResult] = []
    started = time.monotonic()
    for member in _iter_reachable_models(app_config):
        label = f"provider answers ({member.name})"
        if time.monotonic() - started > _PROBE_TOTAL_BUDGET_SECONDS:
            # Reported, not dropped: a budget-exhausted probe must not read as a
            # passing one.
            results.append(
                CheckResult(
                    label,
                    "skip",
                    f"probe budget of {_PROBE_TOTAL_BUDGET_SECONDS:.0f}s exhausted",
                    fix="Raise ALPHA_DOCTOR_PROBE_TOTAL_BUDGET to probe every reachable model.",
                )
            )
            continue
        try:
            model = create_chat_model(name=member.name, app_config=app_config, attach_tracing=False)
            model = model.with_config({"timeout": _PROBE_TIMEOUT_SECONDS, "max_retries": 0})
        except Exception as exc:
            # Construction already failed; report it there, not twice.
            results.append(CheckResult(label, "skip", f"construction failed: {type(exc).__name__}"))
            continue
        try:
            reply = model.invoke([HumanMessage(content="Reply with the single word: PONG")], stop=["\n"])
            text = getattr(reply, "content", "")
        except Exception as exc:
            results.append(
                CheckResult(
                    label,
                    "fail",
                    f"{type(exc).__name__}: {str(exc)[:300]}",
                    fix=(f"Alpha cannot complete a run on model '{member.name}'. A run would fail here with this error."),
                )
            )
            continue
        results.append(CheckResult(label, "ok", f"responded: {str(text)[:60]!r}"))

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config.yaml"

    # Load .env early so key checks work
    try:
        from dotenv import load_dotenv

        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

    print()
    print(bold("Alpha Health Check"))
    print("═" * 40)

    sections: list[tuple[str, list[CheckResult]]] = []

    # ── System Requirements ────────────────────────────────────────────────────
    sys_checks = [
        check_python(),
        check_node(),
        check_pnpm(),
        check_uv(),
        check_nginx(),
    ]
    sections.append(("System Requirements", sys_checks))

    # ── Configuration ─────────────────────────────────────────────────────────
    cfg_checks: list[CheckResult] = [
        check_env_file(project_root),
        check_config_exists(config_path),
        check_config_version(config_path, project_root),
        check_config_loadable(config_path),
        check_models_configured(config_path),
    ]
    sections.append(("Configuration", cfg_checks))

    # ── LLM Provider ──────────────────────────────────────────────────────────
    llm_checks: list[CheckResult] = [
        *check_llm_api_key(config_path),
        *check_llm_auth(config_path),
        *check_llm_package(config_path),
    ]
    sections.append(("LLM Provider", llm_checks))

    # ── Run Readiness ─────────────────────────────────────────────────────────
    # Can this checkout actually complete a run? Every check above is static and
    # reported Ready on a checkout where all four P0 defects were present, so
    # these two do the real thing: build the model clients, then talk to the
    # providers. They are fail-closed and are the only checks that can make
    # doctor refuse to say Ready for an unrunnable product.
    #
    # Both are counted as required below, and ``check_provider_reachable``
    # honours ``AGENT_WORKSPACE_DOCTOR_NO_NETWORK`` for an air-gapped host --
    # construction still runs, only the request is skipped, and the report says
    # so rather than quietly reporting Ready for something it never tried.
    sections.append(
        (
            "Run Readiness",
            [
                *check_models_construct(config_path),
                *check_provider_reachable(config_path),
            ],
        )
    )

    # ── Web Capabilities ─────────────────────────────────────────────────────
    search_checks = [
        check_web_search(config_path),
        check_web_fetch(config_path),
        check_web_capture(config_path),
        check_image_search(config_path),
    ]
    sections.append(("Web Capabilities", search_checks))

    # ── Sandbox ──────────────────────────────────────────────────────────────
    sandbox_checks = check_sandbox(config_path)
    sections.append(("Sandbox", sandbox_checks))

    # ── Runtime Readiness ────────────────────────────────────────────────────
    # The only section that can observe the *running* deployment, and the only
    # one that can catch a status file which outlived its process or a Gateway
    # that is up but unable to serve. Placed after the static checks because it
    # is the last word on the verdict, not the first.
    deployment_checks: list[CheckResult] = [
        check_launcher_status_file(project_root),
        check_launcher_pid_agreement(project_root),
        check_gateway_readiness(),
        check_frontend_http(),
    ]
    sections.append(("Runtime Readiness", deployment_checks))

    # ── Render ────────────────────────────────────────────────────────────────
    total_fails = 0
    total_warns = 0
    total_unknown = 0
    readiness_failed = False
    unknown_checks: list[str] = []

    for section_title, checks in sections:
        print()
        print(bold(section_title))
        for cr in checks:
            cr.print()
            if cr.status == "fail":
                total_fails += 1
                if section_title == "Run Readiness":
                    readiness_failed = True
            elif cr.status == "warn":
                total_warns += 1
            elif cr.status == "skip":
                # Absence of evidence, counted separately so it can never be
                # absorbed into "Ready".
                total_unknown += 1
                unknown_checks.append(f"{section_title}: {cr.label}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("═" * 40)
    if total_fails:
        summary = f"{total_fails} error(s), {total_warns} warning(s)"
        if total_unknown:
            summary += f", {total_unknown} unknown"
        print(f"Status: {red(summary)}")
        print("Fix the errors above, then run 'make doctor' again.")
    elif total_unknown:
        # Not green, and not red either: we could not determine the answer. The
        # names of the undetermined checks are printed so "unknown" is
        # actionable instead of a shrug.
        summary = f"Indeterminate - {total_unknown} check(s) could not be determined"
        if total_warns:
            summary += f", {total_warns} warning(s)"
        print(f"Status: {yellow(summary)}")
        print("Undetermined checks (absence of evidence, treated as NOT ready):")
        for name in unknown_checks:
            print(f"  {yellow('?')} {name}")
        print("Start the stack and re-run 'make doctor' for a real verdict.")
    elif total_warns:
        print(f"Status: {yellow(f'Ready ({total_warns} warning(s))')}")
        print(f"Run {cyan('make dev')} to start Alpha")
    else:
        print(f"Status: {green('Ready')}")
        print(f"Run {cyan('make dev')} to start Alpha")
    if readiness_failed:
        # Redundant with the exit code, and deliberately so: a checkout that
        # cannot complete a run must not be summarisable as Ready even by
        # skimming, and that is precisely what shipped.
        print(red("Alpha cannot complete a run in this checkout (see Run Readiness above)."))

    print()
    if total_fails:
        return EXIT_NOT_READY
    if total_unknown:
        return EXIT_INDETERMINATE
    return EXIT_READY


if __name__ == "__main__":
    sys.exit(main())
