from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.awareness.models import (
    AwarenessEntity,
    AwarenessGraphSnapshot,
    AwarenessRelationship,
)


Freshness = Literal["fresh", "stale"]


class AwarenessTopologyRepository(Protocol):
    async def find_awareness_entities(
        self,
        tenant_id: str,
        cluster: str,
        *,
        name: str | None = None,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[AwarenessEntity]: ...

    async def get_awareness_entity(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
    ) -> AwarenessEntity | None: ...

    async def get_awareness_neighbors(
        self,
        entity_ids: set[str],
        tenant_id: str,
        cluster: str,
        *,
        limit: int,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]: ...


class ReconciledEntity(AwarenessEntity):
    freshness: Freshness
    age_seconds: float
    stale_since: datetime | None = None
    depth: int | None = None


class ReconciledRelationship(AwarenessRelationship):
    freshness: Freshness
    age_seconds: float
    stale_since: datetime | None = None


class ReconciledGraphSnapshot(BaseModel):
    tenant_id: str
    cluster: str
    entities: list[ReconciledEntity] = Field(default_factory=list)
    relationships: list[ReconciledRelationship] = Field(default_factory=list)
    stale_after_minutes: int
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ServiceTopologyProjection(BaseModel):
    tenant_id: str
    cluster: str
    service_name: str
    namespace: str | None = None
    root: ReconciledEntity
    entities: list[ReconciledEntity] = Field(default_factory=list)
    relationships: list[ReconciledRelationship] = Field(default_factory=list)
    requested_depth: int
    effective_depth: int
    max_nodes: int
    max_edges: int
    truncated: bool = False
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class BlastRadiusPathStep(BaseModel):
    relationship_id: str
    relationship: str
    from_id: str
    to_id: str
    stored_source_id: str
    stored_target_id: str


class BlastRadiusImpact(BaseModel):
    entity: ReconciledEntity
    depth: int
    path: list[BlastRadiusPathStep] = Field(default_factory=list)


class BlastRadiusResult(BaseModel):
    tenant_id: str
    cluster: str
    source: ReconciledEntity
    impacts: list[BlastRadiusImpact] = Field(default_factory=list)
    requested_depth: int
    effective_depth: int
    max_nodes: int
    max_edges: int
    truncated: bool = False
    traversed_relationship_types: list[str] = Field(default_factory=list)
    ignored_relationship_types: list[str] = Field(default_factory=list)
    skipped_stale_relationship_ids: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AwarenessEntityNotFoundError(LookupError):
    pass


class AmbiguousServiceError(LookupError):
    pass


# Relationship semantics are explicit because graph direction does not always
# match impact direction. Unknown relationships are never traversed for blast
# radius; they remain visible in topology projections only.
IMPACT_DIRECTION: dict[str, Literal["forward", "reverse", "both"]] = {
    "calls": "reverse",
    "depends_on": "reverse",
    "reads_from": "reverse",
    "writes_to": "reverse",
    "consumes_from": "reverse",
    "scheduled_on": "reverse",
    "owned_by": "reverse",
    "managed_by": "reverse",
    "syncs_from": "reverse",
    "represented_by": "reverse",
    "runs_as": "reverse",
    "serves": "forward",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _freshness(
    last_seen: datetime,
    *,
    as_of: datetime,
    stale_after_minutes: int,
) -> tuple[Freshness, float, datetime | None]:
    normalized_last_seen = _utc(last_seen)
    normalized_as_of = _utc(as_of)
    age_seconds = max(
        0.0,
        (normalized_as_of - normalized_last_seen).total_seconds(),
    )
    threshold = timedelta(minutes=stale_after_minutes)
    is_stale = normalized_as_of - normalized_last_seen > threshold
    return (
        "stale" if is_stale else "fresh",
        age_seconds,
        normalized_last_seen + threshold if is_stale else None,
    )


def reconcile_entity(
    entity: AwarenessEntity,
    *,
    as_of: datetime,
    stale_after_minutes: int,
    depth: int | None = None,
) -> ReconciledEntity:
    freshness, age_seconds, stale_since = _freshness(
        entity.last_seen,
        as_of=as_of,
        stale_after_minutes=stale_after_minutes,
    )
    return ReconciledEntity(
        **entity.model_dump(),
        freshness=freshness,
        age_seconds=age_seconds,
        stale_since=stale_since,
        depth=depth,
    )


def reconcile_relationship(
    relationship: AwarenessRelationship,
    *,
    as_of: datetime,
    stale_after_minutes: int,
) -> ReconciledRelationship:
    freshness, age_seconds, stale_since = _freshness(
        relationship.last_seen,
        as_of=as_of,
        stale_after_minutes=stale_after_minutes,
    )
    return ReconciledRelationship(
        **relationship.model_dump(),
        freshness=freshness,
        age_seconds=age_seconds,
        stale_since=stale_since,
    )


def reconcile_snapshot(
    snapshot: AwarenessGraphSnapshot,
    *,
    stale_after_minutes: int,
    as_of: datetime | None = None,
) -> ReconciledGraphSnapshot:
    now = as_of or datetime.now(timezone.utc)
    return ReconciledGraphSnapshot(
        tenant_id=snapshot.tenant_id,
        cluster=snapshot.cluster,
        entities=[
            reconcile_entity(
                entity,
                as_of=now,
                stale_after_minutes=stale_after_minutes,
            )
            for entity in snapshot.entities
        ],
        relationships=[
            reconcile_relationship(
                relationship,
                as_of=now,
                stale_after_minutes=stale_after_minutes,
            )
            for relationship in snapshot.relationships
        ],
        stale_after_minutes=stale_after_minutes,
        generated_at=now,
    )


async def _bounded_neighborhood(
    repo: AwarenessTopologyRepository,
    *,
    root: AwarenessEntity,
    tenant_id: str,
    cluster: str,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
) -> tuple[
    dict[str, AwarenessEntity],
    dict[str, AwarenessRelationship],
    dict[str, int],
    bool,
]:
    entities: dict[str, AwarenessEntity] = {root.id: root}
    relationships: dict[str, AwarenessRelationship] = {}
    depth_by_id: dict[str, int] = {root.id: 0}
    frontier: set[str] = {root.id}
    truncated = False

    for depth in range(max_depth):
        if not frontier:
            break
        remaining_edges = max_edges - len(relationships)
        if remaining_edges <= 0:
            truncated = True
            break

        neighbors, adjacent = await repo.get_awareness_neighbors(
            frontier,
            tenant_id,
            cluster,
            limit=remaining_edges + 1,
        )
        if len(adjacent) > remaining_edges:
            truncated = True
            adjacent = adjacent[:remaining_edges]
        neighbor_map = {item.id: item for item in neighbors}
        next_frontier: set[str] = set()

        for relationship in adjacent:
            if relationship.id in relationships:
                continue
            candidate_ids = (
                relationship.source_id,
                relationship.target_id,
            )
            missing = [
                candidate_id
                for candidate_id in candidate_ids
                if candidate_id not in entities
            ]
            if len(entities) + len(missing) > max_nodes:
                truncated = True
                continue

            for candidate_id in missing:
                candidate = neighbor_map.get(candidate_id)
                if candidate is None:
                    truncated = True
                    continue
                entities[candidate_id] = candidate
                depth_by_id[candidate_id] = depth + 1
                next_frontier.add(candidate_id)

            if all(candidate_id in entities for candidate_id in candidate_ids):
                relationships[relationship.id] = relationship

        frontier = next_frontier

    return entities, relationships, depth_by_id, truncated


async def build_service_topology(
    repo: AwarenessTopologyRepository,
    *,
    tenant_id: str,
    cluster: str,
    service_name: str,
    namespace: str | None,
    requested_depth: int,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    stale_after_minutes: int,
    as_of: datetime | None = None,
) -> ServiceTopologyProjection:
    candidates = await repo.find_awareness_entities(
        tenant_id,
        cluster,
        name=service_name,
        namespace=namespace,
        kind="Service",
        limit=3,
    )
    if not candidates:
        raise AwarenessEntityNotFoundError(
            f"service not found: {service_name}"
        )
    if len(candidates) > 1 and namespace is None:
        raise AmbiguousServiceError(
            f"service name is ambiguous across namespaces: {service_name}"
        )

    root = candidates[0]
    effective_depth = min(requested_depth, max_depth)
    entities, relationships, depth_by_id, truncated = await _bounded_neighborhood(
        repo,
        root=root,
        tenant_id=tenant_id,
        cluster=cluster,
        max_depth=effective_depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    truncated = truncated or requested_depth > max_depth

    now = as_of or datetime.now(timezone.utc)
    reconciled_entities = [
        reconcile_entity(
            entity,
            as_of=now,
            stale_after_minutes=stale_after_minutes,
            depth=depth_by_id.get(entity.id),
        )
        for entity in entities.values()
    ]
    reconciled_entities.sort(
        key=lambda item: (
            item.depth if item.depth is not None else 999,
            item.kind.lower(),
            item.name,
        )
    )
    reconciled_relationships = [
        reconcile_relationship(
            relationship,
            as_of=now,
            stale_after_minutes=stale_after_minutes,
        )
        for relationship in relationships.values()
    ]
    reconciled_relationships.sort(
        key=lambda item: (item.relationship, item.source_id, item.target_id)
    )

    root_view = next(item for item in reconciled_entities if item.id == root.id)
    return ServiceTopologyProjection(
        tenant_id=tenant_id,
        cluster=cluster,
        service_name=service_name,
        namespace=root.namespace,
        root=root_view,
        entities=reconciled_entities,
        relationships=reconciled_relationships,
        requested_depth=requested_depth,
        effective_depth=effective_depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        truncated=truncated,
        generated_at=now,
    )


def _impact_target(
    relationship: AwarenessRelationship,
    current_id: str,
) -> str | None:
    direction = IMPACT_DIRECTION.get(relationship.relationship)
    if direction is None:
        return None
    if direction in {"forward", "both"} and current_id == relationship.source_id:
        return relationship.target_id
    if direction in {"reverse", "both"} and current_id == relationship.target_id:
        return relationship.source_id
    return None


async def calculate_blast_radius(
    repo: AwarenessTopologyRepository,
    *,
    tenant_id: str,
    cluster: str,
    source_id: str,
    requested_depth: int,
    max_depth: int,
    max_nodes: int,
    max_edges: int,
    stale_after_minutes: int,
    as_of: datetime | None = None,
) -> BlastRadiusResult:
    source = await repo.get_awareness_entity(
        source_id,
        tenant_id,
        cluster,
    )
    if source is None:
        raise AwarenessEntityNotFoundError(
            f"awareness entity not found: {source_id}"
        )

    effective_depth = min(requested_depth, max_depth)
    visited: set[str] = {source.id}
    depth_by_id: dict[str, int] = {source.id: 0}
    entity_by_id: dict[str, AwarenessEntity] = {source.id: source}
    path_by_id: dict[str, list[BlastRadiusPathStep]] = {source.id: []}
    queue: deque[str] = deque([source.id])
    traversed_relationships: set[str] = set()
    ignored_relationships: set[str] = set()
    skipped_stale_relationship_ids: set[str] = set()
    examined_edges: set[str] = set()
    truncated = requested_depth > max_depth
    traversal_as_of = as_of or datetime.now(timezone.utc)

    while queue:
        current_id = queue.popleft()
        current_depth = depth_by_id[current_id]
        if current_depth >= effective_depth:
            continue
        remaining_edges = max_edges - len(examined_edges)
        if remaining_edges <= 0:
            truncated = True
            break

        neighbors, adjacent = await repo.get_awareness_neighbors(
            {current_id},
            tenant_id,
            cluster,
            limit=remaining_edges + 1,
        )
        if len(adjacent) > remaining_edges:
            truncated = True
            adjacent = adjacent[:remaining_edges]
        neighbor_map = {item.id: item for item in neighbors}

        for relationship in adjacent:
            relationship_freshness, _, _ = _freshness(
                relationship.last_seen,
                as_of=traversal_as_of,
                stale_after_minutes=stale_after_minutes,
            )
            if relationship_freshness == "stale":
                if relationship.id not in examined_edges:
                    examined_edges.add(relationship.id)
                    skipped_stale_relationship_ids.add(relationship.id)
                continue

            if relationship.relationship not in IMPACT_DIRECTION:
                if relationship.id not in examined_edges:
                    examined_edges.add(relationship.id)
                    ignored_relationships.add(relationship.relationship)
                continue

            target_id = _impact_target(relationship, current_id)
            if target_id is None:
                # A directed relationship may first be observed from the
                # non-propagating endpoint. Do not globally consume it yet;
                # the causal endpoint can still be reached through another path.
                continue
            if relationship.id in examined_edges:
                continue
            examined_edges.add(relationship.id)
            traversed_relationships.add(relationship.relationship)
            if target_id in visited:
                continue
            if len(visited) >= max_nodes:
                truncated = True
                continue

            target = neighbor_map.get(target_id)
            if target is None:
                truncated = True
                continue

            visited.add(target_id)
            entity_by_id[target_id] = target
            depth_by_id[target_id] = current_depth + 1
            path_by_id[target_id] = [
                *path_by_id[current_id],
                BlastRadiusPathStep(
                    relationship_id=relationship.id,
                    relationship=relationship.relationship,
                    from_id=current_id,
                    to_id=target_id,
                    stored_source_id=relationship.source_id,
                    stored_target_id=relationship.target_id,
                ),
            ]
            queue.append(target_id)

    now = traversal_as_of
    source_view = reconcile_entity(
        source,
        as_of=now,
        stale_after_minutes=stale_after_minutes,
        depth=0,
    )
    impacts = [
        BlastRadiusImpact(
            entity=reconcile_entity(
                entity_by_id[target_id],
                as_of=now,
                stale_after_minutes=stale_after_minutes,
                depth=depth_by_id[target_id],
            ),
            depth=depth_by_id[target_id],
            path=path_by_id[target_id],
        )
        for target_id in visited
        if target_id != source.id
    ]
    impacts.sort(key=lambda item: (item.depth, item.entity.kind, item.entity.name))

    return BlastRadiusResult(
        tenant_id=tenant_id,
        cluster=cluster,
        source=source_view,
        impacts=impacts,
        requested_depth=requested_depth,
        effective_depth=effective_depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        truncated=truncated,
        traversed_relationship_types=sorted(traversed_relationships),
        ignored_relationship_types=sorted(ignored_relationships),
        skipped_stale_relationship_ids=sorted(
            skipped_stale_relationship_ids
        ),
        generated_at=now,
    )
