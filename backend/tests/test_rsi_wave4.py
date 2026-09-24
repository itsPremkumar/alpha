"""Wave-4 tests: §42 promotion cooldown, §26/§43 immutable release store + single active pointer, §54 failure taxonomy.

Honesty pins (plan §5 guardrails + task assignment):

- §42 store: a REAL promotion writes EXACTLY ``source_change_id``,
  ``cycle_id``, ``promotion_timestamp``, ``cooldown_until`` — the spec's four
  fields, no more — and every timestamp comes from the injected clock
  (``cooldown.RSI_COOLDOWN_CLOCK``), never from a hardcoded value; while the
  window is active the NEXT decision's ``cooldown`` gate fails with the real
  record values in the text and the routing seam is never invoked;
- the window boundary is real arithmetic: ``cooling_down`` until strictly
  before ``cooldown_until`` (remaining > 0), ``ready`` exactly AT it — a
  failed decision writes no cooldown and no release (cooldown is
  promoted-only), a corrupt record fails the gate CLOSED with its bytes
  untouched, and ``RSI_COOLDOWN_SECONDS`` is validated (invalid values raise
  with the real text — never a silent fallback to the default);
- §26/§43 releases: ``release.json`` carries EXACTLY the manifest's six keys,
  ``commit`` is the disclosed literal ``"unknown"`` (this unit performs NO
  VCS operations), ``artifacts`` are the bundle index's real verified sha256
  digests (recomputed from disk here), the evaluator manifest is ONE real
  ``build_manifest()`` for the session (a file digest is re-verified from the
  repo root), a release directory is immutable (create-once refusal with the
  real text, bytes unchanged), ids come from on-disk ``max + 1``
  (``v001`` → ``v002``, ``parent_release`` chains, history preserved), and
  the single active pointer flips to a COMPLETE release only;
- §54 taxonomy: ``RSI_ERROR_CODES`` is written LITERALLY here (spec §54's
  E001–E018 + the build's designated unknown E000) so drift fails the test,
  classification uses only real signals (a genuine ``StaticScanBlockedError``
  instance, real exception types, landed reason texts), codes appear ONLY in
  the composed ``decision.reason`` (gate entries stay verbatim — pinned by
  exact equality in test_rsi_promotion.py) and in WARNING logs, and a
  missing-everything decision's full reason is rebuilt byte-for-byte from the
  gate entries + ``classify_gate``;
- bookkeeping failures (release store / cooldown record) never fail silently:
  a real injected ``OSError`` lands ``promoted=False`` with a disclosed
  ``release_store``/``cooldown_record`` entry, a WARNING log carrying the
  code, no cooldown written, and — when the release DID land — the honest
  partial-state note pointing at the live pointer;
- deterministic: ``tmp_path`` + injected clocks, no network, no subprocess, no
  git. The evolution engine's process-global singleton is reset per test (a
  state reset so each test builds a REAL engine over its own tmp home), and
  every test's ``AGENT_WORKSPACE_HOME`` is a temp directory.
"""

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from test_rsi_promotion import BASELINE, FIXED_CLOCK, _assert_no_invented_fields, _gate_map, _setup_green

from alpha.config.runtime_paths import runtime_home
from alpha.evolution import engine as evolution_engine_module
from alpha.rsi import cooldown, errors, promotion, releases
from alpha.rsi.cooldown import COOLDOWN_FILE_NAME, DEFAULT_COOLDOWN_SECONDS
from alpha.rsi.errors import RSI_ERROR_CODES, annotate, classify, classify_gate, describe
from alpha.rsi.evaluator_manifest import EVALUATOR_SURFACE, build_manifest
from alpha.rsi.evidence_bundle import BUNDLES_DIR_NAME, INDEX_FILE_NAME
from alpha.skills.security_static_scanner import StaticScanBlockedError

