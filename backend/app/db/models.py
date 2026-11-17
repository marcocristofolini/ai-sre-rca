from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.memory.embedding import EMBEDDING_DIM


class Base(DeclarativeBase):
    pass


class InvestigationRecord(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"inc-{uuid4().hex[:12]}")
    status: Mapped[str] = mapped_column(String(32), index=True)
    tenant_id: Mapped[str] = mapped_column(String(255), default="default", index=True)
    title: Mapped[str] = mapped_column(Text)
    alert_name: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    pod: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    root_cause: Mapped[str] = mapped_column(Text)
    fingerprint_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(EMBEDDING_DIM))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)



class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    actor: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(255), index=True)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )


class AlertOccurrenceRecord(Base):
    __tablename__ = "alert_occurrences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cluster", "fingerprint", "starts_at", name="uq_alert_occurrence_identity"),
        Index("idx_alert_occurrences_group", "tenant_id", "group_key", "starts_at"),
        Index("idx_alert_occurrences_seen", "tenant_id", "last_received_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255))
    cluster: Mapped[str] = mapped_column(String(255))
    namespace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_kind: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(255))
    group_key: Mapped[str] = mapped_column(String(64))
    group_id: Mapped[str] = mapped_column(String(64), index=True)
    alert_name: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notifications: Mapped[int] = mapped_column(Integer, default=1)


class RemediationPlanRecord(Base):
    __tablename__ = "remediation_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    namespace: Mapped[str] = mapped_column(String(255), index=True)
    deployment: Mapped[str] = mapped_column(String(255), index=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OrchestrationJobRecord(Base):
    __tablename__ = "orchestration_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="orchestration_jobs_tenant_idem_uq"),
        Index("orchestration_jobs_claim_idx", "status", "lease_expires_at", "submitted_at"),
        Index("orchestration_jobs_tenant_created_idx", "tenant_id", "submitted_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), index=True)
    cluster: Mapped[str] = mapped_column(String(255))
    incident_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class OrchestrationProgressEventRecord(Base):
    __tablename__ = "orchestration_progress_events"
    __table_args__ = (Index("orchestration_progress_events_created_idx", "job_id", "sequence"),)

    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("orchestration_jobs.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AwarenessEntityRecord(Base):
    __tablename__ = "awareness_entities"
    __table_args__ = (
        Index(
            "awareness_entities_scope_last_seen_idx",
            "tenant_id",
            "cluster",
            "last_seen",
        ),
        Index(
            "awareness_entities_scope_kind_namespace_idx",
            "tenant_id",
            "cluster",
            "kind",
            "namespace",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    namespace: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_incident_id: Mapped[str] = mapped_column(String(64), index=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    tombstoned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    tombstone_snapshot_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    tombstone_source: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )


class AwarenessRelationshipRecord(Base):
    __tablename__ = "awareness_relationships"
    __table_args__ = (
        Index(
            "awareness_relationships_scope_last_seen_idx",
            "tenant_id",
            "cluster",
            "last_seen",
        ),
        Index(
            "awareness_relationships_scope_endpoints_idx",
            "tenant_id",
            "cluster",
            "source_id",
            "target_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    relationship: Mapped[str] = mapped_column(String(128), index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_incident_id: Mapped[str] = mapped_column(String(64), index=True)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    tombstoned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    tombstone_snapshot_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    tombstone_source: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )


class AwarenessObservationRecord(Base):
    __tablename__ = "awareness_observations"
    __table_args__ = (
        Index(
            "awareness_observations_scope_target_time_idx",
            "tenant_id",
            "cluster",
            "target_id",
            "observed_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(255), index=True)
    cluster: Mapped[str] = mapped_column(String(255), index=True)
    incident_id: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(64), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
