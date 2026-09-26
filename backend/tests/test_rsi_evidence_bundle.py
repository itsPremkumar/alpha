"""WP-C2/C2a tests: RSI promotion evidence bundle.

Honesty pins (plan §3 WP-C2 evidence-bundle design bullet, §5.6 guardrails):

- all nine plan-named files land under ``runtime_home()/rsi/bundles/<id>/``;
  ``bundle_index.json`` sha256 digests equal the real bytes on disk; the
  append-only ledger receives exactly ONE event; ``verify_bundle`` detects
  corruption, deletion, strays, and a tampered index with the real text;
- every item carries an ``evidence_kind`` from the literal §5.6 whitelist
  ``{measured, simulated, heuristic, unverified}`` — asserted against THIS
  file's literal set (drift-proof) as well as the implementation constant;
- only ``measured`` can satisfy a pass condition: ``simulated`` (never
  gates), ``heuristic``, ``unverified``, and missing items fail with the
  real reason; kind eligibility is not a verdict (the real holdout gate is
  cross-checked against the real ``alpha.rsi.holdout`` API);
- a missing/failed upstream source propagates as ``unavailable``/``failed``
  with the real reason. Shadow evidence is integrated duck-typed and
  optional (WP-C1 lands concurrently): these tests force both the
  module-absent and module-present paths, so they pass whether or not
  ``alpha/rsi/shadow.py`` exists yet;
- serialized bundle output contains no invented score/confidence/pass-rate
  fields and none of the codebase's fabricated defaults (0.89, 96.4/0.964);
  upstream payloads, by contrast, are carried VERBATIM (pinned here);
- ``add()`` refuses ``None``/unknown/duplicate/post-finalize payloads and
  unsafe ids; writes are atomic (no leftover ``*.tmp``) and round-trip;
- deterministic: injectable fixed clock, ``AGENT_WORKSPACE_HOME`` ->
  ``tmp_path`` autouse fixture, no network, no subprocesses.
"""

import hashlib
import json
import sys
import types

import pytest

from alpha.rsi import evidence_bundle
from alpha.rsi.evidence_bundle import (
    BUNDLE_FILES,
    begin_bundle,
    failed_source,
    shadow_evidence,
    unavailable_source,
    verify_bundle,
)
from alpha.rsi.holdout import holdout_gate, holdout_not_run

#: The plan §5.6 honesty whitelist, written LITERALLY here so any drift in
#: the implementation constant fails the test instead of following it.
WHITELIST = {"measured", "simulated", "heuristic", "unverified"}

#: Forbidden metric keys in bundle-GENERATED structures (normalized: lower
#: case, underscores stripped). Upstream payloads may legitimately carry
#: labeled metrics verbatim; the bundle itself must never originate one.
FORBIDDEN_GENERATED_KEYS = {
    "confidence",
    "score",
    "passrate",
    "promotionrate",
    "accuracy",
    "winrate",
    "successrate",
    "improved",
}

#: Fabricated defaults from the surrounding codebase (RSI preview 0.89,
#: enterprise holdout 96.4/0.964) that must never appear anywhere in this
#: bundle's output — generated structures OR the deliberately score-free
#: payloads below.
FORBIDDEN_FABRICATED_NUMBERS = ("0.89", "0.964", "96.4")

#: Bare metric keys as key-form text, banned in bundle-GENERATED output
#: (the recursive key scan enforces the same set structurally). Stored
#: payloads are scanned for the numbers only: they are carried verbatim,
#: and a *labeled* confidence (``confidence_kind`` alongside, spec §21 —
#: see the reviews fixture) is honest upstream content, not fabrication.
FORBIDDEN_GENERATED_KEY_FORMS = ('"score"', '"confidence"', '"pass_rate"', '"improved": true')

FIXED_CLOCK = 1_700_000_000.0


@pytest.fixture(autouse=True)
def rsi_home(tmp_path, monkeypatch):
    """Run every test against a temp AGENT_WORKSPACE_HOME (env is global)."""
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    return tmp_path


