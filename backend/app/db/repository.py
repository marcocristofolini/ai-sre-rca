from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

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
                    current.metadata = {**current.metadata, **entity.metadata}
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
                    current.metadata = {**current.metadata, **relationship.metadata}
                current.evidence_ids = sorted(set(current.evidence_ids + relationship.evidence_ids))
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
                    record.metadata_json = {**(record.metadata_json or {}), **entity.metadata}
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
                    record.metadata_json = {**(record.metadata_json or {}), **relationship.metadata}
                record.evidence_ids = sorted(set((record.evidence_ids or []) + relationship.evidence_ids))
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
            statement = statement.where(AwarenessEntityRecord.kind.ilike(kind))

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
