from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as PathParameter
from pydantic import BaseModel, ConfigDict, JsonValue

from app.gateway.auth_disabled import AUTH_SOURCE_PAT
from app.gateway.authz import require_cancel_permission_if, require_permission
from app.gateway.deps import get_current_user_from_request
from agent_workspace.config.paths import get_paths, make_safe_user_id
from agent_workspace.goals import AttemptStatus, GoalContract, GoalStore, InvalidTransitionError, PlanVersion, RecordNotFoundError, StoreCorruptionError, TaskAttempt
from agent_workspace.goals.models import NonEmptyString
from agent_workspace.utils.file_io import run_file_io

router = APIRouter(prefix="/api/goals/contracts", tags=["goal-contracts"])
_stores: dict[Path, GoalStore] = {}
_stores_lock = Lock()


async def _goal_owner(request: Request) -> str:
    user = await get_current_user_from_request(request)
    if getattr(request.state, "auth_source", None) == AUTH_SOURCE_PAT:
        raise HTTPException(status_code=403, detail="PAT credentials are not permitted on this route")
    owner_id = getattr(user, "id", None)
    if owner_id is None or not str(owner_id).strip():
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(owner_id)


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class ContractCreateRequest(_RequestModel):
    objective: NonEmptyString


class PlanCreateRequest(_RequestModel):
    content: dict[str, JsonValue]


class AttemptCreateRequest(_RequestModel):
    plan_id: NonEmptyString
    intent: NonEmptyString


class AttemptTransitionRequest(_RequestModel):
    status: AttemptStatus


async def _operate[T](owner_id: str, operation: Callable[[GoalStore], T]) -> T:
    def execute() -> T:
        storage_dir = (get_paths().user_dir(make_safe_user_id(owner_id)) / "goal_contracts").resolve()
        with _stores_lock:
            store = _stores.get(storage_dir)
            if store is None:
                store = GoalStore(storage_dir)
                _stores[storage_dir] = store
        return operation(store)

    try:
        return await run_file_io(execute)
    except RecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Record not found") from exc
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (StoreCorruptionError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Goal contract storage unavailable") from exc


@router.get("", response_model=list[GoalContract])
@require_permission("runs", "read")
async def list_contracts(request: Request, owner_id: str = Depends(_goal_owner)) -> list[GoalContract]:
    return await _operate(owner_id, lambda store: store.list_contracts(owner_id=owner_id))


@router.post("", response_model=GoalContract, status_code=201)
@require_permission("runs", "create")
async def create_contract(payload: ContractCreateRequest, request: Request, owner_id: str = Depends(_goal_owner)) -> GoalContract:
    return await _operate(owner_id, lambda store: store.create_contract(payload.objective, owner_id=owner_id))


@router.get("/{contract_id}", response_model=GoalContract)
@require_permission("runs", "read")
async def get_contract(contract_id: str, request: Request, owner_id: str = Depends(_goal_owner)) -> GoalContract:
    return await _operate(owner_id, lambda store: store.get_contract(contract_id, owner_id=owner_id))


@router.post("/{contract_id}/plans", response_model=PlanVersion, status_code=201)
@require_permission("runs", "create")
async def create_plan(contract_id: str, payload: PlanCreateRequest, request: Request, owner_id: str = Depends(_goal_owner)) -> PlanVersion:
    return await _operate(owner_id, lambda store: store.create_plan(contract_id, payload.content, owner_id=owner_id))


@router.post("/{contract_id}/plans/{version}/approve", response_model=PlanVersion)
@require_permission("runs", "create")
async def approve_plan(contract_id: str, version: Annotated[int, PathParameter(ge=1)], request: Request, owner_id: str = Depends(_goal_owner)) -> PlanVersion:
    def approve(store: GoalStore) -> PlanVersion:
        for plan in store.list_plans(contract_id, owner_id=owner_id):
            if plan.version == version:
                return store.approve_plan(plan.id, owner_id=owner_id)
        raise RecordNotFoundError("Record not found")

    return await _operate(owner_id, approve)


@router.post("/{contract_id}/attempts", response_model=TaskAttempt, status_code=201)
@require_permission("runs", "create")
async def create_attempt(contract_id: str, payload: AttemptCreateRequest, request: Request, owner_id: str = Depends(_goal_owner)) -> TaskAttempt:
    def create(store: GoalStore) -> TaskAttempt:
        store.get_contract(contract_id, owner_id=owner_id)
        plan = store.get_plan(payload.plan_id, owner_id=owner_id)
        if plan.contract_id != contract_id:
            raise RecordNotFoundError("Record not found")
        return store.create_attempt(plan.id, payload.intent, owner_id=owner_id)

    return await _operate(owner_id, create)


@router.post("/{contract_id}/attempts/{attempt_id}/transition", response_model=TaskAttempt)
@require_permission("runs", "create")
async def transition_attempt(contract_id: str, attempt_id: str, payload: AttemptTransitionRequest, request: Request, owner_id: str = Depends(_goal_owner)) -> TaskAttempt:
    require_cancel_permission_if(request, payload.status == "cancelled")

    def transition(store: GoalStore) -> TaskAttempt:
        store.get_contract(contract_id, owner_id=owner_id)
        attempt = store.get_attempt(attempt_id, owner_id=owner_id)
        plan = store.get_plan(attempt.plan_id, owner_id=owner_id)
        if plan.contract_id != contract_id:
            raise RecordNotFoundError("Record not found")
        return store.transition_attempt(attempt.id, payload.status, owner_id=owner_id)

    return await _operate(owner_id, transition)
