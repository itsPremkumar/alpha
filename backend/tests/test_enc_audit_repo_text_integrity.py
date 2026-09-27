"""Repository text-encoding integrity gate for the files this audit owns.

The damage this hunts is *invisible to a plain decode*. Mojibake is UTF-8 that
was decoded as cp1252 and re-saved, so ``bytes.decode("utf-8")`` still succeeds
and every build still passes; a C0 control byte that ate the first letter of a
word is a valid character in a string; a BOM in front of a ``.py`` file is
invisible to the interpreter. Each class below therefore needs its own
detector, and each detector needs proof that it is not vacuous -- a scan that
silently stopped looking is indistinguishable from a clean repository.

Detected classes
----------------
``invalid_utf8``      ``bytes.decode("utf-8")`` raises outright.
``stray_bom``         UTF-8 BOM on a source file that must not carry one.
``mixed_eol``         More than one line-terminator style in one file, or a CR
                      that is not part of a CRLF (an interpreted ``\\r``).
``mojibake``          A maximal non-ASCII run that round-trips through cp1252
                      into valid UTF-8 -- i.e. was decoded as cp1252.
``u_fffd``            U+FFFD, a decode that already lost information.
``unpaired_quote``    A U+201D that closes no U+201C (stateful across the
                      whole file, so a quote spanning lines is legal).
``unclosed_quote``    A U+201C still open at end of file.
``stray_control``     C0/DEL outside TAB/LF/CR.
``inline_tab``        A TAB after non-whitespace on a line, which is how an
                      eaten ``t`` renders.

Exemptions
----------
``.ps1`` keeps its UTF-8 BOM: PowerShell 5.1 requires it, and a stripped BOM
breaks the script. Non-source names (``.gitignore``, ``LICENSE``, ``Makefile``)
are never BOM-stripped either. Both are asserted as positive controls, so the
exemptions cannot rot into blanket immunity.

``OWNERSHIP_EXEMPTIONS`` records files that still carry damage this audit
detected but must not edit (another agent owns the path). Each entry names a
reason, and ``test_ownership_exemptions_are_not_stale`` fails once an exempted
path comes clean, so the list cannot become a permanent blanket.
"""

from __future__ import annotations

import bisect
import re
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DETECTOR_PATH = "backend/tests/test_enc_audit_repo_text_integrity.py"

# --------------------------------------------------------------------------- #
# Surface: exactly the text this audit owns. Anything else is reported, never
# edited, and never asserted clean -- see the charter that produced this file.
# --------------------------------------------------------------------------- #

TEXT_SUFFIXES = frozenset(
    {
        ".bat",
        ".cfg",
        ".cjs",
        ".cmd",
        ".css",
        ".example",
        ".html",
        ".ini",
        ".js",
        ".json",
        ".jsx",
        ".markdown",
        ".md",
        ".mjs",
        ".ps1",
        ".py",
        ".rst",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".vbs",
        ".yaml",
        ".yml",
    }
)

# Extensionless text files we still want read, and never want a BOM stripped.
TEXT_BASENAMES = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".gitmessage",
        "CODEOWNERS",
        "Dockerfile",
        "LICENSE",
        "Makefile",
    }
)

# Repo-root files this audit owns.
OWNED_ROOT_SUFFIXES = frozenset({".bat", ".json", ".markdown", ".md", ".mjs", ".ps1", ".sh", ".txt", ".yaml", ".yml"})

# Directories owned in full.
OWNED_DIRS = ("contracts/", "docs/", "examples/", "installer/", "references/", "skills/")

# backend/** text, minus the trees other owners hold.
OWNED_BACKEND_SUFFIXES = frozenset({".markdown", ".md", ".py"})

# Trees and files other agents own: never edited, never asserted clean.
FOREIGN_PREFIXES = (
    ".github/workflows/",
    "backend/app/channels/",
    "backend/app/gateway/",
    "backend/packages/harness/alpha/memory/",
    "backend/packages/harness/alpha/persistence/",
    "backend/packages/harness/alpha/runtime/",
    "backend/packages/harness/alpha/state/",
    "deploy/",
    "docker/",
    "electron/",
    "scripts/",
)
FOREIGN_FILES = frozenset({"backend/Dockerfile", "backend/packages/harness/alpha/config/paths.py", "Makefile"})
FOREIGN_ROOT_STEMS = ("start.", "stop.")