def _fixed_clock():
    return FIXED_CLOCK


def _payload(name, **overrides):
    """Sample caller-supplied evidence: plain fields only, no metrics."""
    base = {"source": name, "note": "caller-supplied evidence, stored verbatim"}
    base.update(overrides)
    return base


def _force_shadow_absent(monkeypatch):
    """Simulate WP-C1's module being absent, whether or not shadow.py exists.

    ``sys.modules[...] = None`` makes ``importlib.import_module`` raise the
    real ModuleNotFoundError ("import of alpha.rsi.shadow halted; None in
    sys.modules"); clearing the package attribute covers from-import styles.
    monkeypatch restores the real module afterwards.
    """
    import alpha.rsi

    monkeypatch.setitem(sys.modules, "alpha.rsi.shadow", None)
    monkeypatch.delattr(alpha.rsi, "shadow", raising=False)


def _install_fake_shadow_module(monkeypatch):
    """Install a test double so module-present paths run regardless of shadow.py."""
    fake = types.ModuleType("alpha.rsi.shadow")
    fake.__marker__ = "test-double"
    monkeypatch.setitem(sys.modules, "alpha.rsi.shadow", fake)
    return fake


def test_bundle_stores_all_nine_files_with_real_sha256_and_one_ledger_event(rsi_home):
    bundle = begin_bundle("cand-nine", clock=_fixed_clock)
    added = {}
    for name in BUNDLE_FILES:
        added[name] = bundle.add(name, _payload(name))
    assert len(BUNDLE_FILES) == 9  # the plan enumerates exactly nine evidence files
    report = bundle.finalize()

    bundle_dir = rsi_home / "rsi" / "bundles" / "cand-nine"
    for name in BUNDLE_FILES:
        assert (bundle_dir / name).is_file()
    assert (bundle_dir / "bundle_index.json").is_file()
    assert not list(bundle_dir.glob("*.tmp"))  # atomic tmp + os.replace leaves no staging file

    index = json.loads((bundle_dir / "bundle_index.json").read_text(encoding="utf-8"))
    assert set(index["files"]) == set(BUNDLE_FILES)
    for name in BUNDLE_FILES:
        blob = (bundle_dir / name).read_bytes()
        assert index["files"][name]["sha256"] == hashlib.sha256(blob).hexdigest()
        assert index["files"][name]["bytes"] == len(blob)
        assert index["files"][name]["added_at"] == FIXED_CLOCK  # deterministic injected clock
        assert added[name]["evidence_kind"] in WHITELIST
    assert index["finalized_at"] == FIXED_CLOCK
    assert index["candidate_id"] == "cand-nine"

    # the report round-trips the on-disk index exactly and discloses the ledger
    assert report["files"] == index["files"]
    assert report["index_path"] == str(bundle.bundle_dir / "bundle_index.json")
    assert report["ledger"]["appended"] is True
    assert report["ledger"]["error"] is None

    # exactly ONE ledger event (append-only JSONL, lineage precedent)
    ledger = rsi_home / "rsi" / "bundles" / "bundles_ledger.jsonl"
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "bundle_finalized"
    assert event["candidate_id"] == "cand-nine"
    assert event["at"] == FIXED_CLOCK
    assert event["files"] == {name: index["files"][name]["sha256"] for name in BUNDLE_FILES}

    ok, reason = verify_bundle("cand-nine")
    assert ok is True
    assert "9 file(s)" in reason


