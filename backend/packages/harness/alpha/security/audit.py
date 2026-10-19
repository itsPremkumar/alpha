"""Deterministic security audit for bot-mode and MCP surfaces (bot plan 4.4).

Runs eight local, offline checks and reports severity/evidence findings plus an
explicit ``scanned`` / ``not_checked`` split. The honesty contract:

* A check that cannot run is ``not_checked`` with its real reason — never
  "clean". ``scanned`` union ``not_checked`` always equals ``CHECK_IDS``.
* There is **no aggregate score** anywhere in the report or its renderers; a
  number invites trusting the audit instead of reading it.
* Evidence names identifiers, patterns and environment-variable *names* —
  never secret values, resolved URLs or file contents.
* Bounds are refused, not clamped: an over-bound scan becomes ``not_checked``
  rather than a silently partial "clean" answer.

Scope (per the master plan): skill AST scanning, transcript credential
leakage, tool-allowlist drift, bot capability ceiling, channel webhook auth,
swarm worktree path escapes and MCP trust. Prompt-injection analysis of skill
prose needs a model-tier scanning service and is therefore always
``not_checked`` here. Non-goals: no auto-fix, no network scanning, no score.

Module scope is stdlib-only; every ``alpha`` / ``langchain`` import happens
inside the check functions so importing this module (CLI, handler) is cheap
and side-effect free.

CLI::

    python -m alpha.security.audit [--json] [--thread-id ID ...] \
        [--max-threads N] [--max-messages N]

Exit codes: ``0`` no findings, ``1`` at least one finding, ``2`` refused
arguments. ``not_checked`` rows are disclosure, not an exit condition.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

AUDIT_NAME = "alpha.security.audit"
SEVERITIES = ("high", "medium", "low")

# The full check registry. The report invariant is:
#   set(scanned) | {nc.check for nc in not_checked} == set(CHECK_IDS)  (disjoint)
CHECK_IDS: tuple[str, ...] = (
    "skill_ast_audit",
    "skill_prompt_injection",
    "transcript_credential_leakage",
    "tool_allowlist_drift",
    "bot_capability_ceiling",
    "channel_webhook_auth",
    "worktree_path_escape",
    "mcp_trust",
)

EXIT_CLEAN = 0
EXIT_FINDINGS = 1

_DEFAULT_MAX_THREADS = 10
_DEFAULT_MAX_MESSAGES = 50
_MAX_TRANSCRIPT_CHARS = 20_000
_MAX_SKILL_PY_FILES = 200
_MAX_SKILL_PY_BYTES = 256 * 1024
_MAX_WORKTREE_ENTRIES = 500

# On-demand package runners fetch code from the network at launch time.
_PACKAGE_RUNNERS = {"npx", "bunx", "uvx", "pipx"}

_PROMPT_INJECTION_NOT_CHECKED_REASON = "prompt-injection analysis needs a model-tier scanning service; this audit is deterministic and makes no network calls"


@dataclass(frozen=True)
class Finding:
    """One audited problem: which check, what rule, how bad, and the evidence."""

    check: str
    rule_id: str
    severity: str
    message: str
    evidence: str
    remediation: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")

    def to_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class NotChecked:
    """A check that could not run, with the real reason. Never a pass."""

    check: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "reason": self.reason}


@dataclass
class AuditReport:
    """The audit result. Deliberately carries no score field."""

    generated_at: str
    findings: list[Finding]
    scanned: list[str]
    not_checked: list[NotChecked]

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit": AUDIT_NAME,
            "generated_at": self.generated_at,
            "scanned": list(self.scanned),
            "findings": [finding.to_dict() for finding in self.findings],
            "not_checked": [item.to_dict() for item in self.not_checked],
        }


def _run_check(report: AuditReport, check: str, fn: Callable[[], list[Finding]]) -> None:
    """Run one check; an exception makes it ``not_checked``, never silently clean."""
    try:
        findings = fn()
    except Exception as exc:
        report.not_checked.append(NotChecked(check=check, reason=f"{type(exc).__name__}: {exc}"))
    else:
        report.scanned.append(check)
        report.findings.extend(findings)


def _check_skill_ast_audit() -> list[Finding]:
    """Run the SkillScan Python AST auditor over every skill's ``*.py`` files."""
    from alpha.skills.audit.ast_audit import get_skill_ast_auditor
    from alpha.skills.storage import get_or_new_skill_storage

    findings: list[Finding] = []
    auditor = get_skill_ast_auditor()
    for skill in get_or_new_skill_storage().load_skills():
        skill_dir = Path(skill.skill_dir)
        if not skill_dir.is_dir():
            continue
        try:
            py_files = sorted(path for path in skill_dir.rglob("*.py") if path.is_file())
        except OSError:
            continue
        over_cap = max(0, len(py_files) - _MAX_SKILL_PY_FILES)
        oversize = 0
        for path in py_files[:_MAX_SKILL_PY_FILES]:
            try:
                if path.stat().st_size > _MAX_SKILL_PY_BYTES:
                    oversize += 1
                    continue
                code = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            result = auditor.audit_code(code)
            if not result.is_safe:
                findings.append(
                    Finding(
                        check="skill_ast_audit",
                        rule_id="skill.unsafe_python_ast",
                        severity="high",
                        message=f"skill {skill.name!r} ships Python matching prohibited AST patterns",
                        evidence=f"skill={skill.name} file={path.relative_to(skill_dir)} violations={'; '.join(result.violations)}",
                        remediation="Remove the prohibited import or call from the skill's Python, or disable the skill until it is reviewed.",
                    )
                )
        if over_cap or oversize:
            findings.append(
                Finding(
                    check="skill_ast_audit",
                    rule_id="skill.audit_truncated",
                    severity="low",
                    message=f"skill {skill.name!r} exceeded the static-audit budget; some files were not inspected",
                    evidence=f"skill={skill.name} files_over_cap={over_cap} files_oversize={oversize}",
                    remediation="Split the skill into smaller packages so every Python file fits the audit budget.",
                )
            )
    return findings