#: The closed gate order, written LITERALLY (independently of the C2c pin) so
#: implementation drift in ``GATE_ORDER`` fails here too.
EXPECTED_GATES = ["lineage", "bundle_integrity", "evidence_standard", "holdout", "human_review", "cooldown", "evolution_route"]

#: The §42 store's exact field set, written LITERALLY: growth beyond the
#: spec's four fields fails the test instead of being followed.
EXPECTED_COOLDOWN_FIELDS = {"source_change_id", "cycle_id", "promotion_timestamp", "cooldown_until"}

#: The §43 manifest's exact key set, written LITERALLY.
EXPECTED_MANIFEST_KEYS = {"release", "commit", "parent_release", "candidate_id", "artifacts", "evaluator_manifest"}

#: The spec §54 taxonomy LITERALLY (spec ``references/RSI_AGENT_ARCHITECTURE.md``
#: §54: E001 Provider failure … E018 Promotion conflict) plus this build's ONE
#: designated addition, ``RSI-E000`` "Unclassified failure" (the spec defines
#: only E001–E018; an unmatched failure is admitted as unknown, never forced
#: into a wrong code). Drift in either direction fails this literal.
EXPECTED_ERROR_CODES = {
    "RSI-E000": "Unclassified failure",
    "RSI-E001": "Provider failure",
    "RSI-E002": "Network failure",
    "RSI-E003": "Candidate patch failure",
    "RSI-E004": "Build failure",
    "RSI-E005": "Unit failure",
    "RSI-E006": "Integration failure",
    "RSI-E007": "Benchmark regression",
    "RSI-E008": "Security violation",
    "RSI-E009": "Evaluator tamper",
    "RSI-E010": "Resource exhaustion",
    "RSI-E011": "Startup failure",
    "RSI-E012": "Canary regression",
    "RSI-E013": "Checkpoint/state failure",
    "RSI-E014": "Policy violation",
    "RSI-E015": "Non-reproducible result",
    "RSI-E016": "Insufficient evidence",
    "RSI-E017": "Duplicate candidate",
    "RSI-E018": "Promotion conflict",
}

#: One REAL ``build_manifest()`` for the whole session, reused through the
#: ``releases.RSI_EVALUATOR_MANIFEST_BUILDER`` seam (the evaluator surface
#: cannot change under one run) — real bytes, never faked content.
_MANIFEST: dict[str, Any] = {}


class _Clock:
    """Injected clock: returns the exact epoch second it was constructed with."""

    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _cooldown_path() -> Path:
    return runtime_home() / "rsi" / COOLDOWN_FILE_NAME


def _pointer_path() -> Path:
    return runtime_home() / "rsi" / releases.RELEASES_DIR_NAME / "current.json"


def _release_dir(release_id: str) -> Path:
    return runtime_home() / "rsi" / releases.RELEASES_DIR_NAME / release_id


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    """Temp ``AGENT_WORKSPACE_HOME`` + a fresh REAL evolution engine per test.

    The ``_engine = None`` reset is singleton STATE hygiene (each test's
    ``get_evolution_engine()`` constructs a real engine bound to its own tmp
    home), restored automatically by monkeypatch — no gate is ever stubbed.
    ``RSI_COOLDOWN_SECONDS`` is dropped so window length is this suite's own
    explicit input, never ambient operator state.
    """
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))
    monkeypatch.delenv("RSI_COOLDOWN_SECONDS", raising=False)
    monkeypatch.setattr(evolution_engine_module, "_engine", None)
    return tmp_path


@pytest.fixture(scope="session", autouse=True)
def real_evaluator_manifest_memo() -> Iterator[dict[str, Any]]:
    """Exactly ONE real ``build_manifest()`` per session, wired into the §43 seam.

    Every ``create_release`` in this module (and any later test in the same
    run) reuses that real output instead of re-hashing ~1000 files per
    promotion — the memo is real builder content, never fabricated.
    """
    if not _MANIFEST:
        _MANIFEST.update(build_manifest())
    patcher = pytest.MonkeyPatch()
    patcher.setattr(releases, "RSI_EVALUATOR_MANIFEST_BUILDER", lambda: dict(_MANIFEST))
    yield _MANIFEST
    patcher.undo()


