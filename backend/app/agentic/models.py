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
    HypothesisStatus,
    IncidentInput,
    RootCause,
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


ContextFreshness = Literal[
    "fresh",
    "stale",
    "historical",
    "tombstoned",
]


class PlannerContextEntity(BaseModel):
    id: str
    kind: str
    name: str
    namespace: str | None = None
    freshness: ContextFreshness
    depth: int | None = None
    last_seen: datetime
    tombstoned_at: datetime | None = None


class PlannerContextRelationship(BaseModel):
    id: str
    source_id: str
    target_id: str
    relationship: str
    freshness: ContextFreshness
    last_seen: datetime
    tombstoned_at: datetime | None = None


class PlannerContextObservation(BaseModel):
    id: str
    target_type: Literal["entity", "relationship"]
    target_id: str
    incident_id: str
    observed_at: datetime
    action: str | None = None
    origin: str | None = None


class PlannerContextChange(BaseModel):
    entity_id: str
    application: str
    revision: str | None = None
    service_name: str | None = None
    workload_kind: str | None = None
    workload_name: str | None = None
    occurred_at: datetime


class PlannerInventorySourceContext(BaseModel):
    source: str
    freshness: Literal["current", "stale", "missing"]
    snapshot_id: str | None = None
    observed_at: datetime | None = None
    age_seconds: int | None = None


class PlannerStateCounts(BaseModel):
    total: int = 0
    current: int = 0
    stale: int = 0
    historical: int = 0
    tombstoned: int = 0


class PlannerGraphHealthContext(BaseModel):
    entities: PlannerStateCounts = Field(
        default_factory=PlannerStateCounts
    )
    relationships: PlannerStateCounts = Field(
        default_factory=PlannerStateCounts
    )
    observations_total: int = 0
    latest_observation_at: datetime | None = None


class PlannerGraphContext(BaseModel):
    root_entity_id: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    relationship_ids: list[str] = Field(default_factory=list)
    entities: list[PlannerContextEntity] = Field(default_factory=list)
    relationships: list[PlannerContextRelationship] = Field(
        default_factory=list
    )
    observations: list[PlannerContextObservation] = Field(
        default_factory=list
    )
    recent_changes: list[PlannerContextChange] = Field(
        default_factory=list
    )
    inventory_sources: list[PlannerInventorySourceContext] = Field(
        default_factory=list
    )
    graph_health: PlannerGraphHealthContext = Field(
        default_factory=PlannerGraphHealthContext
    )
    truncated: bool = False
    history_truncated: bool = False


class ModelPlanningAdvice(BaseModel):
    requested_specialists: list[str] = Field(default_factory=list)
    specialist_reasons: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""


class PlannerAdvisoryTrace(BaseModel):
    source: Literal["model"] = "model"
    requested_specialists: list[str] = Field(default_factory=list)
    accepted_specialists: list[SpecialistKind] = Field(default_factory=list)
    rejected_specialists: list[str] = Field(default_factory=list)
    forced_specialists: list[SpecialistKind] = Field(default_factory=list)
    rationale: str = ""
    fallback_reason: str | None = None


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
    planner_origin: Literal["deterministic", "model-assisted"] = "deterministic"
    advisory_trace: PlannerAdvisoryTrace | None = None
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
    context_references: list[str] = Field(default_factory=list)
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
    operational_context: PlannerGraphContext = Field(
        default_factory=PlannerGraphContext
    )
    evidence: list["Evidence"] = Field(default_factory=list)
    entities: list["Entity"] = Field(default_factory=list)
    graph_edges: list["GraphEdge"] = Field(default_factory=list)
    evidence_integrity_ok: bool = True
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


EvaluationDecision = Literal[
    "selected",
    "supported",
    "testing",
    "ruled_out",
    "inconclusive",
    "blocked_by_precedence",
    "blocked_by_incomplete_context",
    "invalid_evidence",
]


class EvaluatedHypothesis(BaseModel):
    id: str
    specialist: SpecialistKind
    statement: str
    status: HypothesisStatus
    decision: EvaluationDecision
    score: float
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    precedence_rank: int


class ExplanationNode(BaseModel):
    id: str
    kind: Literal["hypothesis", "evidence", "gate", "verdict"]
    label: str
    status: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class ExplanationEdge(BaseModel):
    source: str
    target: str
    relationship: Literal[
        "supports",
        "contradicts",
        "blocks",
        "selected",
        "considered",
    ]


class HypothesisExplanationGraph(BaseModel):
    nodes: list[ExplanationNode] = Field(default_factory=list)
    edges: list[ExplanationEdge] = Field(default_factory=list)


class AgenticEvaluationResult(BaseModel):
    schema_version: Literal["ai-sre-rca.agentic-evaluation.v1"] = (
        "ai-sre-rca.agentic-evaluation.v1"
    )
    plan_id: str
    tenant_id: str
    cluster: str
    status: Literal["resolved", "inconclusive"]
    root_cause: RootCause | None = None
    evaluated_hypotheses: list[EvaluatedHypothesis] = Field(
        default_factory=list
    )
    explanation_graph: HypothesisExplanationGraph
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


class AgenticInvestigationResult(BaseModel):
    execution: AgenticExecutionResult
    evaluation: AgenticEvaluationResult
