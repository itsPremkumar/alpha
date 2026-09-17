"""Production readiness pre-flight check for DeerFlow.

Validates the things that most often break a production deployment, before
you deploy it. Run from the repository root:

    python scripts/prod_check.py          # warnings allowed, failures block
    python scripts/prod_check.py --strict # warnings also block (CI gate)

Portable stdlib-only Python so it runs anywhere (no uv/pnpm needed).

FAIL (exit 1):
  - version sources disagree (backend/pyproject.toml, frontend/package.json,
    deploy/helm/agent-workspace/Chart.yaml version + appVersion). Fix with
    scripts/bump_version.sh <version>, like CI's verify-versions gate.
  - config.yaml or extensions_config.json missing (create with `make config`).

WARN (exit 0, exit 1 with --strict):
  - .env missing, BETTER_AUTH_SECRET unset/placeholder, or
    GATEWAY_ENABLE_DOCS not false.
  - config.yaml config_version older than config.example.yaml (run
    `make config-upgrade`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "deer-flow" / "Chart.yaml"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
CONFIG = ROOT / "config.yaml"
CONFIG_EXAMPLE = ROOT / "config.example.yaml"
EXTENSIONS_CONFIG = ROOT / "extensions_config.json"
DOTENV = ROOT / ".env"

PLACEHOLDER_MARKERS = ("your-", "changeme", "example", "placeholder", "replace-me")


def _results() -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    return failures, warnings


def check_versions(failures: list[str], warnings: list[str]) -> str | None:
    """All version sources must agree; returns the agreed version or None."""
    try:
        chart_text = CHART.read_text(encoding="utf-8")
        chart_version = re.search(r"^version:\s*(\S+)", chart_text, re.M).group(1)
        chart_app = re.search(r'^appVersion:\s*"?([^"\s]+)"?', chart_text, re.M).group(1)
        py_text = PYPROJECT.read_text(encoding="utf-8")
        py_version = re.search(r'^version\s*=\s*"([^"]+)"', py_text, re.M).group(1)
        js_version = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, AttributeError, KeyError, json.JSONDecodeError) as exc:
        failures.append(f"version sources unreadable: {exc}")
        return None

    print(f"  Chart.yaml version/appVersion: {chart_version} / {chart_app}")
    print(f"  backend/pyproject.toml:        {py_version}")
    print(f"  frontend/package.json:         {js_version}")
    mismatched = False
    for name, actual in (
        ("Chart.yaml appVersion", chart_app),
        ("backend/pyproject.toml", py_version),
        ("frontend/package.json", js_version),
    ):
        if actual != chart_version:
            failures.append(f"{name} is '{actual}' but expected '{chart_version}' (run scripts/bump_version.sh {chart_version})")
            mismatched = True
    return None if mismatched else chart_version


def check_config_files(failures: list[str], warnings: list[str]) -> None:
    if not CONFIG.is_file():
        failures.append("config.yaml missing at repo root (run `make config`)")
    else:
        print("  config.yaml present")
        try:
            example_text = CONFIG_EXAMPLE.read_text(encoding="utf-8")
            current_text = CONFIG.read_text(encoding="utf-8")
            example_ver = re.search(r"^config_version:\s*(\d+)", example_text, re.M)
            current_ver = re.search(r"^config_version:\s*(\d+)", current_text, re.M)
            if example_ver and current_ver and current_ver.group(1) != example_ver.group(1):
                warnings.append(
                    f"config.yaml version {current_ver.group(1)} is older than template version {example_ver.group(1)} "
                    "(run `make config-upgrade`)"
                )
            elif example_ver and current_ver:
                print(f"  config_version {current_ver.group(1)} matches template")
        except OSError as exc:
            warnings.append(f"could not compare config versions: {exc}")
    if not EXTENSIONS_CONFIG.is_file():
        failures.append("extensions_config.json missing at repo root (run `make config`)")
    else:
        print("  extensions_config.json present")


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


def check_env(failures: list[str], warnings: list[str]) -> None:
    if not DOTENV.is_file():
        warnings.append(".env missing at repo root (copy from .env.production.example for production)")
        return
    values = _parse_dotenv(DOTENV)
    secret = values.get("BETTER_AUTH_SECRET", "")
    if not secret or any(marker in secret.lower() for marker in PLACEHOLDER_MARKERS):
        warnings.append("BETTER_AUTH_SECRET is unset or looks like a placeholder (generate: openssl rand -hex 32)")
    else:
        print("  BETTER_AUTH_SECRET set")
    docs = values.get("GATEWAY_ENABLE_DOCS", "true").lower()
    if docs != "false":
        warnings.append("GATEWAY_ENABLE_DOCS is not 'false' (disable Swagger/ReDoc/OpenAPI in production)")
    else:
        print("  GATEWAY_ENABLE_DOCS=false")


def main() -> int:
    parser = argparse.ArgumentParser(description="DeerFlow production readiness pre-flight check")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures (CI gate)")
    args = parser.parse_args()

    failures, warnings = _results()

    print("== versions ==")
    check_versions(failures, warnings)
    print("== config files ==")
    check_config_files(failures, warnings)
    print("== environment ==")
    check_env(failures, warnings)

    print()
    for warning in warnings:
        print(f"[WARN] {warning}")
    for failure in failures:
        print(f"[FAIL] {failure}")

    blocking = failures + (warnings if args.strict else [])
    if blocking:
        print(f"\nprod-check: {len(failures)} failure(s), {len(warnings)} warning(s) -> NOT production ready")
        return 1
    if warnings:
        print(f"\nprod-check: 0 failures, {len(warnings)} warning(s) -> ready with warnings")
        return 0
    print("\nprod-check: all checks passed -> production ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
