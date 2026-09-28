from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    Hypothesis,
    IncidentInput,
    StatefulDependencyAnalysis,
    TraceAnalysis,
)


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


SpecialistExecutionStatus = Literal[
    "completed",
    "skipped",
    "failed",
    "budget_exhausted",
]


class SpecialistResult(BaseModel):
    task_id: str
    specialist: SpecialistKind
    status: SpecialistExecutionStatus
    evidence: list["Evidence"] = Field(default_factory=list)
    entities: list["Entity"] = Field(default_factory=list)
    graph_edges: list["GraphEdge"] = Field(default_factory=list)
    trace_analyses: list["TraceAnalysis"] = Field(default_factory=list)
    stateful_dependencies: list["StatefulDependencyAnalysis"] = Field(
        default_factory=list
    )
    hypotheses: list["Hypothesis"] = Field(default_factory=list)
    hypotheses_advisory_only: bool = True
    query_count: int = 0
    query_limit: int
    integrity_ok: bool = True
    reason: str = ""
    error_type: str | None = None
    started_at: datetime
    finished_at: datetime


class SharedEvidenceEnvelope(BaseModel):
    plan_id: str
    tenant_id: str
    cluster: str
    evidence: list["Evidence"] = Field(default_factory=list)
    entities: list["Entity"] = Field(default_factory=list)
    graph_edges: list["GraphEdge"] = Field(default_factory=list)
    evidence_integrity_ok: bool = True
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
class AgenticExecutionResult(BaseModel):
    schema_version: Literal["ai-sre-rca.agentic.v1"] = (
        "ai-sre-rca.agentic.v1"
    )
    plan: InvestigationPlan
    status: Literal["completed", "partial", "failed"]
    specialist_results: list[SpecialistResult] = Field(default_factory=list)
    shared_evidence: SharedEvidenceEnvelope
    query_budget: dict[str, object] = Field(default_factory=dict)
    started_at: datetime
    finished_at: datetime