def test_add_refuses_none_unknown_duplicate_and_post_finalize(rsi_home):
    bundle = begin_bundle("cand-refuse", clock=_fixed_clock)
    with pytest.raises(ValueError, match="payload is None"):
        bundle.add("holdout.json", None)
    with pytest.raises(ValueError, match="unknown evidence bundle file"):
        bundle.add("extra_notes.json", {"x": 1})
    with pytest.raises(ValueError, match="must be a JSON object or array"):
        bundle.add("canary.json", "not structured evidence")
    with pytest.raises(ValueError, match="not JSON-serializable"):
        bundle.add("canary.json", {"at": object()})
    bundle.add("holdout.json", _payload("holdout"))
    with pytest.raises(ValueError, match="recorded exactly once"):
        bundle.add("holdout.json", _payload("holdout"))
    bundle.finalize()
    with pytest.raises(ValueError, match="already finalized"):
        bundle.add("reviews.json", [{"reviewer": "r"}])
    with pytest.raises(ValueError, match="already finalized"):
        bundle.finalize()
    # only the accepted file exists — every refusal left no partial write
    assert sorted(p.name for p in (rsi_home / "rsi" / "bundles" / "cand-refuse").iterdir()) == ["bundle_index.json", "holdout.json"]


def test_evidence_kind_whitelist_is_enforced_on_every_item(rsi_home):
    assert set(evidence_bundle.EVIDENCE_KINDS) == WHITELIST  # implementation constant == literal §5.6 set
    assert set(evidence_bundle.KIND_SOURCES) == {"declared", "payload", "absent_default"}
    bundle = begin_bundle("cand-kinds", clock=_fixed_clock)

    # payload-declared kind, validated at the boundary
    item = bundle.add("holdout.json", {"evidence_kind": "measured", "detail": "executed suite"})
    assert item["evidence_kind"] == "measured" and item["kind_source"] == "payload"
    # legacy "unknown" normalizes honestly to "unverified" (lineage rule)
    legacy = bundle.add("manifest.json", {"evidence_kind": "unknown"})
    assert legacy["evidence_kind"] == "unverified" and legacy["kind_source"] == "payload"
    # absent label -> honest unverified, never measured (no back-filled default)
    absent = bundle.add("hypothesis.json", {"statement": "tighten compaction budget"})
    assert absent["evidence_kind"] == "unverified" and absent["kind_source"] == "absent_default"
    # explicit declaration for payloads without their own label (e.g. review lists)
    declared = bundle.add("reviews.json", [{"reviewer": "hyperplan", "status": "ok"}], evidence_kind="heuristic")
    assert declared["evidence_kind"] == "heuristic" and declared["kind_source"] == "declared"
    for record in bundle.items.values():
        assert record["evidence_kind"] in WHITELIST
        assert set(record) == {"name", "evidence_kind", "kind_source", "added_at"}

    # invalid labels fail closed with the real whitelist text
    with pytest.raises(ValueError, match="whitelist"):
        bundle.add("baseline_metrics.json", {"evidence_kind": "authoritative"})
    with pytest.raises(ValueError, match="whitelist"):
        bundle.add("candidate_metrics.json", {"detail": "x"}, evidence_kind="measured-ish")
    # conflicting labels refuse instead of picking a winner
    with pytest.raises(ValueError, match="conflicting evidence_kind"):
        bundle.add("canary.json", {"evidence_kind": "simulated"}, evidence_kind="measured")

    # refused adds left nothing on disk
    bundle_dir = rsi_home / "rsi" / "bundles" / "cand-kinds"
    assert not (bundle_dir / "baseline_metrics.json").exists()
    assert not (bundle_dir / "canary.json").exists()

    # kinds survive the atomic round trip into the index
    bundle.finalize()
    index = json.loads((bundle_dir / "bundle_index.json").read_text(encoding="utf-8"))
    for entry in index["files"].values():
        assert entry["evidence_kind"] in WHITELIST
        assert entry["kind_source"] in evidence_bundle.KIND_SOURCES


