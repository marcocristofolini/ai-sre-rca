from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryReconciliationResult,
    inventory_observation_id,
    materialize_inventory_entities,
    materialize_inventory_relationships,
    resource_in_coverage,
)
from app.awareness.models import (
    AwarenessEntity,
    AwarenessGraphSnapshot,
    AwarenessObservation,
    AwarenessRelationship,
    canonicalize_investigation,
)
from app.db.models import (
    AuditEventRecord,
    AwarenessEntityRecord,
    AwarenessObservationRecord,
    AwarenessRelationshipRecord,
    Base,
    InvestigationRecord,
    RemediationPlanRecord,
)
from app.memory.embedding import cosine_similarity, hash_embedding
from app.schemas import AuditEvent, IncidentInput, InvestigationResult, RemediationPlan, SimilarIncident


class InvestigationRepository(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, result: InvestigationResult) -> None: ...
    async def get(self, incident_id: str, tenant_id: str | None = None) -> InvestigationResult | None: ...
    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]: ...
    async def get_awareness_graph(
        self,
        tenant_id: str,
        cluster: str,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> AwarenessGraphSnapshot: ...
    async def get_awareness_history(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
        limit: int = 100,
    ) -> list[AwarenessObservation]: ...
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
    async def reconcile_authoritative_inventory(
        self,
        snapshot: AuthoritativeInventorySnapshot,
    ) -> InventoryReconciliationResult: ...
    async def save_remediation_plan(self, plan: RemediationPlan) -> None: ...
    async def get_remediation_plan(
        self,
        plan_id: str,
        tenant_id: str | None = None,
    ) -> RemediationPlan | None: ...
    async def save_remediation_plan_if_status(
        self,
        plan: RemediationPlan,
        expected_status: str,
    ) -> bool: ...
    async def list_remediation_plans(
        self,
        tenant_id: str,
        incident_id: str | None = None,
        limit: int = 50,
    ) -> list[RemediationPlan]: ...
    async def append_audit(self, event: AuditEvent) -> None: ...
    async def list_audit(self, incident_id: str, tenant_id: str) -> list[AuditEvent]: ...
    async def purge_expired(
        self,
        incident_days: int,
        audit_days: int,
        tenant_id: str | None = None,
    ) -> dict[str, int]: ...
    async def close(self) -> None: ...


def incident_fingerprint(incident: IncidentInput, root_cause: str | None = None) -> str:
    labels = " ".join(f"{k}={v}" for k, v in sorted(incident.labels.items()))
    return " ".join(filter(None, [
        incident.tenant_id,
        incident.alert_name,
        incident.title,
        incident.cluster,
        incident.namespace or "",
        incident.pod or "",
        incident.node or "",
        labels,
        root_cause or "",
    ]))


class MemoryRepository:
    def __init__(self):
        self._items: dict[str, InvestigationResult] = {}
        self._remediation: dict[str, RemediationPlan] = {}
        self._audit: list[AuditEvent] = []
        self._awareness_entities: dict[str, AwarenessEntity] = {}
        self._awareness_relationships: dict[str, AwarenessRelationship] = {}
        self._awareness_observations: dict[str, AwarenessObservation] = {}

    async def initialize(self) -> None:
        return None

    async def save(self, result: InvestigationResult) -> None:
        self._items[result.incident_id] = result
        self._ingest_awareness(result)

    def _ingest_awareness(self, result: InvestigationResult) -> None:
        entities, relationships, observations = canonicalize_investigation(result)
        observation_by_target = {item.target_id: item for item in observations}

        for entity in entities:
            observation = observation_by_target[entity.id]
            if observation.id in self._awareness_observations:
                continue
            current = self._awareness_entities.get(entity.id)
            if current is None:
                self._awareness_entities[entity.id] = entity.model_copy(deep=True)
            else:
                current.first_seen = min(current.first_seen, entity.first_seen)
                if entity.last_seen >= current.last_seen:
                    current.last_seen = entity.last_seen
                    current.last_incident_id = entity.last_incident_id
                    current.metadata = dict(entity.metadata)
                    if (
                        current.tombstoned_at is not None
                        and entity.last_seen >= current.tombstoned_at
                    ):
                        current.tombstoned_at = None
                        current.tombstone_snapshot_id = None
                        current.tombstone_source = None
                current.observation_count += 1
            self._awareness_observations[observation.id] = observation.model_copy(deep=True)

        for relationship in relationships:
            observation = observation_by_target[relationship.id]
            if observation.id in self._awareness_observations:
                continue
            current = self._awareness_relationships.get(relationship.id)
            if current is None:
                self._awareness_relationships[relationship.id] = relationship.model_copy(deep=True)
            else:
                current.first_seen = min(current.first_seen, relationship.first_seen)
                if relationship.last_seen >= current.last_seen:
                    current.last_seen = relationship.last_seen
                    current.last_incident_id = relationship.last_incident_id
                    current.metadata = dict(relationship.metadata)
                    current.evidence_ids = list(relationship.evidence_ids)
                    if (
                        current.tombstoned_at is not None
                        and relationship.last_seen >= current.tombstoned_at
                    ):
                        current.tombstoned_at = None
                        current.tombstone_snapshot_id = None
                        current.tombstone_source = None
                current.observation_count += 1
            self._awareness_observations[observation.id] = observation.model_copy(deep=True)

    async def get(
        self,
        incident_id: str,
        tenant_id: str | None = None,
    ) -> InvestigationResult | None:
        result = self._items.get(incident_id)
        if result is None:
            return None
        if tenant_id is not None and result.incident.tenant_id != tenant_id:
            return None
        return result

    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]:
        query = hash_embedding(incident_fingerprint(incident))
        ranked: list[tuple[float, InvestigationResult]] = []
        for result in self._items.values():
            if result.incident.tenant_id != incident.tenant_id:
                continue
            vector = hash_embedding(incident_fingerprint(result.incident, result.root_cause.title))
            ranked.append((cosine_similarity(query, vector), result))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            SimilarIncident(
                incident_id=result.incident_id,
                title=result.incident.title,
                root_cause=result.root_cause.title,
                similarity=round(max(0.0, score), 4),
                occurred_at=result.created_at,
                cluster=result.incident.cluster,
                namespace=result.incident.namespace,
            )
            for score, result in ranked[:limit]
            if score > 0.05
        ]

    async def get_awareness_graph(
        self,
        tenant_id: str,
        cluster: str,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> AwarenessGraphSnapshot:
        max_items = max(1, min(limit, 2000))
        entities = [
            item.model_copy(deep=True)
            for item in self._awareness_entities.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and (namespace is None or item.namespace == namespace)
            and (kind is None or item.kind.lower() == kind.lower())
        ]
        entities.sort(key=lambda item: item.last_seen, reverse=True)
        entities = entities[:max_items]
        entity_ids = {item.id for item in entities}
        relationships = [
            item.model_copy(deep=True)
            for item in self._awareness_relationships.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and item.source_id in entity_ids
            and item.target_id in entity_ids
        ]
        relationships.sort(key=lambda item: item.last_seen, reverse=True)
        relationships = relationships[: max_items * 4]
        return AwarenessGraphSnapshot(
            tenant_id=tenant_id,
            cluster=cluster,
            entities=entities,
            relationships=relationships,
        )

    async def get_awareness_history(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
        limit: int = 100,
    ) -> list[AwarenessObservation]:
        items = [
            item.model_copy(deep=True)
            for item in self._awareness_observations.values()
            if item.target_id == target_id
            and item.tenant_id == tenant_id
            and item.cluster == cluster
        ]
        items.sort(key=lambda item: item.observed_at, reverse=True)
        return items[:max(1, min(limit, 1000))]

    async def find_awareness_entities(
        self,
        tenant_id: str,
        cluster: str,
        *,
        name: str | None = None,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[AwarenessEntity]:
        items = [
            item.model_copy(deep=True)
            for item in self._awareness_entities.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and (name is None or item.name == name)
            and (namespace is None or item.namespace == namespace)
            and (kind is None or item.kind.lower() == kind.lower())
            and item.tombstoned_at is None
        ]
        items.sort(key=lambda item: item.last_seen, reverse=True)
        return items[:max(1, min(limit, 500))]

    async def get_awareness_entity(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
    ) -> AwarenessEntity | None:
        item = self._awareness_entities.get(target_id)
        if item is None:
            return None
        if item.tenant_id != tenant_id or item.cluster != cluster:
            return None
        return item.model_copy(deep=True)

    async def get_awareness_neighbors(
        self,
        entity_ids: set[str],
        tenant_id: str,
        cluster: str,
        *,
        limit: int,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]:
        if not entity_ids:
            return [], []
        bounded_limit = max(1, min(limit, 8001))
        relationships = [
            item.model_copy(deep=True)
            for item in self._awareness_relationships.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and (
                item.source_id in entity_ids
                or item.target_id in entity_ids
            )
        ]
        relationships.sort(key=lambda item: item.last_seen, reverse=True)
        relationships = relationships[:bounded_limit]
        neighbor_ids = {
            endpoint
            for relationship in relationships
            for endpoint in (relationship.source_id, relationship.target_id)
        }
        entities = [
            item.model_copy(deep=True)
            for item in self._awareness_entities.values()
            if item.id in neighbor_ids
            and item.tenant_id == tenant_id
            and item.cluster == cluster
        ]
        return entities, relationships

    async def reconcile_authoritative_inventory(
        self,
        snapshot: AuthoritativeInventorySnapshot,
    ) -> InventoryReconciliationResult:
        entities = materialize_inventory_entities(snapshot)
        relationships = materialize_inventory_relationships(snapshot)
        origin = f"inventory:{snapshot.snapshot_id}"
        result = InventoryReconciliationResult(
            snapshot_id=snapshot.snapshot_id,
            source=snapshot.source,
            tenant_id=snapshot.tenant_id,
            cluster=snapshot.cluster,
            observed_at=snapshot.observed_at,
            resources_observed=len(entities),
            relationships_observed=len(relationships),
        )

        def append_observation(
            target_type: str,
            target_id: str,
            action: str,
            payload: dict,
        ) -> bool:
            observation_id = inventory_observation_id(
                snapshot.snapshot_id,
                target_type,
                target_id,
                action,
            )
            if observation_id in self._awareness_observations:
                result.duplicate_observations += 1
                return False
            self._awareness_observations[observation_id] = AwarenessObservation(
                id=observation_id,
                tenant_id=snapshot.tenant_id,
                cluster=snapshot.cluster,
                incident_id=origin,
                target_type=target_type,
                target_id=target_id,
                observed_at=snapshot.observed_at,
                payload={
                    "origin": "authoritative_inventory",
                    "source": snapshot.source,
                    "snapshot_id": snapshot.snapshot_id,
                    "action": action,
                    **payload,
                },
            )
            return True

        for entity in entities.values():
            if not append_observation(
                "entity",
                entity.id,
                "observed",
                {"metadata": entity.metadata},
            ):
                continue
            current = self._awareness_entities.get(entity.id)
            if current is None:
                self._awareness_entities[entity.id] = entity.model_copy(deep=True)
                result.entities_created += 1
                continue

            current.first_seen = min(current.first_seen, entity.first_seen)
            current.observation_count += 1
            if entity.last_seen < current.last_seen:
                result.skipped_newer_entities += 1
                continue

            was_tombstoned = current.tombstoned_at is not None
            if (
                was_tombstoned
                and current.tombstoned_at is not None
                and entity.last_seen >= current.tombstoned_at
            ):
                current.tombstoned_at = None
                current.tombstone_snapshot_id = None
                current.tombstone_source = None
                result.entities_reactivated += 1
            else:
                result.entities_refreshed += 1
            current.last_seen = entity.last_seen
            current.last_incident_id = entity.last_incident_id
            current.metadata = dict(entity.metadata)

        covered_entity_ids = {
            item.id
            for item in self._awareness_entities.values()
            if item.tenant_id == snapshot.tenant_id
            and item.cluster == snapshot.cluster
            and item.metadata.get("temporal_role") != "event"
            and resource_in_coverage(
                item.kind,
                item.namespace,
                snapshot.coverage,
            )
        }
        newly_tombstoned_entity_ids: set[str] = set()
        for entity_id in sorted(covered_entity_ids - set(entities)):
            current = self._awareness_entities[entity_id]
            if current.last_seen > snapshot.observed_at:
                result.skipped_newer_entities += 1
                continue
            if current.tombstoned_at is not None:
                continue
            if not append_observation(
                "entity",
                entity_id,
                "tombstoned",
                {
                    "kind": current.kind,
                    "name": current.name,
                    "namespace": current.namespace,
                },
            ):
                continue
            current.tombstoned_at = snapshot.observed_at
            current.tombstone_snapshot_id = snapshot.snapshot_id
            current.tombstone_source = snapshot.source
            newly_tombstoned_entity_ids.add(entity_id)
            result.entities_tombstoned += 1

        for relationship in relationships.values():
            if not append_observation(
                "relationship",
                relationship.id,
                "observed",
                {
                    "metadata": relationship.metadata,
                    "relationship": relationship.relationship,
                },
            ):
                continue
            current = self._awareness_relationships.get(relationship.id)
            if current is None:
                self._awareness_relationships[relationship.id] = (
                    relationship.model_copy(deep=True)
                )
                result.relationships_created += 1
                continue

            current.first_seen = min(
                current.first_seen,
                relationship.first_seen,
            )
            current.observation_count += 1
            if relationship.last_seen < current.last_seen:
                result.skipped_newer_relationships += 1
                continue

            was_tombstoned = current.tombstoned_at is not None
            if (
                was_tombstoned
                and current.tombstoned_at is not None
                and relationship.last_seen >= current.tombstoned_at
            ):
                current.tombstoned_at = None
                current.tombstone_snapshot_id = None
                current.tombstone_source = None
                result.relationships_reactivated += 1
            else:
                result.relationships_refreshed += 1
            current.last_seen = relationship.last_seen
            current.last_incident_id = relationship.last_incident_id
            current.metadata = dict(relationship.metadata)
            current.evidence_ids = list(relationship.evidence_ids)

        authoritative_relationship_types = set(
            snapshot.coverage.relationship_types
        )
        for current in self._awareness_relationships.values():
            if (
                current.tenant_id != snapshot.tenant_id
                or current.cluster != snapshot.cluster
                or current.metadata.get("temporal_role") == "event"
                or current.id in relationships
                or current.tombstoned_at is not None
            ):
                continue

            cascaded = (
                current.source_id in newly_tombstoned_entity_ids
                or current.target_id in newly_tombstoned_entity_ids
            )
            explicitly_covered = (
                current.relationship in authoritative_relationship_types
                and current.source_id in covered_entity_ids
                and current.target_id in covered_entity_ids
            )
            if not cascaded and not explicitly_covered:
                continue
            if current.last_seen > snapshot.observed_at:
                result.skipped_newer_relationships += 1
                continue
            if not append_observation(
                "relationship",
                current.id,
                "tombstoned",
                {
                    "relationship": current.relationship,
                    "source_id": current.source_id,
                    "target_id": current.target_id,
                    "reason": (
                        "endpoint_tombstoned"
                        if cascaded
                        else "absent_from_authoritative_relationship_inventory"
                    ),
                },
            ):
                continue
            current.tombstoned_at = snapshot.observed_at
            current.tombstone_snapshot_id = snapshot.snapshot_id
            current.tombstone_source = snapshot.source
            result.relationships_tombstoned += 1

        return result

    async def save_remediation_plan(self, plan: RemediationPlan) -> None:
        self._remediation[plan.id] = plan.model_copy(deep=True)

    async def get_remediation_plan(
        self,
        plan_id: str,
        tenant_id: str | None = None,
    ) -> RemediationPlan | None:
        plan = self._remediation.get(plan_id)
        if plan is None:
            return None
        if tenant_id is not None and plan.tenant_id != tenant_id:
            return None
        return plan.model_copy(deep=True)

    async def save_remediation_plan_if_status(
        self,
        plan: RemediationPlan,
        expected_status: str,
    ) -> bool:
        current = self._remediation.get(plan.id)
        if current is None or current.status.value != expected_status:
            return False
        self._remediation[plan.id] = plan.model_copy(deep=True)
        return True

    async def list_remediation_plans(
        self,
        tenant_id: str,
        incident_id: str | None = None,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        plans = [
            item.model_copy(deep=True)
            for item in self._remediation.values()
            if item.tenant_id == tenant_id
            and (incident_id is None or item.incident_id == incident_id)
        ]
        plans.sort(key=lambda item: item.created_at, reverse=True)
        return plans[:max(1, min(limit, 100))]

    async def append_audit(self, event: AuditEvent) -> None:
        self._audit.append(event)

    async def list_audit(self, incident_id: str, tenant_id: str) -> list[AuditEvent]:
        return [
            event
            for event in self._audit
            if event.incident_id == incident_id and event.tenant_id == tenant_id
        ]

    async def purge_expired(
        self,
        incident_days: int,
        audit_days: int,
        tenant_id: str | None = None,
    ) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        incident_cutoff = now - timedelta(days=max(0, incident_days))
        audit_cutoff = now - timedelta(days=max(0, audit_days))

        stale_incidents = [
            incident_id
            for incident_id, result in self._items.items()
            if result.created_at < incident_cutoff
            and (tenant_id is None or result.incident.tenant_id == tenant_id)
        ]
        for incident_id in stale_incidents:
            self._items.pop(incident_id, None)

        retained_audit = []
        deleted_audit = 0
        for event in self._audit:
            in_scope = tenant_id is None or event.tenant_id == tenant_id
            if in_scope and event.created_at < audit_cutoff:
                deleted_audit += 1
                continue
            retained_audit.append(event)
        self._audit = retained_audit
        return {
            "incidents": len(stale_incidents),
            "audit_events": deleted_audit,
        }

    async def close(self) -> None:
        return None


class SQLRepository:
    def __init__(self, database_url: str):
        self.engine: AsyncEngine = create_async_engine(database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
            # Lightweight forward migrations for existing volumes.
            await conn.execute(text(
                "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS fingerprint_text TEXT"
            ))
            await conn.execute(text(
                "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS embedding vector(128)"
            ))
            await conn.execute(text(
                "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255) DEFAULT 'default'"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS investigations_tenant_id_idx "
                "ON investigations (tenant_id)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS investigations_embedding_hnsw "
                "ON investigations USING hnsw (embedding vector_cosine_ops)"
            ))
            for table in ("awareness_entities", "awareness_relationships"):
                await conn.execute(text(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ"
                ))
                await conn.execute(text(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN IF NOT EXISTS tombstone_snapshot_id VARCHAR(128)"
                ))
                await conn.execute(text(
                    f"ALTER TABLE {table} "
                    "ADD COLUMN IF NOT EXISTS tombstone_source VARCHAR(128)"
                ))

    async def save(self, result: InvestigationResult) -> None:
        async with self.sessions() as session:
            existing = await session.get(InvestigationRecord, result.incident_id)
            payload = result.model_dump(mode="json")
            fingerprint = incident_fingerprint(result.incident, result.root_cause.title)
            embedding = hash_embedding(fingerprint)
            if existing:
                existing.status = result.status
                existing.tenant_id = result.incident.tenant_id
                existing.root_cause = result.root_cause.title
                existing.fingerprint_text = fingerprint
                existing.embedding = embedding
                existing.payload = payload
            else:
                session.add(InvestigationRecord(
                    id=result.incident_id,
                    status=result.status,
                    tenant_id=result.incident.tenant_id,
                    title=result.incident.title,
                    alert_name=result.incident.alert_name,
                    cluster=result.incident.cluster,
                    namespace=result.incident.namespace,
                    pod=result.incident.pod,
                    root_cause=result.root_cause.title,
                    fingerprint_text=fingerprint,
                    embedding=embedding,
                    payload=payload,
                ))
            await self._ingest_awareness_session(session, result)
            await session.commit()

    async def _ingest_awareness_session(self, session, result: InvestigationResult) -> None:
        entities, relationships, observations = canonicalize_investigation(result)
        observation_by_target = {item.target_id: item for item in observations}

        for entity in entities:
            observation = observation_by_target[entity.id]
            if await session.get(AwarenessObservationRecord, observation.id):
                continue
            record = await session.get(AwarenessEntityRecord, entity.id)
            if record is None:
                session.add(AwarenessEntityRecord(
                    id=entity.id,
                    tenant_id=entity.tenant_id,
                    cluster=entity.cluster,
                    kind=entity.kind,
                    name=entity.name,
                    namespace=entity.namespace,
                    metadata_json=entity.metadata,
                    first_seen=entity.first_seen,
                    last_seen=entity.last_seen,
                    last_incident_id=entity.last_incident_id,
                    observation_count=1,
                ))
            else:
                record.first_seen = min(record.first_seen, entity.first_seen)
                if entity.last_seen >= record.last_seen:
                    record.last_seen = entity.last_seen
                    record.last_incident_id = entity.last_incident_id
                    record.metadata_json = dict(entity.metadata)
                    if (
                        record.tombstoned_at is not None
                        and entity.last_seen >= record.tombstoned_at
                    ):
                        record.tombstoned_at = None
                        record.tombstone_snapshot_id = None
                        record.tombstone_source = None
                record.observation_count += 1
            session.add(AwarenessObservationRecord(
                id=observation.id,
                tenant_id=observation.tenant_id,
                cluster=observation.cluster,
                incident_id=observation.incident_id,
                target_type=observation.target_type,
                target_id=observation.target_id,
                observed_at=observation.observed_at,
                payload=observation.payload,
            ))

        for relationship in relationships:
            observation = observation_by_target[relationship.id]
            if await session.get(AwarenessObservationRecord, observation.id):
                continue
            record = await session.get(AwarenessRelationshipRecord, relationship.id)
            if record is None:
                session.add(AwarenessRelationshipRecord(
                    id=relationship.id,
                    tenant_id=relationship.tenant_id,
                    cluster=relationship.cluster,
                    source_id=relationship.source_id,
                    target_id=relationship.target_id,
                    relationship=relationship.relationship,
                    metadata_json=relationship.metadata,
                    evidence_ids=relationship.evidence_ids,
                    first_seen=relationship.first_seen,
                    last_seen=relationship.last_seen,
                    last_incident_id=relationship.last_incident_id,
                    observation_count=1,
                ))
            else:
                record.first_seen = min(record.first_seen, relationship.first_seen)
                if relationship.last_seen >= record.last_seen:
                    record.last_seen = relationship.last_seen
                    record.last_incident_id = relationship.last_incident_id
                    record.metadata_json = dict(relationship.metadata)
                    record.evidence_ids = list(relationship.evidence_ids)
                    if (
                        record.tombstoned_at is not None
                        and relationship.last_seen >= record.tombstoned_at
                    ):
                        record.tombstoned_at = None
                        record.tombstone_snapshot_id = None
                        record.tombstone_source = None
                record.observation_count += 1
            session.add(AwarenessObservationRecord(
                id=observation.id,
                tenant_id=observation.tenant_id,
                cluster=observation.cluster,
                incident_id=observation.incident_id,
                target_type=observation.target_type,
                target_id=observation.target_id,
                observed_at=observation.observed_at,
                payload=observation.payload,
            ))

    async def get(
        self,
        incident_id: str,
        tenant_id: str | None = None,
    ) -> InvestigationResult | None:
        statement = select(InvestigationRecord).where(
            InvestigationRecord.id == incident_id
        )
        if tenant_id is not None:
            statement = statement.where(InvestigationRecord.tenant_id == tenant_id)
        async with self.sessions() as session:
            record = await session.scalar(statement)
            if not record:
                return None
            return InvestigationResult.model_validate(record.payload)

    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]:
        vector = hash_embedding(incident_fingerprint(incident))
        distance = InvestigationRecord.embedding.cosine_distance(vector).label("distance")
        statement = (
            select(InvestigationRecord, distance)
            .where(InvestigationRecord.tenant_id == incident.tenant_id)
            .order_by(distance)
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = (await session.execute(statement)).all()
        output: list[SimilarIncident] = []
        for record, dist in rows:
            similarity = max(0.0, 1.0 - float(dist or 0.0))
            if similarity <= 0.05:
                continue
            occurred_at = record.created_at or datetime.now(timezone.utc)
            output.append(SimilarIncident(
                incident_id=record.id,
                title=record.title,
                root_cause=record.root_cause,
                similarity=round(similarity, 4),
                occurred_at=occurred_at,
                cluster=record.cluster,
                namespace=record.namespace,
            ))
        return output

    async def get_awareness_graph(
        self,
        tenant_id: str,
        cluster: str,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> AwarenessGraphSnapshot:
        max_items = max(1, min(limit, 2000))
        statement = (
            select(AwarenessEntityRecord)
            .where(
                AwarenessEntityRecord.tenant_id == tenant_id,
                AwarenessEntityRecord.cluster == cluster,
            )
            .order_by(AwarenessEntityRecord.last_seen.desc())
            .limit(max_items)
        )
        if namespace is not None:
            statement = statement.where(AwarenessEntityRecord.namespace == namespace)
        if kind is not None:
            statement = statement.where(
                func.lower(AwarenessEntityRecord.kind) == kind.lower()
            )

        async with self.sessions() as session:
            entity_rows = list((await session.scalars(statement)).all())
            entity_ids = [row.id for row in entity_rows]
            relationship_rows = []
            if entity_ids:
                rel_statement = (
                    select(AwarenessRelationshipRecord)
                    .where(
                        AwarenessRelationshipRecord.tenant_id == tenant_id,
                        AwarenessRelationshipRecord.cluster == cluster,
                        AwarenessRelationshipRecord.source_id.in_(entity_ids),
                        AwarenessRelationshipRecord.target_id.in_(entity_ids),
                    )
                    .order_by(AwarenessRelationshipRecord.last_seen.desc())
                    .limit(max_items * 4)
                )
                relationship_rows = list(
                    (await session.scalars(rel_statement)).all()
                )

        entities = [
            AwarenessEntity(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                kind=row.kind,
                name=row.name,
                namespace=row.namespace,
                metadata=row.metadata_json or {},
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                last_incident_id=row.last_incident_id,
                observation_count=row.observation_count,
            )
            for row in entity_rows
        ]
        relationships = [
            AwarenessRelationship(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                source_id=row.source_id,
                target_id=row.target_id,
                relationship=row.relationship,
                metadata=row.metadata_json or {},
                evidence_ids=row.evidence_ids or [],
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                last_incident_id=row.last_incident_id,
                observation_count=row.observation_count,
            )
            for row in relationship_rows
        ]
        return AwarenessGraphSnapshot(
            tenant_id=tenant_id,
            cluster=cluster,
            entities=entities,
            relationships=relationships,
        )

    async def get_awareness_history(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
        limit: int = 100,
    ) -> list[AwarenessObservation]:
        statement = (
            select(AwarenessObservationRecord)
            .where(
                AwarenessObservationRecord.target_id == target_id,
                AwarenessObservationRecord.tenant_id == tenant_id,
                AwarenessObservationRecord.cluster == cluster,
            )
            .order_by(AwarenessObservationRecord.observed_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        async with self.sessions() as session:
            rows = list((await session.scalars(statement)).all())
        return [
            AwarenessObservation(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                incident_id=row.incident_id,
                target_type=row.target_type,
                target_id=row.target_id,
                observed_at=row.observed_at,
                payload=row.payload or {},
            )
            for row in rows
        ]

    async def find_awareness_entities(
        self,
        tenant_id: str,
        cluster: str,
        *,
        name: str | None = None,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[AwarenessEntity]:
        statement = (
            select(AwarenessEntityRecord)
            .where(
                AwarenessEntityRecord.tenant_id == tenant_id,
                AwarenessEntityRecord.cluster == cluster,
            )
            .order_by(AwarenessEntityRecord.last_seen.desc())
            .limit(max(1, min(limit, 500)))
        )
        if name is not None:
            statement = statement.where(AwarenessEntityRecord.name == name)
        if namespace is not None:
            statement = statement.where(
                AwarenessEntityRecord.namespace == namespace
            )
        if kind is not None:
            statement = statement.where(
                func.lower(AwarenessEntityRecord.kind) == kind.lower()
            )
        async with self.sessions() as session:
            rows = list((await session.scalars(statement)).all())
        return [
            AwarenessEntity(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                kind=row.kind,
                name=row.name,
                namespace=row.namespace,
                metadata=row.metadata_json or {},
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                last_incident_id=row.last_incident_id,
                observation_count=row.observation_count,
            )
            for row in rows
        ]

    async def get_awareness_entity(
        self,
        target_id: str,
        tenant_id: str,
        cluster: str,
    ) -> AwarenessEntity | None:
        statement = select(AwarenessEntityRecord).where(
            AwarenessEntityRecord.id == target_id,
            AwarenessEntityRecord.tenant_id == tenant_id,
            AwarenessEntityRecord.cluster == cluster,
        )
        async with self.sessions() as session:
            row = await session.scalar(statement)
        if row is None:
            return None
        return AwarenessEntity(
            id=row.id,
            tenant_id=row.tenant_id,
            cluster=row.cluster,
            kind=row.kind,
            name=row.name,
            namespace=row.namespace,
            metadata=row.metadata_json or {},
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            last_incident_id=row.last_incident_id,
            observation_count=row.observation_count,
        )

    async def get_awareness_neighbors(
        self,
        entity_ids: set[str],
        tenant_id: str,
        cluster: str,
        *,
        limit: int,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]:
        if not entity_ids:
            return [], []
        bounded_limit = max(1, min(limit, 8001))
        relationship_statement = (
            select(AwarenessRelationshipRecord)
            .where(
                AwarenessRelationshipRecord.tenant_id == tenant_id,
                AwarenessRelationshipRecord.cluster == cluster,
                or_(
                    AwarenessRelationshipRecord.source_id.in_(entity_ids),
                    AwarenessRelationshipRecord.target_id.in_(entity_ids),
                ),
            )
            .order_by(AwarenessRelationshipRecord.last_seen.desc())
            .limit(bounded_limit)
        )
        async with self.sessions() as session:
            relationship_rows = list(
                (await session.scalars(relationship_statement)).all()
            )
            neighbor_ids = {
                endpoint
                for relationship in relationship_rows
                for endpoint in (
                    relationship.source_id,
                    relationship.target_id,
                )
            }
            entity_rows = []
            if neighbor_ids:
                entity_statement = select(AwarenessEntityRecord).where(
                    AwarenessEntityRecord.tenant_id == tenant_id,
                    AwarenessEntityRecord.cluster == cluster,
                    AwarenessEntityRecord.id.in_(neighbor_ids),
                )
                entity_rows = list(
                    (await session.scalars(entity_statement)).all()
                )

        entities = [
            AwarenessEntity(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                kind=row.kind,
                name=row.name,
                namespace=row.namespace,
                metadata=row.metadata_json or {},
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                last_incident_id=row.last_incident_id,
                observation_count=row.observation_count,
            )
            for row in entity_rows
        ]
        relationships = [
            AwarenessRelationship(
                id=row.id,
                tenant_id=row.tenant_id,
                cluster=row.cluster,
                source_id=row.source_id,
                target_id=row.target_id,
                relationship=row.relationship,
                metadata=row.metadata_json or {},
                evidence_ids=row.evidence_ids or [],
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                last_incident_id=row.last_incident_id,
                observation_count=row.observation_count,
            )
            for row in relationship_rows
        ]
        return entities, relationships

    async def save_remediation_plan(self, plan: RemediationPlan) -> None:
        async with self.sessions() as session:
            record = await session.get(RemediationPlanRecord, plan.id)
            payload = plan.model_dump(mode="json")
            if record:
                record.status = plan.status.value
                record.payload = payload
                record.updated_at = plan.updated_at
            else:
                session.add(RemediationPlanRecord(
                    id=plan.id,
                    incident_id=plan.incident_id,
                    tenant_id=plan.tenant_id,
                    cluster=plan.cluster,
                    namespace=plan.namespace,
                    deployment=plan.deployment,
                    action_type=plan.action_type,
                    status=plan.status.value,
                    payload=payload,
                    created_at=plan.created_at,
                    updated_at=plan.updated_at,
                ))
            await session.commit()

    async def get_remediation_plan(
        self,
        plan_id: str,
        tenant_id: str | None = None,
    ) -> RemediationPlan | None:
        statement = select(RemediationPlanRecord).where(
            RemediationPlanRecord.id == plan_id
        )
        if tenant_id is not None:
            statement = statement.where(RemediationPlanRecord.tenant_id == tenant_id)
        async with self.sessions() as session:
            record = await session.scalar(statement)
        if not record:
            return None
        return RemediationPlan.model_validate(record.payload)

    async def save_remediation_plan_if_status(
        self,
        plan: RemediationPlan,
        expected_status: str,
    ) -> bool:
        statement = (
            update(RemediationPlanRecord)
            .where(
                RemediationPlanRecord.id == plan.id,
                RemediationPlanRecord.tenant_id == plan.tenant_id,
                RemediationPlanRecord.status == expected_status,
            )
            .values(
                status=plan.status.value,
                payload=plan.model_dump(mode="json"),
                updated_at=plan.updated_at,
            )
        )
        async with self.sessions() as session:
            result = await session.execute(statement)
            await session.commit()
        return int(result.rowcount or 0) == 1

    async def list_remediation_plans(
        self,
        tenant_id: str,
        incident_id: str | None = None,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        statement = (
            select(RemediationPlanRecord)
            .where(RemediationPlanRecord.tenant_id == tenant_id)
            .order_by(RemediationPlanRecord.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        if incident_id is not None:
            statement = statement.where(
                RemediationPlanRecord.incident_id == incident_id
            )
        async with self.sessions() as session:
            records = (await session.scalars(statement)).all()
        return [RemediationPlan.model_validate(record.payload) for record in records]

    async def append_audit(self, event: AuditEvent) -> None:
        async with self.sessions() as session:
            session.add(AuditEventRecord(
                id=event.id,
                incident_id=event.incident_id,
                tenant_id=event.tenant_id,
                actor=event.actor,
                action=event.action,
                detail=event.detail,
                created_at=event.created_at,
            ))
            await session.commit()

    async def list_audit(self, incident_id: str, tenant_id: str) -> list[AuditEvent]:
        statement = (
            select(AuditEventRecord)
            .where(
                AuditEventRecord.incident_id == incident_id,
                AuditEventRecord.tenant_id == tenant_id,
            )
            .order_by(AuditEventRecord.created_at)
        )
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [
            AuditEvent(
                id=row.id,
                incident_id=row.incident_id,
                tenant_id=row.tenant_id,
                actor=row.actor,
                action=row.action,
                detail=row.detail or {},
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def purge_expired(
        self,
        incident_days: int,
        audit_days: int,
        tenant_id: str | None = None,
    ) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        incident_cutoff = now - timedelta(days=max(0, incident_days))
        audit_cutoff = now - timedelta(days=max(0, audit_days))

        incident_delete = delete(InvestigationRecord).where(
            InvestigationRecord.created_at < incident_cutoff
        )
        audit_delete = delete(AuditEventRecord).where(
            AuditEventRecord.created_at < audit_cutoff
        )
        if tenant_id is not None:
            incident_delete = incident_delete.where(
                InvestigationRecord.tenant_id == tenant_id
            )
            audit_delete = audit_delete.where(
                AuditEventRecord.tenant_id == tenant_id
            )

        async with self.sessions() as session:
            incidents_result = await session.execute(incident_delete)
            audit_result = await session.execute(audit_delete)
            await session.commit()
        return {
            "incidents": int(incidents_result.rowcount or 0),
            "audit_events": int(audit_result.rowcount or 0),
        }

    async def close(self) -> None:
        await self.engine.dispose()
