from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.integrity import compute_evidence_hash


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    RULED_OUT = "ruled_out"
    INCONCLUSIVE = "inconclusive"


class WorkloadMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ReplicaSet", "Pod"]
    name: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
    uid: str = Field(min_length=1, max_length=255)
    terminating: bool = False


class WorkloadScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["Deployment", "StatefulSet"]
    name: str = Field(min_length=1, max_length=253)
    uid: str = Field(min_length=1, max_length=255)
    members: list[WorkloadMember] = Field(max_length=32)


class IncidentInput(BaseModel):
    title: str
    tenant_id: str = "default"
    cluster: str = "unknown"
    namespace: str | None = None
    pod: str | None = None
    node: str | None = None
    service: str | None = None
    alert_name: str = "ManualInvestigation"
    severity: str = "warning"
    starts_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    ends_at: datetime | None = None
    workload: WorkloadScope | None = None

    @model_validator(mode="after")
    def validate_explicit_window(self):
        if self.ends_at is not None:
            if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
                raise ValueError("explicit time bounds require a timezone")
            if not 0 < (self.ends_at - self.starts_at).total_seconds() <= 3600:
                raise ValueError("explicit window must be positive and at most one hour")
        return self

    def query_window(self, before_minutes: int, after_minutes: int = 5):
        if self.ends_at is not None:
            return self.starts_at, self.ends_at
        return self.starts_at - timedelta(minutes=before_minutes), self.starts_at + timedelta(minutes=after_minutes)


class Entity(BaseModel):
    id: str
    kind: str
    name: str
    namespace: str | None = None
    relationship: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relationship: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: f"ev-{uuid4().hex[:10]}")
    source: str
    category: str
    summary: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    facts: dict[str, Any] = Field(default_factory=dict)
    query: str | None = None
    raw_reference: str | None = None
    strength: Literal["weak", "medium", "strong"] = "medium"
    integrity_sha256: str | None = None

    @model_validator(mode="after")
    def ensure_integrity_hash(self):
        if not self.integrity_sha256:
            self.integrity_sha256 = compute_evidence_hash(self)
        return self


class Hypothesis(BaseModel):
    id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    origin: Literal["playbook", "llm"] = "playbook"
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    required_checks: list[str] = Field(default_factory=list)
    score: float = 0.0
    rationale: str = ""


class TimelineEvent(BaseModel):
    timestamp: datetime
    source: str
    message: str


class RootCause(BaseModel):
    title: str
    summary: str
    hypothesis_id: str | None = None
    evidence_strength: Literal["weak", "medium", "strong"]
    supporting_signals: int
    contradicting_signals: int


class Remediation(BaseModel):
    priority: Literal["P0", "P1", "P2", "P3"]
    action: str
    reason: str
    requires_approval: bool = True




class RemediationPlanStatus(str, Enum):
    POLICY_DENIED = "policy_denied"
    DRY_RUN_FAILED = "dry_run_failed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"
    EXPIRED = "expired"


class ScaleDeploymentPlanRequest(BaseModel):
    incident_id: str
    action_type: Literal["scale_deployment"] = "scale_deployment"
    namespace: str
    deployment: str
    desired_replicas: int = Field(ge=0, le=10000)
    rationale: str = Field(min_length=3, max_length=1000)


class RemediationDecisionRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class RemediationPlan(BaseModel):
    id: str = Field(default_factory=lambda: f"rem-{uuid4().hex[:12]}")
    incident_id: str
    tenant_id: str
    cluster: str
    action_type: Literal["scale_deployment"]
    namespace: str
    deployment: str
    desired_replicas: int
    rationale: str
    requested_by: str
    status: RemediationPlanStatus
    policy_checks: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    approval_note: str | None = None
    execution: dict[str, Any] = Field(default_factory=dict)
    executed_at: datetime | None = None
    rollback_execution: dict[str, Any] = Field(default_factory=dict)
    rolled_back_at: datetime | None = None
    failure: str | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SimilarIncident(BaseModel):
    incident_id: str
    title: str
    root_cause: str
    similarity: float
    occurred_at: datetime
    cluster: str
    namespace: str | None = None


class IncidentSummary(BaseModel):
    incident_id: str
    title: str
    tenant_id: str
    cluster: str
    namespace: str | None = None
    status: Literal["investigating", "resolved", "inconclusive"]
    severity: str
    root_cause: str
    investigation_mode: Literal["classic", "agentic"]
    created_at: datetime


class IncidentList(BaseModel):
    items: list[IncidentSummary]
    limit: int
    offset: int
    next_offset: int | None = None