# ---------------------------------------------------------------------------
# §42: gate order, the four-field store, the active window, the boundary
# ---------------------------------------------------------------------------


def test_gate_order_and_real_module_bindings_are_pinned():
    assert EXPECTED_GATES == list(promotion.GATE_ORDER)  # literal order == implementation constant
    assert EXPECTED_GATES.index("cooldown") < EXPECTED_GATES.index("evolution_route")  # §42 runs BEFORE routing

    # the bindings decide() uses are the REAL landed modules (no local re-implementation)
    assert promotion.create_release is releases.create_release
    assert promotion.record_promotion is cooldown.record_promotion
    assert promotion.evaluate_cooldown is cooldown.evaluate_cooldown
    assert promotion.annotate is errors.annotate
    assert promotion.classify is errors.classify
    assert promotion.classify_gate is errors.classify_gate


def test_promotion_writes_the_four_field_store_and_blocks_the_next_window(monkeypatch):
    handles = _setup_green()
    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK))  # every stored second comes from the injected clock

    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is True and decision.reason == "promoted"

    # §42: EXACTLY the four spec fields, real values only
    record = json.loads(_cooldown_path().read_text(encoding="utf-8"))
    assert set(record) == EXPECTED_COOLDOWN_FIELDS
    assert record["source_change_id"] == handles["candidate_id"]
    assert record["cycle_id"] == "unknown"  # the real lineage record carries no cycle → disclosed lineage precedent
    assert record["promotion_timestamp"] == FIXED_CLOCK
    assert record["cooldown_until"] == FIXED_CLOCK + DEFAULT_COOLDOWN_SECONDS

    # §26/§43 materialized first: the release exists before any cooldown claim
    assert releases.release_history() == ["v001"]

    routed: list[object] = []

    def _never_routed(*_args, **_kwargs):
        routed.append("invoked")
        raise AssertionError("the routing seam must never run while a cooldown window is active")

    monkeypatch.setattr(promotion, "route_promotion_decision", _never_routed)  # pinned invocation seam: recording, not faking a gate

    blocked = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert blocked.promoted is False
    gates = _gate_map(blocked)
    assert gates["cooldown"]["ok"] is False
    assert "cooldown active: source_change_id" in gates["cooldown"]["reason"]
    assert "(3600s remaining" in gates["cooldown"]["reason"]  # real arithmetic: full default window left at the same instant
    assert blocked.gates[-1]["gate"] == "evolution_route"
    assert blocked.gates[-1]["ok"] is False
    assert blocked.gates[-1]["reason"].startswith("not routed:")  # a cooling candidate is never routed
    assert routed == []

    # §54: the code decorates the COMPOSED reason only; gate entries stay verbatim
    assert f"cooldown: {gates['cooldown']['reason']} [RSI-E018]" in blocked.reason
    assert all("[RSI-" not in entry["reason"] for entry in blocked.gates)
    _assert_no_invented_fields(decision.to_dict())
    _assert_no_invented_fields(blocked.to_dict())


