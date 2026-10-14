"""Regression coverage for `GET /api/bots/{name}/companies`.

The route exists because nothing could answer "which organizations employs this
bot". `GET /api/companies` lists organizations without their employments and
`GET /api/companies/{id}` needs an id you do not have yet, so the detail page
would have had to fan out over every company from the client.

These tests pin the parts that are easy to get silently wrong:

  - employment is matched on `Employment.agent_handle` case-insensitively,
    because `hire()` stores the roster handle while `_validate_bot_name`
    lowercases the request.
  - a terminated employment keeps its own `status` and is never dropped, so
    "never worked here" stays distinguishable from "no longer works here".
  - dangling `role_id` / `unit_id` yield `null`, not a fabricated stub.
  - an unreadable company store is 503 carrying its own reason, never an empty
    200: a corrupt store and an owner with no companies drive opposite
    decisions.
  - tenancy comes from the shared `_owner()` request resolver, so this route
    cannot become a second owner-derivation path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

# Prime `alpha.groups.room` before touching `app.gateway.routers`.
#
# `routers/__init__.py` eagerly imports every router, and `routers/groups.py`
# reads `VALID_INTENTS` at module scope from `alpha.groups.room`. This file
# sorts before `test_bot_projects_route.py`, so it would load the router package
# cold and hit `NameError: name 'VALID_INTENTS' is not defined` unless the
# harness module is already present.
import alpha.groups.room  # noqa: F401
from app.gateway.routers.bots import bot_companies


@dataclass
class FakeCompany:
    """Only the `Company` fields the route reads."""

    company_id: str
    name: str
    archetype: str = "company"
    state: Any = "draft"
    created_at: float = 1.0
    employments: list[Any] = field(default_factory=list)
    roles: dict[str, Any] = field(default_factory=dict)
    units: dict[str, Any] = field(default_factory=dict)

    def role(self, role_id: str | None) -> Any:
        if not role_id:
            return None
        return self.roles.get(role_id)

    def unit(self, unit_id: str | None) -> Any:
        if not unit_id:
            return None
        return self.units.get(unit_id)


class FakeService:
    def __init__(self, companies: list[FakeCompany], error: Exception | None = None) -> None:
        self._companies = companies
        self._error = error

    def list_companies(self, owner_id: str) -> list[FakeCompany]:
        if self._error is not None:
            raise self._error
        return self._companies


def _patch(monkeypatch: pytest.MonkeyPatch, service: FakeService) -> None:
    import alpha.company_os.service as svc_mod

    monkeypatch.setattr(svc_mod, "get_company_service", lambda *a, **k: service)


def _request(owner_id: str = "u1") -> Any:
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=owner_id)))


async def _call(name: str, request: Any = None) -> dict[str, Any]:
    """No permission decorator here: tenancy is the boundary (as in `companies.py`)."""
    return await bot_companies(name, request if request is not None else _request())


def _employment(handle: str, **kw: Any):
    from alpha.company_os.models import Employment

    return Employment(agent_handle=handle, **kw)


@pytest.mark.asyncio
async def test_returns_only_companies_employing_the_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        FakeService(
            companies=[
                FakeCompany("co1", "Alpha Labs", employments=[_employment("architect")]),
                FakeCompany("co2", "Other Co", employments=[_employment("coder")]),
            ]
        ),
    )

    payload = await _call("architect")

    assert [c["company_id"] for c in payload["companies"]] == ["co1"]
    assert payload["bot"] == "architect"
    assert payload["companies_checked"] == 2


@pytest.mark.asyncio
async def test_handle_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_validate_bot_name` lowercases the request; the stored handle may not be."""
    _patch(monkeypatch, FakeService(companies=[FakeCompany("co1", "Alpha Labs", employments=[_employment("Architect")])]))

    payload = await _call("Architect")

    assert len(payload["companies"]) == 1


@pytest.mark.asyncio
async def test_terminated_employment_is_kept_with_its_own_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.company_os.models import EmploymentStatus

    _patch(
        monkeypatch,
        FakeService(
            companies=[
                FakeCompany(
                    "co1",
                    "Alpha Labs",
                    employments=[_employment("architect", status=EmploymentStatus.TERMINATED)],
                )
            ]
        ),
    )

    payload = await _call("architect")

    assert payload["companies"][0]["employment"]["status"] == "terminated"
    assert "status" in payload["note"], "the payload must say a past employment is reported, not dropped"


@pytest.mark.asyncio
async def test_dangling_role_and_unit_are_null_not_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.company_os.models import Employment, Role

    _patch(
        monkeypatch,
        FakeService(
            companies=[
                FakeCompany(
                    "co1",
                    "Alpha Labs",
                    employments=[Employment(agent_handle="architect", role_id="role-missing", unit_id=None)],
                    roles={"role-present": Role(title="Lead")},
                )
            ]
        ),
    )

    payload = await _call("architect")

    entry = payload["companies"][0]
    assert entry["role"] is None, "a dangling role_id must not render as a fabricated role"
    assert entry["unit"] is None


@pytest.mark.asyncio
async def test_resolved_role_travels_with_the_employment(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.company_os.models import Employment, Role

    _patch(
        monkeypatch,
        FakeService(
            companies=[
                FakeCompany(
                    "co1",
                    "Alpha Labs",
                    employments=[Employment(agent_handle="architect", role_id="r1")],
                    roles={"r1": Role(title="Staff Architect")},
                )
            ]
        ),
    )

    payload = await _call("architect")

    assert payload["companies"][0]["role"]["title"] == "Staff Architect"


@pytest.mark.asyncio
async def test_unreadable_store_is_503_not_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    from alpha.company_os.store import CompanyStoreUnreadable

    _patch(monkeypatch, FakeService(companies=[], error=CompanyStoreUnreadable("companies.json will not parse")))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        await _call("architect")

    assert excinfo.value.status_code == 503
    assert "will not parse" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_owner_is_resolved_from_the_request_not_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tenancy is the boundary, and it is read off the authenticated principal."""
    seen: list[str] = []

    class Spy(FakeService):
        def list_companies(self, owner_id: str) -> list[FakeCompany]:
            seen.append(owner_id)
            return super().list_companies(owner_id)

    _patch(monkeypatch, Spy(companies=[]))

    payload = await _call("architect", request=_request("owner-42"))

    assert seen == ["owner-42"]
    assert payload["companies"] == []
    assert payload["companies_checked"] == 0


@pytest.mark.asyncio
async def test_invalid_bot_name_is_rejected_by_the_shared_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    _patch(monkeypatch, FakeService(companies=[]))

    with pytest.raises(HTTPException) as excinfo:
        await _call("not a valid name!")

    assert excinfo.value.status_code == 422