def test_only_measured_evidence_can_satisfy_a_pass_condition(rsi_home):
    bundle = begin_bundle("cand-gate", clock=_fixed_clock)
    bundle.add("holdout.json", {"evidence_kind": "simulated", "note": "preview value"})
    bundle.add("shadow.json", {"evidence_kind": "unverified", "state": "inconclusive"})
    bundle.add("reviews.json", [{"reviewer": "hyperplan", "status": "ok"}], evidence_kind="heuristic")
    bundle.add("manifest.json", {"evidence_kind": "measured", "state": "complete"})

    ok, reason = bundle.meets_evidence_standard("holdout.json")
    assert ok is False and "simulated" in reason and "never" in reason
    ok, reason = bundle.meets_evidence_standard("shadow.json")
    assert ok is False and "unverified" in reason
    ok, reason = bundle.meets_evidence_standard("reviews.json")
    assert ok is False and "heuristic" in reason
    ok, reason = bundle.meets_evidence_standard("manifest.json")
    assert ok is True and "measured" in reason
    # a missing item is never a pass
    ok, reason = bundle.meets_evidence_standard("promotion_decision.json")
    assert ok is False and "missing" in reason

    # the REAL alpha.rsi.holdout canonical not-run payload is carried
    # verbatim and, being unverified 0.5-neutral, can never pass — and the
    # real holdout gate agrees (cross-check against the landed A3 API)
    not_run = holdout_not_run("holdout not run: candidate_view was not provided")
    bundle2 = begin_bundle("cand-holdout", clock=_fixed_clock)
    bundle2.add("holdout.json", not_run)
    stored = json.loads((rsi_home / "rsi" / "bundles" / "cand-holdout" / "holdout.json").read_text(encoding="utf-8"))
    assert stored == not_run  # verbatim: no field added, edited, or dropped
    ok, reason = bundle2.meets_evidence_standard("holdout.json")
    assert ok is False and "unverified" in reason
    assert holdout_gate(not_run) == (False, "holdout unverified — cannot gate on unverified evidence")


def test_missing_and_failed_upstream_sources_propagate_honestly(rsi_home, monkeypatch):
    # (a) module absent — forced, so this passes whether or not C1 landed
    _force_shadow_absent(monkeypatch)
    marker = shadow_evidence("cand-upstream")
    assert marker["state"] == "unavailable"
    assert marker["state"] in evidence_bundle.SOURCE_STATES
    assert marker["evidence_kind"] == "unverified" and marker["evidence_kind"] in WHITELIST
    assert marker["record"] is None
    assert "alpha.rsi.shadow" in marker["reason"]
    # the REAL import error text travels verbatim (type name + message)
    assert "ModuleNotFoundError" in marker["reason"]
    assert "None in sys.modules" in marker["reason"]

    # (b) module present (test double) but no record names this candidate
    _install_fake_shadow_module(monkeypatch)
    marker = shadow_evidence("cand-upstream")
    assert marker["state"] == "unavailable"
    assert marker["record"] is None
    assert "no shadow comparison record names candidate 'cand-upstream'" in marker["reason"]
    assert "does not exist" in marker["reason"]  # real location disclosed

    # (c) a real on-disk record naming the candidate -> recorded, verbatim
    shadow_dir = rsi_home / "rsi" / "shadow"
    shadow_dir.mkdir(parents=True)
    record = {"candidate_id": "cand-upstream", "run_id": "run-1", "state": "improved", "evidence_kind": "measured", "deltas": {"fixture_a": 0.1}}
    (shadow_dir / "run-1.json").write_text(json.dumps(record), encoding="utf-8")
    marker = shadow_evidence("cand-upstream")
    assert marker["state"] == "recorded"
    assert marker["evidence_kind"] == "measured"
    assert marker["record"] == record  # unknown fields preserved verbatim

    # (d) a record without a label is honestly unverified, never measured
    (shadow_dir / "run-2.json").write_text(json.dumps({"candidate_id": "cand-unlabeled", "state": "improved"}), encoding="utf-8")
    marker = shadow_evidence("cand-unlabeled")
    assert marker["state"] == "recorded" and marker["evidence_kind"] == "unverified"

    # (e) an invalid label on a real record fails closed with the real text
    (shadow_dir / "run-3.json").write_text(json.dumps({"candidate_id": "cand-badlabel", "evidence_kind": "definitely-measured"}), encoding="utf-8")
    marker = shadow_evidence("cand-badlabel")
    assert marker["state"] == "failed"
    assert marker["record"] is None
    assert "whitelist" in marker["reason"]

    # (f) an upstream evaluation error propagates as failed with the real text
    def _boom(candidate_id):
        raise RuntimeError("real shadow read failure")

    monkeypatch.setattr(evidence_bundle, "_shadow_record_for", _boom)
    marker = shadow_evidence("cand-upstream")
    assert marker["state"] == "failed"
    assert marker["reason"] == "shadow record scan failed: RuntimeError: real shadow read failure"

    # (g) canonical markers: shape honest, empty reasons refused, never a pass
    missing = unavailable_source("holdout", "holdout not run: candidate_view was not provided")
    assert missing == {
        "source": "holdout",
        "state": "unavailable",
        "evidence_kind": "unverified",
        "reason": "holdout not run: candidate_view was not provided",
        "record": None,
    }
    failed = failed_source("release_gate", "RuntimeError: evaluator blew up")
    assert failed["state"] == "failed" and failed["record"] is None
    assert failed["evidence_kind"] == "unverified"
    assert missing["state"] in evidence_bundle.SOURCE_STATES
    assert failed["state"] in evidence_bundle.SOURCE_STATES
    with pytest.raises(ValueError, match="real reason"):
        unavailable_source("canary", "")
    with pytest.raises(ValueError, match="real reason"):
        failed_source("canary", "")

    # an unavailable marker added to a bundle still cannot pass
    bundle = begin_bundle("cand-upstream-bundle", clock=_fixed_clock)
    bundle.add("holdout.json", missing)
    ok, reason = bundle.meets_evidence_standard("holdout.json")
    assert ok is False and "unverified" in reason