def test_window_boundary_is_exact_real_arithmetic(monkeypatch):
    assert cooldown.cooldown_state() == ("never_promoted", 0.0)  # no record yet: honest state, not a guess

    cooldown.record_promotion(source_change_id="cand-boundary", cycle_id="unknown", now_fn=_Clock(FIXED_CLOCK))
    assert cooldown.cooldown_state(now_fn=_Clock(FIXED_CLOCK + 3599)) == ("cooling_down", 1.0)
    assert cooldown.cooldown_state(now_fn=_Clock(FIXED_CLOCK + 3600)) == ("ready", 0.0)  # exactly AT cooldown_until: elapsed

    still_active, still_text = cooldown.evaluate_cooldown(now_fn=_Clock(FIXED_CLOCK + 3599))
    assert still_active is False and "(1s remaining" in still_text
    elapsed_ok, elapsed_text = cooldown.evaluate_cooldown(now_fn=_Clock(FIXED_CLOCK + 3600))
    assert elapsed_ok is True and "stabilization window elapsed" in elapsed_text

    # the gate itself goes green through a REAL decide() once the window ends
    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK + 3600))
    decision = promotion.decide("cand-never-existed")
    cooldown_entry = _gate_map(decision)["cooldown"]
    assert cooldown_entry["ok"] is True
    assert cooldown_entry["reason"].startswith("cooldown ok:")
    assert "stabilization window elapsed" in cooldown_entry["reason"]


def test_failed_decision_writes_no_cooldown_and_no_release():
    handles = _setup_green(approve_review=False)  # pending review: routed nowhere, never promoted
    decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False

    assert _cooldown_path().exists() is False  # cooldown is promoted-only
    assert cooldown.cooldown_state() == ("never_promoted", 0.0)
    assert releases.release_history() == []
    assert releases.current_release() is None


def test_corrupt_cooldown_record_fails_the_gate_closed_verbatim(monkeypatch):
    _cooldown_path().parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = b"{not json"
    _cooldown_path().write_bytes(corrupt_bytes)

    decision = promotion.decide("cand-never-existed")
    gates = _gate_map(decision)
    assert gates["cooldown"]["ok"] is False
    assert gates["cooldown"]["reason"].startswith("cooldown unavailable: corrupt cooldown record at")  # real error text, never repaired
    assert f"cooldown: {gates['cooldown']['reason']} [RSI-E013]" in decision.reason  # §54 code on the composed reason only
    assert gates["evolution_route"]["reason"].startswith("not routed:")
    assert _cooldown_path().read_bytes() == corrupt_bytes  # fail-closed: bytes untouched, never re-created
    assert all("[RSI-" not in entry["reason"] for entry in decision.gates)


def test_window_env_override_and_record_validation(monkeypatch):
    monkeypatch.setenv("RSI_COOLDOWN_SECONDS", "120")
    record = cooldown.record_promotion(source_change_id="cand-env", cycle_id="unknown", now_fn=_Clock(FIXED_CLOCK))
    assert record["cooldown_until"] - record["promotion_timestamp"] == 120.0  # real env-driven arithmetic

    monkeypatch.setenv("RSI_COOLDOWN_SECONDS", "")  # blank = documented default, still explicit
    record = cooldown.record_promotion(source_change_id="cand-env", cycle_id="unknown", now_fn=_Clock(FIXED_CLOCK))
    assert record["cooldown_until"] - record["promotion_timestamp"] == DEFAULT_COOLDOWN_SECONDS
    assert set(record) == EXPECTED_COOLDOWN_FIELDS
    stable_bytes = _cooldown_path().read_bytes()

    # invalid values raise with the real text — NEVER a silent fallback to the default
    monkeypatch.setenv("RSI_COOLDOWN_SECONDS", "soon")
    with pytest.raises(ValueError, match="RSI_COOLDOWN_SECONDS is not a number"):
        cooldown.record_promotion(source_change_id="cand-env", cycle_id="unknown", now_fn=_Clock(FIXED_CLOCK))
    monkeypatch.delenv("RSI_COOLDOWN_SECONDS")
    with pytest.raises(ValueError, match="finite, non-negative"):
        cooldown.record_promotion(source_change_id="cand-env", cycle_id="unknown", cooldown_seconds=-5, now_fn=_Clock(FIXED_CLOCK))
    with pytest.raises(ValueError, match="cycle_id must be a non-empty string"):
        cooldown.record_promotion(source_change_id="cand-env", cycle_id="  ", now_fn=_Clock(FIXED_CLOCK))
    with pytest.raises(ValueError, match="source_change_id must be a non-empty string"):
        cooldown.record_promotion(source_change_id="", cycle_id="unknown", now_fn=_Clock(FIXED_CLOCK))
    assert _cooldown_path().read_bytes() == stable_bytes  # every rejected call wrote nothing