# --------------------------------------------------------------------------- #
# Exemptions with a reason. Kept minimal on purpose; the stale guard below
# forces an entry to be deleted as soon as the file comes clean.
# --------------------------------------------------------------------------- #

OWNERSHIP_EXEMPTIONS: dict[str, str] = {
    "BUG_FIX_BOARD.md": "owned by the integrator as the merge surface; this audit reports its sites and does not edit it",
    "backend/packages/harness/alpha/groups/AGENTS.md": ("an AGENTS.md other agents may be editing; the whole file is UTF-16LE, so any repair is a full re-encode rather than a surgical edit"),
    "backend/tests/test_system_one_wiring.py": (
        "mixed EOL is a working-tree artifact: .gitattributes sets '* text=auto eol=lf', so the committed blob is LF and this checkout is CRLF for lines 1-939 only. "
        "Unifying it rewrites 87 line breaks, which this audit's charter forbids as a non-surgical change"
    ),
}

# A UTF-8 BOM is required on .ps1 (PowerShell 5.1) and never removed from a
# non-source name. These two sets are the positive controls for that promise.
BOM_REQUIRED_SUFFIXES = frozenset({".ps1"})
BOM_EXEMPT_BASENAMES = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".gitattributes",
        ".gitignore",
        ".gitmessage",
        "CODEOWNERS",
        "Dockerfile",
        "LICENSE",
        "Makefile",
    }
)

BOM_FORBIDDEN_SUFFIXES = TEXT_SUFFIXES - BOM_REQUIRED_SUFFIXES

# --------------------------------------------------------------------------- #
# Character classes
# --------------------------------------------------------------------------- #

BOM_BYTES = b"\xef\xbb\xbf"

# Everything in C0 except TAB/LF/CR, plus DEL. TAB gets its own detector
# because a TAB after non-whitespace is how an eaten "t" renders, while a TAB
# at the start of a line is ordinary indentation.
FORBIDDEN_CONTROLS = frozenset(chr(code) for code in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F))

LEFT_DOUBLE_QUOTE = "“"
RIGHT_DOUBLE_QUOTE = "”"
# Written as an escape, not a literal: a literal U+FFFD in this file would be
# a u_fffd finding against the file that defines the rule.
REPLACEMENT_CHARACTER = "\ufffd"

# Compiled once: the per-file detectors run over ~3k tracked text files, so a
# per-character Python loop costs minutes of wall clock. Every pattern below is
# the exact equivalent of the character test it replaces, at C speed.
_NON_ASCII_RUN = re.compile("[^\\x00-\\x7f]+")
_NEWLINE = re.compile("\n")
_REPLACEMENT = re.compile(REPLACEMENT_CHARACTER)
_FORBIDDEN_CONTROL = re.compile("[" + "".join(f"\\x{ord(char):02x}" for char in sorted(FORBIDDEN_CONTROLS)) + "]")
_TAB = re.compile("\t")


def _cp1252_byte(char: str) -> bytes | None:
    """Return the single byte a cp1252 decoder would have produced for *char*."""
    try:
        return char.encode("cp1252")
    except UnicodeEncodeError:
        code = ord(char)
        # The undefined cp1252 slots decode to the matching latin-1 controls
        # (0x81 -> U+0081 and friends), so recover the original byte value.
        return bytes((code,)) if code <= 0xFF else None


def cp1252_round_trip(run: str) -> str | None:
    """Re-decode a non-ASCII *run* the way a cp1252 round trip would have.

    Returns the recovered text when the run is exactly the UTF-8 encoding of
    something that was decoded as cp1252, and ``None`` otherwise -- including
    for text that merely happens to be non-ASCII.
    """
    if len(run) < 2 or all(ord(char) <= 0x7F for char in run):
        return None
    raw = bytearray()
    for char in run:
        encoded = _cp1252_byte(char)
        if encoded is None:
            return None
        raw += encoded
    try:
        recovered = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        return None
    if recovered == run or not any(ord(char) > 0x7F for char in recovered):
        return None
    return recovered


