"""Durable storage for the Harness Improvement subsystem.

Everything lives under ``runtime_home()/harness_improvement/`` — single-process
JSON/JSONL, atomic against a crash in one process, **never** cross-process
exactly-once. That is the same durability class as the rest of Alpha's local
self-improvement stores, and the router discloses it.

Layout::

    blobs/<sha256-hex>            # immutable component content, content-addressed
    bundles/<safe-bundle-id>.json # manifest JSON; id == content hash
    active.json                   # active pointer + previous (rollback ref)
    runs/<run_id>.json            # durable run record / state machine
    runs/<run_id>.events.jsonl    # append-only run journal
    runs/<run_id>/probes/...      # isolated per-candidate workspaces (probes.py)
    experiences/<id>/EXPERIENCE.md
    experiences/<id>/metadata.json
    ledger.jsonl                  # budget ledger entries, append-only

Two honesty rules shape this module:

* **Absent is not corrupt.** A missing file is a legitimate first-run state;
  unreadable or malformed *existing* files are reported through
  :class:`Listing` / raised as :class:`StoreUnreadable` rather than being
  presented as empty collections.
* **A bundle never disagrees with its id.** :meth:`HarnessImprovementStore.save_bundle`
  recomputes the id from the payload it is about to store and refuses a
  mismatch, so a hand-edited manifest fails loudly instead of becoming a
  trusted baseline.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home
from alpha.harness_improvement.models import HarnessBundle, compute_bundle_id

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class StoreUnreadable(RuntimeError):
    """The store could not be read. Distinct from "the store is empty"."""


class StoreCorrupt(RuntimeError):
    """A stored record exists but cannot be parsed or disagrees with its hash."""


@dataclass(frozen=True, slots=True)
class Listing:
    """A list plus the rows that could not be read.

    ``unreadable`` is never folded into the item list: "three bundles" and
    "three bundles, one of which would not parse" drive opposite decisions.
    """

    items: tuple[Any, ...] = ()
    unreadable: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.unreadable


@dataclass(slots=True)
class ActivePointer:
    """The active bundle pointer plus the rollback reference."""

    bundle_id: str
    previous_bundle_id: str | None
    activated_at: str
    actor: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "previous_bundle_id": self.previous_bundle_id,
            "activated_at": self.activated_at,
            "actor": self.actor,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActivePointer:
        return cls(
            bundle_id=str(data["bundle_id"]),
            previous_bundle_id=data.get("previous_bundle_id"),
            activated_at=str(data.get("activated_at", "")),
            actor=str(data.get("actor", "")),
            reason=str(data.get("reason", "")),
        )


@dataclass(slots=True)
class BudgetLedgerEntry:
    """One ledger row: what was charged, by which stage, against which ceiling."""

    run_id: str
    stage: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    elapsed_ms: int
    recorded_at: str
    ceilings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "elapsed_ms": self.elapsed_ms,
            "recorded_at": self.recorded_at,
            "ceilings": self.ceilings,
        }


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (same-directory temp + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomic pretty JSON write, shared with the run and experience stores."""
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_id(raw: str) -> str:
    """Map an id onto a filesystem-safe name without changing its meaning."""
    if not _SAFE_ID.match(raw):
        raise ValueError(f"id {raw!r} contains characters this store does not accept")
    return raw.replace(":", "_")