class SpecialistExecutionSummary(BaseModel):
    task_id: str
    specialist: str
    status: Literal["completed", "skipped", "failed", "budget_exhausted"]
    query_count: int
    query_limit: int
    integrity_ok: bool
    reason: str = ""
    error_type: str | None = None
    started_at: datetime
    finished_at: datetime


class HypothesisDecisionSummary(BaseModel):
    hypothesis_id: str
    specialist: str
    decision: Literal[
        "selected", "supported", "testing", "ruled_out", "inconclusive",
        "blocked_by_precedence", "blocked_by_incomplete_context", "invalid_evidence",
    ]
    reasons: list[str] = Field(default_factory=list)


class AgenticDiagnosis(BaseModel):
    schema_version: Literal["ai-sre-rca.agentic-diagnosis.v1"] = "ai-sre-rca.agentic-diagnosis.v1"
    plan_id: str
    planner_origin: Literal["deterministic", "model-assisted"]
    execution_status: Literal["completed", "partial", "failed"]
    evidence_integrity_ok: bool
    started_at: datetime
    finished_at: datetime
    specialists: list[SpecialistExecutionSummary]
    decisions: list[HypothesisDecisionSummary]
    # A snapshot of the evaluator's explanation, never reinterpreted as topology.
    explanation_graph: dict[str, Any]


class InvestigationResult(BaseModel):
    incident_id: str
    agentic: AgenticDiagnosis | None = None
    status: Literal["investigating", "resolved", "inconclusive"]
    incident: IncidentInput
    entities: list[Entity]
    graph_edges: list[GraphEdge] = Field(default_factory=list)
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    similar_incidents: list[SimilarIncident] = Field(default_factory=list)
    timeline: list[TimelineEvent]
    root_cause: RootCause
    remediation: list[Remediation]
    trace_analyses: list[TraceAnalysis] = Field(default_factory=list)
    stateful_dependencies: list[StatefulDependencyAnalysis] = Field(default_factory=list)
    query_budget: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CopilotQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class CopilotAnswer(BaseModel):
    answer: str
    insufficient_evidence: bool
    evidence_ids: list[str] = Field(default_factory=list)
    mode: Literal["model", "deterministic"]


class TraceCausalHop(BaseModel):
    service: str
    operation: str
    span_id: str
    parent_span_id: str | None = None
    kind: str = "unspecified"
    status: Literal["ok", "error", "unset"] = "unset"
    duration_ms: float = 0.0
    role: Literal["origin", "propagated", "context"] = "context"
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceAnalysis(BaseModel):
    trace_id: str
    root_service: str
    root_operation: str
    duration_ms: float
    error_count: int = 0
    services: list[str] = Field(default_factory=list)
    causal_chain: list[TraceCausalHop] = Field(default_factory=list)
    candidate_service: str | None = None
    candidate_operation: str | None = None
    candidate_instance: str | None = None
    candidate_reason: str | None = None
    candidate_strength: Literal["weak", "medium", "strong"] = "weak"
    source_query: str | None = None
    deployment_context: dict[str, dict[str, Any]] = Field(default_factory=dict)


class StatefulDependencySignal(BaseModel):
    name: str
    value: float | None = None
    unit: str | None = None
    status: Literal["normal", "warning", "critical", "unknown"] = "unknown"
    threshold: float | None = None
    query: str | None = None
    description: str = ""


class StatefulDependencyAnalysis(BaseModel):
    dependency_id: str
    kind: Literal["postgresql", "kafka", "redis"]
    name: str
    target: str | None = None
    health: Literal["healthy", "degraded", "critical", "unknown"] = "unknown"
    causal_role: Literal["origin", "supporting", "context"] = "context"
    cause: str | None = None
    cause_strength: Literal["weak", "medium", "strong"] = "weak"
    hypothesis_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    signals: list[StatefulDependencySignal] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertmanagerAlert(BaseModel):
    status: str
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: datetime | None = None
    endsAt: datetime | None = None


class AlertmanagerWebhook(BaseModel):
    receiver: str | None = None
    status: str | None = None
    alerts: list[AlertmanagerAlert]
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)



class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"audit-{uuid4().hex[:12]}")
    incident_id: str | None = None
    tenant_id: str
    actor: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceCapability(BaseModel):
    source: str
    status: Literal["healthy", "degraded", "unavailable", "disabled"]
    detail: str = ""
    cluster: str | None = None
    latency_ms: float | None = None


class CapabilityReport(BaseModel):
    cluster: str
    tenant_id: str
    auth_enabled: bool
    notifications_enabled: bool
    persistence: str
    sources: list[SourceCapability]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IncidentExport(BaseModel):
    schema_version: str = "ai-sre-rca.export.v1"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    incident_id: str
    tenant_id: str
    evidence_integrity_ok: bool
    evidence_hashes: dict[str, str]
    investigation: InvestigationResult