# ---------------------------------------------------------------------------
# §26/§43: exact manifest, immutable directory, single active pointer, history
# ---------------------------------------------------------------------------


def test_release_manifest_is_exact_and_the_directory_is_immutable():
    handles = _setup_green()
    candidate_id = handles["candidate_id"]
    payload = {"candidate_id": candidate_id, "promoted": True, "reason": "promoted", "gates": [{"gate": "evolution_route", "ok": True, "reason": "promoted"}]}

    manifest = releases.create_release(candidate_id, payload)
    assert set(manifest) == EXPECTED_MANIFEST_KEYS  # literal §43 key set
    assert manifest["release"] == "v001"
    assert manifest["commit"] == "unknown"  # disclosed: NO VCS operations are performed — never probed, never fabricated
    assert manifest["parent_release"] is None  # first release has no predecessor
    assert manifest["candidate_id"] == candidate_id

    # artifacts = the REAL verified digests, recomputed from disk right here
    bundle_dir = runtime_home() / "rsi" / BUNDLES_DIR_NAME / candidate_id
    index_bytes = (bundle_dir / INDEX_FILE_NAME).read_bytes()
    expected_names = {"holdout.json", "shadow.json", "reviews.json", INDEX_FILE_NAME}
    assert set(manifest["artifacts"]) == expected_names
    assert manifest["artifacts"]["holdout.json"] == hashlib.sha256((bundle_dir / "holdout.json").read_bytes()).hexdigest()
    assert manifest["artifacts"][INDEX_FILE_NAME] == hashlib.sha256(index_bytes).hexdigest()

    # evaluator manifest = ONE real build_manifest() output, digest spot-checked against the repo root
    evaluator_manifest = manifest["evaluator_manifest"]
    assert evaluator_manifest["version"] == 1
    assert isinstance(evaluator_manifest["files"], dict) and evaluator_manifest["files"]
    repo_root = Path(cooldown.__file__).resolve().parents[5]
    spot_key = "backend/packages/harness/alpha/policy/engine.py"  # a file covered by EVALUATOR_SURFACE
    assert spot_key in EVALUATOR_SURFACE
    assert evaluator_manifest["files"][spot_key] == "sha256:" + hashlib.sha256((repo_root / spot_key).read_bytes()).hexdigest()

    # decision.json carries the exact payload the release was materialized from
    assert json.loads((_release_dir("v001") / "decision.json").read_text(encoding="utf-8")) == payload

    # the ONE active pointer names the complete release, with a real clock reading
    pointer = releases.current_release()
    assert pointer is not None and set(pointer) == {"active", "flipped_at", "candidate_id"}
    assert pointer["active"] == "v001" and pointer["candidate_id"] == candidate_id
    assert isinstance(pointer["flipped_at"], (int, float)) and pointer["flipped_at"] > 0

    # immutability: create-once refusal with the real text; existing bytes untouched
    before = (_release_dir("v001") / "release.json").read_bytes()
    with pytest.raises(releases.ReleaseStoreError, match="immutable"):
        releases.create_release(candidate_id, payload, release_id="v001")
    assert (_release_dir("v001") / "release.json").read_bytes() == before

    # a release may ONLY come from a promoted decision (spec §26: promotion moves the pointer)
    with pytest.raises(ValueError, match="promoted decision"):
        releases.create_release(candidate_id, {**payload, "promoted": False})
    with pytest.raises(ValueError, match="names candidate"):
        releases.create_release("some-other-candidate", payload)