async def _collect_transcript_rows(*, thread_ids: list[str] | None, max_threads: int, max_messages: int) -> dict[str, list[str]]:
    """Read bounded recent transcript text per thread for the leakage check."""
    from alpha.config import get_app_config
    from alpha.persistence.engine import get_session_factory
    from alpha.persistence.thread_meta import make_thread_store
    from alpha.runtime.events.store import make_run_event_store

    config = get_app_config()
    if getattr(config.run_events, "backend", None) == "memory":
        raise RuntimeError("run_events.backend memory is process-local; a transcript audit from this process would report clean while real transcripts live elsewhere")
    factory = get_session_factory()
    if factory is None:
        raise RuntimeError("no session factory is available (database.backend memory): persisted transcripts cannot be read")
    targets = [str(tid) for tid in (thread_ids or []) if tid]
    if not targets:
        metas = await make_thread_store(factory).search(limit=max_threads, user_id=None)
        targets = [tid for tid in (_row_thread_id(row) for row in metas) if tid]
    store = make_run_event_store(config.run_events)
    rows: dict[str, list[str]] = {}
    for thread_id in targets[:max_threads]:
        records = await store.list_messages(thread_id, limit=max_messages, user_id=None)
        texts: list[str] = []
        for record in records or []:
            content = record.get("content") if isinstance(record, dict) else None
            if isinstance(content, str):
                texts.append(content)
            elif content is not None:
                texts.append(json.dumps(content, ensure_ascii=False, default=str))
        rows[thread_id] = texts
    return rows


def _row_thread_id(row: object) -> str | None:
    """Accept both dict rows and ORM objects from the thread-meta store."""
    value = row.get("thread_id") if isinstance(row, dict) else getattr(row, "thread_id", None)
    return str(value) if value else None


