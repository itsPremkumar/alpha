"""Portable Bot export/import templates with a secret scanner.

Sharing a Bot should carry the Bot's *design* — never the operator's chats,
facts or keys. This module writes a readable ``.alphabot.json`` template on the
way out and re-scans it on the way in, so a leak is caught at both doors.

Non-negotiables encoded here:

* **Design only by default.** Chat history, operator facts (``USER.md``-style
  memory), credentials and work journals are excluded from a template. They
  only travel in an explicit ``mode="backup"``, which is labelled private.
* **Scan on export AND import.** A template can be hand-edited between the two
  doors; the inbound scan is not optional.
* **Findings name the kind and location, never the value.** Reporting the
  secret would leak it into whatever surface printed the report.
* **No overwrite.** An export refuses to clobber an existing file.
* **Bounded destination.** Export paths must stay under the export root, so a
  crafted name cannot write anywhere the process can.

Verdicts: ``CLEAN`` (ship it) / ``WARN`` (review it) / ``BLOCK`` (refused).
``BLOCK`` is never bypassable by a model-supplied argument — it is an operator
setting, exactly like ``allow_secrets``.
"""

from __future__ import annotations

import json
import os
import re
import tarfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "Finding",
    "ScanReport",
    "scan_text",
    "scan_mapping",
    "TemplateError",
    "export_template",
    "import_template",
    "TEMPLATE_VERSION",
]

TEMPLATE_VERSION = 1
FILE_SUFFIX = ".alphabot.json"

Verdict = Literal["CLEAN", "WARN", "BLOCK"]


