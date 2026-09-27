from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    CONFIRMED = "confirmed"
    RULED_OUT = "ruled_out"
    INCONCLUSIVE = "inconclusive"


class IncidentInput(BaseModel):
    title: str
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


class SimilarIncident(BaseModel):
    incident_id: str
    title: str
    root_cause: str
    similarity: float
    occurred_at: datetime
    cluster: str
    namespace: str | None = None


class InvestigationResult(BaseModel):
    incident_id: str
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
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    candidate_reason: str | None = None
    candidate_strength: Literal["weak", "medium", "strong"] = "weak"
    source_query: str | None = None
    deployment_context: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
