from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.awareness.change_events import augment_with_change_events
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
    tombstoned_at: datetime | None = None
    tombstone_snapshot_id: str | None = None
    tombstone_source: str | None = None


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
    tombstoned_at: datetime | None = None
    tombstone_snapshot_id: str | None = None
    tombstone_source: str | None = None


class AwarenessObservation(BaseModel):
    id: str
    tenant_id: str
    cluster: str
    incident_id: str
    target_type: Literal["entity", "relationship", "inventory_snapshot"]
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

    source_entities, source_edges = augment_with_change_events(result)

    for entity in source_entities:
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

    for edge in source_edges:
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