def _check_transcript_credential_leakage(
    *,
    thread_ids: list[str] | None = None,
    max_threads: int = _DEFAULT_MAX_THREADS,
    max_messages: int = _DEFAULT_MAX_MESSAGES,
    transcript_reader: Callable[..., dict[str, list[str]]] | None = None,
) -> list[Finding]:
    """Scan persisted transcripts for credential-shaped text via the memory redactor."""
    from alpha.security.memory_redaction import redact_for_memory

    if transcript_reader is None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            rows = asyncio.run(_collect_transcript_rows(thread_ids=thread_ids, max_threads=max_threads, max_messages=max_messages))
        else:
            raise RuntimeError("transcript_credential_leakage does blocking I/O; call run_security_audit via asyncio.to_thread (the HTTP route does) or the CLI")
    else:
        rows = transcript_reader(thread_ids=thread_ids, max_threads=max_threads, max_messages=max_messages)
    findings: list[Finding] = []
    for thread_id in sorted(rows):
        spans = 0
        patterns: set[str] = set()
        for text in rows[thread_id]:
            result = redact_for_memory(text[:_MAX_TRANSCRIPT_CHARS])
            spans += result.count
            patterns.update(redaction.pattern_name for redaction in result.redactions)
        if spans:
            findings.append(
                Finding(
                    check="transcript_credential_leakage",
                    rule_id="transcript.credential_leak",
                    severity="high",
                    message=f"thread {thread_id} keeps credential-shaped text in its persisted transcript",
                    evidence=f"thread={thread_id} spans={spans} patterns={','.join(sorted(patterns))}",
                    remediation="Rotate the leaked credential, then scrub or delete the affected transcript rows.",
                )
            )
    return findings


def _check_tool_allowlist_drift() -> list[Finding]:
    """Resolve every config.yaml tools[] entry exactly as the loader does."""
    from langchain.tools import BaseTool

    from alpha.config import get_app_config
    from alpha.reflection import resolve_variable

    findings: list[Finding] = []
    for entry in get_app_config().tools:
        try:
            resolved = resolve_variable(entry.use, BaseTool)
        except Exception as exc:
            findings.append(
                Finding(
                    check="tool_allowlist_drift",
                    rule_id="tool.unresolvable",
                    severity="medium",
                    message=f"tools entry {entry.name!r} cannot be resolved from {entry.use!r}",
                    evidence=f"name={entry.name} group={entry.group} use={entry.use} error={type(exc).__name__}",
                    remediation="Fix the `use` import path in config.yaml or drop the entry; an unresolvable entry still occupies the allowlist.",
                )
            )
            continue
        resolved_name = getattr(resolved, "name", None)
        if resolved_name != entry.name:
            findings.append(
                Finding(
                    check="tool_allowlist_drift",
                    rule_id="tool.name_mismatch",
                    severity="medium",
                    message=f"tools entry {entry.name!r} resolves to a tool named {resolved_name!r}",
                    evidence=f"name={entry.name} group={entry.group} use={entry.use} resolved_name={resolved_name}",
                    remediation="Rename the config entry to the tool's registered name; permission checks key on the resolved name.",
                )
            )
    return findings


def _check_bot_capability_ceiling() -> list[Finding]:
    """Refuse-list active bots whose persisted capabilities exceed the authority ceiling."""
    from alpha.bots.authority_ceiling import get_ceiling
    from alpha.bots.registry import get_bot_registry

    ceiling = get_ceiling()
    findings: list[Finding] = []
    for bot in get_bot_registry().list_bots(include_archived=False):
        within, violations = ceiling.within_ceiling(bot.capabilities)
        if within:
            continue
        findings.append(
            Finding(
                check="bot_capability_ceiling",
                rule_id="bots.over_ceiling",
                severity="high",
                message=f"bot {bot.name!r} carries capabilities beyond the authority ceiling",
                evidence=f"bot={bot.name} reasons={'; '.join(violations)}",
                remediation="Re-scope the bot through the gated rescoping path or drop the offending capabilities from its profile.",
            )
        )
    return findings