def non_ascii_runs(text: str) -> list[tuple[int, str]]:
    """Maximal runs of non-ASCII characters in *text*, with their start index."""
    return [(match.start(), match.group()) for match in _NON_ASCII_RUN.finditer(text)]


def describe(char: str) -> str:
    return f"U+{ord(char):04X}"


def line_starts(text: str) -> list[int]:
    """Offset of the first character of every line, so an index maps to a line."""
    starts = [0]
    starts += [match.end() for match in _NEWLINE.finditer(text)]
    return starts


def line_of(starts: list[int], index: int) -> int:
    return bisect.bisect_right(starts, index)


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}  {self.kind}: {self.detail}"


# --------------------------------------------------------------------------- #
# Per-file detectors. Each takes a repo-relative POSIX path and the raw bytes,
# so a fixture in a tmp_path exercises exactly the code the repo scan uses.
# --------------------------------------------------------------------------- #


def _detect_mojibake(path: str, text: str, starts: list[int]) -> Iterator[Finding]:
    for index, run in non_ascii_runs(text):
        recovered = cp1252_round_trip(run)
        if recovered is not None:
            yield Finding(path, line_of(starts, index), "mojibake", f"{run!r} is UTF-8 read as cp1252; it should be {recovered!r}")


def _detect_replacement_characters(path: str, text: str, starts: list[int]) -> Iterator[Finding]:
    for match in _REPLACEMENT.finditer(text):
        yield Finding(path, line_of(starts, match.start()), "u_fffd", "U+FFFD; a decode already lost this character")


def _detect_curly_quotes(path: str, text: str) -> Iterator[Finding]:
    """Stateful across the whole file, so a quote that spans lines is legal."""
    open_at: int | None = None
    for number, line in enumerate(text.split("\n"), 1):
        if LEFT_DOUBLE_QUOTE not in line and RIGHT_DOUBLE_QUOTE not in line:
            continue
        for char in line:
            if char == LEFT_DOUBLE_QUOTE:
                if open_at is None:
                    open_at = number
            elif char == RIGHT_DOUBLE_QUOTE:
                if open_at is None:
                    yield Finding(path, number, "unpaired_quote", f"{describe(char)} closes no {describe(LEFT_DOUBLE_QUOTE)}")
                else:
                    open_at = None
    if open_at is not None:
        yield Finding(path, open_at, "unclosed_quote", f"{describe(LEFT_DOUBLE_QUOTE)} opened here is never closed")


def _detect_stray_controls(path: str, text: str, starts: list[int]) -> Iterator[Finding]:
    for match in _FORBIDDEN_CONTROL.finditer(text):
        line = line_of(starts, match.start())
        column = match.start() - starts[line - 1] + 1
        yield Finding(path, line, "stray_control", f"column {column} holds {describe(match.group())}")


def _detect_inline_tabs(path: str, text: str, starts: list[int]) -> Iterator[Finding]:
    """A TAB with non-whitespace before it on the same line is an eaten "t".

    Anchored on the TAB and scanning back to the line start, rather than
    matching ``\\S\\s*\\t``: that pattern backtracks quadratically over a long
    run of indentation, which this corpus has plenty of.
    """
    for match in _TAB.finditer(text):
        line = line_of(starts, match.start())
        line_start = starts[line - 1]
        before = text[line_start : match.start()]
        if before.strip():
            yield Finding(path, line, "inline_tab", f"column {match.start() - line_start + 1} holds U+0009 after {before.strip()[-24:]!r}")


def _detect_stray_bom(path: str, raw: bytes) -> Iterator[Finding]:
    if not raw.startswith(BOM_BYTES):
        return
    name = path.rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    if suffix in BOM_REQUIRED_SUFFIXES or name in BOM_EXEMPT_BASENAMES:
        return
    if suffix in BOM_FORBIDDEN_SUFFIXES:
        yield Finding(path, 1, "stray_bom", f"file starts with {BOM_BYTES.hex(' ').upper()} (UTF-8 BOM)")


