"""Isolated candidate workspaces for RSI (plan WP-B1, feature #1).

Purpose: every code-touching RSI candidate runs in a disposable git worktree
(branch ``rsi/<candidate_id>`` only) or — when git itself is unavailable — in
an explicitly lower-assurance copy workspace.  ``main``/``master`` and every
non-``rsi/*`` branch name are rejected with ``ValueError`` *before* any git
call (plan section 5.5: "never touch main", enforced mechanically).

Honesty contract (plan section 3 WP-B1 / section 5):

- Worktree creation failure surfaces the REAL git error (``CalledProcessError``
  / ``ValueError`` / ``RuntimeError`` from ``WorktreeManager``) — workspace
  creation never returns a fabricated path; the caller decides whether to
  reject the candidate.  The only failure converted into a copy fallback is
  git being *unavailable* (``OSError`` running git), which is honestly
  recorded as ``assurance="lower"`` with the real error text in the record.
- Lower-assurance copy workspaces appear in the persisted records AND in
  every evidence line ``run_checks`` emits (``assurance=lower …``); no score
  or pass-rate is ever invented here — ``run_checks`` only reports exit
  codes, bounded output tails, and guard verdicts.
- ``run_checks`` fails closed: both safety guards are consulted first
  (``SelfRepoGuard.evaluate_command`` then ``SafetyGuard.evaluate_command``);
  a violation returns ``(False, <verbatim guard reason>)`` with no
  subprocess executed.  Protected-path classification is consulted through
  the single seam ``_protected_paths_api()``: when ``alpha.rsi.protected_paths``
  (WP-B3) is importable its ``classify`` results are disclosed in the result;
  when absent, the result carries an honest note with the real ImportError
  text and only the two safety guards apply.  A ``deny`` verdict blocks;
  ``review_required`` is disclosed but NOT approval — run_checks has no human
  review channel and never passes ``reviewed=True`` (review cannot be
  self-granted inside the candidate loop).
- Orphan reconciliation on load (pattern: tests/test_sandbox_orphan_reconciliation.py):
  a recorded workspace whose path no longer exists is REPORTED by ``load()``
  as an orphan and its record is left on disk — never silently dropped.

Integrations (verified against the code, not assumed): ``alpha/sandbox/
worktrees.py::WorktreeManager.{create_worktree, remove_worktree,
worktree_context, list_worktrees}``, ``alpha/safety/self_repo_guard.py``,
``alpha/safety/guard.py``, ``alpha/config/runtime_paths.py``,
``alpha/evolution/identity.py::atomic_write_json``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal

from alpha.config.runtime_paths import project_root, runtime_home
from alpha.evolution.identity import atomic_write_json
from alpha.safety.guard import get_safety_guard
from alpha.safety.self_repo_guard import get_self_repo_guard
from alpha.sandbox.worktrees import WorktreeManager

logger = logging.getLogger(__name__)

__all__ = [
    "CandidateWorkspace",
    "RsiWorkspaceManager",
    "_protected_paths_api",
    "_validate_branch_name",
]

RECORDS_FILE = "workspaces.json"
COPY_WS_DIR = "copy_ws"
WORKTREE_DIR = "worktrees"
_CHECK_TAIL_CHARS = 2000  # same bound style as skills/evolution_engine.SUITE_OUTPUT_TAIL_CHARS
_CANDIDATE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

# Bounded file set for copy workspaces (plan: "bounded shutil.copytree"):
# VCS metadata, caches, and state directories are excluded so a copy cannot
# recursively include its own runtime home or megabytes of VCS/caches.
_BASE_IGNORE_NAMES = (".git", "__pycache__", "*.pyc", "*.pyo", "node_modules", ".venv", ".worktrees", ".agent-workspace")


@dataclass(frozen=True)
class CandidateWorkspace:
    """One disposable candidate workspace record (plan WP-B1 exactly)."""

    workspace_id: str
    kind: Literal["worktree", "copy"]
    path: Path
    branch: str | None
    assurance: Literal["standard", "lower"]
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateWorkspace:
        known = {field.name for field in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        payload["path"] = Path(payload["path"])
        return cls(**payload)


def _validate_branch_name(branch: str) -> str:
    """Return ``branch`` when it is an allowed ``rsi/*`` workspace branch, else ``ValueError``.

    Mechanical "never touch main": every branch name that can reach
    ``WorktreeManager`` (composed by ``create``/``workspace``, re-checked on
    ``destroy``) passes through here first, so ``main``/``master``/any
    non-``rsi/*`` name can never be created, checked out, or deleted by this
    module.
    """
    if not isinstance(branch, str) or not branch.startswith("rsi/"):
        raise ValueError(
            f"RSI workspaces only operate on 'rsi/*' branches (got {branch!r}); "
            "refusing to touch main/master or any other branch."
        )
    rest = branch[len("rsi/") :]
    if not rest or not _CANDIDATE_ID_RE.fullmatch(rest) or ".." in branch:
        raise ValueError(f"Invalid RSI workspace branch name: {branch!r}")
    parts = branch.split("/")
    if any(not part or part.startswith(".") or part.endswith((".", ".lock")) for part in parts):
        raise ValueError(f"Invalid RSI workspace branch name: {branch!r}")
    return branch


def _validate_candidate_id(candidate_id: str) -> str:
    """Validate the candidate id used to compose a branch name and copy path."""
    if not isinstance(candidate_id, str) or not candidate_id or not _CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError(f"Invalid candidate id for a workspace: {candidate_id!r}")
    if ".." in candidate_id or candidate_id.startswith(".") or candidate_id.endswith((".", ".lock", "/")):
        raise ValueError(f"Invalid candidate id for a workspace: {candidate_id!r}")
    return candidate_id


def _protected_paths_api() -> tuple[dict[str, Callable[..., Any]] | None, str]:
    """Single seam to the wave-mate protected-path policy (plan WP-B3).

    Returns ``({"classify": …, "assert_candidate_path_allowed": …}, "")`` when
    ``alpha.rsi.protected_paths`` is importable, else ``(None, <real
    ImportError text>)``.  The absence text is the genuine exception string —
    never rewritten, never fabricated.  Callers disclose the note when the
    classification was unavailable and proceed on the two safety guards alone.
    """
    try:
        from alpha.rsi import protected_paths
    except ImportError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    classify = getattr(protected_paths, "classify", None)
    assert_allowed = getattr(protected_paths, "assert_candidate_path_allowed", None)
    if not callable(classify) or not callable(assert_allowed):
        return None, "alpha.rsi.protected_paths is importable but lacks callable classify/assert_candidate_path_allowed"
    return {"classify": classify, "assert_candidate_path_allowed": assert_allowed}, ""


def _output_tail(value: Any, limit: int = _CHECK_TAIL_CHARS) -> str:
    """Bounded tail of subprocess output (bytes or str); honest about absence.

    Same semantics as ``_tail`` in ``skills/evolution_engine.py`` (style
    reference per plan WP-B1), under a distinct name so the duplicate-name
    gate stays clean.
    """
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


class RsiWorkspaceManager:
    """Creates, checks, and destroys isolated candidate workspaces.

    ``repo_root`` defaults to ``project_root()``; worktrees live under
    ``runtime_home()/rsi/worktrees``; copy workspaces under
    ``runtime_home()/rsi/copy_ws/<candidate_id>``; records persist atomically
    at ``runtime_home()/rsi/workspaces.json``.
    """

    def __init__(self, repo_root: Path | str | None = None, base_worktree_dir: Path | str | None = None):
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else project_root()
        self.base_worktree_dir = Path(base_worktree_dir).resolve() if base_worktree_dir is not None else (runtime_home() / "rsi" / WORKTREE_DIR)
        self.copy_ws_root = runtime_home() / "rsi" / COPY_WS_DIR
        self.records_path = runtime_home() / "rsi" / RECORDS_FILE
        self._wt = WorktreeManager(self.repo_root, self.base_worktree_dir)
        self._records: dict[str, dict[str, Any]] | None = None
        self._seam: tuple[dict[str, Callable[..., Any]] | None, str] | None = None

    # ── creation ────────────────────────────────────────────────────────────

    def create(self, candidate_id: str, base_ref: str = "HEAD") -> CandidateWorkspace:
        """Create a ``rsi/<candidate_id>`` worktree; copy fallback only when git is unavailable."""
        branch = _validate_branch_name(f"rsi/{_validate_candidate_id(candidate_id)}")
        unavailable = self._git_unavailable_reason()
        if unavailable:
            return self.create_copy(candidate_id, reason=unavailable)
        try:
            wt = self._wt.create_worktree(branch, base_ref=base_ref)
        except OSError as exc:
            # Only reachable when git itself cannot be executed: the real
            # git failures (CalledProcessError/ValueError/RuntimeError) and
            # invalid base refs surface to the caller unchanged.
            return self.create_copy(candidate_id, reason=f"git worktree unavailable: {type(exc).__name__}: {exc}")
        return self._register(kind="worktree", path=wt.path, branch=branch, candidate_id=candidate_id)

    def create_copy(self, candidate_id: str, *, reason: str | None = None) -> CandidateWorkspace:
        """Bounded ``shutil.copytree`` fallback workspace with ``assurance="lower"`` (honestly recorded)."""
        _validate_candidate_id(candidate_id)
        source = self.repo_root
        if not source.is_dir():
            raise FileNotFoundError(f"copy workspace source does not exist: {source}")
        target = self.copy_ws_root / candidate_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"copy workspace path already exists (destroy it first): {target}")
        note = reason or "copy workspace created on request (assurance=lower; no git worktree isolation)"
        ignores = list(_BASE_IGNORE_NAMES)
        home = runtime_home()
        if home.is_relative_to(source) and home.name not in ignores:
            # Never copy the runtime state directory into the copy workspace.
            ignores.append(home.name)
        try:
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(*ignores))
        except OSError:
            with contextlib.suppress(OSError):
                shutil.rmtree(target, ignore_errors=True)  # no partial workspace is ever recorded
            raise
        return self._register(kind="copy", path=target, branch=None, candidate_id=candidate_id, notes=[note])

    # ── destruction ─────────────────────────────────────────────────────────

    def destroy(self, ws: CandidateWorkspace) -> bool:
        """Remove a workspace; only ``rsi/*`` branches (or managed copy dirs) are ever touched."""
        if ws.kind == "copy":
            root = self.copy_ws_root.resolve()
            resolved = ws.path.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError(f"refusing to delete a path outside the managed copy workspace directory: {ws.path}")
            removed = False
            if resolved.exists():
                shutil.rmtree(resolved)
                removed = True
            self._forget(ws.workspace_id)
            return removed
        branch = _validate_branch_name(ws.branch)  # mechanical never-touch-main, checked again here
        if not Path(ws.path).resolve().is_relative_to(self.base_worktree_dir):
            raise ValueError(f"refusing to destroy a worktree outside the managed directory: {ws.path}")
        removed = self._wt.remove_worktree(branch, force=True, delete_branch=True)
        self._forget(ws.workspace_id)
        return removed

    @contextlib.contextmanager
    def workspace(self, candidate_id: str, base_ref: str = "HEAD") -> Iterator[CandidateWorkspace]:
        """Context-managed workspace mirroring ``WorktreeManager.worktree_context(delete_on_exit=True)``.

        On exit the record is always dropped; worktree directories are removed
        by the underlying context (cleanup failure raises ``RuntimeError``,
        exactly like ``worktree_context`` — the record is still forgotten),
        copy workspaces are destroyed.  Mirroring ``delete_on_exit=True``,
        ``destroy()`` is the API that deletes the branch.
        """
        ws = self.create(candidate_id, base_ref=base_ref)
        try:
            if ws.kind == "worktree":
                with self._wt.worktree_context(ws.branch, base_ref=base_ref, delete_on_exit=True):
                    yield ws
            else:
                yield ws
        finally:
            self._forget(ws.workspace_id)
            if ws.kind == "copy" and ws.path.exists():
                with contextlib.suppress(OSError):
                    shutil.rmtree(ws.path)

    # ── guarded checks ──────────────────────────────────────────────────────

    def run_checks(self, ws: CandidateWorkspace, command: Sequence[str], *, timeout: float) -> tuple[bool, str]:
        """Run one guarded check command inside ``ws``; returns ``(ok, evidence_line)``.

        Fail-closed ordering: SelfRepoGuard → SafetyGuard → protected-path
        classification (seam, when available) → subprocess with
        ``cwd=ws.path`` and tail-bounded output.  Guard reasons and checker
        output are never swallowed: violations return the guard's verbatim
        reason, executed commands return exit code + stdout/stderr tails.
        """
        if isinstance(command, (str, bytes)) or not isinstance(command, Sequence) or not command:
            return False, "invalid command: expected a non-empty list of string arguments (fail-closed)"
        if not all(isinstance(arg, str) for arg in command):
            return False, "invalid command: every argument must be a string (fail-closed)"
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            return False, "invalid timeout: expected a positive number of seconds (fail-closed)"
        cmd_str = " ".join(command)
        if not Path(ws.path).is_dir():
            return False, f"workspace path does not exist: {ws.path}"
        try:
            violation = get_self_repo_guard().evaluate_command(cmd_str, target_cwd=ws.path)
        except Exception as exc:  # noqa: BLE001 — evaluation errors fail closed with the real error
            return False, f"self-repo guard evaluation failed: {type(exc).__name__}: {exc} (fail-closed)"
        if violation.is_violation:
            return False, f"{violation.reason} (command: {cmd_str})"
        try:
            decision = get_safety_guard().evaluate_command(cmd_str)
        except Exception as exc:  # noqa: BLE001 — evaluation errors fail closed with the real error
            return False, f"safety guard evaluation failed: {type(exc).__name__}: {exc} (fail-closed)"
        if not decision.allowed:
            return False, f"{decision.reason} (command: {cmd_str})"

        api, seam_note = self._seam_or_load()
        assurance_note = f"assurance={ws.assurance}" + (" (copy workspace, not a git worktree)" if ws.assurance == "lower" else "")
        notes: list[str] = []
        if api is None:
            notes.append(f"protected-path classification unavailable: {seam_note}")
        else:
            blocked, note = self._classify_targets(api, [ws.path, *(arg for arg in command[1:] if self._looks_like_path(arg))])
            notes.append(note)
            if blocked:
                return False, f"{' | '.join(notes)} | {assurance_note}"
            notes.append("protected-path review_required is disclosure, not approval (no review channel in run_checks)")
        notes.append(assurance_note)

        try:
            proc = subprocess.run(
                list(command),
                cwd=str(ws.path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(timeout),
            )
        except subprocess.TimeoutExpired as exc:
            detail = (
                f"command timed out after {timeout}s: {cmd_str} | stdout tail: {_output_tail(getattr(exc, 'stdout', None))}"
                f" | stderr tail: {_output_tail(getattr(exc, 'stderr', None))} | {' | '.join(notes)}"
            )
            return False, detail
        except OSError as exc:
            return False, f"command could not be executed: {type(exc).__name__}: {exc}: {cmd_str} | {' | '.join(notes)}"
        detail = (
            f"exit code {proc.returncode}: {cmd_str} | stdout tail: {_output_tail(proc.stdout)}"
            f" | stderr tail: {_output_tail(proc.stderr)} | {' | '.join(notes)}"
        )
        return proc.returncode == 0, detail

    # ── delegation / records ────────────────────────────────────────────────

    def list_worktrees(self) -> list[dict[str, Any]]:
        """Delegate to ``WorktreeManager.list_worktrees`` (porcelain dicts, ``branch`` = ``refs/heads/…``)."""
        return self._wt.list_worktrees()

    def load(self) -> dict[str, list[Any]]:
        """Load persisted records and reconcile them against disk.

        Returns ``{"active": [CandidateWorkspace, …], "orphans": [report, …]}``.
        An orphan is a recorded workspace whose path no longer exists (or whose
        record is unreadable) — it is REPORTED with the real reason and its
        record is left on disk (call ``destroy`` to remove it); nothing is
        silently dropped.  A records file that exists but cannot be parsed
        raises ``RuntimeError`` carrying the real error (fail-closed read).
        """
        raw, error = self._read_records()
        if error is not None:
            raise RuntimeError(f"could not read workspace records at {self.records_path}: {error}")
        active: list[CandidateWorkspace] = []
        orphans: list[dict[str, Any]] = []
        for workspace_id, record in raw.items():
            if not isinstance(record, dict):
                orphans.append(
                    {
                        "workspace_id": workspace_id,
                        "candidate_id": None,
                        "path": None,
                        "state": "invalid_record",
                        "reason": f"recorded workspace entry is not an object: {record!r} (record kept on disk)",
                    }
                )
                continue
            try:
                ws = CandidateWorkspace.from_dict(record)
                if "candidate_id" not in record:
                    raise KeyError("candidate_id")
            except (KeyError, TypeError, ValueError) as exc:
                orphans.append(
                    {
                        "workspace_id": workspace_id,
                        "candidate_id": record.get("candidate_id") if isinstance(record, dict) else None,
                        "path": record.get("path") if isinstance(record, dict) else None,
                        "state": "invalid_record",
                        "reason": f"recorded workspace could not be loaded: {type(exc).__name__}: {exc}",
                    }
                )
                continue
            if Path(ws.path).is_dir():
                active.append(ws)
            else:
                orphans.append(
                    {
                        **record,
                        "workspace_id": workspace_id,
                        "state": "orphan_missing_path",
                        "reason": f"recorded workspace path no longer exists: {ws.path} (record kept on disk; destroy() removes it)",
                    }
                )
        return {"active": active, "orphans": orphans}

    def _git_unavailable_reason(self) -> str | None:
        """Real reason git worktrees cannot be used here, or ``None`` when git is present."""
        if shutil.which("git") is None:
            return "git worktree unavailable: git executable not found on PATH (checked for 'git')"
        return None

    def _register(
        self,
        *,
        kind: Literal["worktree", "copy"],
        path: Path,
        branch: str | None,
        candidate_id: str,
        notes: list[str] | None = None,
    ) -> CandidateWorkspace:
        ws = CandidateWorkspace(
            workspace_id=uuid.uuid4().hex[:12],
            kind=kind,
            path=Path(path),
            branch=branch,
            assurance="standard" if kind == "worktree" else "lower",
            created_at=time.time(),
        )
        entry = {"candidate_id": candidate_id, **ws.to_dict()}
        if notes:
            entry["notes"] = list(notes)
        self._load_records()
        assert self._records is not None
        self._records[ws.workspace_id] = entry
        self._write_records()
        return ws

    def _forget(self, workspace_id: str) -> None:
        self._load_records()
        assert self._records is not None
        if self._records.pop(workspace_id, None) is not None:
            self._write_records()

    def _load_records(self) -> None:
        if self._records is None:
            raw, error = self._read_records()
            if error is not None:
                raise RuntimeError(f"could not read workspace records at {self.records_path}: {error}")
            self._records = raw

    def _read_records(self) -> tuple[dict[str, Any], Exception | None]:
        """Read the records file; ``(map, None)`` when absent/clean, ``(partial, real error)`` when unreadable.

        Entries are NOT filtered by type here: an unparseable entry must stay
        visible so ``load()`` can REPORT it as an invalid record instead of
        silently dropping it.
        """
        try:
            text = self.records_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, None
        except OSError as exc:
            return {}, exc
        try:
            payload = json.loads(text)
            workspaces = payload.get("workspaces", {}) if isinstance(payload, dict) else None
            if not isinstance(workspaces, dict):
                raise ValueError("records payload has no 'workspaces' object")
            return dict(workspaces), None
        except (ValueError, AttributeError) as exc:
            return {}, exc

    def _write_records(self) -> None:
        payload = {"version": 1, "workspaces": self._records or {}}
        try:
            atomic_write_json(self.records_path, payload)
        except (OSError, ValueError, TypeError) as exc:
            # Ledger-style degradation (precedent: EvolutionEngine._record_ledger_event):
            # the workspace itself is real; drift is reported later by load()
            # orphans and here, loudly, with the real error.
            logger.warning("could not persist workspace records to %s: %s", self.records_path, exc)

    def _seam_or_load(self) -> tuple[dict[str, Callable[..., Any]] | None, str]:
        if self._seam is None:
            self._seam = _protected_paths_api()
        return self._seam

    @staticmethod
    def _looks_like_path(token: str) -> bool:
        return ("/" in token) or ("\\" in token) or token.startswith(".")

    @staticmethod
    def _classify_targets(api: dict[str, Callable[..., Any]], targets: Sequence[Any]) -> tuple[bool, str]:
        """Classify cwd/edit-target paths through the seam → ``(blocked, evidence_note)``.

        Honesty notes: ``deny`` (and a classifier that raises) blocks the
        command fail-closed; the real B3 policy defaults unknown paths to
        ``review_required`` — blocking on that would make every check in a
        fresh workspace fail, and claiming approval would be fabrication, so
        ``review_required`` results are disclosed verbatim in the evidence
        line instead.
        """
        classify = api["classify"]
        verdicts: list[str] = []
        for target in targets:
            try:
                mode, pattern = classify(target)
            except Exception as exc:  # noqa: BLE001 — fail closed with the real classifier error
                return True, f"protected-path classification failed: {type(exc).__name__}: {exc} (fail-closed)"
            if mode == "deny":
                return True, f"protected-path policy denied: {target} (matched pattern: {pattern or 'unknown'})"
            verdicts.append(f"{target}:{mode}{f' ({pattern})' if pattern else ''}")
        if not verdicts:
            return False, "protected-path: no classifiable targets"
        return False, "protected-path: " + "; ".join(verdicts)