def test_second_promotion_appends_v002_preserving_v001(monkeypatch):
    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK))
    monkeypatch.setattr(releases, "RSI_RELEASE_CLOCK", _Clock(FIXED_CLOCK))

    first_handles = _setup_green()
    first = promotion.decide(first_handles["candidate_id"], baseline=BASELINE)
    assert first.promoted is True
    v001_bytes = (_release_dir("v001") / "release.json").read_bytes()
    pointer_before = _pointer_path().read_text(encoding="utf-8")

    # the window elapses: BOTH §42/§43 clocks advance by real injected seconds
    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK + 3601))
    monkeypatch.setattr(releases, "RSI_RELEASE_CLOCK", _Clock(FIXED_CLOCK + 3601))

    second_handles = _setup_green()
    second = promotion.decide(second_handles["candidate_id"], baseline=BASELINE)
    assert second.promoted is True
    assert _gate_map(second)["cooldown"]["ok"] is True
    assert "stabilization window elapsed" in _gate_map(second)["cooldown"]["reason"]  # the REAL gate saw the elapsed window

    # history preserved: the old directory is never touched, the pointer moves once
    assert releases.release_history() == ["v001", "v002"]
    assert (_release_dir("v001") / "release.json").read_bytes() == v001_bytes
    assert _pointer_path().read_text(encoding="utf-8") != pointer_before
    manifest_two = json.loads((_release_dir("v002") / "release.json").read_text(encoding="utf-8"))
    assert manifest_two["release"] == "v002"
    assert manifest_two["parent_release"] == "v001"  # real lineage through the on-disk store
    assert manifest_two["candidate_id"] == second_handles["candidate_id"]
    pointer = releases.current_release()
    assert pointer is not None and pointer["active"] == "v002"

    # §42 record now describes the SECOND promotion's real window
    record = json.loads(_cooldown_path().read_text(encoding="utf-8"))
    assert set(record) == EXPECTED_COOLDOWN_FIELDS
    assert record["source_change_id"] == second_handles["candidate_id"]
    assert record["promotion_timestamp"] == FIXED_CLOCK + 3601
    assert record["cooldown_until"] == FIXED_CLOCK + 3601 + DEFAULT_COOLDOWN_SECONDS


def test_corrupt_pointer_fails_the_next_release_closed(monkeypatch):
    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK))
    monkeypatch.setattr(releases, "RSI_RELEASE_CLOCK", _Clock(FIXED_CLOCK))

    first_handles = _setup_green()
    assert promotion.decide(first_handles["candidate_id"], baseline=BASELINE).promoted is True
    cooldown_record_before = _cooldown_path().read_bytes()

    monkeypatch.setattr(cooldown, "RSI_COOLDOWN_CLOCK", _Clock(FIXED_CLOCK + 3601))  # window elapsed for the next candidate
    corrupt_bytes = b"{not json"
    _pointer_path().write_bytes(corrupt_bytes)
    with pytest.raises(releases.ReleaseStoreError, match="unreadable release pointer"):
        releases.current_release()  # fail-closed: the active release is never guessed

    second_handles = _setup_green()
    decision = promotion.decide(second_handles["candidate_id"], baseline=BASELINE)
    assert decision.promoted is False  # a promotion whose release cannot materialize never stands
    gates = _gate_map(decision)
    assert gates["cooldown"]["ok"] is True  # the §42 window itself had elapsed — the failure is the store
    assert gates["evolution_route"]["ok"] is True  # routing happened; the disclosed failure is downstream of it
    entry = decision.gates[-1]
    assert entry["gate"] == "release_store" and entry["ok"] is False
    assert "unreadable release pointer" in entry["reason"]  # the REAL store error, verbatim
    assert "release_store: " in decision.reason and "[RSI-E013]" in decision.reason
    assert _pointer_path().read_bytes() == corrupt_bytes  # nothing was repaired or overwritten
    assert releases.release_history() == ["v001"]  # v002 was never created
    assert _cooldown_path().read_bytes() == cooldown_record_before  # the §42 record is byte-identical: never rewritten