def _detect_mixed_line_endings(path: str, raw: bytes) -> Iterator[Finding]:
    """More than one line-terminator style, or a CR that is not part of a CRLF.

    A bare CR inside an otherwise LF-only file is how an interpreted ``\\r``
    escape renders, so it counts even when the file holds no CRLF at all.
    """
    crlf = raw.count(b"\r\n")
    lf_only = raw.count(b"\n") - crlf
    cr_only = raw.count(b"\r") - crlf
    styles = sum(1 for count in (crlf, lf_only, cr_only) if count)
    if styles > 1 or cr_only:
        yield Finding(path, 1, "mixed_eol", f"{crlf} CRLF, {lf_only} bare LF, {cr_only} bare CR in one file")


def scan_bytes(path: str, raw: bytes) -> list[Finding]:
    """Every finding for one file. Returns ``[]`` for a clean file."""
    if not raw.startswith(BOM_BYTES):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return [Finding(path, 1, "invalid_utf8", f"{exc.reason} at byte {exc.start}")]
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            return [Finding(path, 1, "invalid_utf8", f"{exc.reason} at byte {exc.start}")]

    findings = list(_detect_stray_bom(path, raw))
    findings += _detect_mixed_line_endings(path, raw)
    # Every remaining class needs a non-ASCII character, a TAB, or a forbidden
    # control, so text with none of the three cannot hold one. TAB is excluded
    # from _FORBIDDEN_CONTROLS (it is legal indentation) and DEL is inside it,
    # so this one search plus one memchr is the whole guard. All C-speed, and
    # most of the corpus is such text.
    if text.isascii() and "\t" not in text and _FORBIDDEN_CONTROL.search(text) is None:
        return findings
    starts = line_starts(text)
    findings += _detect_mojibake(path, text, starts)
    findings += _detect_replacement_characters(path, text, starts)
    findings += _detect_curly_quotes(path, text)
    findings += _detect_stray_controls(path, text, starts)
    findings += _detect_inline_tabs(path, text, starts)
    return findings


# --------------------------------------------------------------------------- #
# Repo surface
# --------------------------------------------------------------------------- #


def is_text_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return Path(name).suffix.lower() in TEXT_SUFFIXES or name in TEXT_BASENAMES


def is_owned(path: str) -> bool:
    """True when this audit owns *path* and may assert it clean."""
    if path in FOREIGN_FILES or any(path.startswith(prefix) for prefix in FOREIGN_PREFIXES):
        return False
    if "/" not in path:
        name = path
        if any(name == stem or name.startswith(stem) for stem in FOREIGN_ROOT_STEMS):
            return False
        return Path(name).suffix.lower() in OWNED_ROOT_SUFFIXES or name in TEXT_BASENAMES
    if path.startswith(OWNED_DIRS):
        return is_text_path(path)
    if path.startswith("backend/"):
        return Path(path).suffix.lower() in OWNED_BACKEND_SUFFIXES
    return False


def tracked_files() -> list[str]:
    """Every tracked path in the repository, as repo-relative POSIX strings.

    Tracked-only is deliberate: the working tree also holds other agents'
    scratch, and a gate whose verdict moves with untracked files cannot be
    trusted as a build gate. A newly added file is covered from the moment it
    is committed, and ``test_this_detector_file_is_itself_clean`` covers the
    uncommitted window for this file.
    """
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z", "--cached"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git ls-files failed ({completed.returncode}): {completed.stderr.decode('utf-8', 'replace')[:400]}")
    return sorted({chunk.decode("utf-8", "surrogateescape") for chunk in completed.stdout.split(b"\0") if chunk})


def owned_surface() -> list[str]:
    return sorted(path for path in tracked_files() if is_text_path(path) and is_owned(path))


@dataclass(frozen=True)
class ScanResult:
    scanned: tuple[str, ...]
    blocking: tuple[Finding, ...]
    exempted: tuple[Finding, ...]


