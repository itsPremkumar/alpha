"""Hermetic contract tests for ``scripts/check_cold_start_imports.py``.

This is the deterministic half of the cold-start property: it gates *eager*
(module-scope) imports on the startup path, which is the unit that cannot flake
on a shared runner. The wall-clock half is ``check_cold_start_budget.py``, whose
own contract is pinned in ``test_cold_start.py``.

Two rules shape this file.

**Every safety property is demonstrated by making it fail.** A gate whose tests
only ever feed it a clean tree have proven nothing: the interesting question is
whether a *newly added eager import* is caught, and the only way to know is to
add one and assert the non-zero exit. That is what the end-to-end tests do,
against a synthetic mini-tree so the negative demonstration costs milliseconds
and never touches the real checkout.

**Fast by construction.** The real tree is 1700+ files and scanning it costs
real file opens (an AV-scanned host multiplies those), so no test here scans
all of it. The committed budget's *coherence* is enforced by the CI job that
runs the gate against the real tree, and the committed budget's *shape*, plus
the specific lazy conversions the cold-start work established, are asserted
here on the handful of files that carry them.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "check_cold_start_imports.py"
PROBE_PATH = REPO_ROOT / "scripts" / "cold_start_probe.py"
BUDGET_PATH = REPO_ROOT / "backend" / "benchmarks" / "cold_start" / "import_budget.json"
HARNESS = REPO_ROOT / "backend" / "packages" / "harness"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered *before* exec: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]`, which is absent for an unregistered module.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The gate, loaded once at module scope so the dataclasses it defines can be
#: constructed directly by the decision-logic tests below.
gate = _load(GATE_PATH, "alpha_cold_start_import_gate_under_test")


def _collect(gate: Any, tmp_path: Path, source: str, package: str = "alpha.demo") -> Any:
    path = tmp_path / "module.py"
    path.write_text(source, encoding="utf-8")
    return gate.collect_file(path, package, frozenset(gate.DEFAULT_FIRST_PARTY_ROOTS))


def _targets(records: Any) -> set[str]:
    return {item.target for item in (*records.first_party, *records.stdlib, *records.third_party)}


# --------------------------------------------------------------------------
# collector: what counts as eager, and what must stay free
# --------------------------------------------------------------------------


class TestWhatCountsAsEager:
    def test_plain_module_scope_imports_are_eager(self, tmp_path: Path) -> None:
        record = _collect(
            gate,
            tmp_path,
            "import numpy\nfrom alpha.config import get_app_config\nfrom app.gateway import app\n",
        )
        assert _targets(record) == {"numpy", "alpha.config.get_app_config", "app.gateway.app"}
        assert [item.root for item in record.third_party] == ["numpy"]
        assert {item.root for item in record.first_party} == {"alpha", "app"}
        assert record.stdlib == ()

    def test_function_body_imports_are_not_eager(self, tmp_path: Path) -> None:
        """The supported way to keep a dependency off startup."""
        record = _collect(
            gate,
            tmp_path,
            "def build():\n    import torch\n    return torch\n"
            "async def go():\n    import pandas\n"
            "f = lambda: __import__('scipy')\n",
        )
        assert _targets(record) == set()

    def test_class_body_imports_are_eager(self, tmp_path: Path) -> None:
        """A class body runs during the import; only methods do not."""
        record = _collect(
            gate,
            tmp_path,
            "class Client:\n    import numpy\n    def go(self):\n        import pandas\n",
        )
        assert _targets(record) == {"numpy"}

    def test_type_checking_block_is_not_eager(self, tmp_path: Path) -> None:
        """`alpha/config/__init__.py` keeps ~50 siblings behind exactly this."""
        record = _collect(
            gate,
            tmp_path,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import alpha.config.app_config\n"
            "    from alpha.memory import Memory\n"
            "if typing.TYPE_CHECKING:\n"
            "    import numpy\n"
            "if TYPE_CHECKING and sys.version_info >= (3, 12):\n"
            "    import pandas\n",
        )
        assert _targets(record) == {"typing.TYPE_CHECKING"}

    def test_negated_type_checking_block_is_eager(self, tmp_path: Path) -> None:
        """`if not TYPE_CHECKING:` is the runtime path, so it is not free."""
        record = _collect(
            gate,
            tmp_path,
            "from typing import TYPE_CHECKING\nif not TYPE_CHECKING:\n    import numpy\n",
        )
        assert _targets(record) == {"typing.TYPE_CHECKING", "numpy"}

    def test_optional_dependency_at_module_scope_is_eager(self, tmp_path: Path) -> None:
        """A guarded import still runs when the dependency is present."""
        record = _collect(
            gate,
            tmp_path,
            "try:\n    import ujson\nexcept ImportError:\n    import json\n",
        )
        assert _targets(record) == {"ujson", "json"}

    def test_relative_imports_resolve_against_the_files_package(self, tmp_path: Path) -> None:
        """``package`` is the containing package, which is what ``.`` means."""
        record = _collect(
            gate,
            tmp_path,
            "from . import paths\nfrom .app_config import AppConfig\nfrom ..memory import Memory\n",
            package="alpha.config",
        )
        assert _targets(record) == {
            "alpha.config.paths",
            "alpha.config.app_config.AppConfig",
            "alpha.memory.Memory",
        }
        assert {item.root for item in record.first_party} == {"alpha"}

    def test_classification_separates_stdlib_third_party_and_first_party(self, tmp_path: Path) -> None:
        record = _collect(
            gate,
            tmp_path,
            "import json\nfrom __future__ import annotations\nimport numpy\nfrom alpha import x\n",
        )
        assert [item.root for item in record.stdlib] == ["json", "__future__"]
        assert [item.root for item in record.third_party] == ["numpy"]
        assert [item.root for item in record.first_party] == ["alpha"]

    def test_workspace_extension_api_counts_as_first_party(self, tmp_path: Path) -> None:
        """It ships from this repository; treating it as a dependency would hide it."""
        record = _collect(gate, tmp_path, "from agent_workspace_extension_api import principal\n")
        assert [item.root for item in record.first_party] == ["agent_workspace_extension_api"]
        assert record.third_party == ()

    def test_line_numbers_are_reported_for_every_eager_import(self, tmp_path: Path) -> None:
        record = _collect(gate, tmp_path, "# comment\nimport numpy\n\nimport pandas\n")
        assert [(item.line, item.root) for item in record.third_party] == [(2, "numpy"), (4, "pandas")]


class TestPackageNaming:
    @pytest.mark.parametrize(
        ("relative", "expected"),
        [
            ("__init__.py", "alpha"),
            ("config/__init__.py", "alpha.config"),
            ("config/app_config.py", "alpha.config"),
            ("memory.py", "alpha"),
            ("memory/_lazy_exports.py", "alpha.memory"),
            ("memory/affective/config.py", "alpha.memory.affective"),
        ],
    )
    def test_package_of_names_the_containing_package(self, relative: str, expected: str) -> None:
        """The budget is per package, so a file and its directory share a key."""
        root = Path("/checkout/alpha")
        assert gate.package_of(root / relative, root, "alpha") == expected

    def test_module_root_is_stated_not_inferred(self) -> None:
        """`backend/app/gateway` is a directory; `app.gateway` is the package."""
        roots = {entry.path: entry.module_root for entry in gate.DEFAULT_SCAN_ROOTS}
        assert roots["backend/app/gateway"] == "app.gateway"
        assert roots["backend/packages/harness/alpha"] == "alpha"


# --------------------------------------------------------------------------
# decision logic
# --------------------------------------------------------------------------


def _budget(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": gate.BUDGET_SCHEMA,
        "tool": "check_cold_start_imports",
        "scan_roots": [{"path": "backend/packages/harness/alpha", "module_root": "alpha"}],
        "first_party_roots": list(gate.DEFAULT_FIRST_PARTY_ROOTS),
        "third_party_roots": ["numpy"],
        "eager_first_party_by_package": {"alpha.config": 1},
    }
    data.update(overrides)
    return data


def _record(gate: Any, package: str, *, first_party: int = 0, third_party: tuple[str, ...] = ()) -> Any:
    return gate.FileImports(
        path=Path(f"/checkout/{package.replace('.', '/')}.py"),
        package=package,
        first_party=tuple(
            gate.EagerImport(10 + index, f"alpha.other{index}", "alpha") for index in range(first_party)
        ),
        stdlib=(),
        third_party=tuple(gate.EagerImport(1 + index, name, name.split(".")[0]) for index, name in enumerate(third_party)),
    )


class TestDecision:
    def test_a_clean_tree_passes(self) -> None:
        records = [_record(gate, "alpha.config", first_party=1, third_party=("numpy",))]
        result = gate._judge(records, _budget())
        assert result["status"] == "pass"
        assert not result["third_party_root_regressions"]
        assert not result["package_regressions"]

    def test_a_new_eager_third_party_root_fails_and_names_the_site(self) -> None:
        """The regression class: someone adds `import torch` to a startup module."""
        records = [_record(gate, "alpha.config", first_party=1, third_party=("numpy", "torch.nn"))]
        result = gate._judge(records, _budget())
        assert result["status"] == "fail"
        assert list(result["third_party_root_regressions"]) == ["torch"]
        assert result["third_party_root_regressions"]["torch"] == [
            f"{records[0].path.as_posix()}:2 -> torch.nn"
        ]

    def test_an_allowlisted_third_party_root_does_not_fail(self) -> None:
        records = [_record(gate, "alpha.config", first_party=1, third_party=("numpy", "numpy.linalg"))]
        assert gate._judge(records, _budget())["status"] == "pass"

    def test_eager_first_party_over_budget_fails(self) -> None:
        records = [_record(gate, "alpha.config", first_party=3)]
        result = gate._judge(records, _budget())
        assert result["status"] == "fail"
        detail = result["package_regressions"]["alpha.config"]
        assert (detail["budget"], detail["observed"]) == (1, 3)
        assert len(detail["imports"]) == 3, "every eager import is listed so the author can defer one"

    def test_a_package_with_no_budget_entry_is_budgeted_at_zero(self) -> None:
        """A package that has no eager import today and gains one tomorrow regresses."""
        records = [_record(gate, "alpha.brand.new", first_party=1)]
        result = gate._judge(records, _budget())
        assert result["status"] == "fail"
        assert result["package_regressions"]["alpha.brand.new"]["budget"] == 0

    def test_counts_summarise_across_files_of_the_same_package(self) -> None:
        records = [_record(gate, "alpha.config", first_party=1), _record(gate, "alpha.config", first_party=1)]
        result = gate._judge(records, _budget())
        assert result["status"] == "fail"
        assert result["package_regressions"]["alpha.config"]["observed"] == 2

    def test_going_under_budget_is_reported_and_never_applied(self) -> None:
        records = [_record(gate, "alpha.config", first_party=1)]
        result = gate._judge(records, _budget(eager_first_party_by_package={"alpha.config": 4}))
        assert result["status"] == "pass"
        assert result["improvements"] == ["alpha.config"]
        # The verdict carries no budget, so there is nothing for it to rewrite.
        assert "eager_first_party_by_package" not in result

    def test_a_package_that_lost_its_last_eager_import_is_stale_not_failing(self) -> None:
        """Deferring the final eager import must not break the build."""
        result = gate._judge([_record(gate, "alpha.config", first_party=0)], _budget())
        assert result["status"] == "pass"
        assert result["stale_entries"] == ["alpha.config"]
        assert result["improvements"] == []


# --------------------------------------------------------------------------
# fail closed
# --------------------------------------------------------------------------


class TestFailsClosed:
    def test_missing_budget_raises(self, tmp_path: Path) -> None:
        with pytest.raises(gate.GateError, match="not found"):
            gate.load_budget(tmp_path / "nope.json")

    def test_corrupt_budget_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(gate.GateError, match="unreadable"):
            gate.load_budget(path)

    def test_wrong_schema_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text(json.dumps({**_budget(), "schema_version": 99}), encoding="utf-8")
        with pytest.raises(gate.GateError, match="schema_version"):
            gate.load_budget(path)

    @pytest.mark.parametrize("key", ["scan_roots", "third_party_roots", "first_party_roots", "eager_first_party_by_package"])
    def test_missing_section_raises(self, key: str, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        payload = _budget()
        del payload[key]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(gate.GateError, match=key):
            gate.load_budget(path)

    def test_malformed_scan_root_entry_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text(json.dumps(_budget(scan_roots=["backend"])), encoding="utf-8")
        with pytest.raises(gate.GateError, match="scan_roots"):
            gate.load_budget(path)

    def test_a_scan_root_with_no_python_files_is_a_failure(self, tmp_path: Path) -> None:
        """A moved directory that scans nothing must not read as green."""
        (tmp_path / "empty").mkdir()
        records, empty = gate.scan(
            tmp_path, [gate.ScanRoot("empty", "alpha")], gate.DEFAULT_FIRST_PARTY_ROOTS
        )
        assert (records, empty) == ([], ["empty"])

    def test_an_unparseable_file_fails_closed_instead_of_being_skipped(self, tmp_path: Path) -> None:
        """A file the collector cannot parse hides exactly the imports it counts.

        Observed for real: a concurrent editor left
        `alpha/community/search_federation/tools.py` mid-write, and the first
        version of this gate leaked a raw ``SyntaxError`` traceback instead of
        the documented fail-closed exit.
        """
        path = tmp_path / "broken.py"
        path.write_text("def f():\n    pass\n)    except ValueError:\n", encoding="utf-8")
        with pytest.raises(gate.GateError) as excinfo:
            gate.collect_file(path, "alpha.broken", frozenset(gate.DEFAULT_FIRST_PARTY_ROOTS))
        message = str(excinfo.value)
        assert "cannot parse" in message
        assert "broken.py" in message.replace("\\", "/")
        assert "fails closed" in message

    def test_a_non_utf8_file_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "latin.py"
        path.write_bytes(b"# -*- coding: latin-1 -*-\nx = '\xff'\n")
        with pytest.raises(gate.GateError, match="cannot read"):
            gate.collect_file(path, "alpha.latin", frozenset(gate.DEFAULT_FIRST_PARTY_ROOTS))


# --------------------------------------------------------------------------
# end to end: the negative demonstration, on a synthetic mini-tree
# --------------------------------------------------------------------------


def _mini_repo(root: Path) -> Path:
    """A checkout with both documented scan roots and nothing else."""
    package = root / "backend" / "packages" / "harness" / "alpha"
    (package / "config").mkdir(parents=True)
    (package / "__init__.py").write_text('"""alpha"""\n', encoding="utf-8")
    (package / "config" / "__init__.py").write_text(
        '"""config"""\n\nfrom __future__ import annotations\n\nimport json\n', encoding="utf-8"
    )
    (package / "config" / "leaf.py").write_text(
        "def load():\n    import yaml\n    return yaml.safe_load('x')\n", encoding="utf-8"
    )
    gateway = root / "backend" / "app" / "gateway"
    gateway.mkdir(parents=True)
    (root / "backend" / "app" / "__init__.py").write_text('"""app"""\n', encoding="utf-8")
    (gateway / "__init__.py").write_text('"""gateway"""\n', encoding="utf-8")
    (gateway / "app.py").write_text('"""asgi app"""\n\nfrom app.gateway import services\n', encoding="utf-8")
    (gateway / "services.py").write_text('"""services"""\n', encoding="utf-8")
    return package


def _run(root: Path, budget: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(GATE_PATH), "--repo-root", str(root), "--budget", str(budget), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
    )


@pytest.fixture
def mini(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _mini_repo(root)
    budget = tmp_path / "import_budget.json"
    seeded = _run(root, budget, "--write-budget")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    return root, budget


class TestEndToEnd:
    def test_a_seeded_budget_passes_on_an_unchanged_tree(self, mini: tuple[Path, Path]) -> None:
        root, budget = mini
        proc = _run(root, budget)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "REGRESS" not in proc.stdout

    def test_adding_an_eager_import_turns_the_gate_red(self, mini: tuple[Path, Path]) -> None:
        """The whole point, demonstrated rather than asserted."""
        root, budget = mini
        (root / "backend" / "packages" / "harness" / "alpha" / "config" / "leaf.py").write_text(
            "import numpy\n\n\ndef load():\n    import yaml\n    return yaml.safe_load('x')\n",
            encoding="utf-8",
        )
        proc = _run(root, budget)
        assert proc.returncode == 1
        assert "NEW EAGER THIRD-PARTY DEPENDENCY" in proc.stdout
        assert "numpy" in proc.stdout
        assert "leaf.py:1 -> numpy" in proc.stdout.replace("\\", "/")

    def test_adding_a_first_party_eager_import_turns_the_gate_red(self, mini: tuple[Path, Path]) -> None:
        """The eager-``__init__`` problem, which is the architecture half."""
        root, budget = mini
        init = root / "backend" / "packages" / "harness" / "alpha" / "config" / "__init__.py"
        init.write_text(init.read_text(encoding="utf-8") + "\nfrom alpha.config.leaf import load\n", encoding="utf-8")
        proc = _run(root, budget)
        assert proc.returncode == 1
        assert "EAGER FIRST-PARTY IMPORTS OVER BUDGET" in proc.stdout
        assert "alpha.config" in proc.stdout

    def test_deferring_the_import_is_what_makes_it_green(self, mini: tuple[Path, Path]) -> None:
        """The remedy is in the same file, so the gate is a budget, not a wall."""
        root, budget = mini
        leaf = root / "backend" / "packages" / "harness" / "alpha" / "config" / "leaf.py"
        leaf.write_text("def load():\n    import numpy\n    return numpy.array([0])\n", encoding="utf-8")
        proc = _run(root, budget)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_missing_budget_exits_two_and_never_reads_as_green(self, mini: tuple[Path, Path]) -> None:
        root, budget = mini
        budget.unlink()
        proc = _run(root, budget)
        assert proc.returncode == 2
        assert "FAILED CLOSED" in proc.stderr

    def test_a_scan_root_that_moved_exits_two(self, mini: tuple[Path, Path]) -> None:
        root, budget = mini
        payload = json.loads(budget.read_text(encoding="utf-8"))
        payload["scan_roots"] = [{"path": "backend/packages/harness/gone", "module_root": "alpha"}]
        budget.write_text(json.dumps(payload), encoding="utf-8")
        proc = _run(root, budget)
        assert proc.returncode == 2
        assert "FAILED CLOSED" in proc.stderr
        assert "gone" in proc.stderr

    def test_a_plain_check_never_rewrites_the_budget(self, mini: tuple[Path, Path]) -> None:
        root, budget = mini
        before = budget.read_bytes()
        (root / "backend" / "packages" / "harness" / "alpha" / "config" / "leaf.py").write_text(
            "import numpy\n", encoding="utf-8"
        )
        assert _run(root, budget).returncode == 1
        assert budget.read_bytes() == before

    def test_write_budget_refuses_when_a_scan_root_is_empty(self, tmp_path: Path) -> None:
        root = tmp_path / "repo"
        (root / "backend" / "packages" / "harness" / "alpha").mkdir(parents=True)
        (root / "backend" / "packages" / "harness" / "alpha" / "__init__.py").write_text("", encoding="utf-8")
        budget = tmp_path / "b.json"
        budget.write_text(
            json.dumps(
                _budget(
                    scan_roots=[{"path": "backend/packages/harness/gone", "module_root": "alpha"}]
                )
            ),
            encoding="utf-8",
        )
        proc = _run(root, budget, "--write-budget")
        assert proc.returncode == 2
        assert "refusing to write a budget" in proc.stderr

    def test_an_unparseable_scanned_file_exits_two_not_a_traceback(self, mini: tuple[Path, Path]) -> None:
        """The same thing, end to end: a clean fail-closed, not a crash."""
        root, budget = mini
        broken = root / "backend" / "packages" / "harness" / "alpha" / "config" / "leaf.py"
        broken.write_text("def f():\n    pass\n)    except ValueError:\n", encoding="utf-8")
        proc = _run(root, budget)
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "FAILED CLOSED" in proc.stderr
        assert "cannot parse" in proc.stderr
        assert "Traceback" not in proc.stderr, "a gate must report, not crash"

    def test_json_output_carries_the_verdict(self, mini: tuple[Path, Path]) -> None:
        root, budget = mini
        proc = _run(root, budget, "--json")
        assert proc.returncode == 0
        # The JSON verdict is printed first, then the human report; decode the
        # first document rather than assuming stdout is JSON and nothing else.
        payload, _end = json.JSONDecoder().raw_decode(proc.stdout[proc.stdout.index("{") :])
        assert payload["status"] == "pass"
        assert payload["files_scanned"] == 6
        assert "cold-start eager imports" in proc.stdout


# --------------------------------------------------------------------------
# the committed budget, and the lazy conversions it must keep protecting
# --------------------------------------------------------------------------


class TestCommittedBudget:
    def test_budget_exists_and_is_loadable(self) -> None:
        assert BUDGET_PATH.exists(), (
            "backend/benchmarks/cold_start/import_budget.json is the committed reference; "
            "without it the eager-import gate cannot run and the property is not enforced"
        )
        budget = gate.load_budget(BUDGET_PATH)
        assert budget["scan_roots"], "no scan root: the gate would inspect nothing"
        assert budget["third_party_roots"], "no third-party roots recorded"
        assert budget["eager_first_party_by_package"], "no package budgeted"

    def test_every_probe_default_target_lives_under_a_scan_root(self) -> None:
        """A target outside the scan roots is a target this gate does not cover."""
        probe = _load(PROBE_PATH, "alpha_cold_start_probe_for_import_budget")
        budget = gate.load_budget(BUDGET_PATH)
        roots = gate.scan_roots_of(budget)
        for target in probe.DEFAULT_TARGETS:
            parts = target.split(".")
            covered = any(parts[: len(entry.module_root.split("."))] == entry.module_root.split(".") for entry in roots)
            assert covered, f"{target} is not under any scan root"

    def test_first_party_roots_are_not_also_third_party_roots(self) -> None:
        budget = gate.load_budget(BUDGET_PATH)
        overlap = set(budget["first_party_roots"]) & set(budget["third_party_roots"])
        assert not overlap, f"a root cannot be both: {sorted(overlap)}"

    def test_third_party_roots_are_sorted_and_unique(self) -> None:
        roots = gate.load_budget(BUDGET_PATH)["third_party_roots"]
        assert roots == sorted(set(roots))

    def test_package_budget_values_are_positive_integers(self) -> None:
        """Zero entries are dropped on write, so a 0 here means a hand edit."""
        for name, value in gate.load_budget(BUDGET_PATH)["eager_first_party_by_package"].items():
            assert isinstance(value, int) and value > 0, f"{name} = {value!r}"

    def test_alpha_config_keeps_its_whole_surface_lazy(self) -> None:
        """``_EXPORTS`` names the submodules the conversion moved behind a
        :pep:`562` ``__getattr__``. The forbidden set is read from the file
        itself, so adding an export cannot quietly make the assertion stale --
        and adding an *eager* import of a new export is caught immediately.
        """
        path = HARNESS / "alpha" / "config" / "__init__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        exports: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "_EXPORTS" for target in node.targets
            ):
                exports = ast.literal_eval(node.value)
        assert exports, "_EXPORTS moved; this test must be updated with it"

        record = gate.collect_file(path, "alpha.config", frozenset(gate.DEFAULT_FIRST_PARTY_ROOTS))
        eager = {item.target for item in (*record.first_party, *record.third_party)}
        for name, submodule in exports.items():
            target = f"alpha.config.{submodule}"
            assert target not in eager, (
                f"alpha/config/__init__.py eagerly imports {target} to provide {name}, "
                f"which undoes the lazy conversion it exists to protect: {sorted(eager)}"
            )
        # The lazy mechanism itself is one tiny eager module, and that is the
        # intended cost -- pinned so it cannot be swapped for the thing it avoids.
        assert eager == {"alpha.memory._lazy_exports.install_lazy_exports"}, (
            f"alpha/config/__init__.py eager import surface changed: {sorted(eager)}"
        )

    def test_the_extensions_loader_does_not_import_the_orm_at_module_scope(self) -> None:
        """The extensions -> persistence -> sqlalchemy chain stays cut."""
        path = HARNESS / "alpha" / "extensions" / "loader.py"
        assert path.exists(), "alpha/extensions/loader.py moved; update this test and the budget together"
        record = gate.collect_file(path, "alpha.extensions", frozenset(gate.DEFAULT_FIRST_PARTY_ROOTS))
        eager = {item.target for item in (*record.first_party, *record.third_party)}
        for target in ("sqlalchemy", "alpha.persistence"):
            assert not any(item == target or item.startswith(f"{target}.") for item in eager), (
                f"alpha/extensions/loader.py eagerly imports {target} at module scope, "
                f"which is the regression the conversion removed: {sorted(eager)}"
            )


def test_the_gate_module_is_runnable_as_a_script() -> None:
    """`python scripts/check_cold_start_imports.py --help` must work in CI."""
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(GATE_PATH), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--write-budget" in proc.stdout
