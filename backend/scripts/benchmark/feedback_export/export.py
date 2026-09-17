"""Export user feedback rows into benchmark eval cases (offline).

Closes the feedback loop DeepSeek Harness's ``message-feedback`` + snapshot
replay demonstrates: polarity alone (+1/-1) cannot drive targeted evals, but
categorized feedback can. This exporter converts feedback records (as returned
by ``GET /api/threads/{id}/runs/{rid}/feedback``) into a versioned eval-case
manifest shaped like ``scripts/benchmark/context_snapshot`` cases, so the
existing grade/summarize tooling works unchanged.

The exporter is deliberately offline and pure: histories ride alongside the
rows in ``--histories-json`` (``{run_id: [{role, content}, ...]}``, pulled
from the existing run-messages endpoints) instead of opening a database, so
no credentials, no network, and no dataset downloads are ever involved.
Protocol/version rules follow ``scripts/benchmark/deermem_eviction``:
pinned ``schema_version`` + ``protocol_id``, caller-supplied paths only.

Usage:
    python -m scripts.benchmark.feedback_export.export \\
        --input-json feedback_rows.json \\
        --histories-json histories.json \\
        --category correctness --rating -1 \\
        --output-dir /tmp/feedback-eval
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EXPORT_PROTOCOL_ID = "feedback-export-v1"

FEEDBACK_CATEGORIES: frozenset[str] = frozenset(
    {
        "correctness",
        "completeness",
        "grounding",
        "format",
        "latency",
        "other",
    }
)
UNCATEGORIZED = "uncategorized"


def _validate_row(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if row.get("rating") not in (1, -1):
        errors.append(f"row {row.get('feedback_id', '?')}: rating must be +1 or -1")
    category = row.get("category")
    if category is not None and category not in FEEDBACK_CATEGORIES:
        errors.append(f"row {row.get('feedback_id', '?')}: unknown category {category!r}")
    for key in ("feedback_id", "run_id", "thread_id"):
        if not row.get(key):
            errors.append(f"row ?: missing required key {key!r}")
    return errors


def build_eval_case(row: Mapping[str, Any], history: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Convert one feedback row + its run history into an eval case.

    Negative feedback carries the user comment as the failure note
    (``expected``); positive feedback carries it as corroboration. History
    entries are ``{role, content}`` with roles restricted to the model-visible
    surface (user/assistant/tool).
    """
    errors = _validate_row(row)
    if errors:
        raise ValueError("; ".join(errors))
    norm_history: list[dict[str, str]] = []
    for turn in history or []:
        role = str(turn.get("role", "user"))
        if role not in ("user", "assistant", "tool"):
            raise ValueError(f"row {row.get('feedback_id')}: invalid history role {role!r}")
        norm_history.append({"role": role, "content": str(turn.get("content", ""))})
    rating = row["rating"]
    return {
        "name": f"feedback-{row['feedback_id']}",
        "category": row.get("category") or UNCATEGORIZED,
        "rating": rating,
        "brief": (row.get("comment") or "").strip()[:500],
        "history": norm_history,
        "expected": (row.get("comment") or "").strip()[:2000] if rating == -1 else None,
        "source": {
            "feedback_id": row["feedback_id"],
            "run_id": row["run_id"],
            "thread_id": row["thread_id"],
            "message_id": row.get("message_id"),
        },
    }


def export_feedback_cases(
    rows: Sequence[Mapping[str, Any]],
    histories_by_run_id: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    categories: Sequence[str] | None = None,
    ratings: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Filter *rows* and build the eval-case manifest (pure, offline)."""
    wanted_categories = set(categories) if categories else None
    wanted_ratings = set(ratings) if ratings else None
    histories = histories_by_run_id or {}
    cases: list[dict[str, Any]] = []
    for row in rows:
        category = row.get("category") or UNCATEGORIZED
        if wanted_categories is not None and category not in wanted_categories:
            continue
        if wanted_ratings is not None and row.get("rating") not in wanted_ratings:
            continue
        cases.append(build_eval_case(row, histories.get(row.get("run_id", ""))))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": EXPORT_PROTOCOL_ID,
        "case_count": len(cases),
        "cases": cases,
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    return manifest


def _manifest_hash(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps({k: v for k, v in manifest.items() if k != "manifest_sha256"}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_manifest(manifest: Mapping[str, Any]) -> list[str]:
    """Structural contract check for an export manifest. Returns error strings (empty = valid)."""
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if manifest.get("protocol_id") != EXPORT_PROTOCOL_ID:
        errors.append(f"protocol_id must be {EXPORT_PROTOCOL_ID!r}")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        return [*errors, "cases must be a list"]
    seen: set[str] = set()
    for case in cases:
        name = case.get("name") if isinstance(case, Mapping) else None
        if not name:
            errors.append("case missing name")
        elif name in seen:
            errors.append(f"duplicate case name {name!r}")
        else:
            seen.add(name)
        if isinstance(case, Mapping):
            if case.get("rating") not in (1, -1):
                errors.append(f"case {name!r}: rating must be +1 or -1")
            if case.get("category") not in FEEDBACK_CATEGORIES | {UNCATEGORIZED}:
                errors.append(f"case {name!r}: unknown category {case.get('category')!r}")
    if manifest.get("case_count") != len(cases):
        errors.append("case_count does not match len(cases)")
    return errors


def write_manifest(manifest: Mapping[str, Any], output_dir: str | Path) -> Path:
    """Write ``manifest.json`` into a fresh-or-existing directory. Returns the path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export feedback rows to a versioned eval-case manifest.")
    parser.add_argument("--input-json", required=True, help="JSON array of feedback rows (from the feedback list endpoints).")
    parser.add_argument("--histories-json", default=None, help="Optional JSON object {run_id: [{role, content}, ...]}.")
    parser.add_argument("--category", action="append", default=None, help="Only include this category (repeatable).")
    parser.add_argument("--rating", action="append", type=int, choices=(1, -1), default=None, help="Only include this rating (repeatable).")
    parser.add_argument("--output-dir", required=True, help="Directory receiving manifest.json.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns process exit code (0 ok, 2 contract errors)."""
    args = _parse_args(argv)
    try:
        rows = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read input rows: {exc}", file=sys.stderr)
        return 2
    if not isinstance(rows, list):
        print("error: input JSON must be an array of feedback rows", file=sys.stderr)
        return 2
    histories = None
    if args.histories_json:
        try:
            histories = json.loads(Path(args.histories_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: cannot read histories: {exc}", file=sys.stderr)
            return 2
        if not isinstance(histories, dict):
            print("error: histories JSON must be an object {run_id: [...]}", file=sys.stderr)
            return 2
    try:
        manifest = export_feedback_cases(rows, histories, categories=args.category, ratings=args.rating)
    except ValueError as exc:
        print(f"error: invalid feedback row: {exc}", file=sys.stderr)
        return 2
    errors = validate_manifest(manifest)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    path = write_manifest(manifest, args.output_dir)
    print(f"exported {manifest['case_count']} cases -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
