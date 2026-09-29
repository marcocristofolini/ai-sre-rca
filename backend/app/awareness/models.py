from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas import Entity, InvestigationResult


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{sha256(payload).hexdigest()[:24]}"


def canonical_entity_id(
    tenant_id: str,
    cluster: str,
    entity: Entity,
) -> str:
    return _stable_id(
        "ae",
        tenant_id,
        cluster,
        entity.kind.lower(),
        entity.namespace or "",
        entity.name,
    )


def canonical_relationship_id(
    tenant_id: str,
    cluster: str,
    source_id: str,
    relationship: str,
    target_id: str,
) -> str:
    return _stable_id(
        "ar",
        tenant_id,
        cluster,
        source_id,
        relationship,
        target_id,
    )


class AwarenessEntity(BaseModel):
    id: str
    tenant_id: str
    cluster: str
    kind: str
    name: str
    namespace: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    first_seen: datetime
    last_seen: datetime
    last_incident_id: str
    observation_count: int = 1


class AwarenessRelationship(BaseModel):
    id: str
    tenant_id: str
    cluster: str
    source_id: str
    target_id: str
    relationship: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    last_incident_id: str
    observation_count: int = 1


class AwarenessChangeEvent(BaseModel):
    id: str
    tenant_id: str
    cluster: str
    event_type: Literal["deployment_sync", "source_commit"]
    source: Literal["argocd", "github"]
    occurred_at: datetime
    namespace: str | None = None
    revision: str | None = None
    subject_entity_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    incident_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    observation_count: int = 1
    first_observed_at: datetime
    last_observed_at: datetime


class AwarenessObservation(BaseModel):
    id: str
    tenant_id: str
    cluster: str
    incident_id: str
    target_type: Literal["entity", "relationship", "change_event"]
    target_id: str
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    payload: dict[str, Any] = Field(default_factory=dict)


class AwarenessGraphSnapshot(BaseModel):
    tenant_id: str
    cluster: str
    entities: list[AwarenessEntity] = Field(default_factory=list)
    relationships: list[AwarenessRelationship] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def canonicalize_investigation(
    result: InvestigationResult,
) -> tuple[
    list[AwarenessEntity],
    list[AwarenessRelationship],
    list[AwarenessObservation],
]:
    tenant_id = result.incident.tenant_id
    cluster = result.incident.cluster
    observed_at = result.created_at
    local_to_canonical: dict[str, str] = {}
    entities: list[AwarenessEntity] = []
    relationships: list[AwarenessRelationship] = []
    observations: list[AwarenessObservation] = []

    for entity in result.entities:
        canonical_id = canonical_entity_id(tenant_id, cluster, entity)
        local_to_canonical[entity.id] = canonical_id
        persistent = AwarenessEntity(
            id=canonical_id,
            tenant_id=tenant_id,
            cluster=cluster,
            kind=entity.kind,
            name=entity.name,
            namespace=entity.namespace,
            metadata=entity.metadata,
            first_seen=observed_at,
            last_seen=observed_at,
            last_incident_id=result.incident_id,
        )
        entities.append(persistent)
        observations.append(AwarenessObservation(
            id=_stable_id(
                "ao", result.incident_id, "entity", canonical_id
            ),
            tenant_id=tenant_id,
            cluster=cluster,
            incident_id=result.incident_id,
            target_type="entity",
            target_id=canonical_id,
            observed_at=observed_at,
            payload={
                "local_id": entity.id,
                "metadata": entity.metadata,
            },
        ))

    for edge in result.graph_edges:
        source_id = local_to_canonical.get(edge.source)
        target_id = local_to_canonical.get(edge.target)
        if not source_id or not target_id:
            continue
        relationship_id = canonical_relationship_id(
            tenant_id,
            cluster,
            source_id,
            edge.relationship,
            target_id,
        )
        persistent = AwarenessRelationship(
            id=relationship_id,
            tenant_id=tenant_id,
            cluster=cluster,
            source_id=source_id,
            target_id=target_id,
            relationship=edge.relationship,
            metadata=edge.metadata,
            evidence_ids=edge.evidence_ids,
            first_seen=observed_at,
            last_seen=observed_at,
            last_incident_id=result.incident_id,
        )
        relationships.append(persistent)
        observations.append(AwarenessObservation(
            id=_stable_id(
                "ao", result.incident_id, "relationship", relationship_id
            ),
            tenant_id=tenant_id,
            cluster=cluster,
            incident_id=result.incident_id,
            target_type="relationship",
            target_id=relationship_id,
            observed_at=observed_at,
            payload={
                "metadata": edge.metadata,
                "evidence_ids": edge.evidence_ids,
            },
        ))

    return entities, relationships, observations