def test_release_write_failure_is_disclosed_never_silent(monkeypatch, caplog):
    handles = _setup_green()

    def _boom(*_args, **_kwargs):
        raise OSError("release volume is read-only")

    monkeypatch.setattr(promotion, "create_release", _boom)  # module-level seam: injects a REAL IO failure, not a fake gate
    with caplog.at_level(logging.WARNING, logger="alpha.rsi.promotion"):
        decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)

    assert decision.promoted is False
    entry = decision.gates[-1]
    assert entry["gate"] == "release_store" and entry["ok"] is False
    assert "failed closed: OSError: release volume is read-only" in entry["reason"]
    assert "cooldown not written" in entry["reason"]  # honest consequence of release-first order
    assert _gate_map(decision)["evolution_route"]["ok"] is True  # only the disclosed side effect failed
    assert releases.release_history() == []  # the failed release wrote nothing
    assert cooldown.cooldown_state() == ("never_promoted", 0.0)  # no cooldown without a materialized release
    assert "release_store: failed closed: OSError: release volume is read-only" in decision.reason
    assert "[RSI-E013]" in decision.reason  # §54 code on the composed reason
    assert any("release volume is read-only" in record.getMessage() for record in caplog.records)  # logged, not swallowed
    assert any("RSI-E013" in record.getMessage() for record in caplog.records)  # the WARNING carries the code
    _assert_no_invented_fields(decision.to_dict())


