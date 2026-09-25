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


class InvestigationResult(BaseModel):
    incident_id: str
    status: Literal["investigating", "resolved", "inconclusive"]
    incident: IncidentInput
    entities: list[Entity]
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    timeline: list[TimelineEvent]
    root_cause: RootCause
    remediation: list[Remediation]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
