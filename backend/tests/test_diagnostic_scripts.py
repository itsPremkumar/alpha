"""The four standalone diagnostic scripts stay runnable, honest, and portable.

Each of these modules exists as a *script*: nothing in production imports it,
so ``test_no_orphan_modules.py`` carries an ``ALLOWED_ORPHANS`` entry for it.
A waiver that lets dead scratch code linger is worse than a delete, so this
suite is what keeps the waiver honest. Every probe must:

1. import without side effects (the originals printed and mutated ``sys.path``
   at import time, against a hard-coded ``C:/Users/.../alpha`` checkout that
   does not exist on any other machine);
2. run against *this* checkout, with paths derived from ``__file__``;
3. answer its question truthfully — in particular the replay probes pin two
   real fold invariants of ``alpha.orchestrator.replay``: a connectivity park
   rebuilds as ``WAITING_CONNECTIVITY`` and an external-wait park rebuilds as
   ``WAITING_EVENT``, never as a runnable ``RUNNING`` run.

The app-side script is loaded by path (``spec_from_file_location``) rather
than by dotted import so exercising a read-only AST audit does not drag in
``app.gateway.routers.__init__``, which imports the whole Gateway.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

BACKEND = Path(__file__).resolve().parents[1]

ORACLE_PROBE = "alpha.orchestrator._oracle_probe"
REPLAY_FOLD_PROBE = "alpha.workflow._probe_replay_fold"
EXTERNAL_WAIT_PROOF = "alpha.workflow._proof_external_wait"
ROUTE_AUDIT_PATH = BACKEND / "app" / "gateway" / "routers" / "route_audit_builder.py"

_PROBE_PATHS = {
    ORACLE_PROBE: BACKEND / "packages" / "harness" / "alpha" / "orchestrator" / "_oracle_probe.py",
    REPLAY_FOLD_PROBE: BACKEND / "packages" / "harness" / "alpha" / "workflow" / "_probe_replay_fold.py",
    EXTERNAL_WAIT_PROOF: BACKEND / "packages" / "harness" / "alpha" / "workflow" / "_proof_external_wait.py",
    "app.gateway.routers.route_audit_builder": ROUTE_AUDIT_PATH,
}

# A router file exercising every declaration shape the audit must count once:
# a module-level handler on a differently-named router object, a plain
# module-level handler, and two class-method routes (which the original
# two-walk implementation counted twice, the class ones once as "<module>").
_ROUTER_FIXTURE = """\
from fastapi import APIRouter

router = APIRouter()
api_router = APIRouter()


@api_router.get("/alpha")
async def alpha_handler():
    return {"ok": True}


@router.post("/beta")
async def beta_handler():
    return {"ok": True}


class AdminRouter:
    @router.get("/gamma")
    async def gamma_handler(self):
        return {"ok": True}

    @router.delete("/gamma")
    async def gamma_delete(self):
        return {"ok": True}
"""


def _load_by_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scripts_import_without_side_effects(capsys) -> None:
    """A fresh execution of each module body prints nothing and mutates nothing.

    The originals ran their whole probe (prints plus a ``sys.path.insert`` to a
    different machine's checkout) at import time, so merely importing one
    leaked output and could shadow the harness with foreign sources.
    """
    for name, path in _PROBE_PATHS.items():
        _load_by_path(f"_diag_{name}", path)
    captured = capsys.readouterr()
    assert captured.out == "", f"import printed: {captured.out!r}"
    assert captured.err == "", f"import wrote to stderr: {captured.err!r}"


def test_oracle_probe_agrees_with_itself() -> None:
    """The imported replay module IS this checkout's file and carries the fold."""
    probe = importlib.import_module(ORACLE_PROBE)
    findings = probe.probe()
    assert findings["imported_has_fix"] is True
    assert findings["working_tree_has_fix"] is True
    assert findings["replay_run_has_fix"] is True
    assert findings["same_file"] is True, f"import resolved elsewhere: {findings['imported_file']}"
    assert findings["consistent"] is True
    assert probe.main() == 0


def test_connectivity_park_replays_as_waiting_connectivity(capsys) -> None:
    """replay_run folds ``connectivity_wait_parked`` — a parked run must not
    replay as a runnable ``RUNNING`` run the scheduler would re-dispatch into
    the outage the original run had already measured."""
    probe = importlib.import_module(REPLAY_FOLD_PROBE)
    result = probe.run_probe()
    assert result["status"] == "waiting_connectivity"
    assert result["node_states"][probe.NODE_ID] == "waiting"
    assert result["folded"] is True
    assert result["waiting_reason"] == "network offline: probe unreachable"
    assert probe.main() == 0
    assert "RESULT = PASS" in capsys.readouterr().out


def test_external_wait_proof_passes(capsys) -> None:
    """replay_run folds ``external_wait_parked`` into WAITING_EVENT with the
    node WAITING; exit code 0 is the proof's PASS."""
    proof = importlib.import_module(EXTERNAL_WAIT_PROOF)
    run, definition = proof.build_run()
    assert run.status.value == "running"
    assert proof.main() == 0
    out = capsys.readouterr().out
    assert "REPLAYED_STATUS = waiting_event" in out
    assert "RESULT = PASS" in out
    assert definition.id == "wf"


def test_route_audit_counts_every_route_once(tmp_path, capsys) -> None:
    """Each declared route appears exactly once under its real function name.

    The original counted class-method routes twice (once by name, once as
    ``"<module>"`` from its second walk) and only recognised module-level
    routes whose router object was literally named ``router``.
    """
    audit = _load_by_path("route_audit_builder_under_test", ROUTE_AUDIT_PATH)
    fixture_dir = tmp_path / "routers"
    fixture_dir.mkdir()
    (fixture_dir / "sample.py").write_text(_ROUTER_FIXTURE, encoding="utf-8")

    report = audit.build_route_audit(fixture_dir)
    assert len(report) == 1
    entry = report[0]
    assert entry["file"] == "sample.py"
    assert entry["count"] == 4 == len(entry["routes"])
    assert sorted((r["name"], r["method"]) for r in entry["routes"]) == [
        ("alpha_handler", "get"),
        ("beta_handler", "post"),
        ("gamma_delete", "delete"),
        ("gamma_handler", "get"),
    ]
    assert all(r["decos"] for r in entry["routes"])
    assert all(r["name"] != "<module>" for r in entry["routes"])

    # CLI surface: JSON on stdout, exit 0.
    assert audit.main([str(fixture_dir)]) == 0
    assert json.loads(capsys.readouterr().out) == report

    # An unreadable directory is refused with exit 2, never an empty "audit".
    assert audit.main([str(tmp_path / "does-not-exist")]) == 2


def test_route_audit_inventories_the_real_gateway() -> None:
    """The default directory resolves from ``__file__`` (not the CWD) and the
    audit parses the real routers without importing them."""
    audit = _load_by_path("route_audit_builder_real_dir", ROUTE_AUDIT_PATH)
    report = audit.build_route_audit()
    assert report, "no Gateway router declares a route?"
    assert len(report) >= 20, f"only {len(report)} router files carry routes"
    names = {entry["file"] for entry in report}
    assert {"models.py", "bots.py"} <= names
    for entry in report:
        assert entry["file"].endswith(".py")
        assert entry["count"] == len(entry["routes"]) >= 1
        assert all(r["method"] in audit._HTTP_METHODS for r in entry["routes"])
