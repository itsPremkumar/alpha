"""`make doctor` must never print "Ready" for a product it could not prove works.

The report this closes: `Status: Ready` for a checkout that could not complete a
run. The model-construction defect behind that is another agent's; what was
missing around it is the *surface*:

* ``skip`` - the tri-state's "unknown" - was never counted, so a check that
  could not be performed at all still produced a green ``Status: Ready``.
* Nothing observed the *running* deployment. A Gateway that is up on its port
  but whose persistence is unreachable answers 200 on ``/health`` (liveness) and
  503 on ``/health/ready``; only the latter can answer "can this serve a run?".
* Nothing checked whether ``logs/alpha_health.json`` was still true. A status
  file claiming ``status: "starting"`` from a launcher that died long ago is the
  most expensive operability bug there is, because every later diagnostic trusts
  it - including this one.

So the contract pinned here is narrow and about reporting, not about any single
check's internals:

    fail present            -> Status is an error count,      exit 1
    no fail, unknown present -> Status is Indeterminate + names, exit 2
    no fail, no unknown      -> Status: Ready,                exit 0

and "Ready" is unreachable while any check is unknown.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import deploy_status as ds
import doctor
import pytest


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch) -> Path:
    """A checkout-shaped directory that is safe to run the whole report against."""
    repo_root = tmp_path / "repo"
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "logs").mkdir()
    # __file__ drives the project root and the pnpm script path.
    (repo_root / "scripts" / "doctor.py").write_text("# shim\n", encoding="utf-8")
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(doctor, "__file__", str(repo_root / "scripts" / "doctor.py"))
    return repo_root


class _CheckResultList(doctor.CheckResult):
    """A CheckResult that also unpacks as a single-element list.

    ``main()`` mixes both shapes - ``check_python()`` returns one result while
    ``check_llm_api_key()`` returns a list that is splatted into a section - so a
    single stub has to satisfy both call sites without the test having to know
    which check returns which.
    """

    def __iter__(self):
        yield self


def _freeze_everything(monkeypatch, *, status: str, detail: str) -> None:
    """Replace every check with a single controlled verdict.

    Only the reporting surface is under test here; the individual checks have
    their own suites. Anything left un-overridden would reach the real network
    or the real toolchain, so the whole surface is replaced.
    """

    def make(*_a, **_k):
        return _CheckResultList("stub", status, detail)

    for name in dir(doctor):
        if not name.startswith("check_"):
            continue
        if not callable(getattr(doctor, name)):
            continue
        monkeypatch.setattr(doctor, name, make)


# ===========================================================================
# The tri-state reaches the summary
# ===========================================================================


class TestSummaryTriState:
    def test_all_ok_is_ready_and_exits_zero(self, fake_repo: Path, monkeypatch, capsys) -> None:
        _freeze_everything(monkeypatch, status="ok", detail="")

        code = doctor.main()
        out = capsys.readouterr().out

        assert code == doctor.EXIT_READY == 0
        assert "Status: Ready" in out
        assert "Indeterminate" not in out

    def test_unknown_checks_make_ready_unreachable(self, fake_repo: Path, monkeypatch, capsys) -> None:
        """A check that could not be performed is absence of evidence, not a pass."""
        _freeze_everything(monkeypatch, status="skip", detail="could not determine")

        code = doctor.main()
        out = capsys.readouterr().out

        assert code == doctor.EXIT_INDETERMINATE == 2, "unknown must not be reported as success"
        assert "Status: Ready" not in out
        assert "Indeterminate" in out
        assert "could not be determined" in out

    def test_indeterminate_names_the_undetermined_checks(self, fake_repo: Path, monkeypatch, capsys) -> None:
        _freeze_everything(monkeypatch, status="skip", detail="n/a")

        doctor.main()
        out = capsys.readouterr().out

        assert "Undetermined checks" in out
        assert "stub" in out

    def test_warnings_alone_stay_green(self, fake_repo: Path, monkeypatch, capsys) -> None:
        """A warning is a checked-and-caveated result, so it is not indeterminate."""
        _freeze_everything(monkeypatch, status="warn", detail="use https://example.com")

        code = doctor.main()
        out = capsys.readouterr().out

        assert code == doctor.EXIT_READY == 0
        # Every check is stubbed to warn, so the count is however many the
        # report happens to run - what matters is that warnings are still Ready
        # and are not folded into Indeterminate.
        assert "Status: Ready (" in out and "warning(s)" in out
        assert "Indeterminate" not in out
        assert "could not be determined" not in out

    def test_a_failure_beats_unknown(self, fake_repo: Path, monkeypatch, capsys) -> None:
        """A known failure is worse than an unknown and owns the verdict."""
        _freeze_everything(monkeypatch, status="fail", detail="broken")
        monkeypatch.setattr(doctor, "check_gateway_readiness", lambda: doctor.CheckResult("gateway readiness", "skip", "down"))

        code = doctor.main()
        out = capsys.readouterr().out

        assert code == doctor.EXIT_NOT_READY == 1
        assert "error(s)" in out
        assert "Status: Ready" not in out

    def test_exit_codes_are_distinct(self) -> None:
        assert doctor.EXIT_READY == 0
        assert doctor.EXIT_NOT_READY == 1
        assert doctor.EXIT_INDETERMINATE == 2
        assert len({doctor.EXIT_READY, doctor.EXIT_NOT_READY, doctor.EXIT_INDETERMINATE}) == 3


# ===========================================================================
# Runtime Readiness: the checks that can observe a lie
# ===========================================================================


class TestLauncherStatusFileCheck:
    def test_absent_file_is_unknown_not_ok(self, fake_repo: Path) -> None:
        result = doctor.check_launcher_status_file(fake_repo)

        assert result.status == "skip", "an absent file must never be reported as a pass"
        assert result.fix, "an unknown must tell the operator what to do next"

    def test_dead_launcher_fails_with_the_pid_and_old_status(self, fake_repo: Path) -> None:
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps(
                {
                    "pid": 4_000_000,
                    "status": "starting",
                    "detail": "attempt 186/450",
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        result = doctor.check_launcher_status_file(fake_repo)

        assert result.status == "fail"
        assert "4000000" in result.detail
        assert "starting" in result.detail
        assert "STALE" in result.detail

    def test_frozen_heartbeat_fails(self, fake_repo: Path) -> None:
        stale = (datetime.now(UTC) - timedelta(seconds=ds.LAUNCHER_MAX_HEARTBEAT_AGE_SECONDS + 60)).isoformat()
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps({"pid": os.getpid(), "status": "healthy", "timestamp_utc": stale}),
            encoding="utf-8",
        )

        assert doctor.check_launcher_status_file(fake_repo).status == "fail"

    def test_live_launcher_is_ok(self, fake_repo: Path) -> None:
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps(
                {"pid": os.getpid(), "status": "healthy", "timestamp_utc": datetime.now(UTC).isoformat()}
            ),
            encoding="utf-8",
        )

        assert doctor.check_launcher_status_file(fake_repo).status == "ok"

    def test_recorded_failure_is_surfaced(self, fake_repo: Path) -> None:
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "status": "failed",
                    "detail": "dependency missing: uv",
                    "timestamp_utc": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )

        result = doctor.check_launcher_status_file(fake_repo)

        assert result.status == "fail"
        assert "failed" in result.detail
        assert "uv" in result.detail


class TestPidAgreementCheck:
    def test_mismatch_fails(self, fake_repo: Path) -> None:
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps({"pid": 111, "status": "healthy", "timestamp_utc": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
        (fake_repo / "logs" / "alpha.pid").write_text("222", encoding="utf-8")

        assert doctor.check_launcher_pid_agreement(fake_repo).status == "fail"

    def test_agreement_is_ok(self, fake_repo: Path) -> None:
        (fake_repo / "logs" / "alpha_health.json").write_text(
            json.dumps({"pid": 111, "status": "healthy", "timestamp_utc": datetime.now(UTC).isoformat()}),
            encoding="utf-8",
        )
        (fake_repo / "logs" / "alpha.pid").write_text("111", encoding="utf-8")

        assert doctor.check_launcher_pid_agreement(fake_repo).status == "ok"


class TestGatewayReadinessCheck:
    def test_uses_the_readiness_route(self, monkeypatch) -> None:
        seen: list[str] = []

        def fake_probe(base_url: str, **kwargs):
            seen.append(base_url)
            return ds.Verdict(ds.OK, "ready")

        monkeypatch.setattr(ds, "probe_readiness", fake_probe)

        result = doctor.check_gateway_readiness()

        assert result.status == "ok"
        assert seen and seen[0].startswith("http://127.0.0.1:")
        assert result.detail

    def test_not_ready_fails_and_names_the_subsystem(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ds,
            "probe_readiness",
            lambda *_a, **_k: ds.Verdict(ds.NOT_OK, "Gateway is NOT ready: database='unreachable'"),
        )

        result = doctor.check_gateway_readiness()

        assert result.status == "fail"
        assert "unreachable" in result.detail

    def test_unreachable_gateway_is_unknown_not_ok(self, monkeypatch) -> None:
        """Pre-flight runs before the stack is up: that is not a failure to pass."""
        monkeypatch.setattr(
            ds, "probe_readiness", lambda *_a, **_k: ds.Verdict(ds.UNKNOWN, "could not reach http://127.0.0.1:8001/health/ready")
        )

        result = doctor.check_gateway_readiness()

        assert result.status == "skip"
        assert result.fix


class TestFrontendHttpCheck:
    def test_refused_is_unknown(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ds, "probe_http_ok", lambda *_a, **_k: ds.Verdict(ds.UNKNOWN, "http://127.0.0.1:3000/ is not answering")
        )

        assert doctor.check_frontend_http().status == "skip"

    def test_error_status_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(
            ds, "probe_http_ok", lambda *_a, **_k: ds.Verdict(ds.NOT_OK, "http://127.0.0.1:3000/ returned HTTP 500")
        )

        assert doctor.check_frontend_http().status == "fail"
