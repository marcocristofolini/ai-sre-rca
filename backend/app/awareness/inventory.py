from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.awareness.models import (
    AwarenessEntity,
    AwarenessRelationship,
    canonical_entity_id,
    canonical_relationship_id,
)
from app.schemas import Entity


def _stable_inventory_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-{sha256(payload).hexdigest()[:24]}"


class InventoryCoverage(BaseModel):
    kinds: list[str] = Field(min_length=1)
    namespaces: list[str] | None = None
    relationship_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scope(self):
        kinds = [item.strip() for item in self.kinds if item.strip()]
        if not kinds:
            raise ValueError("inventory coverage requires at least one kind")
        if len({item.casefold() for item in kinds}) != len(kinds):
            raise ValueError("inventory coverage kinds must be unique")
        if self.namespaces is not None:
            namespaces = [item.strip() for item in self.namespaces if item.strip()]
            if not namespaces:
                raise ValueError(
                    "inventory namespaces cannot be empty; use null for cluster-wide coverage"
                )
            if len(set(namespaces)) != len(namespaces):
                raise ValueError("inventory namespaces must be unique")
        relationship_types = [
            item.strip() for item in self.relationship_types if item.strip()
        ]
        if len(set(relationship_types)) != len(relationship_types):
            raise ValueError("inventory relationship types must be unique")
        return self


class InventoryResourceRef(BaseModel):
    kind: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=512)
    namespace: str | None = Field(default=None, max_length=255)


class InventoryResource(InventoryResourceRef):
    metadata: dict[str, Any] = Field(default_factory=dict)


class InventoryRelationship(BaseModel):
    source: InventoryResourceRef
    target: InventoryResourceRef
    relationship: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuthoritativeInventoryRequest(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128)
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    complete: bool
    coverage: InventoryCoverage
    resources: list[InventoryResource] = Field(default_factory=list)
    relationships: list[InventoryRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_authoritative_snapshot(self):
        if not self.complete:
            raise ValueError(
                "authoritative reconciliation requires a complete inventory snapshot"
            )

        resource_keys = {resource_identity(item) for item in self.resources}
        if len(resource_keys) != len(self.resources):
            raise ValueError("inventory resources must be unique")

        for resource in self.resources:
            if not resource_in_coverage(
                resource.kind,
                resource.namespace,
                self.coverage,
            ):
                raise ValueError(
                    "inventory resource falls outside declared coverage: "
                    f"{resource.kind}/{resource.namespace or '-'}"
                    f"/{resource.name}"
                )

        relationship_keys: set[tuple[str, str, str]] = set()
        allowed_relationships = set(self.coverage.relationship_types)
        for relationship in self.relationships:
            if relationship.relationship not in allowed_relationships:
                raise ValueError(
                    "inventory relationship is not declared authoritative in coverage: "
                    f"{relationship.relationship}"
                )
            source_key = resource_identity(relationship.source)
            target_key = resource_identity(relationship.target)
            if source_key not in resource_keys or target_key not in resource_keys:
                raise ValueError(
                    "inventory relationship endpoints must both be present in resources"
                )
            key = (
                "|".join(source_key),
                relationship.relationship,
                "|".join(target_key),
            )
            if key in relationship_keys:
                raise ValueError("inventory relationships must be unique")
            relationship_keys.add(key)
        return self


class AuthoritativeInventorySnapshot(AuthoritativeInventoryRequest):
    tenant_id: str = Field(min_length=1, max_length=255)
    cluster: str = Field(min_length=1, max_length=255)


class InventoryReconciliationResult(BaseModel):
    snapshot_id: str
    source: str
    tenant_id: str
    cluster: str
    observed_at: datetime
    resources_observed: int
    relationships_observed: int
    entities_created: int = 0
    entities_refreshed: int = 0
    entities_reactivated: int = 0
    entities_tombstoned: int = 0
    relationships_created: int = 0
    relationships_refreshed: int = 0
    relationships_reactivated: int = 0
    relationships_tombstoned: int = 0
    skipped_newer_entities: int = 0
    skipped_newer_relationships: int = 0
    duplicate_observations: int = 0


def resource_identity(
    resource: InventoryResourceRef,
) -> tuple[str, str, str]:
    return (
        resource.kind.casefold(),
        resource.namespace or "",
        resource.name,
    )


def resource_in_coverage(
    kind: str,
    namespace: str | None,
    coverage: InventoryCoverage,
) -> bool:
    covered_kinds = {item.casefold() for item in coverage.kinds}
    if kind.casefold() not in covered_kinds:
        return False
    if coverage.namespaces is None:
        return True
    return namespace in set(coverage.namespaces)


def inventory_entity_id(
    snapshot: AuthoritativeInventorySnapshot,
    resource: InventoryResourceRef,
) -> str:
    return canonical_entity_id(
        snapshot.tenant_id,
        snapshot.cluster,
        Entity(
            id="inventory",
            kind=resource.kind,
            name=resource.name,
            namespace=resource.namespace,
        ),
    )


def materialize_inventory_entities(
    snapshot: AuthoritativeInventorySnapshot,
) -> dict[str, AwarenessEntity]:
    output: dict[str, AwarenessEntity] = {}
    origin = f"inventory:{snapshot.snapshot_id}"
    for resource in snapshot.resources:
        entity_id = inventory_entity_id(snapshot, resource)
        output[entity_id] = AwarenessEntity(
            id=entity_id,
            tenant_id=snapshot.tenant_id,
            cluster=snapshot.cluster,
            kind=resource.kind,
            name=resource.name,
            namespace=resource.namespace,
            metadata=dict(resource.metadata),
            first_seen=snapshot.observed_at,
            last_seen=snapshot.observed_at,
            last_incident_id=origin,
        )
    return output


def materialize_inventory_relationships(
    snapshot: AuthoritativeInventorySnapshot,
) -> dict[str, AwarenessRelationship]:
    output: dict[str, AwarenessRelationship] = {}
    origin = f"inventory:{snapshot.snapshot_id}"
    for item in snapshot.relationships:
        source_id = inventory_entity_id(snapshot, item.source)
        target_id = inventory_entity_id(snapshot, item.target)
        relationship_id = canonical_relationship_id(
            snapshot.tenant_id,
            snapshot.cluster,
            source_id,
            item.relationship,
            target_id,
        )
        output[relationship_id] = AwarenessRelationship(
            id=relationship_id,
            tenant_id=snapshot.tenant_id,
            cluster=snapshot.cluster,
            source_id=source_id,
            target_id=target_id,
            relationship=item.relationship,
            metadata=dict(item.metadata),
            evidence_ids=[],
            first_seen=snapshot.observed_at,
            last_seen=snapshot.observed_at,
            last_incident_id=origin,
        )
    return output


def inventory_observation_id(
    source: str,
    snapshot_id: str,
    target_type: str,
    target_id: str,
    action: str,
) -> str:
    return _stable_inventory_id(
        "ao",
        "inventory",
        source,
        snapshot_id,
        target_type,
        target_id,
        action,
    )
