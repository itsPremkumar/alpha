#!/usr/bin/env python3
"""CI gate: fail the build when a caught exception is silently discarded.

The rule
--------
A caught exception is a **silent failure** when its handler does none of these:

1. **re-raises** -- any ``raise`` (bare re-raise, ``raise X``, ``raise X from Y``);
2. **reports** -- a logging call (``logger.warning``, ``logging.exception``,
   ``self._log.error``) or a registry report (``alpha.errors.report_error`` /
   ``report_exception`` / ``log_error``);
3. **propagates** -- the handler *reads* the bound exception name, so the error
   leaves as data (``return _unavailable(str(exc))``, ``raise ... from exc``,
   ``self.errors.append(exc)``);
4. **delegates** -- it calls ``sys.exception()`` / ``sys.exc_info()``.

Reading the bound name is what separates *handling* from *swallowing*. A handler
that never mentions its exception discards it, whatever else it does in the
body: ``except: pass``, ``except ValueError: return None``, and
``except Exception: x = 0`` are the same failure at three verbosity levels.

Why a ratchet instead of zero tolerance
---------------------------------------
This rule was added to a tree that already contained **~1,570** pre-existing
silent failures across ~850 files, almost all of them intentional best-effort
degradations (a sampler that must not raise into its tick, a probe that degrades
to ``None``) and a large number of them in files this change did not own. A gate
that turned red on 1,570 pre-existing lines would be switched off, and a gate
that was scoped to only its own files would be a gate that cannot fail.

So the rule is **never weakened** and the debt is **enumerated**:

* a silent failure not present in :data:`BASELINE` fails the build (exit 1);
* a baseline entry with no matching finding is reported as *resolved* -- burn
  down debt by deleting the line from the baseline in the same change that fixes
  the handler;
* ``--fail-on-stale`` promotes "resolved but not yet un-waived" to a failure,
  for the burn-down branch.

That is a ratchet, not an exemption list: debt can only go down, every entry is
machine-reviewable, and each new silent failure is blocked on arrival.

**The regeneration path is prune-only.** ``--update-baseline`` deletes waivers
whose handler is gone and is structurally incapable of adding one, so the
routine "sync the baseline" step cannot be used to make a red build green.
Admitting new debt is a separate, explicit, loud act: ``--accept-new`` prints
every single finding it admits. Use it when you have read ``--report`` and have
decided the debt is genuinely pre-existing, never to silence a gate you did not
like.

Usage
-----
::

    python scripts/check_no_silent_failures.py                    # the CI gate
    python scripts/check_no_silent_failures.py --report out.md    # triage list
    python scripts/check_no_silent_failures.py --json             # machine output
    python scripts/check_no_silent_failures.py --update-baseline  # prune only
    python scripts/check_no_silent_failures.py --update-baseline --accept-new

Exit codes: ``0`` clean, ``1`` new silent failures (or stale waivers with
``--fail-on-stale``), ``2`` the gate could not verify the tree (bad root, a
file that does not parse). Exit 2 is deliberately distinct: "I did not look" must
never be reported as "all clear".
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

# --------------------------------------------------------------------------
# Rule definition. Changing anything in this block changes the gate.
# --------------------------------------------------------------------------

#: Attribute / plain names that count as "the error was reported". Deliberately
#: a closed set: a random method called ``handle`` is not a report.
LOG_METHODS: Final[frozenset[str]] = frozenset(
    {
        "critical",
        "debug",
        "error",
        "exception",
        "info",
        "log",
        "log_error",
        "log_exception",
        "warning",
        "warn",
    }
)

#: Callables that count as "the error was reported" by the error registry.
REPORTER_CALLS: Final[frozenset[str]] = frozenset(
    {
        "log_error",
        "log_exception",
        "record_error",
        "report_error",
        "report_exception",
    }
)

#: Module-level names whose *attributes* are loggers (``logging.warning``).
LOGGER_MODULES: Final[frozenset[str]] = frozenset({"logging", "log", "logger"})

#: ``sys.*`` accessors that hand the live exception to a caller.
EXC_ACCESSORS: Final[frozenset[str]] = frozenset({"exc_info", "exception"})

#: Exceptions that are *control flow*, not failures. Swallowing one of these is
#: still a bug, but it is reported at ``critical`` so triage sees the difference.
CONTROL_FLOW: Final[frozenset[str]] = frozenset(
    {
        "BaseException",
        "CancelledError",
        "GeneratorExit",
        "KeyboardInterrupt",
        "StopAsyncIteration",
        "StopIteration",
        "SystemExit",
    }
)

#: Directories scanned, relative to the repository root. Every tracked Python
#: tree is included: production, Gateway, tests and tooling alike.
SCAN_DIRS: Final[tuple[str, ...]] = (
    "backend/app",
    "backend/packages",
    "backend/scripts",
    "backend/tests",
    "scripts",
    "examples",
    "tests",
)

#: Never scanned: vendored code, caches, and installed trees.
SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)

#: Declared swallows are not *silent* -- the exception type is written at the
#: call site -- so they are reported as their own class, never as a violation.
SUPPRESSOR: Final[str] = "contextlib.suppress"

BREADTH_BARE: Final = "bare"
BREADTH_BASE: Final = "base"
BREADTH_CONTROL: Final = "controlflow"
BREADTH_EXCEPTION: Final = "exception"
BREADTH_NARROW: Final = "narrow"

_BREADTH_ORDER: Final[dict[str, int]] = {
    BREADTH_BARE: 0,
    BREADTH_BASE: 1,
    BREADTH_CONTROL: 2,
    BREADTH_EXCEPTION: 3,
    BREADTH_NARROW: 4,
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One silent failure."""

    path: str
    lineno: int
    breadth: str
    shape: str
    fingerprint: str
    count: int = 1
    source: str = ""
    is_suppress: bool = False

    def as_row(self) -> str:
        return f"{self.path}:{self.lineno}"


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    suppressed: list[Finding] = field(default_factory=list)
    unparsed: list[tuple[str, int, str]] = field(default_factory=list)
    files_scanned: int = 0
    handlers_seen: int = 0
    suppress_seen: int = 0

    def fingerprint_map(self) -> dict[str, dict[str, int]]:
        """``path -> fingerprint -> count``. A multiset, so two identical
        handlers in one file are two violations, not one."""
        out: dict[str, dict[str, int]] = {}
        for finding in self.findings:
            out.setdefault(finding.path, {})
            out[finding.path][finding.fingerprint] = out[finding.path].get(finding.fingerprint, 0) + 1
        return out


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def _callee_name(node: ast.Call) -> str:
    return _dotted_tail(node.func)