class HarnessImprovementStore:
    """The one owner of every durable artifact of this subsystem."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else runtime_home() / "harness_improvement"

    # -- paths ---------------------------------------------------------------
    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def bundles_dir(self) -> Path:
        return self.root / "bundles"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def experiences_dir(self) -> Path:
        return self.root / "experiences"

    @property
    def active_path(self) -> Path:
        return self.root / "active.json"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger.jsonl"

    @property
    def promotions_path(self) -> Path:
        return self.root / "promotions.jsonl"

    def run_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{safe_id(run_id)}.json"

    def run_events_path(self, run_id: str) -> Path:
        return self.runs_dir / f"{safe_id(run_id)}.events.jsonl"

    def run_workspace_root(self, run_id: str) -> Path:
        return self.runs_dir / safe_id(run_id) / "probes"

    def bundle_path(self, bundle_id: str) -> Path:
        return self.bundles_dir / f"{safe_id(bundle_id)}.json"

    def blob_path(self, sha256: str) -> Path:
        digest = sha256.split(":", 1)[-1]
        return self.blobs_dir / digest

    def experience_dir(self, experience_id: str) -> Path:
        return self.experiences_dir / safe_id(experience_id)

    # -- blobs ---------------------------------------------------------------
    def put_blob(self, content: str) -> str:
        """Store *content* content-addressed; returns ``sha256:<hex>``.

        Writing an existing digest is a no-op (idempotent by construction).
        """
        from alpha.harness_improvement.models import content_hash

        digest = content_hash(content)
        path = self.blob_path(digest)
        if path.exists():
            return digest
        _atomic_write_text(path, content)
        return digest

    def get_blob(self, sha256: str) -> str | None:
        """Return blob content or ``None`` when absent. Unreadable raises."""
        path = self.blob_path(sha256)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreUnreadable(f"blob {sha256} exists but could not be read: {exc}") from exc

    # -- bundles -------------------------------------------------------------
    def save_bundle(self, bundle: HarnessBundle, contents: Iterable[tuple[str, str]]) -> None:
        """Persist component blobs then the manifest, refusing an id mismatch."""
        for _component_id, content in contents:
            self.put_blob(content)
        expected = compute_bundle_id(
            parent_bundle_id=bundle.parent_bundle_id,
            components=bundle.components,
            alpha_runtime_version=bundle.alpha_runtime_version,
            tool_schema_version=bundle.tool_schema_version,
            policy_version=bundle.policy_version,
        )
        if expected != bundle.bundle_id:
            raise StoreCorrupt(f"bundle id {bundle.bundle_id} does not match its payload hash {expected}; refusing to store")
        atomic_write_json(self.bundle_path(bundle.bundle_id), bundle.to_dict())

    def load_bundle(self, bundle_id: str) -> HarnessBundle | None:
        """Load one bundle; ``None`` when absent, :class:`StoreCorrupt` when malformed."""
        path = self.bundle_path(bundle_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreUnreadable(f"bundle {bundle_id} could not be read: {exc}") from exc
        try:
            bundle = HarnessBundle.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreCorrupt(f"bundle {bundle_id} is malformed: {exc}") from exc
        expected = compute_bundle_id(
            parent_bundle_id=bundle.parent_bundle_id,
            components=bundle.components,
            alpha_runtime_version=bundle.alpha_runtime_version,
            tool_schema_version=bundle.tool_schema_version,
            policy_version=bundle.policy_version,
        )
        if expected != bundle.bundle_id:
            raise StoreCorrupt(f"bundle {bundle_id} manifest was modified: payload hashes to {expected}")
        return bundle

    def list_bundles(self) -> Listing:
        try:
            files = sorted(self.bundles_dir.glob("*.json"))
        except OSError as exc:
            raise StoreUnreadable(f"bundle directory could not be listed: {exc}") from exc
        bundles: list[HarnessBundle] = []
        unreadable: list[str] = []
        for path in files:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                unreadable.append(f"{path.name}: {exc}")
                continue
            try:
                payload = json.loads(raw)
            except ValueError as exc:
                unreadable.append(f"{path.name}: malformed JSON: {exc}")
                continue
            try:
                bundle = HarnessBundle.from_dict(payload)
                expected = compute_bundle_id(
                    parent_bundle_id=bundle.parent_bundle_id,
                    components=bundle.components,
                    alpha_runtime_version=bundle.alpha_runtime_version,
                    tool_schema_version=bundle.tool_schema_version,
                    policy_version=bundle.policy_version,
                )
                if expected != bundle.bundle_id:
                    raise StoreCorrupt("payload hash mismatch")
                bundles.append(bundle)
            except (ValueError, KeyError, TypeError, StoreCorrupt) as exc:
                unreadable.append(f"{path.name}: malformed bundle: {exc}")
        return Listing(items=tuple(bundles), unreadable=tuple(unreadable))

    # -- active pointer ------------------------------------------------------
    def save_active(self, pointer: ActivePointer) -> None:
        atomic_write_json(self.active_path, pointer.to_dict())

    def load_active(self) -> ActivePointer | None:
        try:
            raw = self.active_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreUnreadable(f"active pointer could not be read: {exc}") from exc
        try:
            return ActivePointer.from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreCorrupt(f"active pointer is malformed: {exc}") from exc

    # -- run records ---------------------------------------------------------
    def save_run(self, record: Any) -> None:
        payload = record.to_dict() if hasattr(record, "to_dict") else record
        atomic_write_json(self.run_path(payload["run_id"]), payload)

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        path = self.run_path(run_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreUnreadable(f"run {run_id} could not be read: {exc}") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise StoreCorrupt(f"run {run_id} is malformed: {exc}") from exc

    def list_runs(self, *, limit: int = 100) -> Listing:
        try:
            files = sorted(self.runs_dir.glob("*.json"))
        except OSError as exc:
            raise StoreUnreadable(f"run directory could not be listed: {exc}") from exc
        runs: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for path in files:
            try:
                runs.append(json.loads(path.read_text(encoding="utf-8")))
            except OSError as exc:
                unreadable.append(f"{path.name}: {exc}")
            except ValueError as exc:
                unreadable.append(f"{path.name}: malformed JSON: {exc}")
        runs.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
        if limit is not None:
            runs = runs[:limit]
        return Listing(items=tuple(runs), unreadable=tuple(unreadable))

    # -- run events ----------------------------------------------------------
    def append_event(self, run_id: str, event: dict[str, Any]) -> None:
        """Append one journal line. The journal is append-only and crash-safe
        at line granularity; a torn final line is skipped on read with a
        disclosure rather than dropping the whole journal."""
        path = self.run_events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, default=str)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line + "\n")
            handle.flush()

    def read_events(self, run_id: str, *, limit: int = 500) -> tuple[list[dict[str, Any]], list[str]]:
        """Return ``(events, unreadable_lines)`` — newest last, bounded tail."""
        path = self.run_events_path(run_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return [], []
        except OSError as exc:
            raise StoreUnreadable(f"events for run {run_id} could not be read: {exc}") from exc
        events: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for index, line in enumerate(raw.splitlines()):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                unreadable.append(f"line {index + 1}")
        if limit is not None and len(events) > limit:
            events = events[-limit:]
        return events, unreadable

    # -- experiences ---------------------------------------------------------
    def save_experience(self, experience_id: str, metadata: dict[str, Any], markdown: str) -> None:
        directory = self.experience_dir(experience_id)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / "metadata.json", metadata)
        _atomic_write_text(directory / "EXPERIENCE.md", markdown)

    def load_experience(self, experience_id: str) -> tuple[dict[str, Any], str] | None:
        directory = self.experience_dir(experience_id)
        meta_path = directory / "metadata.json"
        md_path = directory / "EXPERIENCE.md"
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StoreUnreadable(f"experience {experience_id} metadata could not be read: {exc}") from exc
        except ValueError as exc:
            raise StoreCorrupt(f"experience {experience_id} metadata is malformed: {exc}") from exc
        try:
            markdown = md_path.read_text(encoding="utf-8")
        except OSError:
            markdown = ""
        return metadata, markdown

    def list_experiences(self) -> Listing:
        try:
            directories = sorted(p for p in self.experiences_dir.iterdir() if p.is_dir())
        except FileNotFoundError:
            return Listing()
        except OSError as exc:
            raise StoreUnreadable(f"experience directory could not be listed: {exc}") from exc
        items: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for directory in directories:
            try:
                metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
                items.append(metadata)
            except OSError as exc:
                unreadable.append(f"{directory.name}: {exc}")
            except ValueError as exc:
                unreadable.append(f"{directory.name}: malformed metadata: {exc}")
        return Listing(items=tuple(items), unreadable=tuple(unreadable))

    # -- budget ledger -------------------------------------------------------
    def append_ledger(self, entry: BudgetLedgerEntry) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n")
            handle.flush()

    # -- promotion events ----------------------------------------------------
    def append_promotion(self, event: dict[str, Any]) -> None:
        """Append one promotion/rollback event to the durable chain."""
        self.promotions_path.parent.mkdir(parents=True, exist_ok=True)
        with self.promotions_path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            handle.flush()

    def read_promotions(self, *, limit: int = 200) -> Listing:
        """Read the promotion chain; a torn line is disclosed, not dropped."""
        try:
            raw = self.promotions_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Listing()
        except OSError as exc:
            raise StoreUnreadable(f"promotion log could not be read: {exc}") from exc
        rows: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for index, line in enumerate(raw.splitlines()):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                unreadable.append(f"line {index + 1}")
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return Listing(items=tuple(rows), unreadable=tuple(unreadable))

    def read_ledger(self, *, limit: int = 500) -> Listing:
        try:
            raw = self.ledger_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Listing()
        except OSError as exc:
            raise StoreUnreadable(f"ledger could not be read: {exc}") from exc
        rows: list[dict[str, Any]] = []
        unreadable: list[str] = []
        for index, line in enumerate(raw.splitlines()):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                unreadable.append(f"line {index + 1}")
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return Listing(items=tuple(rows), unreadable=tuple(unreadable))