def test_serialized_bundle_contains_no_invented_scores_or_confidences(rsi_home):
    bundle = begin_bundle("cand-clean", clock=_fixed_clock)
    payloads = {
        "manifest.json": {"state": "complete", "version": 1, "files": ["a.py"]},
        "hypothesis.json": {"statement": "tighten compaction budget", "parent": None},
        "baseline_metrics.json": {"source": "alpha.benchmarks (caller-supplied)", "fixtures": 5},
        "candidate_metrics.json": {"source": "alpha.benchmarks (caller-supplied)", "fixtures": 5},
        "holdout.json": {"source": "run_holdout (caller-supplied)", "evidence_kind": "measured"},
        "shadow.json": unavailable_source("shadow", "alpha.rsi.shadow is not importable (forced in test)"),
        # a spec §21-shaped verdict: confidence is allowed ONLY with its
        # declared kind alongside — the label is what makes it honest
        "reviews.json": [{"reviewer": "hyperplan", "status": "approved", "confidence": 0.4, "confidence_kind": "heuristic"}],
        "canary.json": {"source": "CanaryWatchdog (caller-supplied)", "status": "healthy"},
        "promotion_decision.json": {"promoted": False, "gates": [{"gate": "kill_switch", "ok": False, "reason": "kill switch engaged: operator STOP"}]},
    }
    for name, payload in payloads.items():
        bundle.add(name, payload)
    report = bundle.finalize()

    index = json.loads((bundle.bundle_dir / "bundle_index.json").read_text(encoding="utf-8"))
    ledger_line = (rsi_home / "rsi" / "bundles" / "bundles_ledger.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
    ledger_event = json.loads(ledger_line)
    generated = {"index": index, "report": report, "item_records": list(bundle.items.values()), "ledger_event": ledger_event}

    # (a) exact-key pins: bundle-generated structures cannot grow invented fields
    assert set(index) == {"version", "candidate_id", "bundle_dir", "finalized_at", "files"}
    assert set(ledger_event) == {"event", "candidate_id", "bundle_dir", "at", "files"}
    assert set(report) == {"version", "candidate_id", "bundle_dir", "finalized_at", "files", "index_path", "ledger"}
    for entry in index["files"].values():
        assert set(entry) == {"evidence_kind", "kind_source", "added_at", "sha256", "bytes"}

    # (b) recursive forbidden-key scan over every bundle-generated structure
    def _normalize(key):
        return str(key).lower().replace("_", "")

    def _walk(node, trail=()):
        if isinstance(node, dict):
            for key, value in node.items():
                assert _normalize(key) not in FORBIDDEN_GENERATED_KEYS, f"fabricated metric key {key!r} at {trail}"
                _walk(value, trail + (key,))
        elif isinstance(node, list):
            for position, value in enumerate(node):
                _walk(value, trail + (str(position),))

    for label, structure in generated.items():
        _walk(structure, (label,))

    # (c) full-text scan: none of the codebase's fabricated numbers appear
    # anywhere (generated structures + stored payloads), and no bare metric
    # key-form appears in anything the BUNDLE itself generated
    serialized_generated = json.dumps(generated, ensure_ascii=False, sort_keys=True, default=str)
    serialized_all = serialized_generated
    for name in BUNDLE_FILES:
        serialized_all += (bundle.bundle_dir / name).read_text(encoding="utf-8")
    for forbidden in FORBIDDEN_FABRICATED_NUMBERS:
        assert forbidden not in serialized_all, f"fabricated default {forbidden!r} found in bundle output"
    for forbidden in FORBIDDEN_GENERATED_KEY_FORMS:
        assert forbidden not in serialized_generated, f"bare metric key {forbidden!r} originated by the bundle"


def test_verify_detects_corruption_deletion_strays_and_tampered_index(rsi_home):
    bundle = begin_bundle("cand-verify", clock=_fixed_clock)
    for name in ("holdout.json", "shadow.json", "reviews.json"):
        bundle.add(name, _payload(name))
    bundle.finalize()
    bundle_dir = bundle.bundle_dir
    index_path = bundle_dir / "bundle_index.json"
    saved_index = index_path.read_text(encoding="utf-8")
    ok, reason = verify_bundle("cand-verify")
    assert ok is True and "3 file(s)" in reason

    # (a) tampered bytes -> sha256 mismatch carrying BOTH real digests
    holdout_path = bundle_dir / "holdout.json"
    original = holdout_path.read_bytes()
    holdout_path.write_bytes(original + b" ")
    ok, reason = verify_bundle("cand-verify")
    assert ok is False
    assert "sha256 mismatch" in reason and "holdout.json" in reason
    assert hashlib.sha256(original + b" ").hexdigest() in reason
    assert json.loads(saved_index)["files"]["holdout.json"]["sha256"] in reason
    holdout_path.write_bytes(original)
    ok, _ = verify_bundle("cand-verify")
    assert ok is True

    # (b) deleted file -> honest missing reason, never a pass
    shadow_path = bundle_dir / "shadow.json"
    shadow_blob = shadow_path.read_bytes()
    shadow_path.unlink()
    ok, reason = verify_bundle("cand-verify")
    assert ok is False and "missing or unreadable" in reason and "shadow.json" in reason
    shadow_path.write_bytes(shadow_blob)
    ok, _ = verify_bundle("cand-verify")
    assert ok is True

    # (c) stray file -> provenance incomplete
    stray = bundle_dir / "extra.json"
    stray.write_text("{}", encoding="utf-8")
    ok, reason = verify_bundle("cand-verify")
    assert ok is False and "unexpected file" in reason and "extra.json" in reason
    stray.unlink()

    # (d) corrupt index -> real parse error
    index_path.write_text("{not json", encoding="utf-8")
    ok, reason = verify_bundle("cand-verify")
    assert ok is False and "corrupt bundle index" in reason

    # (e) tampered index naming a path outside the nine -> fail-closed refusal
    index = json.loads(saved_index)
    index["files"]["../../evil.json"] = {"evidence_kind": "measured", "kind_source": "payload", "added_at": FIXED_CLOCK, "sha256": "0" * 64, "bytes": 0}
    index_path.write_text(json.dumps(index), encoding="utf-8")
    ok, reason = verify_bundle("cand-verify")
    assert ok is False and "not one of the plan's nine" in reason

    # (f) never-finalized candidate -> honest missing directory
    index_path.write_text(saved_index, encoding="utf-8")
    ok, reason = verify_bundle("cand-never-begun")
    assert ok is False and "no bundle directory" in reason
    ok, _ = verify_bundle("cand-verify")
    assert ok is True


def test_payloads_round_trip_verbatim_with_atomic_writes(rsi_home):
    bundle = begin_bundle("cand-roundtrip", clock=_fixed_clock)
    promotion_decision = {
        "promoted": False,
        "risk": "R3",
        "gates": [
            {"gate": "kill_switch", "ok": True, "reason": "no kill switch engaged"},
            {"gate": "holdout", "ok": False, "reason": "holdout unverified — cannot gate on unverified evidence"},
        ],
        "human_approved": False,
        "unknown_future_field": {"kept": "verbatim"},
    }
    reviews = [
        {
            "reviewer": "hyperplan",
            "status": "approved",
            "findings": [],
            "required_changes": [],
            "confidence": 0.42,
            "confidence_kind": "heuristic",
        }
    ]
    bundle.add("promotion_decision.json", promotion_decision)
    bundle.add("reviews.json", reviews)
    bundle.add("holdout.json", holdout_not_run("holdout not run: candidate_view was not provided"))
    bundle.finalize()

    bundle_dir = rsi_home / "rsi" / "bundles" / "cand-roundtrip"
    # byte-exact JSON round trip: every field — including unknown ones — verbatim
    stored_decision = json.loads((bundle_dir / "promotion_decision.json").read_text(encoding="utf-8"))
    assert stored_decision == promotion_decision
    assert json.loads((bundle_dir / "reviews.json").read_text(encoding="utf-8")) == reviews
    # no staging files left behind (atomic unique tmp + os.replace)
    assert not list(bundle_dir.glob("*.tmp"))
    # promotion_decision.json reconstructs WHY from the verbatim gate outcomes
    failed_gates = [gate for gate in stored_decision["gates"] if not gate["ok"]]
    assert failed_gates == [{"gate": "holdout", "ok": False, "reason": "holdout unverified — cannot gate on unverified evidence"}]
    ok, _ = verify_bundle("cand-roundtrip")
    assert ok is True


def test_begin_bundle_rejects_unsafe_ids_and_existing_evidence(rsi_home):
    for bad in ("", "../escape", "a/b", ".hidden", "x" * 129, None, 42):
        with pytest.raises(ValueError, match="unsafe candidate_id"):
            begin_bundle(bad, clock=_fixed_clock)
    # an existing populated bundle directory is immutable evidence
    occupied = rsi_home / "rsi" / "bundles" / "cand-occupied"
    occupied.mkdir(parents=True)
    (occupied / "holdout.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="already contains evidence"):
        begin_bundle("cand-occupied", clock=_fixed_clock)
    assert (occupied / "holdout.json").read_text(encoding="utf-8") == "{}"  # never overwritten
    # an empty pre-created directory is fine to begin into
    empty = rsi_home / "rsi" / "bundles" / "cand-empty"
    empty.mkdir(parents=True)
    bundle = begin_bundle("cand-empty", clock=_fixed_clock)
    assert bundle.bundle_dir == empty


def test_empty_bundle_finalizes_honestly_without_inventing_evidence(rsi_home):
    bundle = begin_bundle("cand-empty-bundle", clock=_fixed_clock)
    report = bundle.finalize()
    assert report["files"] == {}  # nothing recorded, nothing claimed
    index = json.loads((bundle.bundle_dir / "bundle_index.json").read_text(encoding="utf-8"))
    assert index["files"] == {}
    # the ledger event discloses the empty bundle once, honestly
    lines = [line for line in (rsi_home / "rsi" / "bundles" / "bundles_ledger.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["files"] == {}
    ok, reason = verify_bundle("cand-empty-bundle")
    assert ok is True and "0 file(s)" in reason
    # no evidence at all can never satisfy a pass condition
    for name in BUNDLE_FILES:
        ok, reason = bundle.meets_evidence_standard(name)
        assert ok is False and "missing" in reason