def scan_repo() -> ScanResult:
    surface = owned_surface()
    blocking: list[Finding] = []
    exempted: list[Finding] = []
    for path in surface:
        target = REPO_ROOT / path
        try:
            raw = target.read_bytes()
        except OSError as exc:  # pragma: no cover - only on a broken checkout
            blocking.append(Finding(path, 1, "unreadable", str(exc)))
            continue
        for finding in scan_bytes(path, raw):
            if path in OWNERSHIP_EXEMPTIONS:
                exempted.append(finding)
            else:
                blocking.append(finding)
    return ScanResult(tuple(surface), tuple(blocking), tuple(exempted))


def render_findings(findings: Iterable[Finding], limit: int = 40) -> str:
    ordered = sorted(findings, key=lambda item: (item.path, item.line, item.kind))
    lines = [finding.render() for finding in ordered[:limit]]
    if len(ordered) > limit:
        lines.append(f"... and {len(ordered) - limit} more")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


def test_no_unowned_text_file_has_encoding_damage() -> None:
    result = scan_repo()
    assert not result.blocking, (
        f"{len(result.blocking)} encoding finding(s) in the files this audit owns.\n"
        "Repair them mechanically (re-encode the maximal non-ASCII run through cp1252, or swap the exact bytes);\n"
        "do not reflow prose and do not touch line endings.\n"
        f"{render_findings(result.blocking)}"
    )


def test_the_repo_scan_actually_covers_the_tree() -> None:
    """A gate that scans nothing passes forever. Pin the corpus it must read."""
    surface = owned_surface()
    assert len(surface) > 1500, f"owned text surface collapsed to {len(surface)} file(s); the surface rule has rotted"
    for expected in (
        "README.md",
        "docs/INDEX.md",
        "references/05-autonomous-operations-and-execution/enterprise-swarm-and-workflow-plan.md",
        "skills/public/chart-visualization/references/generate_area_chart.md",
        "backend/tests/test_client.py",
    ):
        assert expected in surface, f"{expected} must be inside the owned surface"
    # A path that is deliberately not ours must stay out, or the gate would be
    # asserting on files this audit may not edit.
    for foreign in (
        "docker/docker-compose.yaml",
        "electron/main.js",
        "frontend/src/lib/kanban-board.ts",
        "Makefile",
        "start.ps1",
        "backend/app/gateway/app.py",
        "backend/packages/harness/alpha/runtime/",
    ):
        assert foreign not in surface, f"{foreign} is owned by another agent and must not be in this surface"


def test_this_detector_file_is_itself_clean() -> None:
    """Covers the uncommitted window: a brand-new file is not in git yet."""
    findings = scan_bytes(DETECTOR_PATH, (REPO_ROOT / DETECTOR_PATH).read_bytes())
    assert not findings, f"{DETECTOR_PATH} violates the rules it defines:\n{render_findings(findings)}"


# --------------------------------------------------------------------------- #
# Ownership exemptions
# --------------------------------------------------------------------------- #


def test_ownership_exemptions_name_a_real_file_and_a_reason() -> None:
    tracked = set(tracked_files())
    for path, reason in OWNERSHIP_EXEMPTIONS.items():
        assert path in tracked, f"exempted path {path} is not tracked; delete the exemption"
        assert reason.strip(), f"exemption for {path} has no reason; an unexplained exemption is a hole"
        assert is_owned(path), f"exemption for {path} should not be needed: the surface rule already excludes it"


def test_ownership_exemptions_are_not_stale() -> None:
    """Once an exempted file comes clean, the exemption must be deleted.

    An allowlist that is never pruned becomes a permanent blanket over whatever
    it named, which is how the next round of damage hides there.
    """
    result = scan_repo()
    by_path: dict[str, list[Finding]] = {}
    for finding in result.exempted:
        by_path.setdefault(finding.path, []).append(finding)
    stale = sorted(set(OWNERSHIP_EXEMPTIONS) - set(by_path))
    assert not stale, f"these exemptions no longer cover any damage; delete them: {stale}"
    for path, findings in sorted(by_path.items()):
        print(f"known debt in {path} ({len(findings)} site(s), reported not edited):")
        print(render_findings(findings))


