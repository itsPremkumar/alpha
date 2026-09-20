"""8-Stage Production Line Pipeline inspired by Hermes production line architecture."""

from __future__ import annotations

import logging
import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ProductionStage(StrEnum):
    REQUIREMENTS = "01_requirements"
    ARCHITECTURE = "02_architecture"
    API_CONTRACT = "03_api_contract"
    BACKEND_IMPLEMENTATION = "04_backend"
    UI_FRONTEND = "05_ui_wireframe"
    DEVOPS_CI = "06_docker_ci"
    TEST_PLAN_QA = "07_test_plan"
    DOCUMENTATION_RELEASE = "08_documentation_release"


_STAGE_BOT_MAPPING = {
    ProductionStage.REQUIREMENTS: "product-owner",
    ProductionStage.ARCHITECTURE: "architect",
    ProductionStage.API_CONTRACT: "solution-architect",
    ProductionStage.BACKEND_IMPLEMENTATION: "senior-backend",
    ProductionStage.UI_FRONTEND: "ui-ux-designer",
    ProductionStage.DEVOPS_CI: "devops-engineer",
    ProductionStage.TEST_PLAN_QA: "qa-lead",
    ProductionStage.DOCUMENTATION_RELEASE: "technical-writer",
}


class StageArtifact(BaseModel):
    stage: ProductionStage
    assigned_bot: str
    filename: str
    summary: str
    content: str
    approved: bool = True
    completed_at: float = Field(default_factory=time.time)


class ProductionLineRun(BaseModel):
    run_id: str = Field(default_factory=lambda: f"prod-{uuid.uuid4().hex[:8]}")
    title: str
    feature_spec: str
    current_stage: ProductionStage = ProductionStage.REQUIREMENTS
    status: str = "in_progress"  # in_progress, completed, blocked
    artifacts: list[StageArtifact] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)


class ProductionLineEngine:
    """Manages 8-stage feature pipeline from requirements through release."""

    def __init__(self):
        # run_id -> ProductionLineRun
        self._runs: dict[str, ProductionLineRun] = {}

    def submit_feature(self, title: str, feature_spec: str) -> ProductionLineRun:
        """Starts a new feature through the 8-stage production line."""
        run = ProductionLineRun(title=title, feature_spec=feature_spec)
        self._runs[run.run_id] = run
        logger.info(f"Initialized Production Line Run {run.run_id}: '{title}'")
        return run

    def advance_stage(
        self,
        run_id: str,
        content: str = "",
        custom_bot: str | None = None,
    ) -> tuple[ProductionLineRun, StageArtifact]:
        """Advances the run to the next sequential stage, producing and validating the stage artifact."""
        run = self._runs.get(run_id)
        if not run:
            raise KeyError(f"Production Run '{run_id}' not found.")

        current_stage = run.current_stage
        assigned_bot = custom_bot or _STAGE_BOT_MAPPING.get(current_stage, "fullstack-dev")

        filenames = {
            ProductionStage.REQUIREMENTS: "01-requirements.md",
            ProductionStage.ARCHITECTURE: "02-architecture.md",
            ProductionStage.API_CONTRACT: "03-api-contract.md",
            ProductionStage.BACKEND_IMPLEMENTATION: "04-backend.py",
            ProductionStage.UI_FRONTEND: "05-wireframe.txt",
            ProductionStage.DEVOPS_CI: "06-docker-ci.yaml",
            ProductionStage.TEST_PLAN_QA: "07-test-plan.md",
            ProductionStage.DOCUMENTATION_RELEASE: "08-readme.md",
        }
        filename = filenames[current_stage]

        artifact = StageArtifact(
            stage=current_stage,
            assigned_bot=assigned_bot,
            filename=filename,
            summary=f"Completed {current_stage.value} signed off by @{assigned_bot}",
            content=content or f"// Automated production artifact generated for {run.title} at stage {current_stage.value}",
        )
        run.artifacts.append(artifact)
        run.updated_at = time.time()

        # Advance to next stage or mark completed
        stages_list = list(ProductionStage)
        current_idx = stages_list.index(current_stage)
        if current_idx < len(stages_list) - 1:
            run.current_stage = stages_list[current_idx + 1]
            run.status = "in_progress"
        else:
            run.status = "completed"

        return run, artifact

    def get_run(self, run_id: str) -> ProductionLineRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[ProductionLineRun]:
        return list(self._runs.values())