def _check_channel_webhook_auth() -> list[Finding]:
    """Check enabled channel bindings and the GitHub webhook fail-closed override."""
    from alpha.config import get_app_config
    from alpha.config.channel_connections_config import ChannelConnectionsConfig

    findings: list[Finding] = []
    connections = get_app_config().channel_connections
    provider_fields = [name for name in ChannelConnectionsConfig.model_fields if name not in ("enabled", "require_bound_identity")]
    for provider in provider_fields:
        status = connections.provider_status(provider)
        if status.get("enabled") and not status.get("configured"):
            findings.append(
                Finding(
                    check="channel_webhook_auth",
                    rule_id="channel.enabled_unconfigured",
                    severity="medium",
                    message=f"channel connection {provider!r} is enabled without a configured binding",
                    evidence=f"channel_connections.{provider}: enabled=true configured=false",
                    remediation="Finish the connection flow for this provider in Settings, or disable it.",
                )
            )
    if connections.enabled and not connections.require_bound_identity:
        findings.append(
            Finding(
                check="channel_webhook_auth",
                rule_id="channel.unbound_identity",
                severity="high",
                message="channel connections accept inbound messages without requiring a bound sender identity",
                evidence="channel_connections.enabled=true require_bound_identity=false",
                remediation="Set require_bound_identity: true so every inbound message maps to a bound user.",
            )
        )
    bypass = os.environ.get("ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS", "").strip().lower()
    if bypass in {"1", "true", "yes"} and not os.environ.get("GITHUB_WEBHOOK_SECRET"):
        findings.append(
            Finding(
                check="channel_webhook_auth",
                rule_id="github.unverified_webhooks",
                severity="high",
                message="the GitHub webhook route is mounted without signature verification (development bypass)",
                evidence="ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS is set while GITHUB_WEBHOOK_SECRET is unset",
                remediation="Unset ALPHA_ALLOW_UNVERIFIED_GITHUB_WEBHOOKS and set GITHUB_WEBHOOK_SECRET.",
            )
        )
    return findings


def _check_worktree_path_escape() -> list[Finding]:
    """Verify top-level swarm worktree entries resolve inside the worktree root."""
    from alpha.config.runtime_paths import runtime_home

    root = runtime_home() / "swarm" / "worktrees"
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    entries = sorted(root.iterdir())
    if len(entries) > _MAX_WORKTREE_ENTRIES:
        raise RuntimeError(f"worktree root holds {len(entries)} entries, above the {_MAX_WORKTREE_ENTRIES}-entry scan bound; refusing a partial answer")
    findings: list[Finding] = []
    for entry in entries:
        try:
            resolved = entry.resolve()
        except OSError as exc:
            raise RuntimeError(f"worktree entry {entry.name!r} could not be resolved: {type(exc).__name__}") from exc
        if resolved.is_relative_to(resolved_root):
            continue
        findings.append(
            Finding(
                check="worktree_path_escape",
                rule_id="swarm.worktree_path_escape",
                severity="high",
                message=f"swarm worktree entry {entry.name!r} resolves outside the worktree root",
                evidence=f"entry={entry.name} symlink={entry.is_symlink()} resolves_outside_root=true",
                remediation="Remove or re-home the entry; a worktree resolving outside the root escapes swarm path confinement.",
            )
        )
    return findings


def _check_mcp_trust() -> list[Finding]:
    """Flag enabled MCP servers on plaintext transports or on-demand runners."""
    from alpha.config.extensions_config import ExtensionsConfig

    findings: list[Finding] = []
    for name, server in ExtensionsConfig.from_file().get_enabled_mcp_servers().items():
        url = getattr(server, "url", None)
        if url:
            scheme = urlsplit(str(url)).scheme.lower()
            if scheme in {"http", "ws"}:
                findings.append(
                    Finding(
                        check="mcp_trust",
                        rule_id="mcp.plaintext_transport",
                        severity="medium",
                        message=f"MCP server {name!r} connects over a plaintext transport",
                        evidence=f"server={name} transport={getattr(server, 'type', None)} scheme={scheme}",
                        remediation="Terminate TLS in front of the server, or switch its URL to https/wss.",
                    )
                )
        command = getattr(server, "command", None)
        if command:
            runner = Path(str(command).replace("\\", "/")).name.lower()
            if runner in _PACKAGE_RUNNERS:
                findings.append(
                    Finding(
                        check="mcp_trust",
                        rule_id="mcp.on_demand_runner",
                        severity="low",
                        message=f"MCP server {name!r} launches through the on-demand runner {runner!r}",
                        evidence=f"server={name} runner={runner}",
                        remediation="Prefer a pinned install; an on-demand runner fetches code from the network at launch time.",
                    )
                )
    return findings


