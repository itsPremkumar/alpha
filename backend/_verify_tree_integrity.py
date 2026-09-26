"""Tripwire: the behaviour-trace source tree carries no leftover mutation.

``_mutation_evidence.py`` rewrites source files in place. If the harness restarts
between "write the mutant" and "restore", a mutant survives and unrelated tests
then fail against a file nobody knows is broken -- which happened twice on this
machine. Worse, a survivor can be *masked*: CPython's ``.pyc`` validity key is
``(mtime_seconds, source_size)`` and several mutations are byte-identical in
length to what they replace, so a restored file can keep serving the mutant from
cache. A reverted mutation kept returning ``redaction_policy='strict'`` from a
stale ``.pyc`` while the source plainly read ``STANDARD``.

So this check does two things a source grep cannot:

* **recovers** an orphaned ``.mutationbak`` (restoring the file and purging its
  bytecode), and
* **imports** the modules and asserts the resolved runtime values, not just the
  source text.

Run from ``backend/``::

    python _verify_tree_integrity.py
"""

from __future__ import annotations

import pathlib
import sys

OBSERVABILITY = pathlib.Path("packages/harness/alpha/observability")
BACKUP_SUFFIX = ".mutationbak"

#: ``file -> (required anchors, forbidden snippets)``.
#:
#: Scoped per file, and anchors-first. Both halves were learned the hard way.
#: Scoping matters: ``truncated=False, truncated_count=0`` at 12-space indent is
#: legitimate in ``contract.py`` (the in-budget branch) and only a mutant in
#: ``writer.py``. Anchors matter more: a forbidden substring that is a **suffix
#: of the correct code** is always present, so an earlier revision of this check
#: reported three phantom mutants. A revert *removes* an anchor, so anchors are
#: the sound direction; forbidden snippets are kept only where the mutant text
#: cannot occur in correct code.
CHECKS: dict[str, tuple[tuple[str, ...], tuple[tuple[str, str], ...]]] = {
    "contract.py": (
        (
            "    if total_bytes <= cap_bytes:",
            '        raise TraceEnvelopeError(f"cap_bytes must be an integer >= {MIN_PAYLOAD_CAP_BYTES}',
            "    return ClampedPayload(\n        value=kept,",
            "    kept[PAYLOAD_TRUNCATION_KEY] = notice",
        ),
        (("payload bound removed", "    total_bytes, digest = payload_digest(payload)\n    if True:"),),
    ),
    "taxonomy.py": (
        (
            'ERROR_CODE_TAXONOMY: Final[str] = "alpha.errors.registry"',
            '    if error_taxonomy_status() != "available":',
            'source="registry_fallback",',
            'source="unverified_registry_unavailable",',
        ),
        (),
    ),
    "behaviour_config.py": (
        (
            '"redaction_policy": STANDARD,',
            '    @field_validator("redaction_policy", mode="before")\n    @classmethod\n    def _reject_unknown_policy',
            "    return str(value)",
        ),
        (("redaction policy default flipped to strict", '"redaction_policy": "strict",'),),
    ),
    "writer.py": (
        (
            "            truncated=clamped.truncated,\n            truncated_count=clamped.truncated_count,",
            "            payload_bytes=clamped.bytes_total,",
            "        if not self.traced:\n            return None",
            "                self._flush_failures[name] = self._flush_failures.get(name, 0) + 1",
            "        with self._lock:\n            if key in self._logged:",
            "        definition = definition_for(event_type)",
            '            raise _Rejection(f"missing_required:{definition.event_type}")',
            '                "candidates": [self._candidate_ref(candidate) for candidate in listed],',
            '                "registry_version": registry_version,',
            "            effective_parent = span.parent_span_id",
            '        return InMemorySink(max_records=READERS["tail_maxlen"].read(self._config))',
            '            "usage_breakdown": {',
        ),
        (
            ("truncation disclosure removed", "            truncated=False,\n            truncated_count=0,"),
            ("sink containment removed", "except _Impossible"),
            ("unknown event type accepted", "def _by_name_fallback"),
            ("flush counter frozen", "self._flush_failures.setdefault(name, 0)"),
            ("required-field contract removed", "        if False:\n            raise _Rejection"),
            ("tail bound removed", "InMemorySink(max_records=10_000_000)"),
            ("candidate set removed", '"candidates": [chosen],'),
            ("registry version removed", '"registry_version": "unknown",'),
            ("token_breakdown name restored", '"token_breakdown": {'),
        ),
    ),
    "ambient.py": (("_current_writer: ContextVar[BehaviourTraceWriter | None]", "def current_writer()"), ()),
}


def recover_orphans() -> list[str]:
    """Restore any module left mutated by an interrupted mutation run.

    This check is the tripwire people actually run, so recovery belongs here and
    not only in ``_mutation_evidence.main``: an interrupted run leaves a mutant
    *and* the backup of the original, and the next person to look should get a
    clean tree and a loud message rather than a failed gate to diagnose.
    """
    recovered: list[str] = []
    for backup in sorted(OBSERVABILITY.glob("*" + BACKUP_SUFFIX)):
        target = backup.with_name(backup.name[: -len(BACKUP_SUFFIX)])
        backup.replace(target)
        for cached in (target.parent / "__pycache__").glob(f"{target.stem}.*.pyc"):
            cached.unlink(missing_ok=True)
        recovered.append(str(target))
    return recovered


def main() -> int:
    recovered = recover_orphans()
    for target in recovered:
        print(f"[RECOVERED] {target} was left mutated by an interrupted run and has been restored")
    if recovered:
        print()

    problems: list[str] = []
    for name, (required, forbidden) in CHECKS.items():
        path = OBSERVABILITY / name
        if not path.is_file():
            problems.append(f"missing {path}")
            continue
        text = path.read_text(encoding="utf-8")
        for needle in required:
            if needle not in text:
                problems.append(f"{name}: required anchor absent -> {needle!r}")
        for label, needle in forbidden:
            if needle in text:
                problems.append(f"{name}: MUTANT PRESENT ({label})")

    # Import the modules *fresh* and assert the resolved runtime values. Source-only
    # checking cannot see a mutant masked by a stale .pyc; asserting the value the
    # writer will really use closes that gap.
    try:
        from alpha.observability.behaviour_config import BehaviourTraceConfig

        policy = BehaviourTraceConfig().redaction_policy
        if policy != "standard":
            problems.append(f"runtime default redaction_policy is {policy!r}, expected 'standard' (stale bytecode?)")
    except Exception as exc:  # noqa: BLE001 - a broken import is itself the finding
        problems.append(f"behaviour_config will not import: {exc}")

    if problems:
        print("SOURCE TREE INTEGRITY: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    anchors = sum(len(v[0]) for v in CHECKS.values())
    print(f"SOURCE TREE INTEGRITY: PASS - {anchors} anchors present, no mutant in {len(CHECKS)} files, runtime default redaction_policy='standard'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