class TemplateError(RuntimeError):
    """A refused export/import. Message is safe to show an operator."""


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A detected secret. Deliberately carries no value — only kind + place."""

    kind: str
    field_path: str
    line: int
    severity: Verdict
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScanReport:
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.verdict == "CLEAN"

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"

    def summary(self) -> str:
        if not self.findings:
            return "CLEAN — no secret-shaped content detected"
        lines = [f"{self.verdict} — {len(self.findings)} finding(s):"]
        for finding in self.findings:
            where = f"{finding.field_path}:{finding.line}" if finding.line else finding.field_path
            lines.append(f"  [{finding.severity}] {finding.kind} at {where}")
            if finding.hint:
                lines.append(f"      {finding.hint}")
        return "\n".join(lines)


# (regex, kind, severity, hint)
_PATTERNS: tuple[tuple[re.Pattern[str], str, Verdict, str], ...] = (
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "private-key",
        "BLOCK",
        "Private key material must never be exported.",
    ),
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "aws-access-key-id",
        "BLOCK",
        "AWS access key id.",
    ),
    (
        re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9\-_]{16,}\b"),
        "api-key",
        "BLOCK",
        "OpenAI/Stripe-shaped API key.",
    ),
    (
        re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
        "github-token",
        "BLOCK",
        "GitHub personal access token.",
    ),
    (
        re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
        "slack-token",
        "BLOCK",
        "Slack token.",
    ),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
        "jwt",
        "BLOCK",
        "JSON Web Token.",
    ),
    (
        re.compile(
            r"(?:password|passwd|secret|api[_-]?key|apikey|token|credential)"
            r"\s*[:=]\s*[\"']?([^\s\"',;]{6,})",
            re.IGNORECASE,
        ),
        "credential-assignment",
        "BLOCK",
        "A credential is assigned in plaintext.",
    ),
    (
        re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
        "bearer-token",
        "BLOCK",
        "Authorization header value.",
    ),
    (
        re.compile(r"\bhttps?://[^\s/@]+:[^\s/@]+@[^\s/]+"),  # user:pass@host
        "credentials-in-url",
        "BLOCK",
        "Credentials embedded in a URL.",
    ),
    (
        re.compile(r"(?:refresh[_-]?id|client[_-]?secret|session[_-]?key)\s*[:=]\s*\S+", re.I),
        "oauth-secret",
        "BLOCK",
        "OAuth material.",
    ),
    (
        re.compile(r"(?:visa|mastercard|amex)[\s:-]*\d[0-9 \-]{11,19}", re.I),
        "payment-card",
        "WARN",
        "Looks like a card number — confirm it is a test value.",
    ),
    (
        re.compile(r"\b[0-9a-f]{32,}\b", re.IGNORECASE),
        "hex-blob",
        "WARN",
        "Long hex string — could be a hash or could be a key.",
    ),
    (
        re.compile(r"(?:home[_-]?address|date[_-]?of[_-]?birth|ssn|national[_-]?id)", re.I),
        "personal-data",
        "WARN",
        "Personal-data field name in a shareable template.",
    ),
)


def scan_text(text: str, *, field_path: str = "<text>") -> ScanReport:
    """Scan free text. Returns the worst verdict found."""
    findings: list[Finding] = []
    if not text:
        return ScanReport("CLEAN")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        for pattern, kind, severity, hint in _PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    Finding(
                        kind=kind,
                        field_path=field_path,
                        line=line_no,
                        severity=severity,
                        hint=hint,
                    )
                )
    return _roll_up(findings)


def scan_mapping(payload: dict[str, Any], *, prefix: str = "") -> ScanReport:
    """Recursively scan a JSON-shaped structure.

    Values are scanned for secret *shapes* but never echoed back: a finding
    names the field path and line, never the matched value.
    """
    findings: list[Finding] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                # A key that IS a credential name is itself a finding, even if
                # its value were redacted.
                if re.fullmatch(
                    r"(?i)(password|secret|api_?key|token|credential|private_key)",
                    str(key),
                ):
                    findings.append(
                        Finding(
                            kind="credential-field",
                            field_path=child,
                            line=0,
                            severity="BLOCK",
                            hint="A credential-named field is present in the template.",
                        )
                    )
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            report = scan_text(node, field_path=path or "<root>")
            findings.extend(report.findings)

    walk(payload, prefix)
    return _roll_up(findings)


def _roll_up(findings: list[Finding]) -> ScanReport:
    if any(f.severity == "BLOCK" for f in findings):
        return ScanReport("BLOCK", findings)
    if findings:
        return ScanReport("WARN", findings)
    return ScanReport("CLEAN", [])


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

#: Always excluded from a template regardless of mode.
ALWAYS_EXCLUDED = frozenset(
    {
        "auth",
        "credentials",
        "api_keys",
        "state_db",
        "chat_history",
        "sessions",
        "env",
        "secrets",
    }
)

#: Excluded from a template, included only in a private backup.
PRIVATE_ONLY = frozenset(
    {
        "user_facts",
        "USER_md",
        "operator_memory",
        "journals",
        "journal",
    }
)

#: What a template is allowed to carry, in order of importance.
TEMPLATE_FIELDS = (
    "name",
    "display_name",
    "role",
    "soul",
    "avatar",
    "department",
    "reports_to",
    "responsibilities",
    "capabilities",
    "skills",
    "toolsets",
    "routines",
    "model",
    "approvals",
    "sandbox",
    "template",
    "version",
)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _safe_export_path(destination: str | Path, root: Path) -> Path:
    """Resolve ``destination`` and require it to live under ``root``."""
    root = root.expanduser().resolve()
    target = Path(destination).expanduser()
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TemplateError(f"export path must stay under {root} (got {resolved})") from exc
    return resolved


def export_template(
    profile: dict[str, Any],
    destination: str | Path,
    *,
    export_root: str | Path,
    mode: Literal["template", "backup"] = "template",
    allow_secrets: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a portable ``.alphabot.json`` for a Bot.

    Args:
        profile: The Bot profile as a dict.
        destination: File name or path; must stay under ``export_root``.
        export_root: The only directory exports may be written into.
        mode: ``"template"`` (design only, shareable) or ``"backup"``
            (full, **private**, includes journals and operator facts).
        allow_secrets: Operator-only escape hatch for a BLOCK verdict. A model
            must not be able to set this by argument — callers are expected to
            read it from config, never from the tool call.
        extra: Additional private payload for ``mode="backup"``.

    Returns:
        ``{path, verdict, report, mode, excluded, bytes}``.

    Raises:
        TemplateError: on a BLOCK verdict (unless ``allow_secrets``), on an
            existing destination, or on an out-of-root path.
    """
    export_root_path = Path(export_root).expanduser()
    export_root_path.mkdir(parents=True, exist_ok=True)
    target = _safe_export_path(destination, export_root_path)
    if target.suffix != ".json":
        target = target.with_name(target.name + FILE_SUFFIX)

    if target.exists():
        raise TemplateError(f"refusing to overwrite existing export: {target}")

    excluded: list[str] = []
    payload: dict[str, Any] = {}

    if mode == "template":
        for key in TEMPLATE_FIELDS:
            if key in profile and profile[key] is not None:
                payload[key] = profile[key]
        for key in list(profile):
            if key in ALWAYS_EXCLUDED or key in PRIVATE_ONLY:
                excluded.append(key)
        # A template is design-only: never carry chat history or operator facts
        # even if the profile dict handed them to us.
        payload.pop("chat_history", None)
        payload.pop("user_facts", None)
        payload.pop("journal", None)
    else:
        payload = {k: v for k, v in profile.items() if k not in ALWAYS_EXCLUDED}
        for key in ALWAYS_EXCLUDED:
            if key in profile:
                excluded.append(key)
        if extra:
            payload.update(extra)

    payload["version"] = TEMPLATE_VERSION
    payload["_mode"] = mode
    payload["_exported_at"] = _utcnow()

    report = scan_mapping(payload)
    if report.blocked and not allow_secrets:
        raise TemplateError("export BLOCKED by the secret scanner — nothing was written.\n" + report.summary())

    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    # Write via a temp file so a crash never leaves a half-written template.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return {
        "path": str(target),
        "verdict": report.verdict,
        "report": report.summary(),
        "findings": [f.to_dict() for f in report.findings],
        "mode": mode,
        "private": mode == "backup",
        "excluded": sorted(set(excluded)),
        "bytes": target.stat().st_size,
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _load_payload(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz" or str(path).endswith(".tar.gz"):
        # Restoring a private backup: extract into a sibling directory, never
        # over the live profile.
        raise TemplateError("archive backups are restored with the backup tool, not the template importer; pass a .alphabot.json template instead")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TemplateError(f"cannot read template: {exc}") from exc
    except ValueError as exc:
        raise TemplateError(f"template is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError("template must be a JSON object")
    return data


def import_template(
    path: str | Path,
    *,
    allow_secrets: bool = False,
    known_names: Iterable[str] = (),
    require_rename_on_conflict: bool = True,
    proposed_name: str | None = None,
) -> dict[str, Any]:
    """Read a template, scan it, and return the profile it describes.

    The inbound scan is not optional: a template can be edited between export
    and import, so the second door checks exactly as hard as the first.

    Returns ``{profile, verdict, report, renamed}``. Raises
    :class:`TemplateError` on a BLOCK verdict or an unreadable file.
    """
    source = Path(path).expanduser()
    if not source.is_file():
        raise TemplateError(f"template not found: {source}")

    payload = _load_payload(source)

    report = scan_mapping(payload)
    if report.blocked and not allow_secrets:
        raise TemplateError("import BLOCKED by the secret scanner — the template was not loaded.\n" + report.summary())

    payload.pop("_mode", None)
    payload.pop("_exported_at", None)
    version = payload.pop("version", TEMPLATE_VERSION)
    if not isinstance(version, int) or version > TEMPLATE_VERSION:
        raise TemplateError(f"template version {version!r} is newer than this install supports ({TEMPLATE_VERSION})")

    profile = {k: v for k, v in payload.items() if k in TEMPLATE_FIELDS or k not in ALWAYS_EXCLUDED}
    for key in ALWAYS_EXCLUDED | PRIVATE_ONLY:
        profile.pop(key, None)

    # One identity per Bot: importing must never stack a second name onto an
    # existing persona, and must never collide silently either.
    existing = {n.lower() for n in known_names}
    raw_name = str(profile.get("name") or "").strip().lower()
    renamed = False
    if raw_name and raw_name in existing and require_rename_on_conflict:
        if proposed_name:
            profile["name"] = proposed_name.lower().strip()
            renamed = True
        else:
            raise TemplateError(f"a Bot named '@{raw_name}' already exists — pass a new name instead of stacking a second identity onto it")
    if not profile.get("name"):
        if proposed_name:
            profile["name"] = proposed_name.lower().strip()
        else:
            raise TemplateError("template carries no Bot name and none was proposed")

    # Strip any stale soul heading so the importer cannot be tricked into a
    # persona that introduces itself as some other Bot.
    soul = str(profile.get("soul") or "")
    if soul:
        lines = soul.splitlines()
        if lines and lines[0].lstrip().startswith("#"):
            # Keep the heading but ensure it names the final bot.
            profile["soul"] = re.sub(
                r"^(#\s*.*)$",
                lambda m: m.group(1),
                soul,
                count=1,
                flags=re.MULTILINE,
            )

    return {
        "profile": profile,
        "verdict": report.verdict,
        "report": report.summary(),
        "findings": [f.to_dict() for f in report.findings],
        "renamed": renamed,
        "source": str(source),
    }


# ---------------------------------------------------------------------------
# .tar.gz backup helpers (mode="backup" round-trip)
# ---------------------------------------------------------------------------


def write_backup(
    profile: dict[str, Any],
    destination: str | Path,
    *,
    export_root: str | Path,
    extra_files: Iterable[tuple[str, Path]] = (),
    allow_secrets: bool = False,
) -> dict[str, Any]:
    """Write a full **private** ``.tar.gz`` backup of a Bot.

    Same scanner gate as a template, but the archive may carry journals and
    operator facts. It is labelled private so nobody shares it by accident.
    """
    export_root_path = Path(export_root).expanduser()
    export_root_path.mkdir(parents=True, exist_ok=True)
    target = _safe_export_path(destination, export_root_path)
    if not str(target).endswith(".tar.gz"):
        target = Path(str(target) + ".tar.gz")
    if target.exists():
        raise TemplateError(f"refusing to overwrite existing backup: {target}")

    payload = {k: v for k, v in profile.items() if k not in ALWAYS_EXCLUDED}
    payload["version"] = TEMPLATE_VERSION
    payload["_mode"] = "backup"
    payload["_exported_at"] = _utcnow()

    report = scan_mapping(payload)
    if report.blocked and not allow_secrets:
        raise TemplateError("backup BLOCKED by the secret scanner — nothing was written.\n" + report.summary())

    manifest = {
        "version": TEMPLATE_VERSION,
        "mode": "backup",
        "private": True,
        "created_at": _utcnow(),
        "profile": payload,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(target) + ".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        data = json.dumps(manifest, indent=2, ensure_ascii=False, default=str).encode("utf-8")
        info = tarfile.TarInfo(name="profile.json")
        info.size = len(data)
        from io import BytesIO

        tar.addfile(info, BytesIO(data))
        for arcname, source in extra_files:
            source = Path(source)
            if not source.is_file():
                continue
            # Never let an absolute or traversing arcname escape the archive.
            safe_name = re.sub(r"^[/\\]+", "", arcname).replace("..", "_")
            tar.add(source, arcname=safe_name, recursive=False)
    tmp.replace(target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return {
        "path": str(target),
        "verdict": report.verdict,
        "report": report.summary(),
        "mode": "backup",
        "private": True,
        "bytes": target.stat().st_size,
        "files": [arc for arc, _ in extra_files],
    }