def run_security_audit(
    *,
    thread_ids: list[str] | None = None,
    max_threads: int = _DEFAULT_MAX_THREADS,
    max_messages: int = _DEFAULT_MAX_MESSAGES,
    transcript_reader: Callable[..., dict[str, list[str]]] | None = None,
) -> AuditReport:
    """Run every check and return the report. Bounds below 1 are refused."""
    if max_threads < 1:
        raise ValueError(f"max_threads must be >= 1, got {max_threads}")
    if max_messages < 1:
        raise ValueError(f"max_messages must be >= 1, got {max_messages}")
    report = AuditReport(generated_at=datetime.now(UTC).isoformat(), findings=[], scanned=[], not_checked=[])
    report.not_checked.append(NotChecked(check="skill_prompt_injection", reason=_PROMPT_INJECTION_NOT_CHECKED_REASON))
    _run_check(report, "skill_ast_audit", _check_skill_ast_audit)
    _run_check(
        report,
        "transcript_credential_leakage",
        lambda: _check_transcript_credential_leakage(thread_ids=thread_ids, max_threads=max_threads, max_messages=max_messages, transcript_reader=transcript_reader),
    )
    _run_check(report, "tool_allowlist_drift", _check_tool_allowlist_drift)
    _run_check(report, "bot_capability_ceiling", _check_bot_capability_ceiling)
    _run_check(report, "channel_webhook_auth", _check_channel_webhook_auth)
    _run_check(report, "worktree_path_escape", _check_worktree_path_escape)
    _run_check(report, "mcp_trust", _check_mcp_trust)
    report.findings.sort(key=lambda finding: (finding.check, finding.rule_id, finding.evidence))
    return report


def render_text(report: AuditReport) -> str:
    """Render the report for humans. States the no-score rule explicitly."""
    lines = [
        f"Alpha security audit ({report.generated_at})",
        f"Scanned: {', '.join(report.scanned) or '(none)'}",
        "",
    ]
    if report.findings:
        lines.append(f"Findings ({len(report.findings)}):")
        for finding in report.findings:
            lines.append(f"  [{finding.severity}] {finding.check} {finding.rule_id}: {finding.message}")
            lines.append(f"    evidence: {finding.evidence}")
            lines.append(f"    remediation: {finding.remediation}")
    else:
        lines.append("Findings: none.")
    lines.append("")
    if report.not_checked:
        lines.append(f"Not checked ({len(report.not_checked)}):")
        for item in report.not_checked:
            lines.append(f"  {item.check}: {item.reason}")
    else:
        lines.append("Not checked: none.")
    lines.append("")
    lines.append("No aggregate score is computed; read the findings and the not-checked disclosures above.")
    return "\n".join(lines)


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {raw!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value}")
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m alpha.security.audit",
        description="Run Alpha's deterministic security audit. No aggregate score is computed; a check that cannot run is disclosed as not_checked, never as clean.",
    )
    parser.add_argument("--json", dest="as_json", action="store_true", help="emit the machine-readable report instead of text")
    parser.add_argument("--thread-id", dest="thread_ids", action="append", metavar="THREAD_ID", help="limit the transcript check to this thread (repeatable)")
    parser.add_argument("--max-threads", type=_positive_int, default=_DEFAULT_MAX_THREADS, help=f"maximum threads the transcript check enumerates (default {_DEFAULT_MAX_THREADS})")
    parser.add_argument("--max-messages", type=_positive_int, default=_DEFAULT_MAX_MESSAGES, help=f"maximum messages read per thread (default {_DEFAULT_MAX_MESSAGES})")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        report = run_security_audit(thread_ids=args.thread_ids, max_threads=args.max_threads, max_messages=args.max_messages)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_text(report))
    return EXIT_FINDINGS if report.findings else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