def _dotted_tail(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _dotted_tail(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _own_nodes(handler: ast.ExceptHandler) -> list[ast.AST]:
    """Every node the handler itself touches.

    Nested ``except`` clauses are excluded: each one is judged on its own, and
    crediting a parent handler for a nested handler's log line would let an
    outer silent swallow hide behind an inner report.
    """
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(handler.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ExceptHandler):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def is_reported(handler: ast.ExceptHandler, nodes: list[ast.AST]) -> bool:
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name in REPORTER_CALLS or name in LOG_METHODS:
            return True
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in LOGGER_MODULES:
            return True
    return False


def is_propagating(handler: ast.ExceptHandler, nodes: list[ast.AST]) -> bool:
    bound = handler.name
    for node in nodes:
        if bound and isinstance(node, ast.Name) and node.id == bound and isinstance(node.ctx, ast.Load):
            return True
        if isinstance(node, ast.Attribute) and node.attr in EXC_ACCESSORS:
            if isinstance(node.value, ast.Name) and node.value.id == "sys":
                return True
    return False


def raises_again(nodes: list[ast.AST]) -> bool:
    return any(isinstance(node, ast.Raise) for node in nodes)


def caught_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return []
    names: list[str] = []
    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.append(node.value.rsplit(".", 1)[-1])
    return names


def breadth_of(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return BREADTH_BARE
    names = set(caught_names(handler))
    if not names:
        return BREADTH_NARROW
    if "BaseException" in names:
        return BREADTH_BASE
    if names & CONTROL_FLOW:
        return BREADTH_CONTROL
    if "Exception" in names:
        return BREADTH_EXCEPTION
    return BREADTH_NARROW


def _is_inert(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, (ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
        return True
    if isinstance(statement, ast.Return):
        return statement.value is None or (isinstance(statement.value, ast.Constant) and statement.value.value is None)
    return False


def shape_of(handler: ast.ExceptHandler) -> str:
    if all(_is_inert(statement) for statement in handler.body):
        return "inert"
    meaningful = [statement for statement in handler.body if not _is_inert(statement)]
    if len(meaningful) == 1:
        only = meaningful[0]
        if isinstance(only, ast.Assign) and len(only.targets) == 1 and isinstance(only.targets[0], ast.Name):
            return "default-value"
        if isinstance(only, ast.Return):
            return "return-fallback"
        if isinstance(only, ast.Raise):
            return "re-raise"
        if isinstance(only, ast.AugAssign):
            return "count-only"
    return "other"


def fingerprint_of(handler: ast.ExceptHandler, breadth: str) -> str:
    """Stable across unrelated edits elsewhere in the file.

    Built from the handler's AST plus its breadth -- never from a line number --
    so inserting a function above a handler does not invalidate its waiver.
    ``ast.dump`` is used rather than ``ast.unparse`` because unparse deepcopies
    every node and costs an order of magnitude more; the AST form is also
    immune to reformatting and comment edits, which is what we want here.
    """
    # ``ast.dump`` is total over a node from a successfully parsed tree, so there
    # is no fallback here to report: an un-dumpable node would mean the tree was
    # never parsed, and ``find_in_file`` already turns that into an "unverified"
    # result rather than a fabricated fingerprint.
    rendered = ast.dump(handler, annotate_fields=True, include_attributes=False)
    digest = hashlib.sha256(f"{breadth}\n{rendered}".encode()).hexdigest()[:16]
    return f"{breadth}:{digest}"


def find_in_file(rel: str, source: str) -> tuple[list[Finding], list[Finding], int, int]:
    findings: list[Finding] = []
    suppressions: list[Finding] = []
    tree = ast.parse(source, filename=rel)
    lines = source.splitlines()
    handlers = 0
    suppress_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                handlers += 1
                own = _own_nodes(handler)
                if raises_again(own) or is_reported(handler, own) or is_propagating(handler, own):
                    continue
                breadth = breadth_of(handler)
                shape = shape_of(handler)
                findings.append(
                    Finding(
                        path=rel,
                        lineno=handler.lineno,
                        breadth=breadth,
                        shape=shape,
                        fingerprint=fingerprint_of(handler, breadth),
                        source=lines[handler.lineno - 1].strip()[:160] if 0 < handler.lineno <= len(lines) else "",
                    )
                )
        if isinstance(node, ast.With) and any(_dotted_tail(item.context_expr) == "suppress" for item in node.items):
            suppress_count += 1
            if node.items[0].context_expr is not None:
                suppressions.append(
                    Finding(
                        path=rel,
                        lineno=node.lineno,
                        breadth=BREADTH_NARROW,
                        shape="declared-suppress",
                        fingerprint=f"suppress:{hashlib.sha256(f'{rel}:{node.lineno}'.encode()).hexdigest()[:12]}",
                        is_suppress=True,
                    )
                )
    return findings, suppressions, handlers, suppress_count


def scan(root: Path, *, scan_dirs: tuple[str, ...] = SCAN_DIRS) -> ScanResult:
    """Parse every scanned file and report every silent failure in it.

    There is deliberately **no cheap pre-filter** here, even though skipping
    files that lack the substring ``except`` would cut the parse cost roughly in
    half. Such a filter has a blind spot exactly where it hurts: a file that is
    *itself* unparseable (an agent mid-write, a bad merge) is precisely the file
    a caller most needs told about, and a substring test cannot tell "has no
    handler" from "cannot be read as Python". Since exit code 2 means "I did not
    look", a scan that silently skips unreadable trees would make that code lie.
    """
    result = ScanResult()
    for rel_dir in scan_dirs:
        base = root / rel_dir
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            try:
                # ``utf-8-sig``, not ``utf-8``: CPython's import machinery
                # strips a UTF-8 BOM before compiling, so a BOM'd module is valid
                # Python and runs fine. Reading it as plain ``utf-8`` leaves a
                # leading U+FEFF that ``ast.parse`` rejects, which would make the
                # gate report a working file as unparseable -- a permanent false
                # "I did not look" for anyone who saves with a BOM.
                source = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError) as exc:
                result.unparsed.append((path.relative_to(root).as_posix(), 0, f"unreadable: {exc}"))
                continue
            rel = path.relative_to(root).as_posix()
            try:
                findings, suppressions, handlers, suppress_count = find_in_file(rel, source)
            except SyntaxError as exc:
                result.unparsed.append((rel, exc.lineno or 0, f"syntax error: {exc.msg}"))
                continue
            result.files_scanned += 1
            result.findings.extend(findings)
            result.suppressed.extend(suppressions)
            result.handlers_seen += handlers
            result.suppress_seen += suppress_count
    return result


# --------------------------------------------------------------------------
# Ratchet baseline
# --------------------------------------------------------------------------

# --- BEGIN BASELINE ---
# GENERATED by `python scripts/check_no_silent_failures.py --update-baseline`.
# Every entry is a silent failure that already existed when this gate landed.
# Delete an entry in the same change that fixes its handler; the gate then
# keeps enforcing the rule for that code forever. See the module docstring.
BASELINE: dict[str, dict[str, int]] = {
    "backend/app/channels/base.py": {"controlflow:27c5b52b62bab86e": 1, "narrow:71166472da128493": 1, "narrow:b9383c18bf0736c2": 1, "narrow:ef484cdfaca2c9bf": 1},
    "backend/app/channels/buzz.py": {"exception:1c95464054f7622d": 1},
    "backend/app/channels/buzz_nostr.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/app/channels/buzz_seen_events.py": {"controlflow:b81348c46137f728": 2, "narrow:71fa437e4dee59ae": 1},
    "backend/app/channels/dedupe_store.py": {"narrow:f17deae7d5da2093": 1},
    "backend/app/channels/dingtalk.py": {"narrow:b287a6ef2ea2148b": 1},
    "backend/app/channels/discord.py": {
        "controlflow:fd0f1600099d8003": 1,
        "exception:349d619249a5044a": 1,
        "exception:79440e8db4a9e936": 1,
        "narrow:5388c6fd8f3f3fd6": 1,
        "narrow:c359ce204af964b1": 1,
    },
    "backend/app/channels/feishu.py": {"exception:349d619249a5044a": 1, "narrow:68e270c77c2b5713": 1},
    "backend/app/channels/manager.py": {"narrow:591be14138503f99": 1, "narrow:e009a941295ad14e": 1},
    "backend/app/channels/message_bus.py": {"narrow:0c11ab8296b315b7": 1},
    "backend/app/channels/signal.py": {"narrow:8c8253ff0c365fff": 1},
    "backend/app/channels/slack.py": {"exception:349d619249a5044a": 1},
    "backend/app/channels/telegram.py": {"narrow:1f83411aa86edf92": 1, "narrow:bfc3a9c8f95ed164": 1, "narrow:c359ce204af964b1": 1, "narrow:dee5f3881b69b927": 1, "narrow:fdc1c48ee1bc6fb8": 1},
    "backend/app/channels/wechat.py": {
        "controlflow:fd0f1600099d8003": 1,
        "narrow:1bd358182944a5b8": 1,
        "narrow:34e28dce35f3c779": 1,
        "narrow:3f5fbd1d59eff7e3": 1,
        "narrow:8d9450d913bbe3a4": 1,
        "narrow:acb1cd5c04f7171b": 1,
        "narrow:b2adc926a31ab6e3": 1,
        "narrow:b58e527161168a03": 1,
        "narrow:b92733b80122965d": 2,
        "narrow:d580fed9969f5762": 1,
    },
    "backend/app/channels/wecom.py": {"exception:349d619249a5044a": 1, "exception:711d7455130f087e": 1, "exception:77d5d041680d5234": 1, "exception:79440e8db4a9e936": 1},
    "backend/app/gateway/auth/jwt.py": {"narrow:68f898ceb05dc101": 1, "narrow:beead42902ef725d": 1, "narrow:f2220e5fe233ecff": 1},
    "backend/app/gateway/auth/oidc.py": {"exception:349d619249a5044a": 1},
    "backend/app/gateway/auth/oidc_state.py": {"narrow:967502533cc8258e": 1},
    "backend/app/gateway/auth/password.py": {"narrow:dc91e12039e1db06": 1},
    "backend/app/gateway/auth/session_cookie.py": {"narrow:dc91e12039e1db06": 1},
    "backend/app/gateway/authz.py": {"narrow:ada2aa7a15541d96": 1, "narrow:d3424866f5145d70": 1},
    "backend/app/gateway/autonomy/loops.py": {"exception:9477b62055716d28": 1},
    "backend/app/gateway/autonomy/supervisor.py": {"controlflow:b81348c46137f728": 1},
    "backend/app/gateway/context_usage.py": {"exception:c2a4bc02d52d69fa": 1, "narrow:c7ccd81d35b6393d": 1},
    "backend/app/gateway/csrf_middleware.py": {"narrow:b92733b80122965d": 1},
    "backend/app/gateway/deps.py": {"narrow:1655050fa13ad218": 2, "narrow:c7ccd81d35b6393d": 1},
    "backend/app/gateway/routers/artifacts.py": {"exception:9b35befbbcb53bd5": 1, "narrow:5a47ab761773993a": 1, "narrow:ba05d23e31be9dc4": 1, "narrow:f2d3b48ea3b7e8ca": 1},
    "backend/app/gateway/routers/auth.py": {"narrow:1db22bae25fa2fea": 1, "narrow:34e28dce35f3c779": 1, "narrow:e042ab1fe438b444": 1},
    "backend/app/gateway/routers/bots.py": {"exception:0287dd039ed0ea56": 1, "narrow:318590a2ae3aa8eb": 1},
    "backend/app/gateway/routers/browser.py": {
        "exception:a20dfe0da18cf262": 1,
        "narrow:2046a5ced1f6ae0e": 1,
        "narrow:2d9f02c60dd48991": 1,
        "narrow:4d7d00793d9c27c3": 1,
        "narrow:56078854003ca8e1": 1,
        "narrow:9629f8012319c93f": 1,
        "narrow:ca7f975bbc28446d": 1,
    },
    "backend/app/gateway/routers/channel_connections.py": {"narrow:34e28dce35f3c779": 1},
    "backend/app/gateway/routers/commands.py": {"narrow:34e28dce35f3c779": 1, "narrow:4d5f6a59352f7f93": 1, "narrow:9b71fa63969e3f4c": 1, "narrow:e90d7fb35ea22060": 1},
    "backend/app/gateway/routers/deliberation.py": {"narrow:6d569d2b665c22e1": 1},
    "backend/app/gateway/routers/groups.py": {"exception:ba5f3915cfbb3c58": 1},
    "backend/app/gateway/routers/integrations.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/app/gateway/routers/jobs.py": {"narrow:34e28dce35f3c779": 2},
    "backend/app/gateway/routers/memory.py": {"narrow:34e28dce35f3c779": 1, "narrow:fb01bfe9283782c7": 1},
    "backend/app/gateway/routers/missions.py": {"exception:097381ae14bdbaa8": 1, "narrow:e58a107493665f60": 1},
    "backend/app/gateway/routers/models.py": {"exception:cbc9b62c42835f31": 1},
    "backend/app/gateway/routers/multimodal.py": {"exception:68acd0911d8dcce2": 1, "exception:6a00dc719c64f1ba": 1, "narrow:c7b25ed81036f77a": 1},
    "backend/app/gateway/routers/ops.py": {"exception:6943b52821d2f076": 1, "exception:79b9cab6c96c128a": 1, "narrow:1220cb5cc60a64fc": 1, "narrow:a155ca0e192c456a": 1},
    "backend/app/gateway/routers/ops_integration.py": {"narrow:b6e01dddd7293d85": 1},
    "backend/app/gateway/routers/peer_network.py": {"narrow:c7b25ed81036f77a": 1, "narrow:e58a107493665f60": 1, "narrow:f75fff9e8dcfed17": 1},
    "backend/app/gateway/routers/projects.py": {
        "exception:4aa915f462ae22b9": 1,
        "exception:8e3c6381881af7e6": 1,
        "exception:95cefcf67e0fbac0": 1,
        "exception:c9957bdf7bffda63": 1,
        "narrow:e0571dfe76a0ada5": 1,
    },
    "backend/app/gateway/routers/scheduled_tasks.py": {"narrow:0b78a2554f6b50ab": 1},
    "backend/app/gateway/routers/skills.py": {"narrow:5a0f56d7e7e84354": 2, "narrow:d3db1fdbdaae6661": 1},
    "backend/app/gateway/routers/suggestions.py": {"exception:130831a2d6b6d44c": 1, "exception:79440e8db4a9e936": 1},
    "backend/app/gateway/routers/swarms.py": {"narrow:7259949ce895ae2a": 1},
    "backend/app/gateway/routers/system_monitor.py": {"narrow:3f5fbd1d59eff7e3": 1},
    "backend/app/gateway/routers/thread_runs.py": {
        "controlflow:30e6717a90910e6a": 1,
        "controlflow:4f3314ca83d383bc": 1,
        "controlflow:fd0f1600099d8003": 1,
        "exception:349d619249a5044a": 1,
        "exception:47ac3a850c4642c2": 1,
        "narrow:2135f5dcd23135d6": 1,
        "narrow:34e28dce35f3c779": 1,
        "narrow:d3126e7c9bd336c4": 1,
    },
    "backend/app/gateway/routers/threads.py": {"narrow:0092f23d78b227ec": 1, "narrow:7beaf4807c3a8b09": 1, "narrow:8b608e7ca37c724d": 1, "narrow:a11451d007bde50e": 1},
    "backend/app/gateway/routers/uploads.py": {"exception:9b35befbbcb53bd5": 1, "narrow:5a47ab761773993a": 4, "narrow:e195278544059d0f": 1},
    "backend/app/gateway/routers/workflows.py": {"exception:79440e8db4a9e936": 1, "exception:98c65aad21359c81": 1},
    "backend/app/gateway/run_recovery.py": {"narrow:2a25c88dc4f13f5b": 1, "narrow:948d61df8716cd2f": 1, "narrow:bd20cd37a4b67a55": 1, "narrow:c359ce204af964b1": 1},
    "backend/app/gateway/services.py": {"controlflow:b81348c46137f728": 1, "controlflow:fd0f1600099d8003": 1, "exception:8a20dc09ea7beb79": 1, "exception:afcc352ff6b6bd82": 1},
    "backend/app/gateway/skill_export.py": {"base:7e87ad0a153b0dce": 1, "controlflow:30e6717a90910e6a": 1, "exception:47ac3a850c4642c2": 1},
    "backend/app/gateway/system_monitor_extras.py": {
        "exception:078183e4dd74b919": 1,
        "exception:26acfeb6a82db028": 1,
        "exception:9ba41f5b51d8321d": 1,
        "exception:cda47790a5036d4b": 1,
        "exception:df4b6805ea1ac492": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:ca9631ccb42df79b": 1,
    },
    "backend/app/gateway/system_monitor_service.py": {
        "exception:078183e4dd74b919": 3,
        "exception:1cd880fdbd271ca0": 1,
        "exception:333ed0f44ae8a213": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:8edef97767db0738": 1,
        "exception:9b35befbbcb53bd5": 3,
        "exception:9de3d310261bf207": 1,
        "exception:b2a0f47231c7b9cf": 1,
        "exception:b779f27e626da0a0": 1,
        "exception:d3d1ad2ac2040403": 1,
        "exception:dc149cd312b0b468": 1,
        "exception:df1f9315d1aefacd": 1,
        "exception:f8ca7819fca9ba2d": 1,
        "exception:fc4125e484d8da0a": 1,
        "narrow:157e43db01022f84": 2,
        "narrow:1df853e91a2c07bb": 1,
        "narrow:347fe08ec79c8beb": 1,
        "narrow:391d890e0ee1d671": 1,
        "narrow:6c0edf280aa0b81b": 1,
        "narrow:74c750dabdba4aa1": 1,
        "narrow:8d4ff3ac7ee6fe3b": 1,
        "narrow:b24bc435f3bbb3c8": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:ca9631ccb42df79b": 1,
        "narrow:e2ed4c914666e075": 1,
    },
    "backend/app/mcp_tasks/service.py": {"controlflow:30e6717a90910e6a": 1, "controlflow:fd0f1600099d8003": 1, "narrow:1b1fba5c9a766438": 1},
    "backend/app/scheduler/job_memory.py": {"narrow:7e1291807a10d793": 1, "narrow:b92733b80122965d": 1},
    "backend/app/scheduler/queue_health.py": {"narrow:b92733b80122965d": 1},
    "backend/app/scheduler/service.py": {"narrow:1b1fba5c9a766438": 1, "narrow:39e8eda9bada5cb0": 1},
    "backend/packages/harness/alpha/agents/assembly_descriptor.py": {
        "exception:349d619249a5044a": 2,
        "exception:79440e8db4a9e936": 1,
        "exception:ea36d891e8b063a7": 1,
        "narrow:909af22c9d22612b": 1,
        "narrow:ac191c402ba9108d": 1,
    },
    "backend/packages/harness/alpha/agents/lead_agent/prompt.py": {"exception:0af8fdfda133adf1": 1, "exception:e843c4530a8750f0": 1},
    "backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/eviction.py": {"narrow:157e43db01022f84": 1, "narrow:3f5fbd1d59eff7e3": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/prompt.py": {"exception:490856ee76fb7a5e": 1, "narrow:38bd9023416d1d77": 1, "narrow:e6fc0598111437b7": 1},
    "backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/retrieval.py": {"narrow:05cc3551c3060e83": 1, "narrow:64ed742692991172": 1, "narrow:a931c4746bee5813": 1},
    "backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/storage.py": {"narrow:440329e1effbf9e1": 2, "narrow:ac191c402ba9108d": 1, "narrow:e195278544059d0f": 4},
    "backend/packages/harness/alpha/agents/memory/backends/deermem/deermem/core/updater.py": {
        "narrow:3d3c58cfa8a54b2f": 1,
        "narrow:5bcba04aa9399ae0": 1,
        "narrow:9398d9dfb1c70fd2": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:d3126e7c9bd336c4": 1,
        "narrow:eb694554cb812556": 1,
    },
    "backend/packages/harness/alpha/agents/memory/backends/fullmemory/fullmemory_manager.py": {
        "exception:349d619249a5044a": 3,
        "exception:5f77835af1f5cb17": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:a12c5bea69e380e3": 1,
    },
    "backend/packages/harness/alpha/agents/memory/backends/openviking/openviking_manager.py": {"narrow:2e4f90b76a2d36db": 1},
    "backend/packages/harness/alpha/agents/memory/l1/dedup.py": {"exception:1808b0e0c12dd0bf": 1},
    "backend/packages/harness/alpha/agents/memory/l1/extractor.py": {"exception:d4759fd7e5fc8342": 1},
    "backend/packages/harness/alpha/agents/memory/l1/parser.py": {"narrow:3ef260e153e0718a": 1, "narrow:682f057ea8f857e4": 1, "narrow:6fd007314f8e9611": 1},
    "backend/packages/harness/alpha/agents/memory/l1/persona.py": {"exception:d4759fd7e5fc8342": 1, "narrow:dad4b979e401bba4": 1},
    "backend/packages/harness/alpha/agents/memory/l1/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/agents/memory/l1/quota.py": {"narrow:60ea5a7b1774a243": 1},
    "backend/packages/harness/alpha/agents/memory/manager.py": {"narrow:532e2dbf1c2814a9": 1, "narrow:8a8297fb8b3b4afe": 1},
    "backend/packages/harness/alpha/agents/memory/tools.py": {"narrow:4396750ea2986daa": 1, "narrow:9d9d5799ed546537": 2, "narrow:c29974a0d0bb668b": 1, "narrow:c826b60f939d99ee": 1},
    "backend/packages/harness/alpha/agents/middlewares/clarification_middleware.py": {"narrow:5c0ce40766257588": 1, "narrow:ab8f46b9c1bb9770": 1},
    "backend/packages/harness/alpha/agents/middlewares/dangling_tool_call_middleware.py": {"narrow:b92733b80122965d": 1, "narrow:c49256a62e07c48c": 1},
    "backend/packages/harness/alpha/agents/middlewares/dynamic_context_middleware.py": {"narrow:7ea4d64a5407f2a6": 1, "narrow:ac191c402ba9108d": 1},
    "backend/packages/harness/alpha/agents/middlewares/finish_first_verifier_middleware.py": {"narrow:d3126e7c9bd336c4": 1},
    "backend/packages/harness/alpha/agents/middlewares/hooks_bridge_middleware.py": {"narrow:68e977f079360bc7": 1, "narrow:9e164be5e986be5d": 1, "narrow:b58e527161168a03": 1},
    "backend/packages/harness/alpha/agents/middlewares/llm_breaker_registry.py": {"exception:a12c5bea69e380e3": 3, "exception:e04608052307f181": 1},
    "backend/packages/harness/alpha/agents/middlewares/llm_error_handling_middleware.py": {"narrow:1aff94f457419b3b": 1, "narrow:844558b9558f8597": 1, "narrow:ce4f6b28c47fc311": 1},
    "backend/packages/harness/alpha/agents/middlewares/loop_detection_middleware.py": {"narrow:6ec6958ec0ebb280": 1, "narrow:b588b3eec19810a8": 1, "narrow:fac335a2f7fb91bd": 1},
    "backend/packages/harness/alpha/agents/middlewares/mcp_routing_middleware.py": {"narrow:451aca5acd2686e1": 1},
    "backend/packages/harness/alpha/agents/middlewares/read_before_write_middleware.py": {"narrow:9ce26907fa4a6e03": 1},
    "backend/packages/harness/alpha/agents/middlewares/sandbox_audit_middleware.py": {"narrow:34e28dce35f3c779": 1},
    "backend/packages/harness/alpha/agents/middlewares/skill_activation_middleware.py": {"narrow:314a4a7d7139aaa2": 1},
    "backend/packages/harness/alpha/agents/middlewares/skill_context.py": {"narrow:6bdfa8596960d177": 1},
    "backend/packages/harness/alpha/agents/middlewares/skill_tool_policy_middleware.py": {"narrow:8b197e19c22c635e": 1},
    "backend/packages/harness/alpha/agents/middlewares/summarization_middleware.py": {"exception:3ddcdff7faeceebc": 2, "narrow:0525241484b4bb84": 2},
    "backend/packages/harness/alpha/agents/middlewares/title_middleware.py": {"exception:b813b50b7c19085a": 1},
    "backend/packages/harness/alpha/agents/middlewares/tool_error_handling_middleware.py": {"narrow:fdc1c48ee1bc6fb8": 1},
    "backend/packages/harness/alpha/agents/middlewares/tool_output_budget_middleware.py": {"narrow:ac191c402ba9108d": 2},
    "backend/packages/harness/alpha/agents/middlewares/tool_output_synopsis.py": {"exception:79440e8db4a9e936": 3, "narrow:3ff8aab0ec025b6c": 1, "narrow:7619f0255e149aed": 1},
    "backend/packages/harness/alpha/agents/middlewares/tool_result_meta.py": {"narrow:229b05f6778d4ce6": 1, "narrow:2c17187b85303807": 1},
    "backend/packages/harness/alpha/agents/middlewares/uploads_middleware.py": {"narrow:bfc3a9c8f95ed164": 1},
    "backend/packages/harness/alpha/agents/middlewares/view_image_middleware.py": {"narrow:ac191c402ba9108d": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/agents/task_continuity/archive.py": {"controlflow:30e6717a90910e6a": 1, "narrow:0f6e934c5a1013fb": 1, "narrow:99156dba2056f5c4": 1},
    "backend/packages/harness/alpha/agents/task_continuity/tools.py": {"narrow:87d954ee6c3cf0a4": 3},
    "backend/packages/harness/alpha/autoconfig/engine.py": {"narrow:34e28dce35f3c779": 1, "narrow:4e5f32b529f61e85": 1},
    "backend/packages/harness/alpha/blackboard/stigmergic_event_mesh.py": {"exception:a12c5bea69e380e3": 1, "narrow:21305e845f72b6ce": 1, "narrow:ff3532ec03c9d5e3": 1},
    "backend/packages/harness/alpha/bots/dm.py": {"exception:79440e8db4a9e936": 2, "narrow:4e78b5b61886412f": 1},
    "backend/packages/harness/alpha/bots/ephemeral.py": {"narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/bots/events.py": {"exception:0e6db885dddeae65": 1, "exception:a12c5bea69e380e3": 1},
    "backend/packages/harness/alpha/bots/health.py": {"exception:75222aa07b37b8ae": 1, "exception:afe0f1b79c0c714b": 1},
    "backend/packages/harness/alpha/bots/inbox.py": {"exception:2ff9f9490f3add26": 1, "exception:487b841842442d5f": 1},
    "backend/packages/harness/alpha/bots/registry.py": {"exception:42dcb4d857299c69": 1, "exception:5de1d5c550f1851d": 1, "exception:d4355f950641eb44": 1},
    "backend/packages/harness/alpha/browser/cdp_bridge.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/packages/harness/alpha/browser/cdp_executor.py": {"narrow:735cb3fd47749365": 1, "narrow:9becc37fb08f48b6": 1, "narrow:9d50be9dd6e8f080": 1, "narrow:fdffa88d2662c444": 1},
    "backend/packages/harness/alpha/browser/cdp_snapshot.py": {"narrow:b11b86037cc0a309": 1},
    "backend/packages/harness/alpha/browser/cdp_transport.py": {"narrow:5388c6fd8f3f3fd6": 1},
    "backend/packages/harness/alpha/browser/dom_snapshot.py": {"exception:349d619249a5044a": 1, "exception:c1dee1489a4a738c": 1, "narrow:1aff94f457419b3b": 1},
    "backend/packages/harness/alpha/browser/element_table.py": {"narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/browser/executor.py": {"exception:a06eb42aa456e5be": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/browser/jev_policy.py": {"narrow:1657a8e604b8c0c5": 1},
    "backend/packages/harness/alpha/checkpoint_patches.py": {"exception:288c24d41797021f": 1},
    "backend/packages/harness/alpha/client.py": {
        "controlflow:cbac8e2cbdef72af": 1,
        "exception:9b9bf84423d044c7": 1,
        "narrow:30e7f08985726f2a": 1,
        "narrow:3d3c58cfa8a54b2f": 1,
        "narrow:595711821b97d226": 1,
        "narrow:7d85b76fed172ffd": 1,
        "narrow:df222a22f8e0bec4": 1,
    },
    "backend/packages/harness/alpha/coding/lsp_client_engine.py": {
        "controlflow:44b5da36ef14e982": 1,
        "exception:d18a4c7ce6e8a476": 1,
        "narrow:1e09dec6a78e03b3": 1,
        "narrow:3b1419e7d16dfc9d": 1,
        "narrow:54f95c357e814b76": 1,
        "narrow:62e2871fe4c983a6": 1,
        "narrow:7d158f804985f99d": 1,
        "narrow:932031f78338a2b7": 1,
        "narrow:b4053c7e030145bb": 1,
        "narrow:d9e963bab31fc662": 1,
        "narrow:eb694554cb812556": 1,
    },
    "backend/packages/harness/alpha/coding/repo_twin/symbol_graph.py": {"exception:349d619249a5044a": 1, "exception:711d7455130f087e": 1},
    "backend/packages/harness/alpha/coding/structural_intelligence/polyglot_cst.py": {"exception:905e7544a3d21816": 1, "exception:e80fe97eca4146c6": 1},
    "backend/packages/harness/alpha/coding/structural_intelligence/symbol_dependency_graph.py": {"exception:a12c5bea69e380e3": 1},
    "backend/packages/harness/alpha/commands/module_a_handlers.py": {"narrow:ac82a83b09932fae": 1},
    "backend/packages/harness/alpha/community/agent_eye/provider.py": {
        "exception:0a8b8244d2381485": 1,
        "narrow:143aff191d598ca5": 1,
        "narrow:1c8ebd307a07dd13": 1,
        "narrow:3f5fbd1d59eff7e3": 2,
        "narrow:54ee2607dfa2f6ea": 1,
        "narrow:aceca6d99966c835": 1,
        "narrow:b92733b80122965d": 1,
    },
    "backend/packages/harness/alpha/community/agent_eye/safe_fetch.py": {"narrow:41821f316a6ece0f": 1, "narrow:54ee2607dfa2f6ea": 1},
    "backend/packages/harness/alpha/community/aio_sandbox/aio_sandbox_provider.py": {"narrow:7d361b926a93a38b": 1},
    "backend/packages/harness/alpha/community/aio_sandbox/backend.py": {"narrow:07f552ebbce31efe": 1, "narrow:138ae690ac3500a1": 1, "narrow:2f73fa6daac06c53": 1, "narrow:651a6ae6eb603588": 1},
    "backend/packages/harness/alpha/community/aio_sandbox/local_backend.py": {
        "exception:349d619249a5044a": 1,
        "narrow:34e28dce35f3c779": 2,
        "narrow:b92733b80122965d": 1,
        "narrow:c49d0ab4b037f58e": 1,
        "narrow:d1c24951ed1f12a5": 1,
        "narrow:dc91e12039e1db06": 1,
        "narrow:e4ef30923d4bb660": 1,
        "narrow:fc4b297ccd30d43b": 1,
    },
    "backend/packages/harness/alpha/community/aio_sandbox/network_proxy.py": {
        "narrow:265818384af42676": 1,
        "narrow:34e28dce35f3c779": 1,
        "narrow:37b6693d9b75032d": 1,
        "narrow:3ea83061d6d2c7a8": 1,
        "narrow:5f28c80209f3786a": 1,
        "narrow:6cf43cc613953f5d": 1,
        "narrow:8f15bd100df667b6": 1,
        "narrow:941df9da61d32729": 1,
        "narrow:ac191c402ba9108d": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:bc3e428439269705": 1,
        "narrow:ce1f51c5f32bedfc": 1,
        "narrow:d0c88b663b2caa8e": 1,
        "narrow:d5c30b1c9c1a9422": 1,
        "narrow:dc91e12039e1db06": 1,
        "narrow:e1e6a36d471dae13": 1,
    },
    "backend/packages/harness/alpha/community/boxlite/box.py": {"narrow:b58e527161168a03": 2, "narrow:ec3a6f19e288d7d6": 1},
    "backend/packages/harness/alpha/community/brave/tools.py": {"narrow:6ac831f8b4889153": 1, "narrow:b92733b80122965d": 1, "narrow:f3ef1696564e24dd": 1},
    "backend/packages/harness/alpha/community/browser_automation/session.py": {
        "exception:2a340d53d53c44bd": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:b2a765d24dc0a558": 1,
        "exception:b9988ddca2388f56": 1,
        "narrow:1655050fa13ad218": 1,
    },
    "backend/packages/harness/alpha/community/browser_automation/tools.py": {"narrow:9c34201b421d5c38": 1},
    "backend/packages/harness/alpha/community/browserless/browserless_client.py": {"narrow:135547b0a535ea6f": 1, "narrow:f399ec0205f60f36": 1},
    "backend/packages/harness/alpha/community/browserless/tools.py": {"narrow:9c34201b421d5c38": 1},
    "backend/packages/harness/alpha/community/crawl4ai/crawl4ai_client.py": {"narrow:4d90bdaf3df7649a": 1, "narrow:8e984a619845d0de": 1},
    "backend/packages/harness/alpha/community/ddg_search/tools.py": {"narrow:3f5fbd1d59eff7e3": 1},
    "backend/packages/harness/alpha/community/e2b_sandbox/e2b_sandbox.py": {"exception:349d619249a5044a": 1, "narrow:526ad924c96f6861": 1, "narrow:b58e527161168a03": 2, "narrow:ca2995b580ef9e52": 1},
    "backend/packages/harness/alpha/community/e2b_sandbox/e2b_sandbox_provider.py": {
        "exception:349d619249a5044a": 8,
        "exception:c1d5dcea36250320": 1,
        "narrow:475743d248b88f83": 1,
        "narrow:81caa92cadfc7c7d": 1,
        "narrow:9250eace91b7d8ef": 1,
        "narrow:c5ee664891af46b0": 1,
        "narrow:e195278544059d0f": 1,
    },
    "backend/packages/harness/alpha/community/jina_ai/jina_client.py": {"narrow:3f5fbd1d59eff7e3": 1},
    "backend/packages/harness/alpha/community/jina_ai/tools.py": {"narrow:3f5fbd1d59eff7e3": 1},
    "backend/packages/harness/alpha/community/opensandbox/provider.py": {"narrow:2f73fa6daac06c53": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/community/opensandbox/sandbox.py": {"narrow:b58e527161168a03": 1},
    "backend/packages/harness/alpha/community/ragflow/client.py": {"narrow:f436785ec2596a05": 1},
    "backend/packages/harness/alpha/community/ragflow/formatting.py": {"narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/community/search_federation/budgets.py": {"narrow:e21ad3d0c9f5f69e": 1},
    "backend/packages/harness/alpha/community/search_federation/federation.py": {"narrow:e21ad3d0c9f5f69e": 1},
    "backend/packages/harness/alpha/community/search_federation/providers.py": {"narrow:5f43f8c1c7ab0253": 1, "narrow:c359ce204af964b1": 1, "narrow:d3126e7c9bd336c4": 1},
    "backend/packages/harness/alpha/community/search_federation/tools.py": {"narrow:3f5fbd1d59eff7e3": 2},
    "backend/packages/harness/alpha/community/serper/tools.py": {"narrow:3f5fbd1d59eff7e3": 1, "narrow:6ac831f8b4889153": 1, "narrow:b92733b80122965d": 1, "narrow:f3ef1696564e24dd": 1},
    "backend/packages/harness/alpha/community/sofya/tools.py": {"narrow:3f5fbd1d59eff7e3": 2},
    "backend/packages/harness/alpha/community/tenki/sandbox.py": {"narrow:b58e527161168a03": 2},
    "backend/packages/harness/alpha/community/url_safety.py": {
        "narrow:291ea14f5a39e61c": 1,
        "narrow:329d3464fc417df7": 1,
        "narrow:5539122cbbc55144": 1,
        "narrow:981084f04f2cd3f3": 1,
        "narrow:a94ae96d226d5e09": 1,
        "narrow:b58e527161168a03": 1,
        "narrow:d4fa7b623a11e223": 1,
    },
    "backend/packages/harness/alpha/company/discovery.py": {"narrow:51ad5102ce871e6d": 1},
    "backend/packages/harness/alpha/company/enterprise_kanban.py": {"exception:b35b75f6f6b2d3fc": 1},
    "backend/packages/harness/alpha/company/organization.py": {"narrow:106bd46e3cc08c99": 1},
    "backend/packages/harness/alpha/computer_use/accessibility.py": {
        "exception:0af8fdfda133adf1": 1,
        "exception:11a2a5a0e47132ee": 1,
        "exception:293ebbadefe66fbb": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:a12c5bea69e380e3": 4,
        "narrow:4d6adfe87c800841": 1,
    },
    "backend/packages/harness/alpha/computer_use/dispatcher.py": {"exception:79440e8db4a9e936": 2, "narrow:9d7490dcd5fda6b9": 1},
    "backend/packages/harness/alpha/computer_use/guard.py": {"narrow:f90b520f90fd36d4": 1},
    "backend/packages/harness/alpha/computer_use/screen.py": {"narrow:0db8fda763a94551": 1, "narrow:5388c6fd8f3f3fd6": 1, "narrow:7ffb80819c852951": 1},
    "backend/packages/harness/alpha/computer_use/system_one_policy.py": {"exception:79440e8db4a9e936": 2, "narrow:378fbaebe7772089": 1, "narrow:844558b9558f8597": 1, "narrow:c359ce204af964b1": 3},
    "backend/packages/harness/alpha/config/app_config.py": {"exception:711d7455130f087e": 1, "narrow:1e32b1b62cf17e1f": 1, "narrow:ac191c402ba9108d": 1, "narrow:d0e1dd8aacc07296": 1},
    "backend/packages/harness/alpha/config/checkpointer_config.py": {"narrow:5a47ab761773993a": 1},
    "backend/packages/harness/alpha/config/extensions_config.py": {"narrow:1e09dec6a78e03b3": 1, "narrow:5a47ab761773993a": 1},
    "backend/packages/harness/alpha/config/file_signature.py": {"narrow:ac191c402ba9108d": 1, "narrow:ecc31f62eba3b373": 1},
    "backend/packages/harness/alpha/config/sandbox_config.py": {"narrow:34e28dce35f3c779": 1},
    "backend/packages/harness/alpha/config/self_tuning/dynamics.py": {"narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/config/self_tuning/validate.py": {"exception:e751bedd0662c99b": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/context/micro_compaction.py": {"narrow:05ae338c2fab25e8": 1},
    "backend/packages/harness/alpha/context/retention.py": {"narrow:3c96eba5bf2a10ea": 1},
    "backend/packages/harness/alpha/context_files.py": {"exception:79440e8db4a9e936": 1, "narrow:7d158f804985f99d": 1, "narrow:b4053c7e030145bb": 1},
    "backend/packages/harness/alpha/debugging/interactive_repl_dap.py": {"exception:2c73163138ea4a05": 1},
    "backend/packages/harness/alpha/deepagent/workspace.py": {"narrow:2ceb709018cfbbb0": 1, "narrow:7d158f804985f99d": 3, "narrow:8a686f48cf4bfe87": 2},
    "backend/packages/harness/alpha/deliberation/parsing.py": {"narrow:9da041a36f08e19d": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/deliberation/router.py": {
        "narrow:34e28dce35f3c779": 2,
        "narrow:484e57d1b0385add": 1,
        "narrow:883092bd804f53b7": 1,
        "narrow:a13cf98ffe7a17b7": 1,
        "narrow:f035032c048f604b": 1,
    },
    "backend/packages/harness/alpha/deliberation/verifier.py": {"narrow:60860ed8acc707e6": 1},
    "backend/packages/harness/alpha/editing/structural_ast_reconciler.py": {"exception:0b9c77a8f5fa95a1": 1},
    "backend/packages/harness/alpha/epistemics/jev_belief.py": {"narrow:2f53bfdbbc289ac1": 1, "narrow:bfc3a9c8f95ed164": 1},
    "backend/packages/harness/alpha/evaluation/autonomous_benchmark_harness.py": {"narrow:a6ee04e0fe92cbd5": 1},
    "backend/packages/harness/alpha/evaluation/system_one_calibration.py": {
        "exception:09811819c8cc877a": 1,
        "exception:349d619249a5044a": 2,
        "exception:d4ba098c311e1f9f": 1,
        "narrow:06152bb4adc81ad5": 1,
        "narrow:1be46c02c6a2bc46": 1,
        "narrow:462533f61ec551e4": 1,
        "narrow:5388c6fd8f3f3fd6": 1,
        "narrow:7c6158d4e94ed8d8": 1,
        "narrow:b391aa43f0a0172f": 1,
        "narrow:b58e527161168a03": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:c3f78f26b9533034": 1,
    },
    "backend/packages/harness/alpha/events/bus.py": {"narrow:1c011b4987d49f74": 1, "narrow:820e11f2ec1671bb": 1, "narrow:c1feefd89b904ac0": 1},
    "backend/packages/harness/alpha/evidence/store.py": {"narrow:6d8afeffb21574f0": 1},
    "backend/packages/harness/alpha/evolution/engine.py": {"narrow:a235314f9839362e": 1, "narrow:e21ad3d0c9f5f69e": 1},
    "backend/packages/harness/alpha/evolution/evidence/gates.py": {"narrow:157e43db01022f84": 1},
    "backend/packages/harness/alpha/evolution/evidence/measure.py": {"narrow:1014416f4ee4f8eb": 1},
    "backend/packages/harness/alpha/evolution/evidence/provenance.py": {
        "narrow:5388c6fd8f3f3fd6": 2,
        "narrow:68abaf52ba6adf85": 1,
        "narrow:7e1291807a10d793": 1,
        "narrow:80cc5db5d97623db": 1,
        "narrow:d31e7a23362ef215": 1,
        "narrow:e195278544059d0f": 1,
    },
    "backend/packages/harness/alpha/evolution/git_source.py": {"narrow:257b6cbd884d0690": 1, "narrow:b92733b80122965d": 1, "narrow:bd9291872f3b7bc9": 1},
    "backend/packages/harness/alpha/evolution/identity.py": {"narrow:5a47ab761773993a": 1, "narrow:a155ca0e192c456a": 1},
    "backend/packages/harness/alpha/evolution/release_check.py": {"narrow:2706c7bf8ad101c8": 1, "narrow:4dfc880ecac40134": 1},
    "backend/packages/harness/alpha/evolution/update_engine.py": {
        "narrow:1d4da65cc8479088": 1,
        "narrow:1e7e37d128f0b33f": 1,
        "narrow:4de06c1c60fef487": 2,
        "narrow:50972560403d78a3": 1,
        "narrow:76e3499ce0683068": 1,
        "narrow:905efecd623b9589": 1,
        "narrow:ad615969920cda90": 2,
        "narrow:dc91e12039e1db06": 2,
    },
    "backend/packages/harness/alpha/evolution/update_policy.py": {"narrow:7f91175c83f3774a": 1},
    "backend/packages/harness/alpha/evolution/update_state.py": {
        "narrow:06e74f87b6381f2b": 1,
        "narrow:0c9ad537b1521a51": 1,
        "narrow:5075e54ffb878ff3": 1,
        "narrow:5a47ab761773993a": 1,
        "narrow:7bf888e254db0b96": 1,
        "narrow:7e1291807a10d793": 1,
        "narrow:9c9b5cc53f108d88": 1,
        "narrow:9ce26907fa4a6e03": 1,
        "narrow:a235314f9839362e": 1,
        "narrow:d29f9c23808fb2ea": 1,
        "narrow:db6e6d5391baaa73": 1,
        "narrow:e1e6a36d471dae13": 2,
        "narrow:e21ad3d0c9f5f69e": 1,
        "narrow:f8de6a6d30e86b2e": 1,
    },
    "backend/packages/harness/alpha/extensions/cli.py": {"narrow:0af738c498abb109": 2},
    "backend/packages/harness/alpha/extensions/loader.py": {"narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/extensions/manager.py": {
        "narrow:17a2d879efe5367f": 1,
        "narrow:a4045894221a3d72": 1,
        "narrow:a4f8fa2aeae39bb1": 1,
        "narrow:dc91e12039e1db06": 2,
        "narrow:e8ef8cdadf5ced09": 1,
        "narrow:eb694554cb812556": 1,
    },
    "backend/packages/harness/alpha/extensions/notify.py": {"narrow:1aff94f457419b3b": 1},
    "backend/packages/harness/alpha/goals/store.py": {"narrow:c999b9297efbe32c": 1},
    "backend/packages/harness/alpha/groups/runner.py": {"exception:349d619249a5044a": 1, "exception:64c9b49264bca0b5": 1, "exception:bc616c9f1e2d433b": 1, "exception:bfba3dc59a2c4494": 1},
    "backend/packages/harness/alpha/groups/service.py": {"exception:314cfce1f0183ad5": 1, "exception:8f25843c4973f0a2": 1, "exception:d11dcdb77e88982f": 1},
    "backend/packages/harness/alpha/harness/continual/snapshots.py": {"exception:078183e4dd74b919": 1, "exception:cf572afe1a197698": 1},
    "backend/packages/harness/alpha/harness/continual/state.py": {"exception:349d619249a5044a": 1, "narrow:ac191c402ba9108d": 1},
    "backend/packages/harness/alpha/harness/continuous/store.py": {"exception:349d619249a5044a": 2},
    "backend/packages/harness/alpha/integrations/lark_broker.py": {
        "controlflow:596f4c1030f3ad05": 1,
        "exception:81ddb723f67972f9": 1,
        "narrow:1b9af099fa5d11dc": 1,
        "narrow:5fc11165d5a5b452": 1,
        "narrow:c04d56381e288dad": 1,
    },
    "backend/packages/harness/alpha/integrations/lark_cli.py": {
        "controlflow:8956108b16af7a8b": 1,
        "exception:333ed0f44ae8a213": 1,
        "exception:349d619249a5044a": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:9b35befbbcb53bd5": 1,
        "exception:fa8444c533ef6401": 1,
        "narrow:0927aee92fbc2c4f": 1,
        "narrow:194d452e5317a846": 2,
        "narrow:1b1ad457b6819765": 1,
        "narrow:2eeff6ebec6fd7fc": 1,
        "narrow:3ef260e153e0718a": 1,
        "narrow:5573cb08e4007728": 1,
        "narrow:6c5539acfb9a387c": 1,
        "narrow:7d361b926a93a38b": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:e195278544059d0f": 1,
        "narrow:e21ad3d0c9f5f69e": 1,
        "narrow:eb1508ec2b2aa6f7": 1,
    },
    "backend/packages/harness/alpha/jobs/runner.py": {
        "controlflow:30e6717a90910e6a": 2,
        "exception:349d619249a5044a": 1,
        "exception:433f212831894f62": 1,
        "narrow:0b38e33fc3c4fbd5": 1,
        "narrow:588e824adc5023b8": 1,
    },
    "backend/packages/harness/alpha/kanban/store.py": {"exception:349d619249a5044a": 2},
    "backend/packages/harness/alpha/knowledge/codebase_knowledge_lake.py": {"exception:8129be5554acc759": 1},
    "backend/packages/harness/alpha/knowledge/self_documentation.py": {"narrow:3ceddbce9756539d": 1, "narrow:7d158f804985f99d": 2, "narrow:b722448b492bed4e": 1},
    "backend/packages/harness/alpha/learning/autonomous_curator.py": {"exception:349d619249a5044a": 1, "exception:caa35676619a34b9": 1, "narrow:f85552866e8aa287": 1},
    "backend/packages/harness/alpha/learning/experience/store.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/learning/graph.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/learning/insights.py": {"narrow:ab5f43ab438c1348": 1},
    "backend/packages/harness/alpha/learning/store.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/ledger/store.py": {"narrow:a24e08ea291c5705": 1},
    "backend/packages/harness/alpha/lineage/semantic_git_delta.py": {
        "exception:0af8fdfda133adf1": 1,
        "exception:79440e8db4a9e936": 1,
        "narrow:54f95c357e814b76": 1,
        "narrow:7d158f804985f99d": 1,
        "narrow:ba2a8186ee03e6e9": 1,
    },
    "backend/packages/harness/alpha/logging_config.py": {
        "exception:308b67324eb594ed": 1,
        "exception:84ef9e1ff189c3dc": 1,
        "exception:e9a462dc923110be": 1,
        "exception:f8a04cf5db1b76f1": 1,
        "exception:fd60606b3454f5ac": 1,
    },
    "backend/packages/harness/alpha/mcp/cache.py": {"narrow:f3c928214c9aa124": 1},
    "backend/packages/harness/alpha/mcp/context_headers.py": {"exception:79440e8db4a9e936": 1},
    "backend/packages/harness/alpha/mcp/gateway.py": {"narrow:34341200b4423091": 1, "narrow:cdcbbda6730a81ac": 1},
    "backend/packages/harness/alpha/mcp/headers.py": {"narrow:5d3280caabced1b1": 1},
    "backend/packages/harness/alpha/mcp/oauth.py": {"controlflow:30e6717a90910e6a": 1, "narrow:fbb300293546c25c": 1},
    "backend/packages/harness/alpha/mcp/session_pool.py": {"controlflow:13e966fa2c872573": 1, "narrow:2ebb304b775bf542": 1, "narrow:39710aee73e04316": 1, "narrow:bfc3a9c8f95ed164": 3},
    "backend/packages/harness/alpha/mcp/tools.py": {
        "narrow:0daf3a80758b6837": 1,
        "narrow:7d158f804985f99d": 1,
        "narrow:a11451d007bde50e": 1,
        "narrow:ac191c402ba9108d": 3,
        "narrow:b92733b80122965d": 1,
        "narrow:ee4c987086cc034e": 1,
    },
    "backend/packages/harness/alpha/mcp/user_scoped_auth.py": {"exception:79440e8db4a9e936": 1},
    "backend/packages/harness/alpha/media/stt.py": {"exception:9b35befbbcb53bd5": 1, "narrow:3466b6b45b077898": 1, "narrow:815a4c7d7ee456fc": 1},
    "backend/packages/harness/alpha/memory/active_memory.py": {"narrow:4e5f32b529f61e85": 1},
    "backend/packages/harness/alpha/memory/affective/extraction.py": {"narrow:eb694554cb812556": 1, "narrow:fc7312aa2cde9661": 1},
    "backend/packages/harness/alpha/memory/affective/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/affective/recall.py": {"narrow:3f5fbd1d59eff7e3": 1},
    "backend/packages/harness/alpha/memory/codebase/hotspots.py": {"narrow:5388c6fd8f3f3fd6": 1, "narrow:b92733b80122965d": 1, "narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/memory/codebase/indexer.py": {
        "narrow:0d44a9d8784b8535": 1,
        "narrow:59bc37c793dca6a8": 1,
        "narrow:68abaf52ba6adf85": 1,
        "narrow:ac191c402ba9108d": 1,
        "narrow:b58e527161168a03": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:e195278544059d0f": 1,
    },
    "backend/packages/harness/alpha/memory/codebase/paths.py": {"narrow:5a47ab761773993a": 1, "narrow:8753842c1d9d10d4": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/memory/cognitive/episodic_memory.py": {"narrow:f3f197032bb4dfd8": 1},
    "backend/packages/harness/alpha/memory/cognitive/procedural_memory.py": {"narrow:32c7d51b3bbcbd4b": 1},
    "backend/packages/harness/alpha/memory/cognitive/semantic_graph.py": {"narrow:69244ab5e457cc5e": 1},
    "backend/packages/harness/alpha/memory/cognitive_memory_tiering.py": {"narrow:59b23936356091f2": 1},
    "backend/packages/harness/alpha/memory/entities/extraction.py": {"narrow:b92733b80122965d": 1, "narrow:c2b4fc877847ebb4": 1, "narrow:c359ce204af964b1": 2, "narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/entities/memory.py": {"narrow:5388c6fd8f3f3fd6": 1},
    "backend/packages/harness/alpha/memory/entities/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/fabric/lifecycle.py": {"narrow:63bc7b7d9c08eccc": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/memory/fabric/models.py": {"narrow:5388c6fd8f3f3fd6": 1},
    "backend/packages/harness/alpha/memory/fabric/provenance.py": {"exception:823650847711b57c": 1},
    "backend/packages/harness/alpha/memory/fabric/store.py": {"narrow:07fb007f12be721a": 1},
    "backend/packages/harness/alpha/memory/fabric/temporal.py": {"narrow:63bc7b7d9c08eccc": 1, "narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/memory/fusion/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/fusion/stages.py": {"narrow:bfbb8267019dddca": 1},
    "backend/packages/harness/alpha/memory/health/health.py": {"exception:79440e8db4a9e936": 1, "narrow:0a3767c6cb97272b": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/memory/health/metrics.py": {"exception:79440e8db4a9e936": 1, "narrow:0a3767c6cb97272b": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/memory/health/probes.py": {
        "exception:79440e8db4a9e936": 1,
        "narrow:0a3767c6cb97272b": 1,
        "narrow:0ddeecf9153032fe": 1,
        "narrow:55f7b9add128cec8": 1,
        "narrow:70560dfc8319a708": 1,
        "narrow:b92733b80122965d": 1,
    },
    "backend/packages/harness/alpha/memory/health/slo.py": {"exception:79440e8db4a9e936": 1, "narrow:0a3767c6cb97272b": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/memory/narrative/memory.py": {"narrow:74c750dabdba4aa1": 2},
    "backend/packages/harness/alpha/memory/narrative/models.py": {"narrow:04ff7703b37ff2a5": 1},
    "backend/packages/harness/alpha/memory/narrative/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/narrative/synthesis.py": {"narrow:3abb4831744a4382": 1, "narrow:4e3a2b7bca2afb72": 1, "narrow:f765a891cafb4676": 1},
    "backend/packages/harness/alpha/memory/policy/provenance.py": {"narrow:b4053c7e030145bb": 1, "narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/prospective/lifecycle.py": {"narrow:681be52757842b68": 1, "narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/memory/prospective/models.py": {"narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/memory/prospective/recall.py": {"narrow:5b43e3494a01ee84": 1},
    "backend/packages/harness/alpha/memory/prospective/store.py": {"narrow:844558b9558f8597": 1, "narrow:932031f78338a2b7": 1},
    "backend/packages/harness/alpha/memory/prospective/triggers.py": {"narrow:1be46c02c6a2bc46": 1, "narrow:69f0421e69f7e940": 1, "narrow:c359ce204af964b1": 2, "narrow:de5232b77ef31507": 2},
    "backend/packages/harness/alpha/memory/rerank.py": {"narrow:35a088c5bf535bce": 1},
    "backend/packages/harness/alpha/memory/scenarios/classifier.py": {
        "narrow:1d12c96c65db239f": 1,
        "narrow:31ed38bca7aefb08": 1,
        "narrow:554221a80d4e840e": 1,
        "narrow:873f296ca8b7a60f": 1,
        "narrow:ca7f975bbc28446d": 1,
        "narrow:d6c7abc52cc98700": 1,
        "narrow:eb694554cb812556": 1,
        "narrow:faf1b955b23e60ad": 1,
    },
    "backend/packages/harness/alpha/memory/scenarios/config.py": {"narrow:747d76521585de2d": 2, "narrow:e241e39e85a10935": 1},
    "backend/packages/harness/alpha/memory/scenarios/models.py": {"narrow:4db224f4ba7cf51a": 1, "narrow:5a71550ef44f5a8d": 1, "narrow:747d76521585de2d": 1},
    "backend/packages/harness/alpha/memory/scenarios/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/scenarios/recall.py": {"narrow:2de9b3572a88ddc0": 1, "narrow:f656f073a34f2889": 1},
    "backend/packages/harness/alpha/memory/scenarios/router.py": {"exception:700b2841573219d1": 1, "exception:711d7455130f087e": 1, "narrow:afdcabe050c84dd5": 1},
    "backend/packages/harness/alpha/memory/session_search.py": {"exception:b03ef1ca826219bf": 1, "narrow:ad2cdec6e51eb941": 1},
    "backend/packages/harness/alpha/memory/social/provenance.py": {"narrow:eb694554cb812556": 1},
    "backend/packages/harness/alpha/memory/social/summary.py": {
        "exception:0952ae78604b3912": 2,
        "exception:0af8fdfda133adf1": 1,
        "exception:312efb6012528642": 1,
        "exception:c15685fb70dcc8e3": 1,
        "exception:f0b93367da8932e0": 1,
        "narrow:23d84abff4b95fa6": 1,
        "narrow:82bf6428faaca4de": 1,
    },
    "backend/packages/harness/alpha/memory/utility/dedup.py": {"narrow:0d9639c7966ae80f": 1, "narrow:844558b9558f8597": 2},
    "backend/packages/harness/alpha/memory/utility/feedback.py": {
        "narrow:48653552c70c5cdc": 1,
        "narrow:5388c6fd8f3f3fd6": 1,
        "narrow:844558b9558f8597": 1,
        "narrow:a2843097def5b1d0": 2,
        "narrow:f5970060c5cf8294": 1,
    },
    "backend/packages/harness/alpha/memory/utility/models.py": {"narrow:b1d7086cd73239a8": 1},
    "backend/packages/harness/alpha/memory/utility/paths.py": {"narrow:ac191c402ba9108d": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/memory/utility/scoring.py": {
        "narrow:48653552c70c5cdc": 1,
        "narrow:844558b9558f8597": 1,
        "narrow:86f8f1a1cade2150": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:dc8d2ce6ded3220f": 1,
    },
    "backend/packages/harness/alpha/memory/utility/signals.py": {"narrow:b1324ae934d30fcc": 1, "narrow:fe99eb2c85428ad9": 1},
    "backend/packages/harness/alpha/memory/utility/store.py": {"narrow:ac191c402ba9108d": 1},
    "backend/packages/harness/alpha/memory/wiki_vault/vault.py": {
        "exception:9b35befbbcb53bd5": 1,
        "narrow:20c8242a952b12a7": 1,
        "narrow:7d158f804985f99d": 5,
        "narrow:b58e527161168a03": 2,
        "narrow:b6e01dddd7293d85": 1,
        "narrow:e1e6a36d471dae13": 2,
    },
    "backend/packages/harness/alpha/metacognition/calibration.py": {"narrow:4cabad9c5f13c3e1": 1, "narrow:81b5cedfd66902a3": 1},
    "backend/packages/harness/alpha/metacompiler/compiler.py": {"narrow:3194301a10396e18": 1, "narrow:5825366b62fa7db8": 1, "narrow:6c8e0bcd9ff11c50": 1},
    "backend/packages/harness/alpha/metacompiler/dynamic_tool_synthesizer.py": {"narrow:0fc2401dd1758322": 1, "narrow:5759db9ff8c0ebff": 1, "narrow:815a4c7d7ee456fc": 1},
    "backend/packages/harness/alpha/mission/acceptance.py": {"narrow:a7a08ab60c08897d": 1},
    "backend/packages/harness/alpha/mission/lifecycle.py": {"exception:711d7455130f087e": 1, "narrow:b58e527161168a03": 1},
    "backend/packages/harness/alpha/missions/store.py": {"exception:83d81f353da90fee": 1, "exception:bda815843b07c0b3": 1, "exception:d5c0274caa6acd99": 1},
    "backend/packages/harness/alpha/models/assistant_payload_replay.py": {"narrow:f2899369e1ff241f": 1},
    "backend/packages/harness/alpha/models/claude_provider.py": {"narrow:fdc1c48ee1bc6fb8": 1},
    "backend/packages/harness/alpha/models/cost_governor.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/models/escalation.py": {"narrow:f25869c8e39746f7": 1},
    "backend/packages/harness/alpha/models/free_router/catalog.py": {"narrow:1ccffad732f31f44": 1, "narrow:e21ad3d0c9f5f69e": 1},
    "backend/packages/harness/alpha/models/free_router/chat_model.py": {"narrow:77e43fc3390dc8eb": 1, "narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/models/free_router/providers.py": {"exception:79440e8db4a9e936": 1, "narrow:d3126e7c9bd336c4": 2},
    "backend/packages/harness/alpha/models/local.py": {"narrow:6156c53cdd874fb4": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/models/local_llm_failover.py": {"exception:349d619249a5044a": 1, "exception:6520f46251e542d5": 1},
    "backend/packages/harness/alpha/models/mindie_provider.py": {"narrow:7b1cc89d2f6f0a1d": 1, "narrow:b199b1afb6b43c58": 1},
    "backend/packages/harness/alpha/models/openai_codex_provider.py": {"exception:9707ec50c64370ab": 1, "narrow:89163bbf3eea535e": 2},
    "backend/packages/harness/alpha/models/patched_mimo.py": {"narrow:d6f518bc9cc4de85": 1},
    "backend/packages/harness/alpha/models/patched_stepfun.py": {"narrow:d6f518bc9cc4de85": 1},
    "backend/packages/harness/alpha/models/provider_manager.py": {"exception:18c34e021700a954": 1, "exception:2148926b4da8f889": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/models/system_one.py": {
        "exception:79440e8db4a9e936": 1,
        "exception:9b35befbbcb53bd5": 1,
        "narrow:03db3a8279950545": 1,
        "narrow:1be46c02c6a2bc46": 2,
        "narrow:2e68d05fb2360dd1": 2,
        "narrow:538d7985a353d28b": 1,
        "narrow:7bba0c578a612c29": 1,
        "narrow:844558b9558f8597": 2,
        "narrow:bdf0c7bc245ff9ac": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:dc91e12039e1db06": 1,
    },
    "backend/packages/harness/alpha/models/task_router.py": {"exception:1e5f38f13bd84b7b": 2, "narrow:1bf79dcb2ad1ce6a": 1},
    "backend/packages/harness/alpha/models/vllm_provider.py": {"narrow:214d5a5259a100b2": 1, "narrow:e2cf3a5db3f3eca3": 2},
    "backend/packages/harness/alpha/multimodal/chain.py": {"exception:7a0595eb1519e167": 1, "narrow:24ce692d7d11f1db": 1, "narrow:d7baaa01ab68b6a3": 1},
    "backend/packages/harness/alpha/multimodal/engines/local.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/multimodal/engines/provider.py": {"exception:1d069ef7c891bc19": 1},
    "backend/packages/harness/alpha/multimodal/local_models.py": {"narrow:29cdd267784e6359": 1, "narrow:e1e6a36d471dae13": 1},
    "backend/packages/harness/alpha/multimodal/wakeword.py": {"narrow:598c4f32a5e65833": 1},
    "backend/packages/harness/alpha/observability/contract.py": {"narrow:afc7112d7e389810": 1},
    "backend/packages/harness/alpha/observability/ids.py": {"narrow:42c1611be51d293e": 1},
    "backend/packages/harness/alpha/observability/recorder.py": {"exception:f1eefeeb96519091": 1},
    "backend/packages/harness/alpha/observability/taxonomy.py": {"exception:9b4e880d2897f50b": 1},
    "backend/packages/harness/alpha/observability/trace/codes.py": {"exception:f3a231f6ab24e83f": 1, "narrow:54f7ac60ce203744": 1},
    "backend/packages/harness/alpha/observability/trace/query.py": {"narrow:d97f7db9c509511b": 1},
    "backend/packages/harness/alpha/observability/trace/sinks.py": {"narrow:1aff94f457419b3b": 1},
    "backend/packages/harness/alpha/observability/trace/writer.py": {"exception:349d619249a5044a": 1, "exception:ec9b116688fe044e": 1},
    "backend/packages/harness/alpha/ops/autonomy_truth.py": {"narrow:b92733b80122965d": 2, "narrow:b9cedefd0e379af5": 1, "narrow:f7bf830e7c20afb9": 1, "narrow:fdcae6b323c38cf5": 1},
    "backend/packages/harness/alpha/ops/event_loop.py": {"narrow:39710aee73e04316": 1, "narrow:6a10cd30ae6fba9c": 1},
    "backend/packages/harness/alpha/ops/monitor.py": {
        "exception:94a266eb9d2d4642": 1,
        "exception:a00402293a624456": 1,
        "narrow:5608d39a1f4ce71a": 1,
        "narrow:6019a7424db749c3": 1,
        "narrow:7c0871a8af6f9832": 1,
    },
    "backend/packages/harness/alpha/ops/recovery_brief.py": {"narrow:40d0619d02d10a8c": 1},
    "backend/packages/harness/alpha/orchestration/goals.py": {"narrow:9ce26907fa4a6e03": 1},
    "backend/packages/harness/alpha/orchestration/intent.py": {"exception:79440e8db4a9e936": 1},
    "backend/packages/harness/alpha/orchestrator/context_engine_plugin.py": {"exception:349d619249a5044a": 2, "exception:a12c5bea69e380e3": 4},
    "backend/packages/harness/alpha/orchestrator/executors.py": {"narrow:6c2e5b6b5ec4becf": 1},
    "backend/packages/harness/alpha/orchestrator/memory_recall.py": {"narrow:2f51efd9604c7194": 1},
    "backend/packages/harness/alpha/orchestrator/mode_mapper.py": {"narrow:03669d02d23c4551": 1},
    "backend/packages/harness/alpha/orchestrator/replay.py": {"narrow:34e28dce35f3c779": 1},
    "backend/packages/harness/alpha/peer_network/discovery.py": {
        "narrow:3c11fbdeba135100": 1,
        "narrow:4f6ff8fe1cc59479": 1,
        "narrow:58a42a75c06bc767": 1,
        "narrow:9c277a75fca3313c": 1,
        "narrow:aa16d2480340afc3": 1,
        "narrow:c2e7120487744ed5": 1,
        "narrow:e195278544059d0f": 1,
    },
    "backend/packages/harness/alpha/peer_network/github.py": {"narrow:83c2466c7ac58eee": 1},
    "backend/packages/harness/alpha/peer_network/identity.py": {
        "exception:349d619249a5044a": 1,
        "exception:a12c5bea69e380e3": 1,
        "narrow:2580e3db9d710f57": 1,
        "narrow:34e28dce35f3c779": 1,
        "narrow:a3e6ce0982753d52": 1,
        "narrow:e195278544059d0f": 4,
    },
    "backend/packages/harness/alpha/peer_network/service.py": {"exception:060122bfaf6b461d": 1, "narrow:5672389e96f04a3c": 1, "narrow:9c34201b421d5c38": 2, "narrow:c0835afc3d57d693": 1},
    "backend/packages/harness/alpha/peer_network/storage.py": {"narrow:3016fcb1f00785df": 1, "narrow:3f5fbd1d59eff7e3": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/peer_network/transport.py": {"narrow:1f2aa1ef1cfb817d": 1, "narrow:760cf8fcdbe4d6f0": 1, "narrow:c202740d49f3af91": 2},
    "backend/packages/harness/alpha/persistence/agents/__init__.py": {"narrow:039c8c927b158373": 1},
    "backend/packages/harness/alpha/persistence/agents/file.py": {"narrow:7d158f804985f99d": 1},
    "backend/packages/harness/alpha/persistence/channel_connections/sql.py": {"exception:a2952aa5d241a5d4": 1},
    "backend/packages/harness/alpha/persistence/json_compat.py": {"narrow:5bdc762712778423": 1},
    "backend/packages/harness/alpha/persistence/managed_subagents/__init__.py": {"exception:eae235c3f869354c": 1},
    "backend/packages/harness/alpha/persistence/managed_subagents/file.py": {"narrow:7d158f804985f99d": 1},
    "backend/packages/harness/alpha/persistence/migrations/_env_filters.py": {"narrow:a3f58bd42c6245a8": 1},
    "backend/packages/harness/alpha/persistence/run/sql.py": {"exception:349d619249a5044a": 2, "narrow:d65c90cdf4206cee": 1},
    "backend/packages/harness/alpha/persistence/storekit/atomic.py": {"narrow:e195278544059d0f": 1, "narrow:e1e6a36d471dae13": 2},
    "backend/packages/harness/alpha/persistence/storekit/config.py": {"narrow:b5983cea1b7efe05": 1, "narrow:e697b00fc4bc2ef5": 1},
    "backend/packages/harness/alpha/persistence/storekit/documents.py": {"narrow:972196ccfd2fd05b": 1},
    "backend/packages/harness/alpha/persistence/storekit/locking.py": {
        "exception:349d619249a5044a": 1,
        "narrow:06d08d2b1ab02c46": 1,
        "narrow:1893701c6f95c99c": 1,
        "narrow:64d2066531f1bf50": 1,
        "narrow:72d4cc5dbdf1a217": 1,
        "narrow:85e49f4b4528019a": 1,
        "narrow:8644ea4eedb64564": 1,
        "narrow:90822556491445ce": 1,
        "narrow:9d69576f16b45019": 1,
        "narrow:c1d8329640ae82d6": 1,
        "narrow:c359ce204af964b1": 1,
        "narrow:e195278544059d0f": 1,
        "narrow:f98ea85e2745f37d": 1,
    },
    "backend/packages/harness/alpha/persistence/storekit/migrations.py": {"narrow:3d225469847e5e60": 1, "narrow:cf7f0ef3247c533a": 1},
    "backend/packages/harness/alpha/persistence/storekit/store.py": {"narrow:0fe02b3d0b17b707": 1, "narrow:3f5fbd1d59eff7e3": 1, "narrow:7cb57cf9b98c67d7": 1, "narrow:7d158f804985f99d": 1},
    "backend/packages/harness/alpha/planning/autonomous.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/planning/bridge.py": {"narrow:d9e963bab31fc662": 1},
    "backend/packages/harness/alpha/planning/profiles.py": {"narrow:6928346b3dc86e2f": 1},
    "backend/packages/harness/alpha/policy/engine.py": {"exception:a207d4ea99c957ab": 1, "exception:bda815843b07c0b3": 1, "exception:d2197e43d98d7a54": 1},
    "backend/packages/harness/alpha/projects/adr_generator.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/projects/audit_council.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/projects/canary_watchdog.py": {"exception:349d619249a5044a": 2},
    "backend/packages/harness/alpha/projects/checkpoint_engine.py": {"exception:349d619249a5044a": 8, "exception:ead0e22ce51dcde7": 1},
    "backend/packages/harness/alpha/projects/constitution.py": {"exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/crew.py": {"exception:50815da4738f336b": 1, "exception:bda815843b07c0b3": 1},
    "backend/packages/harness/alpha/projects/decisions.py": {"exception:bda815843b07c0b3": 1, "exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/events.py": {"exception:06b5fb149e3212cf": 1, "exception:a12c5bea69e380e3": 2, "exception:bda815843b07c0b3": 1, "exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/goals.py": {"exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/handoffs.py": {"exception:bda815843b07c0b3": 1, "exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/locks.py": {"exception:5868ddb6e032a853": 1, "exception:9c7ca789fa23d046": 1, "exception:bda815843b07c0b3": 1},
    "backend/packages/harness/alpha/projects/membership.py": {"exception:bda815843b07c0b3": 1, "exception:d0442452c0c290ee": 1, "exception:d5c0274caa6acd99": 1},
    "backend/packages/harness/alpha/projects/memory_bridge.py": {"narrow:948d61df8716cd2f": 1},
    "backend/packages/harness/alpha/projects/pr_synthesizer.py": {"exception:79440e8db4a9e936": 1},
    "backend/packages/harness/alpha/projects/routing.py": {"exception:c155c70d8d9c7b0a": 1},
    "backend/packages/harness/alpha/projects/self_healing_runner.py": {"narrow:214d5a5259a100b2": 1},
    "backend/packages/harness/alpha/projects/standup_engine.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/projects/state.py": {"exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/workspace.py": {"exception:ccce1167b854ba40": 1},
    "backend/packages/harness/alpha/projects/worktree_hook.py": {"narrow:15398287dd99ca1e": 1},
    "backend/packages/harness/alpha/reasoning/atoms.py": {"narrow:3764c687431dfcc6": 1},
    "backend/packages/harness/alpha/research/backends.py": {"narrow:3ead2f140a248cca": 1},
    "backend/packages/harness/alpha/research/engine.py": {"exception:e5cd126f090804d9": 1, "narrow:25b04091556dc6d3": 1, "narrow:bc0f3557654cdf7d": 1, "narrow:d2de0bb2ac651cf8": 1},
    "backend/packages/harness/alpha/rsi/budgets.py": {"narrow:57eca8ad5d2897b6": 1},
    "backend/packages/harness/alpha/rsi/cooldown.py": {"narrow:9e4bacca1fdb8b99": 1, "narrow:efa2db8f4b2bcb15": 1},
    "backend/packages/harness/alpha/rsi/evaluator_manifest.py": {"narrow:68abaf52ba6adf85": 1},
    "backend/packages/harness/alpha/rsi/generator.py": {"narrow:b4c64a37afd0eb61": 1, "narrow:b7e3052d0837627b": 1, "narrow:d8f57fa281caac89": 1},
    "backend/packages/harness/alpha/rsi/lineage.py": {"narrow:9545699927f55f04": 1, "narrow:a235314f9839362e": 1, "narrow:c359ce204af964b1": 1},
    "backend/packages/harness/alpha/rsi/opportunity.py": {"narrow:0cf321cf9f8e65b4": 1, "narrow:b464dc9f5f16aaaa": 1},
    "backend/packages/harness/alpha/rsi/review.py": {"narrow:9ce26907fa4a6e03": 1, "narrow:b92733b80122965d": 1},
    "backend/packages/harness/alpha/rsi/state.py": {"narrow:cd0dad5b316cf902": 1},
    "backend/packages/harness/alpha/rsi/strategy_memory.py": {"narrow:a235314f9839362e": 1, "narrow:d8f57fa281caac89": 1, "narrow:f73c24848e5e5949": 1},
    "backend/packages/harness/alpha/rsi/switchboard.py": {"exception:032e35c9d2f65018": 1},
    "backend/packages/harness/alpha/rsi/workspace.py": {"narrow:74937c2b6174d704": 1},
    "backend/packages/harness/alpha/rules/hierarchy.py": {"exception:349d619249a5044a": 1, "narrow:80ad8c483a7712a5": 1},
    "backend/packages/harness/alpha/runtime/checkpoint_cache/provider.py": {"exception:e1458cd5b8f183e8": 1},
    "backend/packages/harness/alpha/runtime/checkpointer/provider.py": {"narrow:05673ed4591076e8": 1, "narrow:7fe1bdbd69b5163e": 1},
    "backend/packages/harness/alpha/runtime/environment_auto_healer.py": {"exception:105d3da39162a122": 1},
    "backend/packages/harness/alpha/runtime/escalation.py": {"exception:3a2a5a621f4ebe9d": 1, "narrow:5d081c29dd03bf20": 1, "narrow:7e1291807a10d793": 1, "narrow:b46469f4380bdffc": 1},
    "backend/packages/harness/alpha/runtime/estop.py": {"exception:e10e78c2e06d9e84": 1, "narrow:51ffbb444d47b744": 1, "narrow:d29f9c23808fb2ea": 1},
    "backend/packages/harness/alpha/runtime/events/search.py": {"narrow:d23d3dd8231bb31b": 1},
    "backend/packages/harness/alpha/runtime/events/store/db.py": {"exception:79440e8db4a9e936": 1, "narrow:18aca0094436efaf": 1, "narrow:e6c473be9c3a6433": 1},
    "backend/packages/harness/alpha/runtime/execution_mode.py": {"narrow:d7307730533f71fd": 1},
    "backend/packages/harness/alpha/runtime/journal.py": {
        "exception:711d7455130f087e": 4,
        "exception:b99cc63ea427d573": 1,
        "narrow:39710aee73e04316": 3,
        "narrow:5388c6fd8f3f3fd6": 1,
        "narrow:948d61df8716cd2f": 1,
    },
    "backend/packages/harness/alpha/runtime/lane_scheduler.py": {"exception:4b742697cbe4fbff": 2, "exception:79440e8db4a9e936": 1, "narrow:af6146f1b5f17d54": 1},
    "backend/packages/harness/alpha/runtime/resilience/recovery.py": {"narrow:1be46c02c6a2bc46": 1},
    "backend/packages/harness/alpha/runtime/runs/manager.py": {
        "controlflow:fd0f1600099d8003": 2,
        "exception:47ac3a850c4642c2": 1,
        "exception:75bf480d7fa8d044": 1,
        "exception:c7676e5ee4f9894f": 1,
        "exception:ee21cd36cb24939a": 2,
        "narrow:1b1fba5c9a766438": 1,
        "narrow:bd20cd37a4b67a55": 1,
        "narrow:c359ce204af964b1": 1,
    },
    "backend/packages/harness/alpha/runtime/runs/store/base.py": {"narrow:20b5a6bfa413fe84": 1},
    "backend/packages/harness/alpha/runtime/runs/store/memory.py": {"narrow:1b2dda223042b4c8": 1, "narrow:f2e810fdd73f1fe6": 1, "narrow:fdc1c48ee1bc6fb8": 1},
    "backend/packages/harness/alpha/runtime/runs/worker.py": {
        "controlflow:a48f0b83dac47649": 1,
        "exception:349d619249a5044a": 2,
        "exception:8a20dc09ea7beb79": 1,
        "exception:cebf82a9c48d544d": 1,
        "narrow:1be46c02c6a2bc46": 1,
        "narrow:45e094bb0ddfc0d5": 1,
        "narrow:5038b912b501a817": 2,
        "narrow:50e0ec85dd6ac9ba": 1,
    },
    "backend/packages/harness/alpha/runtime/sentinel/checkpoint.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/runtime/sentinel/cli.py": {"controlflow:d62de6cad92fe0e9": 1},
    "backend/packages/harness/alpha/runtime/sentinel/commit.py": {"narrow:15398287dd99ca1e": 1},
    "backend/packages/harness/alpha/runtime/sentinel/report_store.py": {"narrow:7e1291807a10d793": 1},
    "backend/packages/harness/alpha/runtime/sentinel/sources/scripts.py": {"exception:9b35befbbcb53bd5": 1, "narrow:8e7a1ee8228b82bd": 1, "narrow:e1e6a36d471dae13": 1},
    "backend/packages/harness/alpha/runtime/serialization.py": {"exception:349d619249a5044a": 2, "exception:45028168c846e478": 1, "narrow:a11451d007bde50e": 1},
    "backend/packages/harness/alpha/runtime/store/provider.py": {"narrow:05673ed4591076e8": 1},
    "backend/packages/harness/alpha/runtime/stream_bridge/memory.py": {"narrow:e930f6f311dbb428": 1},
    "backend/packages/harness/alpha/runtime/user_context.py": {"narrow:0525241484b4bb84": 1},
    "backend/packages/harness/alpha/safety/authority/census.py": {"narrow:8e01192c3450e217": 1, "narrow:964a25e8aaef23b9": 1},
    "backend/packages/harness/alpha/safety/authority/config.py": {"narrow:b58e527161168a03": 1},
    "backend/packages/harness/alpha/safety/canary_sandbox.py": {"narrow:15a968f2af48d336": 1},
    "backend/packages/harness/alpha/safety/net_policy.py": {"exception:23a69976a715e80f": 1, "narrow:3777751484172ece": 1, "narrow:57a0030fc304761b": 1},
    "backend/packages/harness/alpha/safety/reversible_delete.py": {"narrow:05f888f8c984d637": 1, "narrow:4eea72106c29e749": 1, "narrow:6311cc5ebcbfaf69": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/safety/self_repo_guard.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/sandbox/container_runner.py": {"exception:349d619249a5044a": 1, "narrow:2eef6afdf1ef6e68": 1},
    "backend/packages/harness/alpha/sandbox/lease.py": {"controlflow:03b69f3629793820": 1},
    "backend/packages/harness/alpha/sandbox/local/list_dir.py": {"narrow:7d158f804985f99d": 1, "narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/sandbox/local/local_sandbox.py": {
        "narrow:350be9283f7afae6": 1,
        "narrow:7b7559c83f965da6": 1,
        "narrow:85e49f4b4528019a": 1,
        "narrow:89aaa2715f48495c": 1,
        "narrow:8f2be8d939e64aa5": 1,
        "narrow:9d69576f16b45019": 1,
        "narrow:cd83170d28010187": 1,
        "narrow:e195278544059d0f": 6,
        "narrow:e1e6a36d471dae13": 1,
    },
    "backend/packages/harness/alpha/sandbox/local/local_sandbox_provider.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/packages/harness/alpha/sandbox/remote_list_dir.py": {"narrow:be2ebbe7d5f95c8f": 1},
    "backend/packages/harness/alpha/sandbox/repl/session.py": {"narrow:15398287dd99ca1e": 1, "narrow:695d793625013aa2": 1, "narrow:bfc3a9c8f95ed164": 1},
    "backend/packages/harness/alpha/sandbox/search.py": {"narrow:7d158f804985f99d": 1, "narrow:d29f9c23808fb2ea": 1},
    "backend/packages/harness/alpha/sandbox/tools.py": {
        "exception:078183e4dd74b919": 1,
        "exception:349d619249a5044a": 6,
        "exception:6ce689f5a4ecbdcd": 1,
        "exception:940650ee29a1c651": 2,
        "exception:c317ce37f2f6956c": 1,
        "exception:e97aa5a602c4aff1": 1,
        "narrow:0145a97a4b465ccf": 2,
        "narrow:03560d3c627a8d5f": 1,
        "narrow:0f164fed8801e33c": 1,
        "narrow:1407e1a366aa39e4": 1,
        "narrow:203ea95fc01bc513": 2,
        "narrow:5135dba86c78c11b": 1,
        "narrow:54af1a8783faea8e": 1,
        "narrow:57b154fb8858bc74": 1,
        "narrow:ac8a75b19b183620": 1,
        "narrow:b58e527161168a03": 1,
        "narrow:bbf7728fd0530fce": 1,
        "narrow:bfe97da472755881": 1,
        "narrow:d8d9086821930bd9": 3,
        "narrow:e2ee71764486310b": 3,
    },
    "backend/packages/harness/alpha/sandbox/worktrees.py": {"narrow:1aff94f457419b3b": 1},
    "backend/packages/harness/alpha/scheduler/cron_manager.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/scheduler/delivery.py": {"exception:877a440a1e036e67": 1, "exception:aec787b27d60a731": 1, "exception:bda815843b07c0b3": 1},
    "backend/packages/harness/alpha/scheduler/incidents.py": {"exception:6a4012b640ae7df0": 1, "exception:bda815843b07c0b3": 1, "exception:ecce1f1019ba58aa": 1},
    "backend/packages/harness/alpha/scheduler/reactive_wake.py": {"exception:349d619249a5044a": 1, "narrow:bacca6b98f0aa9db": 1},
    "backend/packages/harness/alpha/scheduler/wake_gate.py": {"exception:349d619249a5044a": 1, "narrow:062ddcdb84324dea": 1},
    "backend/packages/harness/alpha/security/shell_ast/parser.py": {"narrow:1111bcf97efa7203": 1},
    "backend/packages/harness/alpha/selfrepair/assertion_guard.py": {"exception:57e6bf6d7a65b62a": 1, "exception:6c7bec423911f4a3": 1, "exception:d3e2b7d28d076d76": 1},
    "backend/packages/harness/alpha/selfrepair/sbfl.py": {"exception:349d619249a5044a": 2, "exception:432072214c03b36c": 1},
    "backend/packages/harness/alpha/selfrepair/surgical_apr.py": {"exception:100c437fe7bddbec": 1},
    "backend/packages/harness/alpha/skills/catalog.py": {"exception:79440e8db4a9e936": 1, "narrow:34069d6aa5ef4236": 1, "narrow:d3407e9853f73499": 1},
    "backend/packages/harness/alpha/skills/curator.py": {"exception:f48f955ff5510cfb": 1, "narrow:792ec4875aec38a2": 1, "narrow:7d158f804985f99d": 1},
    "backend/packages/harness/alpha/skills/evolution_engine.py": {
        "exception:9be98e2d8535083e": 1,
        "narrow:20e888e8feb99093": 1,
        "narrow:7beeb9ae7b6fb3a1": 1,
        "narrow:ba6ee19cdec92a51": 1,
        "narrow:e195278544059d0f": 3,
        "narrow:f1a746a65f15a0a4": 1,
    },
    "backend/packages/harness/alpha/skills/executable.py": {"narrow:289ce270b8f28e35": 1},
    "backend/packages/harness/alpha/skills/export.py": {
        "narrow:059efbc00efe43a8": 1,
        "narrow:6f5bbb1cc62c45ac": 1,
        "narrow:8706b4d6559e79f5": 1,
        "narrow:cf08aa860d13a303": 1,
        "narrow:e0a607cef9cbd50b": 1,
    },
    "backend/packages/harness/alpha/skills/hub/discovery.py": {"exception:c66b2433bbc09fd3": 1},
    "backend/packages/harness/alpha/skills/installer.py": {"narrow:3d3c58cfa8a54b2f": 1, "narrow:e1e6a36d471dae13": 1},
    "backend/packages/harness/alpha/skills/mcp_lifecycle.py": {"narrow:6eb4cd73b11ec3c0": 1},
    "backend/packages/harness/alpha/skills/projection.py": {"exception:8dfab3d3ce2aa137": 1, "exception:e1a83b1a7a5ce0e8": 1, "narrow:194d452e5317a846": 1, "narrow:7d361b926a93a38b": 1},
    "backend/packages/harness/alpha/skills/proposals.py": {"narrow:b92733b80122965d": 2, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/skills/review/readers.py": {"narrow:a6b1346ff25c1be9": 1, "narrow:d580fed9969f5762": 1, "narrow:e3c64e8b059f3e21": 1},
    "backend/packages/harness/alpha/skills/review/resource_graph.py": {"narrow:f6655cb5e373843a": 1},
    "backend/packages/harness/alpha/skills/security_scanner.py": {"exception:4b742697cbe4fbff": 1, "narrow:143ebded094321bf": 1, "narrow:3ef260e153e0718a": 1},
    "backend/packages/harness/alpha/skills/skillscan/orchestrator.py": {
        "exception:1bc7a24908bcfdc8": 1,
        "exception:a12c5bea69e380e3": 1,
        "narrow:af4db45c55b9640f": 1,
        "narrow:c3c54aea7c42ba24": 1,
        "narrow:d580fed9969f5762": 1,
    },
    "backend/packages/harness/alpha/skills/storage/skill_storage.py": {"narrow:dc91e12039e1db06": 1},
    "backend/packages/harness/alpha/skills/storage/user_scoped_skill_storage.py": {"narrow:b58e527161168a03": 1, "narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/skills/tiers.py": {"exception:632d2f4d4ca5f774": 1, "exception:b24fa024627f46a6": 1, "exception:bda815843b07c0b3": 1, "exception:d83c834f02476f7f": 1},
    "backend/packages/harness/alpha/skills/triggers/path_trigger.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/skills/usage.py": {"exception:bda815843b07c0b3": 1, "exception:f48f955ff5510cfb": 1},
    "backend/packages/harness/alpha/state/boulder.py": {"exception:79440e8db4a9e936": 1},
    "backend/packages/harness/alpha/subagents/acceptance_checks.py": {
        "exception:4fedd4454b7c3e39": 2,
        "exception:79440e8db4a9e936": 2,
        "narrow:56941930804158b0": 2,
        "narrow:b3d91a26d1e0377d": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:e1e6a36d471dae13": 1,
    },
    "backend/packages/harness/alpha/subagents/batch_service.py": {"narrow:bd20cd37a4b67a55": 2},
    "backend/packages/harness/alpha/subagents/builtins/deep_architect_agent.py": {"exception:2b4a43944669d076": 1},
    "backend/packages/harness/alpha/subagents/builtins/deep_code_reviewer_agent.py": {"narrow:b66b9c4acd4a3e00": 1},
    "backend/packages/harness/alpha/subagents/capacity.py": {"narrow:d2e7405a4fd961a5": 1},
    "backend/packages/harness/alpha/subagents/context_snapshot.py": {"narrow:753526f82bd1c57e": 1},
    "backend/packages/harness/alpha/subagents/executor.py": {"controlflow:1d67b4964b25f258": 1, "exception:79440e8db4a9e936": 1, "narrow:d8da14a9a2a2e0b5": 1},
    "backend/packages/harness/alpha/subagents/hierarchical_delegator.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/subagents/jev_acceptance.py": {"narrow:cc1be1b54ddb02bd": 1},
    "backend/packages/harness/alpha/subagents/report_contract.py": {"exception:986b4a5794289897": 1},
    "backend/packages/harness/alpha/subagents/specialists.py": {"narrow:81f2adf7657c6a98": 1},
    "backend/packages/harness/alpha/subagents/token_collector.py": {"narrow:2cc6865a7b3b9cb5": 1},
    "backend/packages/harness/alpha/swarm/communication.py": {"narrow:2a7169a151e02e2f": 1, "narrow:3b3f84ed39104c00": 1, "narrow:a50f1efc605c1423": 1, "narrow:f753b601df4bde9c": 1},
    "backend/packages/harness/alpha/swarm/consensus.py": {"narrow:3dafc4323b1a5365": 1},
    "backend/packages/harness/alpha/swarm/coordinator.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/swarm/models.py": {"narrow:0d290b9a88d16adb": 1, "narrow:3f5fbd1d59eff7e3": 2, "narrow:7259949ce895ae2a": 1, "narrow:883ed4406be3fa28": 1},
    "backend/packages/harness/alpha/swarm/runner.py": {"narrow:34e28dce35f3c779": 1, "narrow:eb77d3f3112af540": 1},
    "backend/packages/harness/alpha/swarm/worker.py": {"narrow:0dee3822f077f5fc": 1},
    "backend/packages/harness/alpha/synthesis/speculative_tournament.py": {"exception:9b35befbbcb53bd5": 1, "exception:c768db126865c014": 1},
    "backend/packages/harness/alpha/system1/classifier.py": {"narrow:f2b571f1d21a6f81": 1},
    "backend/packages/harness/alpha/system1/engine.py": {"narrow:0525241484b4bb84": 1},
    "backend/packages/harness/alpha/testing/differential_invariant_fuzzer.py": {"exception:7ddc648e3288972b": 1, "exception:d89dfaf02e2a98b7": 1},
    "backend/packages/harness/alpha/testing/mutation_fuzzer.py": {"exception:078183e4dd74b919": 1},
    "backend/packages/harness/alpha/tools/builtins/agency_competence_tool.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/tools/builtins/autonomous_command_tool.py": {"narrow:34e28dce35f3c779": 1, "narrow:f5a338848d0fc546": 1},
    "backend/packages/harness/alpha/tools/builtins/autonomy_control_tool.py": {"narrow:d78d5a664d39728d": 1},
    "backend/packages/harness/alpha/tools/builtins/autoplan_tool.py": {"exception:349d619249a5044a": 3, "exception:c47ca0eed3852145": 1, "exception:cd5dbfdab37de9c9": 1},
    "backend/packages/harness/alpha/tools/builtins/blackboard_tool.py": {"exception:74947f0bc42fcb60": 1},
    "backend/packages/harness/alpha/tools/builtins/browser_supervisor_tool.py": {"narrow:bfc3a9c8f95ed164": 3},
    "backend/packages/harness/alpha/tools/builtins/canvas_widget_tool.py": {"exception:bd338ae73fa52d85": 2},
    "backend/packages/harness/alpha/tools/builtins/code_agentic_core.py": {
        "exception:2d62b984c1c3ccc9": 1,
        "exception:349d619249a5044a": 4,
        "narrow:d1342f1744336ad8": 1,
        "narrow:e7cb6af66725504a": 1,
    },
    "backend/packages/harness/alpha/tools/builtins/cognitive_compiler_tool.py": {"exception:20a303f2f00c6889": 1},
    "backend/packages/harness/alpha/tools/builtins/cognitive_memory_tiering_tool.py": {"narrow:617cb4bd6e36c917": 1, "narrow:cc075783f46448c9": 1},
    "backend/packages/harness/alpha/tools/builtins/cognitive_plan_tool.py": {"exception:db631429104b0b69": 1},
    "backend/packages/harness/alpha/tools/builtins/company_tool.py": {"exception:2396c6c9495692aa": 1, "exception:7c638df761b41dc7": 1},
    "backend/packages/harness/alpha/tools/builtins/computer_system_one_tool.py": {"narrow:230ae2fdaccdd6a2": 1, "narrow:a89b5b48b26b252d": 1, "narrow:e4ef7f0b422db9d5": 1},
    "backend/packages/harness/alpha/tools/builtins/curriculum_tool.py": {"exception:d336c6065862cffb": 1},
    "backend/packages/harness/alpha/tools/builtins/deep_agent_tool.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/tools/builtins/deep_research_tool.py": {"narrow:0f6e934c5a1013fb": 1},
    "backend/packages/harness/alpha/tools/builtins/deep_web_search_tool.py": {"narrow:6ac831f8b4889153": 1, "narrow:74639cb364181fd5": 1, "narrow:ad1a01e61fa23d48": 1},
    "backend/packages/harness/alpha/tools/builtins/delta_checkpoint_tool.py": {"exception:bbc9a4f2281496ee": 1},
    "backend/packages/harness/alpha/tools/builtins/discipline_team_tool.py": {"exception:dfb6a30a286592e0": 1},
    "backend/packages/harness/alpha/tools/builtins/durable_replay_tool.py": {"exception:f43a223692ebd78e": 1},
    "backend/packages/harness/alpha/tools/builtins/dynamic_tool_synthesizer_tool.py": {"narrow:7d2eeda040a036b0": 1},
    "backend/packages/harness/alpha/tools/builtins/enclave_security_tool.py": {"exception:349d619249a5044a": 2, "exception:6b4cc003df412356": 1, "exception:f43a223692ebd78e": 1},
    "backend/packages/harness/alpha/tools/builtins/estop_tool.py": {"exception:b633d6ae97b839a0": 1},
    "backend/packages/harness/alpha/tools/builtins/evaluation_benchmark_tool.py": {"exception:137adf019c738b49": 1},
    "backend/packages/harness/alpha/tools/builtins/experience_tool.py": {"narrow:5d6ced7be091a40c": 1},
    "backend/packages/harness/alpha/tools/builtins/goal_integrity_tool.py": {"exception:f892b1f1f742cad9": 1},
    "backend/packages/harness/alpha/tools/builtins/hashline_tool.py": {"narrow:2fbc565903665507": 1, "narrow:7d65f29c3001f434": 1, "narrow:8b7efd91e9133f79": 2, "narrow:e855f1cd29d4c7b4": 2},
    "backend/packages/harness/alpha/tools/builtins/invoke_acp_agent_tool.py": {"exception:349d619249a5044a": 1, "narrow:3361fc0944bf7bc0": 1},
    "backend/packages/harness/alpha/tools/builtins/job_tool.py": {"narrow:2ac21d6e1b7a1c54": 1, "narrow:34e28dce35f3c779": 2},
    "backend/packages/harness/alpha/tools/builtins/keyless_web_search_tool.py": {"narrow:b8ac93835792d7f7": 1},
    "backend/packages/harness/alpha/tools/builtins/knowledge_graph_tool.py": {"exception:f3dbed4f39a6ac43": 1},
    "backend/packages/harness/alpha/tools/builtins/list_uploaded_files_tool.py": {"narrow:0525241484b4bb84": 1, "narrow:8d6d8a9d8a65b9ef": 1},
    "backend/packages/harness/alpha/tools/builtins/metacognitive_tool.py": {"exception:6d838aaf08b870d5": 1},
    "backend/packages/harness/alpha/tools/builtins/mission_hierarchy_tool.py": {"exception:2fb9e56489d89c33": 1},
    "backend/packages/harness/alpha/tools/builtins/os_computer_tool.py": {"narrow:b03800c6bb009332": 1},
    "backend/packages/harness/alpha/tools/builtins/peer_network_tool.py": {"controlflow:b81348c46137f728": 1, "narrow:d9e963bab31fc662": 1},
    "backend/packages/harness/alpha/tools/builtins/present_file_tool.py": {"narrow:0525241484b4bb84": 1, "narrow:65605bae134525d5": 1},
    "backend/packages/harness/alpha/tools/builtins/progress_card_tool.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/tools/builtins/python_repl_tool.py": {"narrow:8717add082f0c950": 1},
    "backend/packages/harness/alpha/tools/builtins/review_skill_package_tool.py": {"exception:349d619249a5044a": 1, "narrow:b58e527161168a03": 1},
    "backend/packages/harness/alpha/tools/builtins/session_search_tool.py": {"exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/tools/builtins/setup_agent_tool.py": {"narrow:5a47ab761773993a": 1},
    "backend/packages/harness/alpha/tools/builtins/skill_forge_tool.py": {"exception:66308c9b21a40498": 1},
    "backend/packages/harness/alpha/tools/builtins/stigmergic_mesh_tool.py": {"narrow:cc075783f46448c9": 1},
    "backend/packages/harness/alpha/tools/builtins/subagent_control_tool.py": {"exception:8f166d308c912686": 1},
    "backend/packages/harness/alpha/tools/builtins/swarm_tool.py": {"narrow:29a7ba558d7f8370": 1, "narrow:3782b50045d77c16": 1, "narrow:b706c6330f04b6c9": 1},
    "backend/packages/harness/alpha/tools/builtins/task_tool.py": {"controlflow:fd0f1600099d8003": 1, "exception:711d7455130f087e": 2, "narrow:edb9f74db91d95c5": 1},
    "backend/packages/harness/alpha/tools/builtins/tom_consult_tool.py": {"exception:cd5dbfdab37de9c9": 1},
    "backend/packages/harness/alpha/tools/builtins/tool_search.py": {"exception:078183e4dd74b919": 1, "narrow:34069d6aa5ef4236": 1, "narrow:948d61df8716cd2f": 1, "narrow:bfc3a9c8f95ed164": 1},
    "backend/packages/harness/alpha/tools/builtins/update_agent_tool.py": {"narrow:921a7834adf962a5": 1},
    "backend/packages/harness/alpha/tools/builtins/variation_operator_tool.py": {"exception:351300dff6fa12d0": 1},
    "backend/packages/harness/alpha/tools/builtins/view_image_tool.py": {"narrow:a527b23fff7d0771": 1, "narrow:ac191c402ba9108d": 1},
    "backend/packages/harness/alpha/tools/builtins/workflow_dag_tool.py": {"narrow:3b06b34c01e82c27": 1},
    "backend/packages/harness/alpha/tools/code_mode/bridge.py": {"exception:0dec74bd1a0c0c63": 1, "narrow:6d8c8a20b7557479": 1},
    "backend/packages/harness/alpha/tools/repair/normalizer.py": {"exception:349d619249a5044a": 4, "exception:ca17400bcf501014": 1},
    "backend/packages/harness/alpha/tools/search/catalog.py": {"exception:078183e4dd74b919": 1, "narrow:5cbb6e14924cad7b": 1},
    "backend/packages/harness/alpha/tools/selection.py": {"exception:711d7455130f087e": 1, "narrow:b5a5d5e5f7d587da": 1},
    "backend/packages/harness/alpha/tools/sync.py": {"exception:79440e8db4a9e936": 1, "narrow:3d3c58cfa8a54b2f": 1},
    "backend/packages/harness/alpha/tools/tool_discovery_metrics.py": {"exception:79440e8db4a9e936": 2},
    "backend/packages/harness/alpha/tools/trace_verify.py": {"narrow:58f92187caf66d25": 1},
    "backend/packages/harness/alpha/trajectory/store.py": {"exception:a6ed034f63ef62ed": 1},
    "backend/packages/harness/alpha/tui/app.py": {
        "exception:35c326b5a072221b": 1,
        "exception:55f14783ffe2318b": 1,
        "exception:746607856c9916ac": 1,
        "exception:9172eb49703f9f27": 1,
        "exception:9772404b9e23946e": 1,
        "exception:b3c6cfc3c2929e15": 1,
        "exception:c659d0c8ef0889b5": 1,
        "exception:d11235632d525731": 1,
        "exception:e603ac662f170440": 1,
        "exception:e7026e54d633c5af": 1,
    },
    "backend/packages/harness/alpha/tui/cli.py": {
        "narrow:30818aac9ba3d597": 1,
        "narrow:518b6df941579d96": 1,
        "narrow:d84f0ff6b4d6fd7c": 1,
        "narrow:dd26d7ff8c7d65c8": 2,
        "narrow:decc2cac6b258d7b": 1,
        "narrow:e195278544059d0f": 1,
    },
    "backend/packages/harness/alpha/tui/message_format.py": {"narrow:61b5e24567e3c538": 1, "narrow:89a8266a9af3c3e4": 1},
    "backend/packages/harness/alpha/tui/persistence.py": {"exception:349d619249a5044a": 2, "exception:d936bfb1de03d5e2": 1},
    "backend/packages/harness/alpha/tui/session.py": {"exception:1d9733afadb60336": 1, "exception:349d619249a5044a": 1},
    "backend/packages/harness/alpha/uploads/manager.py": {"narrow:5a47ab761773993a": 1, "narrow:becdc363d94ebc89": 2, "narrow:d2cf2b2fa5b35e7e": 1, "narrow:d8f57fa281caac89": 1},
    "backend/packages/harness/alpha/utils/assembly_io.py": {"narrow:34e28dce35f3c779": 1},
    "backend/packages/harness/alpha/utils/file_conversion.py": {"exception:349d619249a5044a": 3, "narrow:3ead2f140a248cca": 1},
    "backend/packages/harness/alpha/utils/file_io.py": {"narrow:34e28dce35f3c779": 1},
    "backend/packages/harness/alpha/utils/file_outline.py": {"exception:078183e4dd74b919": 1},
    "backend/packages/harness/alpha/utils/network.py": {"narrow:e1e6a36d471dae13": 1},
    "backend/packages/harness/alpha/utils/readability.py": {"narrow:34e28dce35f3c779": 1, "narrow:7711f341edfc7eb1": 1},
    "backend/packages/harness/alpha/utils/time.py": {"narrow:16b53fbd952e30f3": 1, "narrow:18d737f70d820abf": 1, "narrow:e07d65e8064ded29": 1},
    "backend/packages/harness/alpha/workflow/dag_engine.py": {"narrow:c39fe9e951a2bece": 1},
    "backend/packages/harness/alpha/workflow/dynamic_perception.py": {"exception:f717eb6a7413ca4d": 1},
    "backend/packages/harness/alpha/workflow/event_log.py": {"narrow:b58e527161168a03": 1, "narrow:e21ad3d0c9f5f69e": 1},
    "backend/packages/harness/alpha/workflow/events.py": {"narrow:3d3c58cfa8a54b2f": 1},
    "backend/packages/harness/alpha/workflow/expressions.py": {"exception:9b35befbbcb53bd5": 1, "narrow:b923237ec53d3d4f": 1},
    "backend/packages/harness/alpha/workflow/registry/mcp.py": {"exception:d4ba098c311e1f9f": 1},
    "backend/packages/harness/alpha/workflow/runtime.py": {"narrow:23c097df80ce19d2": 1, "narrow:948d61df8716cd2f": 1},
    "backend/packages/harness/alpha/workspace_changes/diff.py": {"narrow:ac191c402ba9108d": 1},
    "backend/packages/harness/alpha/workspace_changes/patch_synthesizer.py": {"narrow:e195278544059d0f": 1},
    "backend/packages/harness/alpha/workspace_changes/recorder.py": {
        "controlflow:b81348c46137f728": 1,
        "controlflow:fd0f1600099d8003": 3,
        "exception:47ac3a850c4642c2": 1,
        "exception:711d7455130f087e": 1,
    },
    "backend/packages/harness/alpha/workspace_changes/scanner.py": {
        "narrow:1de266e012c620ff": 1,
        "narrow:8e7a1ee8228b82bd": 1,
        "narrow:ac191c402ba9108d": 4,
        "narrow:bd6897c5e9eeeca6": 1,
        "narrow:d580fed9969f5762": 1,
    },
    "backend/scripts/benchmark/checkpoint/bench_channels.py": {"narrow:d1ad88a739eea0b9": 1},
    "backend/scripts/benchmark/checkpoint/bench_production.py": {"narrow:d1ad88a739eea0b9": 1},
    "backend/scripts/benchmark/checkpoint/checkpoint_bench_common.py": {
        "narrow:5a0a258b8235870f": 1,
        "narrow:75b320de551cfa64": 1,
        "narrow:820d1da2377e7474": 1,
        "narrow:905d8a6929637905": 1,
        "narrow:add311e41fcdc539": 1,
    },
    "backend/scripts/benchmark/concurrency/run_concurrency_bench.py": {"narrow:f7d4f8df84ec5c88": 1},
    "backend/scripts/benchmark/context_snapshot/runner.py": {"narrow:8f4562c1dc3f788f": 1},
    "backend/scripts/benchmark/deermem_eviction/runner.py": {"narrow:68abaf52ba6adf85": 1},
    "backend/scripts/benchmark/sandbox/bench_provider.py": {"exception:349d619249a5044a": 2, "narrow:187d265e1bfe203f": 1},
    "backend/scripts/generate_feature_manifest.py": {"narrow:b66b9c4acd4a3e00": 1},
    "backend/scripts/setup_voice.py": {"narrow:e195278544059d0f": 1, "narrow:e1e6a36d471dae13": 1, "narrow:fc3f71ec7222f134": 1},
    "backend/scripts/system_one_calibration.py": {"exception:f160b439331e91d3": 1, "exception:f844ba89943d3751": 1},
    "backend/scripts/system_one_laya_setup.py": {"controlflow:d01f819ff3328a8f": 1, "narrow:1e09dec6a78e03b3": 1},
    "backend/tests/_posix_shell.py": {"narrow:c48e9a7629a1a231": 1},
    "backend/tests/_replay_fixture.py": {"narrow:218e61a9edbf34f4": 1},
    "backend/tests/blocking_io/test_buzz_channel_seen_events.py": {"controlflow:88c03735c63a36a4": 1},
    "backend/tests/blocking_io/test_repl_session_offloop.py": {"narrow:85e49f4b4528019a": 1, "narrow:9d69576f16b45019": 1},
    "backend/tests/blocking_io/test_skills_load.py": {"narrow:f4713c9d5df2e93c": 1},
    "backend/tests/conftest.py": {"narrow:c4dfcf086a2abf63": 3},
    "backend/tests/support/detectors/blocking_io_static.py": {"narrow:4435a861c757a245": 1},
    "backend/tests/support/detectors/thread_boundaries.py": {"narrow:4435a861c757a245": 1},
    "backend/tests/test_action_ledger.py": {"narrow:b92733b80122965d": 1},
    "backend/tests/test_aio_sandbox_local_backend.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/tests/test_aio_sandbox_provider.py": {"controlflow:fd0f1600099d8003": 2},
    "backend/tests/test_artifact_archive.py": {"narrow:5252256be74a0ebc": 1, "narrow:b55f092a3c06685a": 2, "narrow:d39773549484e7d9": 1},
    "backend/tests/test_artifacts_router.py": {"narrow:b55f092a3c06685a": 1},
    "backend/tests/test_autonomous_planner.py": {"narrow:34e28dce35f3c779": 1},
    "backend/tests/test_bounded_subprocess_deadlines.py": {"narrow:85e49f4b4528019a": 1, "narrow:9d69576f16b45019": 1},
    "backend/tests/test_buzz_channel.py": {"controlflow:fd0f1600099d8003": 3},
    "backend/tests/test_channel_file_attachments.py": {"narrow:b55f092a3c06685a": 1},
    "backend/tests/test_channel_intake_backpressure.py": {
        "controlflow:0421f9957a1965d4": 1,
        "controlflow:0a73c59c13cb8664": 1,
        "controlflow:1c2b4ef4787517f9": 2,
        "controlflow:a94c678528fb8ac1": 1,
        "narrow:b9383c18bf0736c2": 1,
    },
    "backend/tests/test_channels.py": {"controlflow:fd0f1600099d8003": 1},
    "backend/tests/test_deep_handoff_contract.py": {"narrow:7711f341edfc7eb1": 2},
    "backend/tests/test_e2b_capacity_store_redis.py": {"exception:71dfa706334dfa33": 1},
    "backend/tests/test_e2b_sandbox_provider.py": {"narrow:c50d1cf83372f316": 1},
    "backend/tests/test_executor_starvation.py": {"narrow:bd20cd37a4b67a55": 1},
    "backend/tests/test_extension_isolation.py": {"narrow:4a7fd9f6af55923c": 1, "narrow:539f322168b4b78b": 2, "narrow:e82fc3f434624dea": 1},
    "backend/tests/test_feature_manifest_wiring.py": {"narrow:b66b9c4acd4a3e00": 1},
    "backend/tests/test_fullmemory_wiki.py": {"exception:349d619249a5044a": 1, "narrow:34e28dce35f3c779": 2},
    "backend/tests/test_gateway_path_utils.py": {"narrow:b55f092a3c06685a": 1},
    "backend/tests/test_gateway_run_drain_shutdown.py": {"controlflow:fd0f1600099d8003": 1},
    "backend/tests/test_goal_contracts.py": {"narrow:36ac1094bd3d3829": 1},
    "backend/tests/test_harness_boundary.py": {"narrow:ba2a8186ee03e6e9": 1},
    "backend/tests/test_hierarchical_delegator.py": {"narrow:7711f341edfc7eb1": 1},
    "backend/tests/test_import_purity.py": {"narrow:183f1141e8564e8e": 1, "narrow:9853ebda138223b4": 1},
    "backend/tests/test_langgraph_studio_routes.py": {"narrow:be2c19daca9ec003": 1},
    "backend/tests/test_lark_cli_integration.py": {"narrow:00b2a6423639eb53": 1},
    "backend/tests/test_mcp_file_migration.py": {"narrow:6d70bfde6ae50670": 1},
    "backend/tests/test_mcp_session_pool.py": {"base:7e87ad0a153b0dce": 3, "controlflow:fd0f1600099d8003": 1},
    "backend/tests/test_memory_codebase.py": {"narrow:ae435a95147c0f0d": 1},
    "backend/tests/test_memory_manager_interface.py": {"narrow:df222a22f8e0bec4": 1},
    "backend/tests/test_memory_updater.py": {"narrow:e996186c45355a40": 1},
    "backend/tests/test_multi_worker_run_ownership.py": {"controlflow:fd0f1600099d8003": 1},
    "backend/tests/test_no_orphan_modules.py": {"narrow:a7fc72eb95ebf7aa": 2, "narrow:b92733b80122965d": 1},
    "backend/tests/test_p0_live_binary_integration.py": {"narrow:a1c95bb9e1e7bb02": 1},
    "backend/tests/test_pat_auth.py": {"exception:349d619249a5044a": 1},
    "backend/tests/test_persistence_bootstrap_concurrency.py": {"controlflow:4f3314ca83d383bc": 1},
    "backend/tests/test_provisioner_request_threading.py": {"narrow:39710aee73e04316": 1},
    "backend/tests/test_reversible_delete.py": {"narrow:33824bd7ab3b0592": 1, "narrow:fb4a865b36ec50b5": 1},
    "backend/tests/test_run_duration_checkpoint.py": {"narrow:63cd4cc1dc8c6fec": 1},
    "backend/tests/test_run_journal.py": {"controlflow:5c2507c0842ffba5": 1},
    "backend/tests/test_run_worker_rollback.py": {"narrow:70647327897b7118": 1},
    "backend/tests/test_runtime_resilience_async_guard.py": {"narrow:1d3c3664f2f269e2": 1, "narrow:f93ac5d6a586a7fd": 1},
    "backend/tests/test_safety_termination_detectors.py": {"exception:711d7455130f087e": 1},
    "backend/tests/test_sandbox_orphan_reconciliation_e2e.py": {"narrow:dffec218efe38cd7": 1},
    "backend/tests/test_sandbox_ownership_store.py": {"exception:9b35befbbcb53bd5": 1, "narrow:aa16d2480340afc3": 1},
    "backend/tests/test_sandbox_search_tools.py": {"narrow:32c7d51b3bbcbd4b": 1, "narrow:ec25f50f5ac8f844": 1},
    "backend/tests/test_stream_bridge.py": {"exception:9b35befbbcb53bd5": 1, "narrow:1e4202533d9ca80d": 1, "narrow:aa16d2480340afc3": 1},
    "backend/tests/test_system_one_cdp_executor.py": {"narrow:a9280fd6c4210c6a": 1},
    "backend/tests/test_system_one_http_executor.py": {"narrow:a9280fd6c4210c6a": 1},
    "backend/tests/test_task_tool_core_logic.py": {"controlflow:fd0f1600099d8003": 1, "exception:349d619249a5044a": 4, "narrow:3d3c58cfa8a54b2f": 2},
    "backend/tests/test_thread_meta_repo.py": {"narrow:3c1378155e3345cd": 1},
    "backend/tests/test_tool_name_references.py": {"narrow:3ffc9d954c691e21": 1, "narrow:def9570a4589c27b": 2},
    "backend/tests/test_uvicorn_reload_exclude.py": {"narrow:48b16ee751096a49": 1},
    "backend/tests/test_wait_disconnect_handling.py": {"controlflow:fd0f1600099d8003": 1},
    "backend/tests/test_web_fetch_relative_links.py": {"exception:9b35befbbcb53bd5": 1},
    "backend/tests/test_work_resume_after_crash.py": {"narrow:9cfc588c651cf6a4": 1, "narrow:c1358e07ea61a148": 1},
    "backend/tests/test_workspace_changes.py": {"narrow:9d1094760d3e82d5": 1},
    "scripts/audit_dup_expressions.py": {"exception:79440e8db4a9e936": 1},
    "scripts/audit_env_wiring.py": {"narrow:7d158f804985f99d": 2},
    "scripts/check.py": {"narrow:78edec812129afa8": 1, "narrow:b722448b492bed4e": 1},
    "scripts/check_agent_guidance.py": {"narrow:dc91e12039e1db06": 1},
    "scripts/check_changed_python_lint.py": {"narrow:4b1859153bcb8828": 1, "narrow:88807d3b9249f3b6": 2, "narrow:ecd87f50b07a436a": 1},
    "scripts/check_cold_start_imports.py": {"narrow:0f3aa70317376dc1": 1, "narrow:b92733b80122965d": 1},
    "scripts/check_generated_drift.py": {"narrow:4b1859153bcb8828": 1, "narrow:88807d3b9249f3b6": 2, "narrow:ace8673cdbd37c27": 1, "narrow:dc91e12039e1db06": 1, "narrow:ecd87f50b07a436a": 1},
    "scripts/cold_start_probe.py": {"narrow:93a605dfb07cb3cc": 1},
    "scripts/deploy_status.py": {
        "exception:349d619249a5044a": 2,
        "exception:868b8500ce67fb58": 1,
        "narrow:67c0376eeb082a6d": 1,
        "narrow:85e49f4b4528019a": 1,
        "narrow:8d9450d913bbe3a4": 1,
        "narrow:9d69576f16b45019": 1,
        "narrow:b92733b80122965d": 1,
        "narrow:c3eef56d3d6a654f": 1,
        "narrow:d29f9c23808fb2ea": 1,
        "narrow:f8a2fdc731ac98c1": 1,
    },
    "scripts/detect_uv_extras.py": {"narrow:b4053c7e030145bb": 1},
    "scripts/doctor.py": {
        "exception:5a809fc73d87a1d8": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:c81451accbe0d25e": 1,
        "narrow:4a3582d4e4f83de4": 1,
        "narrow:a11451d007bde50e": 1,
        "narrow:b722448b492bed4e": 1,
    },
    "scripts/export_run_trace.py": {"narrow:b710eaf9f5860dfc": 1},
    "scripts/fix-electron-icon.py": {"narrow:8d7fba0837bcc1d7": 1},
    "scripts/generate_docs_index.py": {"narrow:fd6857476901b7c3": 1},
    "scripts/sandbox_memory_profile.py": {"narrow:994f952dd853883b": 1, "narrow:b58e527161168a03": 1, "narrow:b92733b80122965d": 1, "narrow:c48007768b504b56": 1},
    "scripts/setup_wizard.py": {"controlflow:955b2c851f355960": 1, "exception:349d619249a5044a": 1},
    "scripts/skill_review_waivers.py": {"narrow:b92733b80122965d": 2},
    "scripts/smoke_routes.py": {"exception:427a986afe604b6f": 1},
    "scripts/support_bundle.py": {
        "exception:0093531414c497da": 1,
        "exception:67ef099287892962": 1,
        "exception:79440e8db4a9e936": 1,
        "exception:daea8270cbbb0e54": 1,
        "narrow:05d3d27cf86afac4": 1,
        "narrow:6dfe555bc856eddb": 1,
        "narrow:ffa2c72e9fdc7f28": 1,
    },
    "scripts/sync_labels.py": {"narrow:0d076d5fa5a3a374": 1},
    "scripts/verify_unified_system.py": {"narrow:20c74c496c033624": 1, "narrow:3c9ee90a3dd690d7": 1},
    "scripts/wizard/noninteractive.py": {"narrow:e1e6a36d471dae13": 1},
    "scripts/wizard/ui.py": {"narrow:9b745ee9875d6a6a": 1},
    "scripts/wizard/writer.py": {"exception:efe61b344e269d71": 1},
}
# --- END BASELINE ---
_BASELINE_HEADER: Final = (
    "# --- BEGIN BASELINE ---\n"
    "# GENERATED by `python scripts/check_no_silent_failures.py --update-baseline`.\n"
    "# Every entry is a silent failure that already existed when this gate landed.\n"
    "# Delete an entry in the same change that fixes its handler; the gate then\n"
    "# keeps enforcing the rule for that code forever. See the module docstring.\n"
)


#: Widest line the emitter will produce. The repo's ruff line-length is 240;
#: a generated block that trips the linter would be fixed by hand, which is
#: exactly the edit this file exists to make impossible.
_BASELINE_MAX_WIDTH: Final = 200


def _emit_baseline(mapping: dict[str, dict[str, int]]) -> list[str]:
    """Render the baseline, one entry per line when a file has several.

    A single line per file is prettier for the common one-entry case, but a
    25-entry file would exceed the line limit, so width decides the layout.
    """
    block = [_BASELINE_HEADER.rstrip("\n"), "BASELINE: dict[str, dict[str, int]] = {"]
    for rel in sorted(mapping):
        pairs = [f'"{key}": {value}' for key, value in sorted(mapping[rel].items())]
        one_line = f'    "{rel}": {{' + ", ".join(pairs) + "},"
        if len(one_line) <= _BASELINE_MAX_WIDTH:
            block.append(one_line)
            continue
        block.append(f'    "{rel}": {{')
        block.extend(f"        {pair}," for pair in pairs)
        block.append("    },")
    block.append("}")
    return block


def rebase_baseline(text: str, mapping: dict[str, dict[str, int]]) -> str:
    """Return ``text`` with the baseline block replaced by ``mapping``.

    Markers are located by *exact whole line*, not by substring search: the
    marker strings also appear as constants further down this file, and a
    substring search would rewrite the wrong region. Re-emitting both markers
    and the generated header keeps ``--update-baseline`` idempotent.
    """
    lines = text.splitlines()
    begin_at = next((i for i, line in enumerate(lines) if line.strip() == _BASELINE_BEGIN), None)
    end_at = next((i for i, line in enumerate(lines) if line.strip() == _BASELINE_END), None)
    if begin_at is None or end_at is None or end_at < begin_at:
        raise RuntimeError(f"baseline markers not found in {__file__}; refusing to rewrite the gate")
    return "\n".join([*lines[:begin_at], *_emit_baseline(mapping), *lines[end_at:]]) + "\n"


def prune_baseline(baseline: dict[str, dict[str, int]], actual: dict[str, dict[str, int]]) -> tuple[dict[str, dict[str, int]], int]:
    """Drop waived entries the tree no longer contains. Never adds anything.

    Prune-only is the point: a routine "sync the baseline" run must be unable to
    absorb a newly introduced silent failure, because absorbing one is exactly
    how a ratchet stops ratcheting. Admitting new debt needs the separate,
    deliberate ``--accept-new`` flag, and prints what it admits.
    """
    pruned: dict[str, dict[str, int]] = {}
    removed = 0
    for rel, entries in baseline.items():
        live = actual.get(rel, {})
        kept: dict[str, int] = {}
        for fp, count in entries.items():
            keep = min(count, live.get(fp, 0))
            if keep > 0:
                kept[fp] = keep
            removed += count - keep
        if kept:
            pruned[rel] = kept
    return pruned, removed


# --------------------------------------------------------------------------
# Reporting and ratchet comparison
# --------------------------------------------------------------------------


def _load_baseline() -> dict[str, dict[str, int]]:
    return {rel: dict(entries) for rel, entries in BASELINE.items()}


def compare(result: ScanResult, baseline: dict[str, dict[str, int]]) -> tuple[list[Finding], list[Finding]]:
    """New findings (not waived) and waived-but-absent findings.

    Counted, not set-compared: two identical handlers in one file are two
    violations, and a waiver therefore has to cover both.
    """
    actual = result.fingerprint_map()
    findings_by_key: dict[tuple[str, str], list[Finding]] = {}
    for finding in result.findings:
        findings_by_key.setdefault((finding.path, finding.fingerprint), []).append(finding)

    new: list[Finding] = []
    resolved: list[Finding] = []
    for rel, entries in sorted(actual.items()):
        waived = baseline.get(rel, {})
        for fp, count in sorted(entries.items()):
            covered = waived.get(fp, 0)
            group = findings_by_key.get((rel, fp), [])
            # Every un-waived copy is its own violation: two identical handlers
            # in one file are two places a failure can vanish.
            for index in range(covered, count):
                sample = group[index] if index < len(group) else group[-1]
                new.append(
                    Finding(
                        path=sample.path,
                        lineno=sample.lineno,
                        breadth=sample.breadth,
                        shape=sample.shape,
                        fingerprint=fp,
                        source=sample.source,
                    )
                )
            for index in range(count, covered):
                sample = group[min(index, len(group) - 1)] if group else None
                resolved.append(
                    Finding(
                        path=rel,
                        lineno=sample.lineno if sample is not None else 0,
                        breadth=fp.split(":", 1)[0],
                        shape="waived-but-absent",
                        fingerprint=fp,
                    )
                )
    for rel, entries in sorted(baseline.items()):
        for fp in sorted(entries):
            if fp not in actual.get(rel, {}):
                resolved.append(
                    Finding(
                        path=rel,
                        lineno=0,
                        breadth=fp.split(":", 1)[0],
                        shape="waived-but-absent",
                        fingerprint=fp,
                    )
                )
    return new, resolved


def summarise(result: ScanResult, new: list[Finding], resolved: list[Finding], baseline: dict[str, dict[str, int]]) -> dict[str, Any]:
    waived_total = sum(sum(entries.values()) for entries in baseline.values())
    return {
        "files_scanned": result.files_scanned,
        "handlers_seen": result.handlers_seen,
        "silent_failures_found": len(result.findings),
        "waived": waived_total,
        "new": len(new),
        "resolved": len(resolved),
        "declared_suppress": result.suppress_seen,
        "unparsed": len(result.unparsed),
        "by_breadth": dict(Counter(finding.breadth for finding in result.findings).most_common()),
        "by_shape": dict(Counter(finding.shape for finding in result.findings).most_common()),
        "new_by_area": dict(Counter(finding.path.split("/")[0] for finding in new).most_common()),
    }


def render_text(result: ScanResult, new: list[Finding], resolved: list[Finding], baseline: dict[str, dict[str, int]], *, quiet: bool) -> str:
    stats = summarise(result, new, resolved, baseline)
    out: list[str] = []
    if stats["unparsed"]:
        out.append("UNVERIFIED FILES (the gate could not check these):")
        for rel, lineno, message in result.unparsed:
            out.append(f"  {rel}:{lineno}  {message}")
        out.append("")
    if new:
        out.append(f"NEW SILENT FAILURES ({len(new)}) - each must raise, log, or report its exception:")
        for finding in sorted(new, key=lambda f: (f.path, f.lineno)):
            out.append(f"  {finding.as_row()}  [{finding.breadth}/{finding.shape}]  {finding.source}")
        out.append("")
    if not quiet:
        out.append(
            "scanned {files} files, {handlers} exception handlers; "
            "{found} silent failures, {waived} pre-existing waived, {new} new, {resolved} resolved, "
            "{suppress} declared suppress() blocks, {unparsed} unparsed".format(
                files=stats["files_scanned"],
                handlers=stats["handlers_seen"],
                found=stats["silent_failures_found"],
                waived=stats["waived"],
                new=stats["new"],
                resolved=stats["resolved"],
                suppress=stats["declared_suppress"],
                unparsed=stats["unparsed"],
            )
        )
        out.append("open silent failures by breadth: " + json.dumps(stats["by_breadth"]))
        out.append("open silent failures by shape:  " + json.dumps(stats["by_shape"]))
    if not new and not result.unparsed and not quiet:
        out.append("OK: no caught exception is silently discarded outside the recorded baseline.")
    return "\n".join(out)


def render_report(result: ScanResult, new: list[Finding], resolved: list[Finding], baseline: dict[str, dict[str, int]]) -> str:
    """A triage document: every open silent failure, file:line, by area."""
    stats = summarise(result, new, resolved, baseline)
    by_file: dict[str, list[Finding]] = {}
    for finding in result.findings:
        by_file.setdefault(finding.path, []).append(finding)
    out: list[str] = [
        "# Silent-failure triage report",
        "",
        "Generated by `scripts/check_no_silent_failures.py --report`.",
        "",
        f"- files scanned: **{stats['files_scanned']}**",
        f"- exception handlers: **{stats['handlers_seen']}**",
        f"- silent failures open: **{stats['silent_failures_found']}**",
        f"- newly introduced (build-blocking): **{stats['new']}**",
        f"- resolved since the baseline was written: **{stats['resolved']}**",
        f"- declared `suppress()` blocks (not violations): **{stats['declared_suppress']}**",
        "",
        "A handler is listed when it catches an exception and then neither raises, logs, reports,",
        "nor propagates the caught error as data. Fix by re-raising, or by reporting through",
        "`alpha.errors.report_error`, then delete the matching baseline entry.",
        "",
        "## Open silent failures by file",
        "",
    ]
    for rel in sorted(by_file):
        findings = sorted(by_file[rel], key=lambda f: f.lineno)
        out.append(f"### `{rel}` ({len(findings)})")
        out.append("")
        for finding in findings:
            waived = finding not in new
            flag = "waived" if waived else "**NEW**"
            out.append(f"- `{finding.as_row()}` [{finding.breadth}/{finding.shape}] {flag} — `{finding.source}`")
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

_BASELINE_BEGIN = "# --- BEGIN BASELINE ---"
_BASELINE_END = "# --- END BASELINE ---"


def default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None, help="repository root (default: the parent of scripts/)")
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="prune the embedded baseline: drop waived entries the tree no longer has. Never adds (prune-only, so it cannot absorb a new violation)",
    )
    parser.add_argument(
        "--accept-new",
        action="store_true",
        help="deliberately admit currently-unwaived silent failures into the baseline, printing each one. Requires --update-baseline",
    )
    parser.add_argument("--fail-on-stale", action="store_true", help="also fail when a waived silent failure is gone but not un-waived")
    parser.add_argument("--report", type=Path, default=None, help="write a markdown triage report to this path")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    args = parser.parse_args(argv)

    root = (args.root or default_root()).resolve()
    if not root.is_dir():
        print(f"root {root} is not a directory", file=sys.stderr)
        return 2

    result = scan(root)
    baseline = _load_baseline()

    if args.accept_new and not args.update_baseline:
        print("--accept-new requires --update-baseline", file=sys.stderr)
        return 2

    if args.update_baseline:
        actual = result.fingerprint_map()
        pruned, removed = prune_baseline(baseline, actual)
        if args.accept_new:
            pending, _ = compare(result, pruned)
            for finding in sorted(pending, key=lambda f: (f.path, f.lineno)):
                print(f"ADMITTING {finding.as_row()}  [{finding.breadth}/{finding.shape}]  {finding.source}")
            admitted = actual
            print(f"--accept-new: admitted {len(pending)} new silent failure(s)")
        else:
            admitted = pruned
            still_new, _ = compare(result, pruned)
            print(f"baseline pruned: {removed} resolved entry/entries removed; {sum(sum(v.values()) for v in pruned.values())} waived remain")
            if still_new:
                print(f"{len(still_new)} silent failure(s) are not waived; the build will fail. Fix them, or re-run with --accept-new after reviewing --report output.")
        text = Path(__file__).read_text(encoding="utf-8")
        Path(__file__).write_text(rebase_baseline(text, admitted), encoding="utf-8", newline="\n")
        return 0

    new, resolved = compare(result, baseline)
    stats = summarise(result, new, resolved, baseline)

    if args.report is not None:
        args.report.write_text(render_report(result, new, resolved, baseline), encoding="utf-8", newline="\n")

    if args.json:
        payload = dict(stats)
        payload["new_findings"] = [
            {"path": f.path, "line": f.lineno, "breadth": f.breadth, "shape": f.shape, "source": f.source} for f in sorted(new, key=lambda f: (f.path, f.lineno))
        ]
        payload["unparsed"] = [{"path": p, "line": n, "error": m} for p, n, m in result.unparsed]
        print(json.dumps(payload, indent=2))
    else:
        print(render_text(result, new, resolved, baseline, quiet=args.quiet))

    if result.unparsed:
        return 2
    if new:
        return 1
    if args.fail_on_stale and resolved:
        print(f"{len(resolved)} waived silent failure(s) are gone; drop them from the baseline", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