# --------------------------------------------------------------------------- #
# Detector non-vacuity: one known-bad fixture per class, plus a positive
# control for every exemption the gate relies on.
# --------------------------------------------------------------------------- #


def _kinds(findings: Iterable[Finding]) -> set[str]:
    return {finding.kind for finding in findings}


def _plant(tmp_path: Path, name: str, data: bytes) -> str:
    (tmp_path / name).write_bytes(data)
    return name


def test_detector_catches_a_mojibake_run() -> None:
    # "a -> b" as UTF-8 read through cp1252: E2 86 92 becomes three characters.
    planted = "step a \u00e2\u2020\u2019 next\n".encode()
    findings = scan_bytes("notes.md", planted)
    assert "mojibake" in _kinds(findings), f"mojibake run not detected: {findings}"
    assert any("should be '→'" in finding.detail for finding in findings), findings
    # A genuinely non-ASCII run that is NOT mojibake must survive.
    assert "mojibake" not in _kinds(scan_bytes("clean.md", "café naïve — already fine\n".encode()))


def test_detector_catches_a_replacement_character() -> None:
    findings = scan_bytes("notes.md", "result: \ufffd\n".encode())
    assert "u_fffd" in _kinds(findings), findings


def test_detector_catches_an_unpaired_curly_quote_but_spares_a_real_pair() -> None:
    planted = 'setError("needs a reason \u201d added in the editor");\n'.encode()
    findings = scan_bytes("card.tsx", planted)
    assert "unpaired_quote" in _kinds(findings), findings

    paired = f"say {LEFT_DOUBLE_QUOTE}hello{RIGHT_DOUBLE_QUOTE} now\n".encode()
    assert not _kinds(scan_bytes("paired.md", paired)), scan_bytes("paired.md", paired)


def test_detector_allows_a_curly_quote_that_spans_lines() -> None:
    """Positive control: a multi-line quotation is legal, and must stay legal."""
    planted = (f"{LEFT_DOUBLE_QUOTE}Every weekday check issues,\ncreate a draft report.{RIGHT_DOUBLE_QUOTE}\n").encode()
    findings = scan_bytes("plan.md", planted)
    assert not _kinds(findings), findings
    unclosed = f"{LEFT_DOUBLE_QUOTE}opened and never closed\n".encode()
    assert "unclosed_quote" in _kinds(scan_bytes("plan.md", unclosed))


def test_detector_catches_stray_controls_and_spares_tab_newline_cr() -> None:
    planted = b"- data: include \x0balue and \x07cademy\n"
    findings = scan_bytes("chart.md", planted)
    assert "stray_control" in _kinds(findings), findings
    assert any("U+000B" in finding.detail for finding in findings), findings
    assert any("U+0007" in finding.detail for finding in findings), findings
    assert "stray_control" not in _kinds(scan_bytes("ok.md", b"first\n\tindented\nlast\r\n"))


def test_detector_catches_an_inline_tab_but_spares_indentation() -> None:
    """Positive control: a leading TAB is ordinary indentation, not damage."""
    findings = scan_bytes("chart.md", b"Ensure consistent \ttime formatting\n")
    assert "inline_tab" in _kinds(findings), findings
    indented = b"- item\n\tcontinued under a tab indent\n"
    assert "inline_tab" not in _kinds(scan_bytes("ok.md", indented)), scan_bytes("ok.md", indented)


def test_detector_catches_a_stray_bom_and_never_flags_a_powershell_one(tmp_path: Path) -> None:
    """Positive control: PowerShell 5.1 requires the .ps1 BOM, so it is exempt."""
    for name in ("mod.py", "readme.md", "compose.yaml", "app.tsx"):
        planted = BOM_BYTES + b"value = 1\n"
        findings = scan_bytes(_plant(tmp_path, name, planted), planted)
        assert "stray_bom" in _kinds(findings), f"{name}: {findings}"
    for name in ("launcher.ps1", "stop.ps1"):
        planted = BOM_BYTES + b'Write-Host "ok"\n'
        findings = scan_bytes(_plant(tmp_path, name, planted), planted)
        assert "stray_bom" not in _kinds(findings), f"{name} must keep its BOM: {findings}"
    for name in (".gitignore", "LICENSE", "Makefile"):
        planted = BOM_BYTES + b"build/\n"
        findings = scan_bytes(_plant(tmp_path, name, planted), planted)
        assert "stray_bom" not in _kinds(findings), f"{name} is not source; its BOM must never be flagged: {findings}"


