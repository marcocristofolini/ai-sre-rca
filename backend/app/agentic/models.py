from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas import IncidentInput


SpecialistKind = Literal[
    "kubernetes",
    "metrics",
    "logs",
    "traces",
    "database",
    "changes",
]


class PlannerGraphContext(BaseModel):
    root_entity_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    relationship_ids: list[str] = Field(default_factory=list)
    truncated: bool = False


class SpecialistTask(BaseModel):
    id: str
    specialist: SpecialistKind
    objective: str
    rationale: str
    depends_on: list[str] = Field(default_factory=list)
    required_entity_ids: list[str] = Field(default_factory=list)
    max_queries: int = Field(ge=1, le=100)
    read_only: bool = True


class InvestigationPlan(BaseModel):
    id: str = Field(default_factory=lambda: f"plan-{uuid4().hex[:12]}")
    tenant_id: str
    cluster: str
    incident: IncidentInput
    planner_origin: Literal["deterministic"] = "deterministic"
    graph_context: PlannerGraphContext = Field(
        default_factory=PlannerGraphContext
    )
    tasks: list[SpecialistTask] = Field(default_factory=list)
    max_parallel_tasks: int = Field(ge=1, le=16)
    max_tasks: int = Field(ge=1, le=32)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
