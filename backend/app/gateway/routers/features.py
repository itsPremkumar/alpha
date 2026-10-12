"""Read-only feature-flag endpoint for the frontend bootstrap.

Reports which optional features are exposed over HTTP so the frontend can gate
UI and avoid firing requests that the backend would reject. Config-only flags
read through ``get_config`` so edits to ``config.yaml`` take effect on the next
request, while startup-scoped capabilities report the runtime that actually
started.

``advertised_capabilities`` answers a different question from every other field
here. The existing flags report "is this configured and started?", which is a
config question. They cannot report "is anything outside the defining module
calling it?", which is the question every honesty bug in this repository turned
on: a stub with good tests and a valid flag answers "available" to all of them.
See ``alpha.capabilities.honesty``.
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from alpha.config.app_config import AppConfig
from alpha.subagents.capacity import configured_subagent_max_running
from app.gateway.browser_capability import browser_capability
from app.gateway.deps import get_config
from app.gateway.routers.capability_honesty import advertised_capabilities

router = APIRouter(prefix="/api", tags=["features"])


class AgentsApiFeature(BaseModel):
    """Availability of the custom-agent management API."""

    enabled: bool = Field(..., description="Whether the agents_api routes are exposed over HTTP")


class BrowserControlFeature(BaseModel):
    """Availability of live agentic browser control."""

    enabled: bool = Field(..., description="Whether the live browser routes and UI are available")


class McpTasksFeature(BaseModel):
    """Availability of the durable MCP task runtime."""

    enabled: bool = Field(..., description="Whether durable MCP task APIs and UI are available")


class SubagentBatchesFeature(BaseModel):
    """Persistence, worker, and process capacity for native-subagent batches."""

    enabled: bool = Field(..., description="Compatibility alias for worker_running")
    repository_available: bool = Field(..., description="Whether durable batch history APIs are available")
    worker_running: bool = Field(..., description="Whether this Gateway process is executing durable batch work")
    max_running: int = Field(..., description="Native subagent execution slots in this Gateway process")


class AdvertisedCapability(BaseModel):
    """The wiring verdict for one advertised capability.

    Separate from every ``enabled`` flag on purpose. "Configured and started"
    and "wired" are different facts, and the combination that reads as a working
    feature is exactly ``enabled`` plus ``unwired``.
    """

    capability_id: str = Field(..., description="Stable capability identifier")
    wiring: str = Field(
        ...,
        description="wired | unwired | unproven - whether a production module invokes the claimed symbol",
    )
    consumers: list[str] = Field(
        default_factory=list,
        description="Production call sites found, as path:line (absent when unwired)",
    )
    detail: str = Field(default="", description="Why this verdict, in operator-readable terms")
    reason: str = Field(default="", description="Which documented invariant this capability upholds")


class FeaturesResponse(BaseModel):
    """Frontend-facing feature availability flags."""

    agents_api: AgentsApiFeature
    browser_control: BrowserControlFeature
    mcp_tasks: McpTasksFeature
    subagent_batches: SubagentBatchesFeature
    advertised_capabilities: list[AdvertisedCapability] = Field(
        default_factory=list,
        description=("Advertised-vs-wired verdicts. A capability listed as unwired is documented but has no production consumer; unproven means a static walk cannot decide and a human must check."),
    )


@router.get(
    "/features",
    response_model=FeaturesResponse,
    summary="List Feature Flags",
    description="Report which optional features are available, so the frontend can gate UI before issuing requests.",
)
async def list_features(request: Request, config: AppConfig = Depends(get_config)) -> FeaturesResponse:
    """Return availability of optional frontend features."""
    browser = browser_capability(config)
    subagent_batch_worker_running = bool(getattr(request.app.state, "subagent_batches_available", False))
    return FeaturesResponse(
        agents_api=AgentsApiFeature(enabled=config.agents_api.enabled),
        browser_control=BrowserControlFeature(enabled=browser.available),
        # MCP task bindings and the submitter are startup-scoped. Report the
        # capability that actually started rather than a hot-reloaded config
        # value that would require a Gateway restart to take effect.
        mcp_tasks=McpTasksFeature(enabled=bool(getattr(request.app.state, "mcp_tasks_available", False))),
        subagent_batches=SubagentBatchesFeature(
            # Keep the historical `enabled` field as a compatibility alias
            # while exposing read persistence independently from execution.
            # A stopped/disabled worker must not hide durable history/export.
            enabled=subagent_batch_worker_running,
            repository_available=getattr(request.app.state, "subagent_batch_repo", None) is not None,
            worker_running=subagent_batch_worker_running,
            max_running=configured_subagent_max_running(),
        ),
        advertised_capabilities=[AdvertisedCapability(**item) for item in advertised_capabilities()],
    )
