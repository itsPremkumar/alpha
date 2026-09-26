"""Mutation evidence: revert each fix, show the test that depends on it failing.

The rule this exists to satisfy: *a test never observed failing is not evidence*.
Every entry below is a revert of one specific mechanism, run against the test
that claims to cover it, and then restored. A mutation whose test still passes
prints ``NO`` and fails the run, because a green revert means the claim is not
backed by an assertion.

Crash safety is the hard part, and both rules here were learned by the harness
being interrupted mid-run:

* the backup lives **beside** the file as ``<name>.mutationbak``, not in a
  ``TemporaryDirectory`` -- a hard kill must not destroy the only copy of the
  original, which is exactly what happened the first time;
* the restore is in a ``finally`` **and** verified byte-for-byte;
* the pytest subprocess runs with ``PYTHONDONTWRITEBYTECODE=1`` and the module's
  cached bytecode is purged after every restore. CPython's ``.pyc`` validity key
  is ``(mtime_seconds, source_size)`` and several mutations below are the *same
  length* as what they replace, so without this a reverted mutation keeps serving
  from cache -- which happened, and is nastier than the mutant itself.

Run from ``backend/``::

    python -B _mutation_evidence.py            # all mutations
    python -B _mutation_evidence.py 0-4        # a chunk, if the host is busy
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

WRITER = pathlib.Path("packages/harness/alpha/observability/writer.py")
CONTRACT = pathlib.Path("packages/harness/alpha/observability/contract.py")
CONFIG = pathlib.Path("packages/harness/alpha/observability/behaviour_config.py")
TAXONOMY = pathlib.Path("packages/harness/alpha/observability/taxonomy.py")
BACKUP_SUFFIX = ".mutationbak"

#: ``(label, file, original, mutated, test-nodeid)``
MUTATIONS: tuple[tuple[str, pathlib.Path, str, str, str], ...] = (
    (
        "redaction-at-write-time removed: payloads are no longer scrubbed before the envelope is built",
        WRITER,
        "        safe_payload, _outcomes = self._redactor.redact_attributes(merged)",
        "        safe_payload = dict(merged)",
        "tests/test_behaviour_trace.py::test_secret_in_prompt_tool_arg_and_tool_result_never_reaches_the_log",
    ),
    (
        "payload bound removed: clamping is replaced by storing the payload whole",
        CONTRACT,
        "    total_bytes, digest = payload_digest(payload)\n    if total_bytes <= cap_bytes:",
        "    total_bytes, digest = payload_digest(payload)\n    if True:",
        "tests/test_behaviour_trace.py::test_oversized_payload_is_truncated_with_flag_size_and_hash",
    ),
    (
        "truncation disclosure removed: the flag and the original byte count are no longer reported",
        WRITER,
        (
            "            payload_bytes=clamped.bytes_total,\n"
            "            payload_stored_bytes=clamped.bytes_stored,\n"
            "            payload_sha256=clamped.sha256,\n"
            "            truncated=clamped.truncated,\n"
            "            truncated_count=clamped.truncated_count,"
        ),
        (
            "            payload_bytes=clamped.bytes_stored,\n"
            "            payload_stored_bytes=clamped.bytes_stored,\n"
            "            payload_sha256=clamped.sha256,\n"
            "            truncated=False,\n"
            "            truncated_count=0,"
        ),
        "tests/test_behaviour_trace.py::test_oversized_payload_is_truncated_with_flag_size_and_hash",
    ),
    (
        "span nesting removed: an event no longer inherits the enclosing span's ids",
        WRITER,
        "        effective_parent = parent_span_id\n        if effective_parent is None and span is not None:\n            effective_parent = span.parent_span_id",
        "        effective_parent = parent_span_id",
        "tests/test_behaviour_trace.py::test_parent_child_spans_nest_for_a_subagent",
    ),
    (
        "candidate set removed from a tool selection: only the winner is recorded",
        WRITER,
        # `tool_selection` and `skill_selection` build a `candidates` list from the
        # same expression at the same indent, so a one-line anchor is ambiguous
        # and this mutation was SKIPPED. The line *preceding* `candidates`
        # differs: the skill emitter inserts `registry_version` first.
        '                "reason": reason,\n                "candidates": [self._candidate_ref(candidate) for candidate in listed],\n',
        '                "reason": reason,\n                "candidates": [chosen],\n',
        "tests/test_behaviour_trace.py::test_tool_selection_records_the_candidate_set_the_choice_and_a_reason",
    ),
    (
        "registry version removed from a skill selection",
        WRITER,
        '                "registry_version": registry_version,',
        '                "registry_version": "unknown",',
        "tests/test_behaviour_trace.py::test_skill_selection_records_the_candidate_set_the_choice_a_reason_and_the_registry_version",
    ),
    (
        # A narrow `except ValueError` leaves the module importable, so the test
        # fails by *asserting* rather than by failing to collect. An earlier
        # version swapped in a helper class and the run "bit" only because the
        # test module stopped importing (pytest exit 4, "found no collectors"),
        # which proves nothing about the containment.
        "sink-failure containment removed: a raising sink now propagates into the caller",
        WRITER,
        "            except Exception as exc:  # noqa: BLE001 - a sink must never break the run",
        "            except ValueError as exc:  # noqa: BLE001 - mutated: containment removed",
        "tests/test_behaviour_trace.py::test_sink_failure_degrades_to_a_counted_warning_and_the_run_still_succeeds",
    ),
    (
        "flush-failure counter removed: failures are no longer counted",
        WRITER,
        "                    self._flush_failures[name] = self._flush_failures.get(name, 0) + 1",
        "                    self._flush_failures.setdefault(name, 0)",
        "tests/test_behaviour_trace.py::test_sink_failure_degrades_to_a_counted_warning_and_the_run_still_succeeds",
    ),
    (
        "log-once-per-sink removed: a broken sink now logs once per event",
        WRITER,
        "        with self._lock:\n            if key in self._logged:\n                return\n            self._logged.add(key)\n        logger.warning(message, *args)",
        "        logger.warning(message, *args)",
        "tests/test_behaviour_trace.py::test_sink_failure_degrades_to_a_counted_warning_and_the_run_still_succeeds",
    ),
    (
        "default-off gate removed: a disabled writer now records",
        WRITER,
        "        if not self.traced:\n            return None\n        try:",
        "        try:",
        "tests/test_behaviour_trace.py::test_disabled_writer_costs_nothing",
    ),
    (
        "one-taxonomy guard removed: an unknown event type is stored instead of refused",
        WRITER,
        "        definition = definition_for(event_type)\n",
        "        definition = _by_name_fallback(event_type)\n",
        "tests/test_behaviour_trace.py::test_unknown_event_type_is_a_counted_rejection_not_an_exception",
    ),
    (
        "required-field contract removed: a tool selection without a reason is stored",
        WRITER,
        "        if any(field not in merged for field in definition.required_fields):\n            raise _Rejection(f\"missing_required:{definition.event_type}\")",
        "        if False:\n            raise _Rejection(f\"missing_required:{definition.event_type}\")",
        "tests/test_behaviour_trace.py::test_a_required_field_must_be_present_or_the_event_is_refused",
    ),
    (
        # The *reused* ring test does not reach this line: handing the recorder a
        # memory sink means `_resolve_tail` returns that one. Hence the
        # writer-owned-ring test exists, and this mutation points at it.
        "tail bound removed: the in-memory ring grows without limit",
        WRITER,
        "        return InMemorySink(max_records=READERS[\"tail_maxlen\"].read(self._config))",
        "        return InMemorySink(max_records=10_000_000)",
        "tests/test_behaviour_trace.py::test_tail_maxlen_bounds_a_writer_owned_ring",
    ),
    (
        "redaction policy default changed from standard to strict: ids, paths and URLs are eaten",
        CONFIG,
        '    "redaction_policy": STANDARD,',
        '    "redaction_policy": "strict",',
        "tests/test_behaviour_trace.py::test_default_policy_keeps_ids_paths_and_urls_but_not_credentials",
    ),
    (
        "unified error taxonomy removed: a supplied code is trusted without consulting the registry",
        TAXONOMY,
        "    if error_taxonomy_status() != \"available\":\n        return ErrorCodeResolution(\n            code=str(supplied) if supplied else TOTAL_FALLBACK_ERROR_CODE,\n            source=\"unverified_registry_unavailable\",\n        )",
        "    return ErrorCodeResolution(code=str(supplied) if supplied else TOTAL_FALLBACK_ERROR_CODE, source=\"unverified_registry_unavailable\")",
        "tests/test_behaviour_trace.py::test_error_codes_come_from_the_one_registry_and_are_never_invented",
    ),
)

#: Appended to the mutated file for the one mutation that needs a name that does
#: not exist there. Checked by label rather than sniffed for in the mutation
#: text, because a "needs a helper" mutation that silently stops getting one
#: produces a collection error that *looks* like a passing bit.
_HELPER = '''

def _by_name_fallback(event_type: str):  # noqa: ANN201 - mutation-only
    """Mutation-only: accept an unknown event type instead of raising."""
    from alpha.observability.taxonomy import definition_for as _real

    try:
        return _real(event_type)
    except KeyError:
        return _real("tool.selection")
'''
_NEEDS_HELPER = frozenset({"one-taxonomy guard removed: an unknown event type is stored instead of refused"})


def _backup_path(target: pathlib.Path) -> pathlib.Path:
    return target.with_name(target.name + BACKUP_SUFFIX)


def _restore(target: pathlib.Path) -> None:
    backup = _backup_path(target)
    if backup.exists():
        backup.replace(target)


def _purge_bytecode(*paths: pathlib.Path) -> None:
    """Delete cached bytecode for *paths*' modules.

    Belt to the ``finally``'s braces. The restore is byte-exact, but a ``.pyc``
    written while the mutant was live is only *probably* invalidated, and
    "probably" is the wrong word for a file that decides whether secrets reach a
    log.
    """
    for path in paths:
        for cached in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
            cached.unlink(missing_ok=True)


def run(nodeid: str) -> tuple[int, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-m", "pytest", nodeid, "-q", "-p", "no:cacheprovider", "--no-header", "-x"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def select(indices: str | None) -> list[tuple[str, pathlib.Path, str, str, str]]:
    """Return the mutations to run, optionally filtered by 0-based index or range.

    Chunking exists because the harness restarts. Each mutation costs one pytest
    launch, so a 15-mutation run is a window a restart can and did interrupt.
    ``0-4`` makes each window short enough to finish, and the index is printed
    with every result so a chunked run can be stitched back together.
    """
    if not indices:
        return list(MUTATIONS)
    wanted: list[int] = []
    for part in indices.split(","):
        part = part.strip()
        if "-" in part:
            start, _, end = part.partition("-")
            wanted.extend(range(int(start), int(end) + 1))
        elif part:
            wanted.append(int(part))
    missing = [index for index in wanted if not 0 <= index < len(MUTATIONS)]
    if missing:
        raise SystemExit(f"mutation index out of range: {missing}; there are {len(MUTATIONS)}")
    return [MUTATIONS[index] for index in wanted]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    indices = argv[0] if argv and argv[0] not in ("-h", "--help") else None

    for orphan in sorted(pathlib.Path("packages/harness/alpha/observability").glob("*" + BACKUP_SUFFIX)):
        target = orphan.with_name(orphan.name[: -len(BACKUP_SUFFIX)])
        _restore(orphan)
        _purge_bytecode(target)
        print(f"[RECOVERED] restored {target} from a leftover backup")
    print()

    results: list[tuple[str, str, bool, str, int]] = []
    for position, (label, target, original, mutated, nodeid) in enumerate(select(indices)):
        source = target.read_text(encoding="utf-8")
        text = mutated + _HELPER if label in _NEEDS_HELPER else mutated
        if source.count(original) != 1:
            results.append((label, nodeid, False, f"SKIPPED: anchor text not found exactly once in {target.name} (found {source.count(original)})", position))
            continue
        backup = _backup_path(target)
        backup.write_text(source, encoding="utf-8", newline="")
        try:
            target.write_text(source.replace(original, text), encoding="utf-8", newline="")
            code, output = run(nodeid)
        finally:
            _restore(target)
            _purge_bytecode(target)
        restored = target.read_bytes() == backup.read_bytes() if backup.exists() else True
        if backup.exists():
            backup.unlink()
        bit = code != 0
        detail = f"exit={code} :: {([line for line in output.strip().splitlines() if line.strip()] or [''])[-1][:150]}"
        if not restored:
            bit = False
            detail = "SOURCE RESTORE MISMATCH -- investigate immediately"
        results.append((label, nodeid, bit, detail, position))

    print("=" * 100)
    print("MUTATION EVIDENCE - each fix reverted, the covering test observed failing, then restored")
    print("=" * 100)
    all_bite = True
    for label, nodeid, bit, detail, position in results:
        all_bite = all_bite and bit
        print(f"[{'BIT ' if bit else 'NO  '}] #{position:>2} {label}")
        print(f"         test : {nodeid}")
        print(f"         {detail}")
        print()
    bit_count = sum(1 for result in results if result[2])
    print("=" * 100)
    print(f"{bit_count}/{len(results)} mutations made their covering test fail")
    print("verdict:", "PASS - every fix is load-bearing" if all_bite else "FAIL - at least one fix is not covered by a failing test")
    return 0 if all_bite else 1


if __name__ == "__main__":
    raise SystemExit(main())
