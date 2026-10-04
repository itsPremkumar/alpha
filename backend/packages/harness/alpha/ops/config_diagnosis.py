"""Configuration diagnosis: what is misconfigured, and what would fix it.

Read-only by construction
-------------------------
Nothing in this module writes a config file, mutates process state, calls a
provider, or reads a secret value. It produces a list of :class:`Finding` records
and, for each, a *proposal* describing the change an operator could make. A
proposal is a proposal: there is no ``apply``, and that is a design decision rather
than an unfinished feature — see "Why there is no apply step" below.

Why there is no ``apply`` step
------------------------------
The repo already has a write path for configuration, and it is not this module's
job to add a second one. ``config.yaml`` and ``extensions_config.json`` are
API-writable through the Gateway under the dual write locks documented in
:mod:`alpha.extensions`, and ``bot_roster``'s self-service actions are explicitly
bounded to "a Bot may configure itself, but may not widen itself". A model tool
that could rewrite ``config.yaml`` would be an unaudited fourth writer competing
with those locks for the same files, and it would do so with the authority of the
agent rather than of the operator. So this module stops at a proposal and says so
in every payload it returns.

What it diagnoses, and why each gap matters
-------------------------------------------
Each finding is a place where Alpha is configured in a way that will silently
reduce what it can do — the class of problem that produces "it said it could do
X, then quietly didn't". The set is deliberately small and each check reads a real
source:

``missing_config_file``
    No ``config.yaml`` at all. Every other check is vacuous without one, and the
    Gateway refuses to boot — so this is reported first and blocks the rest.
``unreadable_config_file``
    The file exists and does not validate. Carries the real validation error,
    because "your config is wrong" without the field and value that failed is not
    actionable.
``no_models_configured``
    Zero models. Alpha starts and then cannot answer anything.
``default_model_not_in_models``
    A hot edit pointed ``default_model`` at a model that is no longer declared.
``missing_api_key``
    An enabled provider names an ``api_key_env`` that is not set in the process
    environment. **Only the variable name is reported, never its value** — the
    same rule :mod:`alpha.ops.autonomy_truth` follows, because a diagnostic that
    echoes a key into a model context is a credential leak with extra steps.
``disabled_capability``
    An optional subsystem declared in ``CAPABILITY_CATALOG`` that is importable
    but not enabled. This is an *opportunity*, reported at the lowest severity:
    a disabled capability is a legitimate operator choice, not a defect.
``unwired_manifest_row``
    A router, middleware or supervisor loop the generated manifest reports as
    declared but unwired. A real defect in the tree.
``feature_manifest_missing``
    The generated artifact is absent, so engine and wiring counts cannot be
    reported at all.
``slash_command_without_handler``
    Aggregate of catalog rows that resolve but cannot execute. Reported once as a
    count with examples rather than 400 times, because the finding is "the catalog
    advertises more than it implements", which is a documentation-honesty problem
    rather than 400 separate bugs.

Severity
--------
``blocker`` / ``warning`` / ``info``, in that order. A ``blocker`` means Alpha
cannot do the thing it is configured to do. ``warning`` means it can, but less than
the operator probably intends. ``info`` is an observation with no action implied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "alpha.config-diagnosis.v1"

#: Severity ordering for report and sort. Lower sorts first.
_SEVERITY_RANK: dict[str, int] = {"blocker": 0, "warning": 1, "info": 2}


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """One measured configuration gap, and the change that would close it."""

    id: str
    severity: Severity
    title: str
    evidence: str
    proposal: str
    remediation: str
    authority: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "title": self.title,
            "evidence": self.evidence,
            "proposal": self.proposal,
            "remediation": self.remediation,
            "authority": self.authority,
            "read_only": True,
        }


@dataclass
class Diagnosis:
    """Every finding from one pass, plus the context needed to interpret them."""

    findings: list[Finding] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {severity.value: sum(1 for finding in self.findings if finding.severity == severity) for severity in (Severity.BLOCKER, Severity.WARNING, Severity.INFO)}

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(self.findings, key=lambda finding: (_SEVERITY_RANK.get(finding.severity.value, 99), finding.id))
        counts = self.counts()
        return {
            "schema_version": SCHEMA_VERSION,
            "read_only": True,
            "status": "blocked" if counts["blocker"] else ("degraded" if counts["warning"] else "ok"),
            "counts": counts,
            "context": self.context,
            "findings": [finding.to_dict() for finding in ordered],
            "notes": _NOTES,
        }


_NOTES: tuple[str, ...] = (
    "Read-only. No config file was written and no provider was contacted; each proposal is a change for an operator to make, not an action taken.",
    "An api_key_env finding names the environment variable only. Its value is never read into this payload.",
    "apply config.yaml and extensions_config.json changes through the Gateway API or an operator; the dual write locks in alpha.extensions own that path.",
)


def _safe(label: str, probe: Any, findings: list[Finding]) -> Any:
    """Run a probe, disclosing failure as a warning finding instead of raising.

    A diagnosis tool that dies because one check found a broken file is useless
    precisely when it is most needed, so every check is isolated and a failure
    becomes a finding with the real exception text.
    """
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 - disclosure is the whole point
        findings.append(
            Finding(
                id=f"probe_failed_{label}",
                severity=Severity.WARNING,
                title=f"Could not run the {label} check",
                evidence=f"{type(exc).__name__}: {exc}",
                proposal="No proposal: the check itself failed, so its subject is unknown rather than healthy.",
                remediation="Read the error above and fix the underlying source before trusting the rest of this report.",
                authority="operator",
            )
        )
        return None


def diagnose_configuration(*, include_disabled_capabilities: bool = True) -> Diagnosis:
    """Diagnose the live configuration. Performs no writes and no network calls.

    Args:
        include_disabled_capabilities: Also report importable-but-disabled
            optional subsystems. Off by default in spirit: these are ``info``, so
            they never change ``status``, but they are the answer to "what could I
            turn on?".

    Blocking I/O (config file, skill storage, the generated manifest). Gateway
    callers must use ``asyncio.to_thread``.
    """
    findings: list[Finding] = []
    context: dict[str, Any] = {}

    config = _check_config_file(findings, context)
    if config is not None:
        _check_models(config, findings, context)
        _check_providers(config, findings, context)
        _check_sandbox(config, findings, context)
    _check_manifest(findings, context)
    _check_capabilities(findings, include_disabled_capabilities)
    _check_command_coverage(findings, context)

    return Diagnosis(findings=findings, context=context)


# -- checks ----------------------------------------------------------------


def _check_config_file(findings: list[Finding], context: dict[str, Any]) -> Any:
    """The config file must exist and validate. Everything else is downstream."""

    def probe() -> tuple[Any, str]:
        import alpha.config.app_config as app_config_module
        from alpha.config.app_config import get_app_config

        app_config = get_app_config()
        loaded_path = getattr(app_config_module, "_app_config_path", None)
        return app_config, str(loaded_path) if loaded_path else "(in-memory or runtime override; no file path recorded)"

    resolved = _safe("configuration", probe, findings)
    if resolved is None:
        # The probe already recorded the real error as a finding. Make the
        # consequence explicit rather than leaving a caller to infer it: no
        # config means no models, no skills, no sandbox — the Gateway will not boot.
        findings.append(
            Finding(
                id="missing_config_file",
                severity=Severity.BLOCKER,
                title="Alpha cannot start: no usable config.yaml",
                evidence="get_app_config() did not return a configuration (see the probe_failed_configuration finding for the real error).",
                proposal="Run `make config` to copy config.example.yaml to config.yaml, then fill in at least one entry under `models:`.",
                remediation="Root Makefile: `make config`. The Gateway refuses to boot without config.yaml.",
                authority="operator",
            )
        )
        context["config"] = {"loaded": False}
        return None

    app_config, source = resolved
    context["config"] = {"loaded": True, "source": source}
    return app_config


def _check_models(config: Any, findings: list[Finding], context: dict[str, Any]) -> None:
    models = list(getattr(config, "models", None) or [])
    names = [str(getattr(model, "name", "") or "") for model in models]
    context["models"] = {
        "count": len(models),
        "names": sorted(name for name in names if name),
        "default_model": getattr(config, "default_model_name", None),
        "source": "config.yaml (declared, not probed)",
    }

    if not models:
        findings.append(
            Finding(
                id="no_models_configured",
                severity=Severity.BLOCKER,
                title="No models are configured",
                evidence=f"config.yaml declares 0 entries under `models:` (loaded from {context['config'].get('source')}).",
                proposal="Add at least one entry under `models:`; `default_model:` must name one of them.",
                remediation="config.example.yaml documents the `models:` schema and the OpenRouter baseline entry.",
                authority="operator",
            )
        )
        return

    default_name = str(getattr(config, "default_model_name", "") or "")
    if default_name and default_name not in {name for name in names if name}:
        # AppConfig validates this at load, so reaching here means the file was
        # replaced after the process cached it. That is worth saying plainly
        # rather than blaming the operator's YAML.
        findings.append(
            Finding(
                id="default_model_not_in_models",
                severity=Severity.BLOCKER,
                title="default_model does not name a declared model",
                evidence=f"default_model={default_name!r}; declared models: {sorted(name for name in names if name)}",
                proposal=f"Set `default_model: {next((name for name in names if name), '<first-declared-model>')}`, or restore the removed model entry.",
                remediation="AppConfig validates this at load; a mismatch here means config.yaml changed after the process started — restart the Gateway to re-read it.",
                authority="operator",
            )
        )


def _check_providers(config: Any, findings: list[Finding], context: dict[str, Any]) -> None:
    """Enabled providers whose declared key env var is not set.

    Only the variable *name* is reported. Reading a value here would put a
    credential into a model-facing payload, which is the one thing this module must
    never do.
    """
    providers = dict(getattr(config, "providers", None) or {})
    context["providers"] = {"count": len(providers), "names": sorted(providers)}
    missing: list[str] = []
    for name, provider in sorted(providers.items()):
        if not bool(getattr(provider, "enabled", True)):
            continue
        env_name = str(getattr(provider, "api_key_env", "") or "")
        if not env_name:
            continue
        if not os.environ.get(env_name):
            missing.append(f"{name} (expects ${env_name})")
    if missing:
        findings.append(
            Finding(
                id="missing_api_key",
                severity=Severity.WARNING,
                title="An enabled provider has no credential in this process environment",
                evidence="enabled provider(s) with an unset api_key_env: " + "; ".join(missing),
                proposal="Export the named variable(s) in the Gateway environment, or set that provider's `enabled: false` until a credential exists.",
                remediation="config.example.yaml documents each provider's `api_key_env`. Values are read from the environment, never from config.yaml.",
                authority="operator",
            )
        )


def _check_sandbox(config: Any, findings: list[Finding], context: dict[str, Any]) -> None:
    sandbox = getattr(config, "sandbox", None)
    use = str(getattr(sandbox, "use", "") or "")
    context["sandbox"] = {"use": use or None}
    if not use:
        findings.append(
            Finding(
                id="sandbox_not_configured",
                severity=Severity.WARNING,
                title="No sandbox provider is configured",
                evidence="config.yaml `sandbox.use` is empty, so file and shell tools have no execution boundary to resolve.",
                proposal="Set `sandbox.use` to a dotted provider path (the shipped template's local provider is the starting point).",
                remediation="/doctor runs an import + subclass smoke call for this value; config.example.yaml documents the schema.",
                authority="operator",
            )
        )
        return

    def probe() -> str:
        from alpha.reflection import resolve_class
        from alpha.sandbox.sandbox_provider import SandboxProvider

        return resolve_class(use, SandboxProvider).__name__

    resolved = _safe("sandbox", probe, findings)
    if isinstance(resolved, str):
        context["sandbox"]["provider_class"] = resolved


def _check_manifest(findings: list[Finding], context: dict[str, Any]) -> None:
    """The generated feature manifest must exist; unwired rows are real defects."""
    from alpha.workflow.registry.manifest_source import load_feature_manifest

    def probe() -> dict[str, Any]:
        return load_feature_manifest(refresh=True)

    manifest = _safe("feature_manifest", probe, findings)
    if manifest is None:
        findings.append(
            Finding(
                id="feature_manifest_missing",
                severity=Severity.WARNING,
                title="The generated feature manifest could not be read",
                evidence="contracts/feature_manifest.json is absent or unparsable (see the probe finding for the real error).",
                proposal="Run `make feature-manifest` from the repo root to regenerate contracts/feature_manifest.json.",
                remediation="scripts/check_generated_drift.py also fails on this; engine and wiring counts cannot be reported without it.",
                authority="developer",
            )
        )
        context["manifest"] = {"read": False}
        return

    counts = {section: len(manifest.get(section) or []) for section in ("tools", "routers", "middlewares", "loops", "engines")}
    context["manifest"] = {"read": True, "generated_at": manifest.get("generated_at"), "counts": counts}

    unwired: list[str] = []
    for section in ("routers", "middlewares", "loops"):
        for row in manifest.get(section) or []:
            if isinstance(row, dict) and row.get("wired") is not True:
                unwired.append(f"{section}:{row.get('id') or '(unnamed)'}")
    if unwired:
        findings.append(
            Finding(
                id="unwired_manifest_row",
                severity=Severity.WARNING,
                title="The manifest declares registry entries that no wiring point references",
                evidence=f"{len(unwired)} row(s): {', '.join(unwired[:8])}{' …' if len(unwired) > 8 else ''}",
                proposal="Add the missing import or config `use:` entry so the module is reachable, or remove the dead entry from the registry it was declared in.",
                remediation="backend/tests/test_feature_manifest_wiring.py fails on an unwired middleware; the same wiring point belongs here.",
                authority="developer",
            )
        )


def _check_capabilities(findings: list[Finding], include_disabled: bool) -> None:
    """Importable-but-disabled optional subsystems.

    Reported at ``info`` only. A disabled capability is an operator decision, and
    this module must not make an operator's deliberate choice read as a defect.
    """
    if not include_disabled:
        return

    def probe() -> list[dict[str, Any]]:
        from alpha.capabilities.catalog import CAPABILITY_CATALOG
        from alpha.capabilities.registry import load_enabled_capabilities

        enabled = set(load_enabled_capabilities())
        rows = []
        for capability_id, spec in sorted(CAPABILITY_CATALOG.items()):
            if capability_id in enabled:
                continue
            rows.append({"id": capability_id, "module": getattr(spec, "module", ""), "kind": getattr(spec, "kind", "")})
        return rows

    disabled = _safe("capabilities", probe, findings)
    if isinstance(disabled, list) and disabled:
        findings.append(
            Finding(
                id="disabled_capability",
                severity=Severity.INFO,
                title=f"{len(disabled)} optional subsystem(s) are importable but not enabled",
                evidence=", ".join(f"{row['id']} ({row['module']})" for row in disabled[:10]) + (" …" if len(disabled) > 10 else ""),
                proposal="No change is required. Enabling one is an operator choice under `config.yaml -> capabilities:`; each entry documents its own requirements.",
                remediation="GET /api/ops/integration-health reports live capability status; alpha.capabilities.catalog is the catalogue.",
                authority="operator",
            )
        )


def _check_command_coverage(findings: list[Finding], context: dict[str, Any]) -> None:
    """Catalog rows that resolve but have no bound handler.

    One aggregate finding, not one per row: the defect is that the shipped catalog
    advertises rows ``execute_slash_command_tool`` cannot run, and 400 separate
    findings would bury the 1 real question behind noise.
    """

    def probe() -> dict[str, Any]:
        from alpha.commands import command_registry

        rows = list(command_registry.list_commands())
        missing = [str(getattr(row, "command", "")) for row in rows if not command_registry.has_handler(str(getattr(row, "command", "") or ""))]
        return {"total": len(rows), "without_handler": missing}

    coverage = _safe("command_coverage", probe, findings)
    if not isinstance(coverage, dict):
        return
    total = int(coverage.get("total") or 0)
    missing = list(coverage.get("without_handler") or [])
    context["commands"] = {
        "total": total,
        "dispatchable": total - len(missing),
        "without_handler": len(missing),
        "note": "availability is gated on has_handler: execute_slash_command_tool returns status='unimplemented' for the rest, never success.",
    }
    if missing:
        findings.append(
            Finding(
                id="slash_command_without_handler",
                severity=Severity.INFO,
                title=f"{len(missing)} of {total} catalog commands have no bound handler",
                evidence=", ".join(missing[:10]) + (" …" if len(missing) > 10 else ""),
                proposal="No change is required to run Alpha. Either bind handlers in alpha.commands.backend_handlers, or mark the rows non-core so the catalog stops implying they run.",
                remediation="This is a catalog-honesty observation: the inventory reports each such command as unavailable with this reason, never as available.",
                authority="developer",
            )
        )


__all__ = [
    "SCHEMA_VERSION",
    "Diagnosis",
    "Finding",
    "Severity",
    "diagnose_configuration",
]