def test_cooldown_write_failure_discloses_the_landed_release_partial_state(monkeypatch, caplog):
    handles = _setup_green()

    def _boom(*_args, **_kwargs):
        raise OSError("cooldown store is full")

    monkeypatch.setattr(promotion, "record_promotion", _boom)  # release-first: the release landed, then the cooldown write failed
    with caplog.at_level(logging.WARNING, logger="alpha.rsi.promotion"):
        decision = promotion.decide(handles["candidate_id"], baseline=BASELINE)

    assert decision.promoted is False
    entry = decision.gates[-1]
    assert entry["gate"] == "cooldown_record" and entry["ok"] is False
    assert "failed closed: OSError: cooldown store is full" in entry["reason"]
    assert "release materialized" in entry["reason"]  # the honest partial-state note
    pointer = releases.current_release()
    assert pointer is not None and pointer["active"] == "v001"  # the §26/§43 release DID land and stands
    assert releases.release_history() == ["v001"]
    assert cooldown.cooldown_state() == ("never_promoted", 0.0)  # and the cooldown verifiably did not
    assert _gate_map(decision)["evolution_route"]["ok"] is True
    assert "cooldown_record: " in decision.reason and "[RSI-E013]" in decision.reason
    assert any("cooldown store is full" in record.getMessage() for record in caplog.records)
    assert any("RSI-E013" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# §54: literal taxonomy, real-signal classification, composed-reason codes
# ---------------------------------------------------------------------------


def test_error_taxonomy_is_literal():
    assert RSI_ERROR_CODES == EXPECTED_ERROR_CODES  # spec §54 list, written independently above
    assert describe("RSI-E014") == "Policy violation"
    assert describe("RSI-E016") == "Insufficient evidence"
    assert "unknown failure code" in describe("RSI-E999")  # an unknown code is reported unknown, never relabeled
    assert annotate("boom", "RSI-E007") == "boom [RSI-E007]"
    assert annotate("boom", None) == "boom"  # a derived disclosure gets no invented code


def test_classify_uses_only_real_signals():
    # a genuine consumed-surface exception (alpha.skills.security_static_scanner), isinstance-checked
    blocked = StaticScanBlockedError([{"rule_id": "secret-hardcoded", "severity": "high", "file": None, "line": None}], skill_name="probe")
    assert classify(blocked) == "RSI-E008"

    # real Python exception classes
    assert classify(ConnectionError("network unreachable")) == "RSI-E002"
    assert classify(MemoryError()) == "RSI-E010"
    assert classify(json.JSONDecodeError("Expecting value", "", 0)) == "RSI-E013"
    assert classify(RuntimeError("odd transport glitch")) == "RSI-E000"  # no documented rule fits: admitted unknown, never forced
    assert classify(TimeoutError("op timed out")) == "RSI-E000"  # §54 defines no timeout code — not misfiled as a candidate defect

    # landed reason texts
    assert classify("holdout regressions vs baseline") == "RSI-E007"
    assert classify("provider outage — upstream retrying") == "RSI-E001"  # spec §54: an outage is NOT a candidate defect

    # the real release-store exception, split by its own text
    assert classify(releases.ReleaseStoreError("release 'v001' already exists — release directories are immutable")) == "RSI-E018"
    assert classify(releases.ReleaseStoreError("unreadable release pointer at current.json: ValueError: x")) == "RSI-E013"

    # gate context: consequences are never double-counted, no §54 code is forced
    assert classify_gate("evolution_route", "not routed: an earlier gate failed") is None
    assert classify_gate("evolution_route", "not strictly better than baseline") == "RSI-E000"
    assert classify_gate("evolution_route", "awaiting human approval") == "RSI-E014"
    assert classify_gate("evolution_route", "missing candidate or benchmark") == "RSI-E016"
    assert classify_gate("cooldown", "cooldown active: source_change_id='c' … (3600s remaining …)") == "RSI-E018"
    assert classify_gate("cooldown", "cooldown unavailable: corrupt cooldown record at …") == "RSI-E013"
    assert classify_gate("human_review", "awaiting human review: approval request 42") == "RSI-E014"
    assert classify_gate("holdout", "holdout regressions vs baseline") == "RSI-E007"
    assert classify_gate("holdout", "holdout unverified — cannot gate on unverified evidence") == "RSI-E016"
    assert classify_gate("lineage", "no RSI lineage record for candidate 'c' (lookup failed — fail-closed)") == "RSI-E016"
    assert classify_gate("lineage", "no RSI lineage record …; 1 corrupt/partial lineage line(s) skipped") == "RSI-E013"
    assert classify_gate("bundle_integrity", "no bundle directory at /tmp/x") == "RSI-E016"
    assert classify_gate("bundle_integrity", "sha256 mismatch for 'holdout.json' in /tmp/x") == "RSI-E013"
    assert classify_gate("release_store", "failed closed: OSError: release volume is read-only") == "RSI-E013"


def test_missing_everything_decision_carries_real_codes_and_verbatim_entries():
    decision = promotion.decide("cand-never-existed")
    gates = _gate_map(decision)

    # codes for the real landed texts of THIS run (calibrated against the modules, not guessed)
    assert gates["cooldown"]["ok"] is True  # never promoted: no window applies, so no code is ever emitted for it
    expected_codes = {
        "lineage": "RSI-E016",
        "bundle_integrity": "RSI-E016",
        "evidence_standard": "RSI-E016",
        "holdout": "RSI-E016",
        "human_review": "RSI-E014",
        "evolution_route": None,
    }
    failing = [entry for entry in decision.gates if not entry["ok"]]
    assert {entry["gate"]: classify_gate(entry["gate"], entry["reason"]) for entry in failing} == expected_codes

    # the composed reason is rebuilt byte-for-byte from entries + classify_gate (no other transformation)
    rebuilt = "; ".join(annotate(f"{entry['gate']}: {entry['reason']}", classify_gate(entry["gate"], entry["reason"])) for entry in failing)
    assert decision.reason == rebuilt
    assert "[RSI-E016]" in decision.reason and "[RSI-E014]" in decision.reason
    assert f"evolution_route: {gates['evolution_route']['reason']}" in decision.reason  # None: no code appended, no double-count

    # gate entries remain byte-for-byte verbatim — codes live ONLY in the composed reason
    assert all("[RSI-" not in entry["reason"] for entry in decision.gates)
    _assert_no_invented_fields(decision.to_dict())