def test_detector_catches_mixed_line_endings_and_spares_uniform_files() -> None:
    # A bare CR with no CRLF anywhere is the same defect: an interpreted \r.
    for planted, label in (
        (b"a\r\nb\r\nc\nd\r\n", "CRLF_then_LF"),
        (b"a\r\nb\rc\r\n", "bare_CR_between_CRLF"),
        (b"a\nb\rc\n", "bare_CR_in_LF_file"),
    ):
        findings = scan_bytes(f"{label}.md", planted)
        assert "mixed_eol" in _kinds(findings), f"{label}: {findings}"
    for planted in (b"a\nb\nc\n", b"a\r\nb\r\n", b"no newline at all"):
        assert "mixed_eol" not in _kinds(scan_bytes("ok.md", planted)), planted


def test_detector_catches_invalid_utf8(tmp_path: Path) -> None:
    # UTF-16LE with a BOM: valid Unicode, and not a valid UTF-8 byte sequence.
    # The bare UTF-16LE bytes of ASCII text would decode as UTF-8 (they are just
    # NUL-interleaved), so the BOM is what makes the file genuinely undecodable.
    planted = b"\xff\xfe" + "# heading\n".encode("utf-16-le")
    findings = scan_bytes(_plant(tmp_path, "note.md", planted), planted)
    assert "invalid_utf8" in _kinds(findings), findings
    assert any("invalid start byte at byte 0" in finding.detail for finding in findings), findings


def test_one_fixture_per_class_is_enough_to_prove_the_whole_gate_bites(tmp_path: Path) -> None:
    """A single end-to-end bite: one file holding one site of every class."""
    planted = BOM_BYTES + b"first\r\n" + "mojibake a \u00e2\u2020\u2019\n".encode() + b"lost \xef\xbf\xbd char\n" + "unpaired \u201d quote\n".encode() + b"control \x0bvalue\n" + b"inline \ttab\n" + b"mixed\r\nendings\n"
    findings = scan_bytes(_plant(tmp_path, "kitchen-sink.md", planted), planted)
    assert _kinds(findings) == {
        "stray_bom",
        "mixed_eol",
        "mojibake",
        "u_fffd",
        "unpaired_quote",
        "stray_control",
        "inline_tab",
    }, sorted(_kinds(findings))


def test_cp1252_round_trip_recovers_the_documented_signatures() -> None:
    """Positive control for the round trip itself, including the undefined slot."""
    for damaged, expected in (
        ("\u00e2\u2020\u2019", "\u2192"),
        ("\u00f0\u0178\u2018\u00a4", "\U0001f464"),
        ("\u00f0\u0178\u201c\u0081", "\U0001f4c1"),
        ("\u00e2\u20ac\u201d", "\u2014"),
        ("\u00c3\u00a9", "\u00e9"),
        ("\u00ef\u00bb\u00bf", "\ufeff"),
    ):
        assert cp1252_round_trip(damaged) == expected, f"{damaged!r} should repair to {expected!r}"
    # U+0081 is cp1252's undefined slot; the latin-1 fallback is what makes
    # the fourth byte of the fourth signature recoverable at all.
    assert cp1252_round_trip("\u00f0\u0178\u201c\u0081") == "\U0001f4c1"
    for not_mojibake in ("\u00e9", "caf\u00e9", "\u2014", "\u201cquoted\u201d", "\U0001f464", "\u2192"):
        assert cp1252_round_trip(not_mojibake) is None, f"{not_mojibake!r} is legitimate text and must not be flagged"
