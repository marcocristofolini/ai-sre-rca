from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, Index, Integer, JSON, String, Text, func
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