def canonical_change_event_id(
    tenant_id: str,
    cluster: str,
    *,
    event_type: str,
    source: str,
    identity: str,
    occurred_at: datetime,
) -> str:
    normalized = occurred_at.astimezone(timezone.utc).isoformat()
    return _stable_id(
        "ac",
        tenant_id,
        cluster,
        event_type,
        source,
        identity,
        normalized,
    )


def extract_change_events(
    result: InvestigationResult,
) -> list[AwarenessChangeEvent]:
    """Compile immutable operational change events from verified RCA evidence.

    Events are temporal context only. They are persisted separately from graph
    entities/relationships and are never interpreted as causal impact edges.
    """

    tenant_id = result.incident.tenant_id
    cluster = result.incident.cluster
    local_to_canonical = {
        entity.id: canonical_entity_id(tenant_id, cluster, entity)
        for entity in result.entities
    }

    service_ids = sorted(
        canonical_id
        for entity in result.entities
        if entity.kind.lower() == "service"
        for canonical_id in [local_to_canonical[entity.id]]
    )
    deployment_ids = sorted(
        canonical_id
        for entity in result.entities
        if entity.kind.lower() == "deployment"
        for canonical_id in [local_to_canonical[entity.id]]
    )
    argocd_ids = sorted(
        canonical_id
        for entity in result.entities
        if entity.kind.lower() == "argocdapplication"
        for canonical_id in [local_to_canonical[entity.id]]
    )
    git_ids = sorted(
        canonical_id
        for entity in result.entities
        if entity.kind.lower() == "gitrepository"
        for canonical_id in [local_to_canonical[entity.id]]
    )

    events: list[AwarenessChangeEvent] = []
    for evidence in result.evidence:
        if evidence.source == "argocd" and evidence.category == "change-correlation":
            application = str(evidence.facts.get("application") or "argocd")
            revision = str(evidence.facts.get("revision") or "") or None
            identity = "|".join([
                application,
                revision or "unknown",
            ])
            subject_ids = sorted(set([
                *deployment_ids,
                *service_ids,
                *argocd_ids,
                *git_ids,
            ]))
            events.append(AwarenessChangeEvent(
                id=canonical_change_event_id(
                    tenant_id,
                    cluster,
                    event_type="deployment_sync",
                    source="argocd",
                    identity=identity,
                    occurred_at=evidence.observed_at,
                ),
                tenant_id=tenant_id,
                cluster=cluster,
                event_type="deployment_sync",
                source="argocd",
                occurred_at=evidence.observed_at,
                namespace=result.incident.namespace,
                revision=revision,
                subject_entity_ids=subject_ids,
                evidence_ids=[evidence.id],
                incident_ids=[result.incident_id],
                metadata={
                    "application": application,
                    "repo_url": evidence.facts.get("repo_url"),
                    "sync_status": evidence.facts.get("sync_status"),
                    "health": evidence.facts.get("health"),
                    "operation_phase": evidence.facts.get("operation_phase"),
                    "delta_minutes": evidence.facts.get("delta_minutes"),
                    "raw_reference": evidence.raw_reference,
                },
                first_observed_at=result.created_at,
                last_observed_at=result.created_at,
            ))
            continue

        if evidence.source == "github" and evidence.category == "source-change":
            revision = str(
                evidence.facts.get("sha")
                or evidence.facts.get("revision")
                or ""
            ) or None
            if not revision:
                continue
            subject_ids = sorted(set([
                *deployment_ids,
                *service_ids,
                *git_ids,
            ]))
            events.append(AwarenessChangeEvent(
                id=canonical_change_event_id(
                    tenant_id,
                    cluster,
                    event_type="source_commit",
                    source="github",
                    identity=revision,
                    occurred_at=evidence.observed_at,
                ),
                tenant_id=tenant_id,
                cluster=cluster,
                event_type="source_commit",
                source="github",
                occurred_at=evidence.observed_at,
                namespace=result.incident.namespace,
                revision=revision,
                subject_entity_ids=subject_ids,
                evidence_ids=[evidence.id],
                incident_ids=[result.incident_id],
                metadata={
                    "message": evidence.facts.get("message"),
                    "author": evidence.facts.get("author"),
                    "url": evidence.facts.get("url"),
                    "raw_reference": evidence.raw_reference,
                },
                first_observed_at=result.created_at,
                last_observed_at=result.created_at,
            ))

    return events
