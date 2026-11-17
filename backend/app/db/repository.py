from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import json
from uuid import uuid4
from typing import Protocol

from sqlalchemy import and_, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.awareness.health import (
    AwarenessGraphHealth,
    AwarenessInventorySourceHealth,
    AwarenessStateCounts,
    inventory_source_health,
)
from app.awareness.retention import AwarenessRetentionResult
from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryReconciliationResult,
    InventorySnapshotConflictError,
    inventory_incident_id,
    inventory_observation_id,
    inventory_snapshot_fingerprint,
    inventory_snapshot_receipt_id,
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
    AlertOccurrenceRecord,
    AuditEventRecord,
    AwarenessEntityRecord,
    AwarenessObservationRecord,
    AwarenessRelationshipRecord,
    Base,
    InvestigationRecord,
    OrchestrationJobRecord,
    OrchestrationProgressEventRecord,
    OrchestrationJobRecord,
    RemediationPlanRecord,
)
from app.memory.embedding import cosine_similarity, hash_embedding
from app.core.alert_grouping import merge_alert_notification, select_group
from app.schemas import AlertOccurrence, AuditEvent, IncidentInput, IncidentSummary, InvestigationResult, RemediationPlan, SimilarIncident


class InvestigationRepository(Protocol):
    async def initialize(self) -> None: ...
    async def save(self, result: InvestigationResult) -> None: ...
    async def get(self, incident_id: str, tenant_id: str | None = None) -> InvestigationResult | None: ...
    async def list_incidents(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None = None, status: str | None = None, query: str = "",
        limit: int = 51, offset: int = 0,
    ) -> list[IncidentSummary]: ...
    async def search_similar(self, incident: IncidentInput, limit: int = 3) -> list[SimilarIncident]: ...
    async def get_awareness_graph(
        self,
        tenant_id: str,
        cluster: str,
        namespace: str | None = None,
        kind: str | None = None,
        limit: int = 500,
    ) -> AwarenessGraphSnapshot: ...
    async def get_awareness_health(
        self,
        tenant_id: str,
        cluster: str,
        *,
        stale_after_minutes: int,
        inventory_max_age_minutes: int,
        authoritative_sources: frozenset[str],
        as_of: datetime | None = None,
    ) -> AwarenessGraphHealth: ...
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
        include_tombstoned: bool = True,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]: ...
    async def reconcile_authoritative_inventory(
        self,
        snapshot: AuthoritativeInventorySnapshot,
        *,
        actor: str | None = None,
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
    async def record_alert_occurrence(self, occurrence: AlertOccurrence, group_window_seconds: int) -> AlertOccurrence: ...
    async def list_alert_occurrences(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None, since: datetime, limit: int = 5000,
    ) -> list[AlertOccurrence]: ...
    async def purge_awareness_retention(
        self,
        tenant_id: str,
        cluster: str,
        *,
        observation_days: int,
        tombstone_days: int,
        actor: str | None = None,
        dry_run: bool = True,
        as_of: datetime | None = None,
    ) -> AwarenessRetentionResult: ...
    async def purge_expired(
        self,
        incident_days: int,
        audit_days: int,
        tenant_id: str | None = None,
    ) -> dict[str, int]: ...
    async def close(self) -> None: ...
    async def create_orchestration_job(self, job: dict, max_queued: int = 1000) -> tuple[dict, bool]: ...
    async def get_orchestration_job(self, job_id: str, tenant_id: str | None = None) -> dict | None: ...
    async def claim_orchestration_job(self, job_id: str, owner: str, lease_seconds: int, max_running: int) -> dict | None: ...
    async def update_orchestration_job(self, job_id: str, values: dict, owner: str | None = None) -> bool: ...
    async def recover_orchestration_jobs(self) -> list[str]: ...
    async def purge_orchestration_jobs(self, retention_seconds: int, max_records: int) -> int: ...
    async def append_orchestration_progress(self, job_id: str, tenant_id: str, event_type: str, payload: dict, max_events: int) -> dict: ...
    async def list_orchestration_progress(self, job_id: str, tenant_id: str, after: int = 0, limit: int = 100) -> list[dict]: ...


def _inventory_reconciliation_audit(
    snapshot: AuthoritativeInventorySnapshot,
    result: InventoryReconciliationResult,
    actor: str,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=snapshot.tenant_id,
        actor=actor,
        action="awareness.inventory.reconcile",
        detail={
            "cluster": snapshot.cluster,
            "source": snapshot.source,
            "snapshot_id": snapshot.snapshot_id,
            "coverage": snapshot.coverage.model_dump(),
            "resources_observed": result.resources_observed,
            "relationships_observed": result.relationships_observed,
            "entities_tombstoned": result.entities_tombstoned,
            "relationships_tombstoned": result.relationships_tombstoned,
            "skipped_newer_entities": result.skipped_newer_entities,
            "skipped_newer_relationships": result.skipped_newer_relationships,
        },
    )


def _awareness_retention_audit(
    result: AwarenessRetentionResult,
    actor: str,
) -> AuditEvent:
    return AuditEvent(
        tenant_id=result.tenant_id,
        actor=actor,
        action="awareness.retention.purge",
        detail={
            "cluster": result.cluster,
            "observation_retention_days": result.observation_retention_days,
            "tombstone_retention_days": result.tombstone_retention_days,
            "observations_deleted": result.observations_deleted,
            "latest_observations_preserved": (
                result.latest_observations_preserved
            ),
            "inventory_receipts_preserved": (
                result.inventory_receipts_preserved
            ),
            "tombstoned_relationships_deleted": (
                result.tombstoned_relationships_deleted
            ),
            "tombstoned_entities_deleted": (
                result.tombstoned_entities_deleted
            ),
            "tombstoned_entities_blocked": (
                result.tombstoned_entities_blocked
            ),
        },
    )


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
        self._alerts: dict[tuple[str, str, str, datetime], AlertOccurrence] = {}
        self._alert_lock = asyncio.Lock()
        self._awareness_entities: dict[str, AwarenessEntity] = {}
        self._awareness_relationships: dict[str, AwarenessRelationship] = {}
        self._awareness_observations: dict[str, AwarenessObservation] = {}
        self._orchestration_jobs: dict[str, dict] = {}
        self._orchestration_events: dict[str, list[dict]] = {}
        self._orchestration_lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def create_orchestration_job(self, job: dict, max_queued: int = 1000) -> tuple[dict, bool]:
        async with self._orchestration_lock:
            if job.get("idempotency_key"):
                for existing in self._orchestration_jobs.values():
                    if existing["tenant_id"] == job["tenant_id"] and existing["idempotency_key"] == job["idempotency_key"]:
                        if existing["request_fingerprint"] != job["request_fingerprint"]:
                            raise ValueError("idempotency key conflict")
                        return json.loads(json.dumps(existing)), False
            if sum(j["status"] in {"queued", "running"} for j in self._orchestration_jobs.values()) >= max_queued:
                raise OverflowError("orchestration queue is full")
            self._orchestration_jobs[job["id"]] = json.loads(json.dumps(job))
            return json.loads(json.dumps(job)), True

    async def get_orchestration_job(self, job_id: str, tenant_id: str | None = None) -> dict | None:
        async with self._orchestration_lock:
            job = self._orchestration_jobs.get(job_id)
            if job is None or (tenant_id is not None and job["tenant_id"] != tenant_id):
                return None
            return json.loads(json.dumps(job))

    async def claim_orchestration_job(self, job_id: str, owner: str, lease_seconds: int, max_running: int) -> dict | None:
        async with self._orchestration_lock:
            now = datetime.now(timezone.utc)
            active = sum(j["status"] == "running" and j.get("lease_expires_at") and datetime.fromisoformat(j["lease_expires_at"]) > now for j in self._orchestration_jobs.values())
            if active >= max_running:
                return None
            job = self._orchestration_jobs.get(job_id)
            if not job or not (job["status"] == "queued" or (job["status"] == "running" and job.get("lease_expires_at") and datetime.fromisoformat(job["lease_expires_at"]) <= now)):
                return None
            job.update(status="running", lease_owner=owner,
                lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat(),
                started_at=job.get("started_at") or now.isoformat())
            return json.loads(json.dumps(job))

    async def update_orchestration_job(self, job_id: str, values: dict, owner: str | None = None) -> bool:
        async with self._orchestration_lock:
            job = self._orchestration_jobs.get(job_id)
            if not job or (owner is not None and job.get("lease_owner") != owner):
                return False
            job.update(values)
            return True

    async def recover_orchestration_jobs(self) -> list[str]:
        async with self._orchestration_lock:
            now = datetime.now(timezone.utc)
            return [job["id"] for job in self._orchestration_jobs.values()
                    if job["status"] == "queued" or (job["status"] == "running" and job.get("lease_expires_at") and datetime.fromisoformat(job["lease_expires_at"]) <= now)]

    async def purge_orchestration_jobs(self, retention_seconds: int, max_records: int) -> int:
        async with self._orchestration_lock:
            terminal = [(key, value) for key, value in self._orchestration_jobs.items()
                        if value["status"] in {"completed", "failed", "cancelled"}
                        and value.get("finished_at")]
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention_seconds)
            remove = {key for key, value in terminal
                      if datetime.fromisoformat(value["finished_at"]) < cutoff}
            remaining = [(key, value) for key, value in terminal if key not in remove]
            remaining.sort(key=lambda item: item[1]["submitted_at"], reverse=True)
            remove.update(key for key, _ in remaining[max_records:])
            for key in remove:
                self._orchestration_jobs.pop(key, None)
                self._orchestration_events.pop(key, None)
            return len(remove)

    async def append_orchestration_progress(self, job_id: str, tenant_id: str, event_type: str, payload: dict, max_events: int) -> dict:
        async with self._orchestration_lock:
            job = self._orchestration_jobs.get(job_id)
            if not job or job["tenant_id"] != tenant_id:
                raise LookupError("orchestration job not found")
            job["progress_sequence"] = job.get("progress_sequence", 0) + 1
            event = {"job_id": job_id, "tenant_id": tenant_id,
                "sequence": job["progress_sequence"], "event_type": event_type,
                "payload": payload, "created_at": datetime.now(timezone.utc).isoformat()}
            events = self._orchestration_events.setdefault(job_id, [])
            events.append(json.loads(json.dumps(event)))
            del events[:-max_events]
            return event

    async def list_orchestration_progress(self, job_id: str, tenant_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        async with self._orchestration_lock:
            job = self._orchestration_jobs.get(job_id)
            if not job or job["tenant_id"] != tenant_id:
                return []
            return [json.loads(json.dumps(event)) for event in
                    self._orchestration_events.get(job_id, []) if event["sequence"] > after][:max(1, min(limit, 500))]

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

    async def list_incidents(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None = None, status: str | None = None, query: str = "",
        limit: int = 51, offset: int = 0,
    ) -> list[IncidentSummary]:
        needle = query.casefold()
        values = [item for item in self._items.values()
                  if (tenant_id is None or item.incident.tenant_id == tenant_id)
                  and (allowed_clusters is None or item.incident.cluster in allowed_clusters)
                  and (cluster is None or item.incident.cluster == cluster)
                  and (status is None or item.status == status)
                  and (not needle or needle in item.incident.title.casefold()
                       or needle in item.incident_id.casefold())]
        values.sort(key=lambda item: (item.created_at, item.incident_id), reverse=True)
        return [IncidentSummary(
            incident_id=item.incident_id, title=item.incident.title,
            tenant_id=item.incident.tenant_id, cluster=item.incident.cluster,
            namespace=item.incident.namespace, status=item.status,
            severity=item.incident.severity, root_cause=item.root_cause.title,
            investigation_mode="agentic" if item.agentic else "classic",
            created_at=item.created_at,
        ) for item in values[offset:offset + limit]]

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
        entities.sort(
            key=lambda item: item.tombstoned_at or item.last_seen,
            reverse=True,
        )
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
        relationships.sort(
            key=lambda item: item.tombstoned_at or item.last_seen,
            reverse=True,
        )
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
        include_tombstoned: bool = True,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]:
        if not entity_ids:
            return [], []
        bounded_limit = max(1, min(limit, 8001))
        relationships = [
            item.model_copy(deep=True)
            for item in self._awareness_relationships.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and (include_tombstoned or item.tombstoned_at is None)
            and (
                item.source_id in entity_ids
                or item.target_id in entity_ids
            )
        ]
        relationships.sort(
            key=lambda item: item.tombstoned_at or item.last_seen,
            reverse=True,
        )
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
            and (include_tombstoned or item.tombstoned_at is None)
        ]
        return entities, relationships

    async def get_awareness_health(
        self,
        tenant_id: str,
        cluster: str,
        *,
        stale_after_minutes: int,
        inventory_max_age_minutes: int,
        authoritative_sources: frozenset[str],
        as_of: datetime | None = None,
    ) -> AwarenessGraphHealth:
        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        stale_cutoff = now - timedelta(minutes=stale_after_minutes)

        def classify(items) -> AwarenessStateCounts:
            scoped = [
                item
                for item in items
                if item.tenant_id == tenant_id and item.cluster == cluster
            ]
            counts = AwarenessStateCounts(total=len(scoped))
            for item in scoped:
                if item.tombstoned_at is not None:
                    counts.tombstoned += 1
                    continue
                if item.metadata.get("temporal_role") == "event":
                    counts.historical += 1
                    continue
                if item.last_seen < stale_cutoff:
                    counts.stale += 1
                else:
                    counts.current += 1
            return counts

        observations = [
            item
            for item in self._awareness_observations.values()
            if item.tenant_id == tenant_id and item.cluster == cluster
        ]
        latest_observation_at = max(
            (item.observed_at for item in observations),
            default=None,
        )

        inventory_sources: list[AwarenessInventorySourceHealth] = []
        for source in sorted(authoritative_sources):
            receipts = [
                item
                for item in observations
                if item.target_type == "inventory_snapshot"
                and item.payload.get("source") == source
            ]
            latest = max(
                receipts,
                key=lambda item: item.observed_at,
                default=None,
            )
            inventory_sources.append(inventory_source_health(
                source=source,
                snapshot_id=(
                    str(latest.payload.get("snapshot_id"))
                    if latest is not None
                    and latest.payload.get("snapshot_id") is not None
                    else None
                ),
                observed_at=latest.observed_at if latest is not None else None,
                as_of=now,
                max_age_minutes=inventory_max_age_minutes,
            ))

        return AwarenessGraphHealth(
            tenant_id=tenant_id,
            cluster=cluster,
            generated_at=now,
            stale_after_minutes=stale_after_minutes,
            inventory_max_age_minutes=inventory_max_age_minutes,
            entities=classify(self._awareness_entities.values()),
            relationships=classify(self._awareness_relationships.values()),
            observations_total=len(observations),
            latest_observation_at=latest_observation_at,
            inventory_sources=inventory_sources,
        )

    async def reconcile_authoritative_inventory(
        self,
        snapshot: AuthoritativeInventorySnapshot,
        *,
        actor: str | None = None,
    ) -> InventoryReconciliationResult:
        receipt_id = inventory_snapshot_receipt_id(snapshot)
        fingerprint = inventory_snapshot_fingerprint(snapshot)
        existing_receipt = self._awareness_observations.get(receipt_id)
        if existing_receipt is not None:
            if existing_receipt.payload.get("fingerprint") != fingerprint:
                raise InventorySnapshotConflictError(
                    "authoritative inventory snapshot_id was reused "
                    "with different content"
                )
        else:
            self._awareness_observations[receipt_id] = AwarenessObservation(
                id=receipt_id,
                tenant_id=snapshot.tenant_id,
                cluster=snapshot.cluster,
                incident_id=f"inventory:{snapshot.snapshot_id}",
                target_type="inventory_snapshot",
                target_id=receipt_id,
                observed_at=snapshot.observed_at,
                payload={
                    "origin": "authoritative_inventory",
                    "source": snapshot.source,
                    "snapshot_id": snapshot.snapshot_id,
                    "fingerprint": fingerprint,
                },
            )

        entities = materialize_inventory_entities(snapshot)
        relationships = materialize_inventory_relationships(snapshot)
        origin = inventory_incident_id(snapshot)
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
                snapshot.source,
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
            current_boundary = current.last_seen
            if (
                current.tombstoned_at is not None
                and current.tombstoned_at > current_boundary
            ):
                current_boundary = current.tombstoned_at
            if entity.last_seen < current_boundary:
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
            current_boundary = current.last_seen
            if (
                current.tombstoned_at is not None
                and current.tombstoned_at > current_boundary
            ):
                current_boundary = current.tombstoned_at
            if relationship.last_seen < current_boundary:
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

        if actor is not None:
            self._audit.append(
                _inventory_reconciliation_audit(snapshot, result, actor)
            )
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

    async def record_alert_occurrence(self, occurrence: AlertOccurrence, group_window_seconds: int) -> AlertOccurrence:
        key = (occurrence.tenant_id, occurrence.cluster, occurrence.fingerprint, occurrence.starts_at)
        async with self._alert_lock:
            existing = self._alerts.get(key)
            if existing is not None:
                stored = merge_alert_notification(existing, occurrence)
            else:
                group_id = select_group(
                    self._alerts.values(), occurrence, timedelta(seconds=group_window_seconds),
                ) or f"grp-{uuid4().hex[:16]}"
                stored = occurrence.model_copy(update={"group_id": group_id})
            self._alerts[key] = stored
            return stored.model_copy()

    async def list_alert_occurrences(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None, since: datetime, limit: int = 5000,
    ) -> list[AlertOccurrence]:
        rows = [
            item for item in self._alerts.values()
            if (tenant_id is None or item.tenant_id == tenant_id)
            and (allowed_clusters is None or item.cluster in allowed_clusters)
            and (cluster is None or item.cluster == cluster)
            and item.last_received_at >= since
        ]
        rows.sort(key=lambda item: (item.last_received_at, item.id), reverse=True)
        return [item.model_copy() for item in rows[:limit]]

    async def purge_awareness_retention(
        self,
        tenant_id: str,
        cluster: str,
        *,
        observation_days: int,
        tombstone_days: int,
        actor: str | None = None,
        dry_run: bool = True,
        as_of: datetime | None = None,
    ) -> AwarenessRetentionResult:
        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        observation_cutoff = now - timedelta(
            days=max(1, observation_days)
        )
        tombstone_cutoff = now - timedelta(days=max(1, tombstone_days))

        scoped_observations = [
            item
            for item in self._awareness_observations.values()
            if item.tenant_id == tenant_id and item.cluster == cluster
        ]
        latest_by_target: dict[tuple[str, str], AwarenessObservation] = {}
        for item in scoped_observations:
            if item.target_type == "inventory_snapshot":
                continue
            key = (item.target_type, item.target_id)
            current = latest_by_target.get(key)
            if current is None or (
                item.observed_at,
                item.id,
            ) > (
                current.observed_at,
                current.id,
            ):
                latest_by_target[key] = item

        latest_ids = {item.id for item in latest_by_target.values()}
        observation_ids = {
            item.id
            for item in scoped_observations
            if item.target_type != "inventory_snapshot"
            and item.observed_at < observation_cutoff
            and item.id not in latest_ids
        }
        latest_preserved = sum(
            1
            for item in latest_by_target.values()
            if item.observed_at < observation_cutoff
        )
        receipts_preserved = sum(
            1
            for item in scoped_observations
            if item.target_type == "inventory_snapshot"
        )

        relationship_ids = {
            item.id
            for item in self._awareness_relationships.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and item.tombstoned_at is not None
            and item.tombstoned_at < tombstone_cutoff
        }
        remaining_relationships = [
            item
            for item in self._awareness_relationships.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and item.id not in relationship_ids
        ]

        old_tombstoned_entities = [
            item
            for item in self._awareness_entities.values()
            if item.tenant_id == tenant_id
            and item.cluster == cluster
            and item.tombstoned_at is not None
            and item.tombstoned_at < tombstone_cutoff
        ]
        entity_ids = {
            item.id
            for item in old_tombstoned_entities
            if not any(
                relationship.source_id == item.id
                or relationship.target_id == item.id
                for relationship in remaining_relationships
            )
        }
        blocked_entities = len(old_tombstoned_entities) - len(entity_ids)

        result = AwarenessRetentionResult(
            tenant_id=tenant_id,
            cluster=cluster,
            dry_run=dry_run,
            as_of=now,
            observation_retention_days=max(1, observation_days),
            tombstone_retention_days=max(1, tombstone_days),
            observation_cutoff=observation_cutoff,
            tombstone_cutoff=tombstone_cutoff,
            observations_eligible=len(observation_ids),
            observations_deleted=0 if dry_run else len(observation_ids),
            latest_observations_preserved=latest_preserved,
            inventory_receipts_preserved=receipts_preserved,
            tombstoned_relationships_eligible=len(relationship_ids),
            tombstoned_relationships_deleted=(
                0 if dry_run else len(relationship_ids)
            ),
            tombstoned_entities_eligible=len(entity_ids),
            tombstoned_entities_deleted=0 if dry_run else len(entity_ids),
            tombstoned_entities_blocked=blocked_entities,
        )

        if dry_run:
            return result

        for observation_id in observation_ids:
            self._awareness_observations.pop(observation_id, None)
        for relationship_id in relationship_ids:
            self._awareness_relationships.pop(relationship_id, None)
        for entity_id in entity_ids:
            self._awareness_entities.pop(entity_id, None)

        if actor is not None:
            self._audit.append(_awareness_retention_audit(result, actor))
        return result

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
            await conn.execute(text(
                "ALTER TABLE orchestration_jobs ADD COLUMN IF NOT EXISTS progress_sequence INTEGER NOT NULL DEFAULT 0"
            ))
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
                for column in (
                    "tombstoned_at",
                    "tombstone_snapshot_id",
                    "tombstone_source",
                ):
                    await conn.execute(text(
                        f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} "
                        f"ON {table} ({column})"
                    ))

    async def create_orchestration_job(self, job: dict, max_queued: int = 1000) -> tuple[dict, bool]:
        values = dict(job)
        result = values.pop("result", None)
        for name in ("submitted_at", "started_at", "finished_at", "lease_expires_at"):
            if isinstance(values.get(name), str):
                values[name] = datetime.fromisoformat(values[name])
        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('ai-sre-rca-orchestrator-claims'))"))
            if job.get("idempotency_key"):
                existing_row = await conn.execute(select(OrchestrationJobRecord).where(
                    OrchestrationJobRecord.tenant_id == job["tenant_id"],
                    OrchestrationJobRecord.idempotency_key == job["idempotency_key"],
                ))
                existing = existing_row.mappings().first()
                if existing:
                    existing_dict = dict(existing)
                    if existing_dict["request_fingerprint"] != job["request_fingerprint"]:
                        raise ValueError("idempotency key conflict")
                    return existing_dict, False
            active = await conn.scalar(select(func.count()).select_from(OrchestrationJobRecord).where(
                OrchestrationJobRecord.status.in_(["queued", "running"])))
            if (active or 0) >= max_queued:
                raise OverflowError("orchestration queue is full")
            statement = insert(OrchestrationJobRecord).values(**values, result=result).on_conflict_do_nothing(
                constraint="orchestration_jobs_tenant_idem_uq").returning(OrchestrationJobRecord.id)
            created_id = (await conn.execute(statement)).scalar_one_or_none()
        if created_id:
            return dict(job), True
        existing = await self.get_orchestration_job_by_key(job["tenant_id"], job.get("idempotency_key"))
        if not existing:
            raise RuntimeError("orchestration job insert conflict without matching idempotency record")
        if existing["request_fingerprint"] != job["request_fingerprint"]:
            raise ValueError("idempotency key conflict")
        return existing, False

    @staticmethod
    def _orchestration_job_dict(record: OrchestrationJobRecord) -> dict:
        return {name: getattr(record, name) for name in (
            "id", "tenant_id", "idempotency_key", "request_fingerprint", "status",
            "cluster", "incident_id", "actor", "payload", "result", "error",
            "error_type", "submitted_at", "started_at", "finished_at",
            "lease_owner", "lease_expires_at",
            "progress_sequence",
        )}

    async def get_orchestration_job_by_key(self, tenant_id: str, idempotency_key: str | None) -> dict | None:
        if idempotency_key is None:
            return None
        async with self.sessions() as session:
            record = (await session.execute(select(OrchestrationJobRecord).where(
                OrchestrationJobRecord.tenant_id == tenant_id,
                OrchestrationJobRecord.idempotency_key == idempotency_key,
            ))).scalar_one_or_none()
            return self._orchestration_job_dict(record) if record else None

    async def get_orchestration_job(self, job_id: str, tenant_id: str | None = None) -> dict | None:
        query = select(OrchestrationJobRecord).where(OrchestrationJobRecord.id == job_id)
        if tenant_id is not None:
            query = query.where(OrchestrationJobRecord.tenant_id == tenant_id)
        async with self.sessions() as session:
            record = (await session.execute(query)).scalar_one_or_none()
            return self._orchestration_job_dict(record) if record else None

    async def claim_orchestration_job(self, job_id: str, owner: str, lease_seconds: int, max_running: int) -> dict | None:
        now = datetime.now(timezone.utc)
        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('ai-sre-rca-orchestrator-claims'))"))
            active = await conn.scalar(select(func.count()).select_from(OrchestrationJobRecord).where(
                OrchestrationJobRecord.status == "running",
                OrchestrationJobRecord.lease_expires_at > now,
            ))
            if (active or 0) >= max_running:
                return None
            claimed = await conn.execute(update(OrchestrationJobRecord).where(
                OrchestrationJobRecord.id == job_id,
                or_(OrchestrationJobRecord.status == "queued", and_(
                    OrchestrationJobRecord.status == "running",
                    OrchestrationJobRecord.lease_expires_at <= now,
                )),
            ).values(status="running", lease_owner=owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=func.coalesce(OrchestrationJobRecord.started_at, now)
            ).returning(OrchestrationJobRecord.id))
            if claimed.scalar_one_or_none() is None:
                return None
        return await self.get_orchestration_job(job_id)

    async def update_orchestration_job(self, job_id: str, values: dict, owner: str | None = None) -> bool:
        allowed = {key: value for key, value in values.items() if key in {
            "status", "result", "error", "error_type", "finished_at", "lease_owner", "lease_expires_at",
        }}
        for name in ("finished_at", "lease_expires_at"):
            value = allowed.get(name)
            if isinstance(value, str):
                allowed[name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        query = update(OrchestrationJobRecord).where(OrchestrationJobRecord.id == job_id)
        if owner is not None:
            query = query.where(OrchestrationJobRecord.lease_owner == owner)
        async with self.engine.begin() as conn:
            result = await conn.execute(query.values(**allowed).returning(OrchestrationJobRecord.id))
            return result.scalar_one_or_none() is not None

    async def recover_orchestration_jobs(self) -> list[str]:
        now = datetime.now(timezone.utc)
        async with self.sessions() as session:
            rows = await session.execute(select(OrchestrationJobRecord.id).where(or_(
                OrchestrationJobRecord.status == "queued",
                and_(OrchestrationJobRecord.status == "running", OrchestrationJobRecord.lease_expires_at <= now),
            )).order_by(OrchestrationJobRecord.submitted_at))
            return list(rows.scalars())

    async def purge_orchestration_jobs(self, retention_seconds: int, max_records: int) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=retention_seconds)
        old = delete(OrchestrationJobRecord).where(
            OrchestrationJobRecord.status.in_(["completed", "failed", "cancelled"]),
            OrchestrationJobRecord.finished_at < cutoff,
        )
        async with self.engine.begin() as conn:
            deleted_old = await conn.execute(old)
            keep = select(OrchestrationJobRecord.id).where(
                OrchestrationJobRecord.status.in_(["completed", "failed", "cancelled"])
            ).order_by(OrchestrationJobRecord.submitted_at.desc()).offset(max_records)
            overflow = await conn.execute(delete(OrchestrationJobRecord).where(
                OrchestrationJobRecord.id.in_(keep)
            ))
            return int(deleted_old.rowcount or 0) + int(overflow.rowcount or 0)

    async def append_orchestration_progress(self, job_id: str, tenant_id: str, event_type: str, payload: dict, max_events: int) -> dict:
        async with self.engine.begin() as conn:
            sequence = await conn.execute(update(OrchestrationJobRecord).where(
                OrchestrationJobRecord.id == job_id,
                OrchestrationJobRecord.tenant_id == tenant_id,
            ).values(progress_sequence=OrchestrationJobRecord.progress_sequence + 1)
                .returning(OrchestrationJobRecord.progress_sequence))
            value = sequence.scalar_one_or_none()
            if value is None:
                raise LookupError("orchestration job not found")
            created = datetime.now(timezone.utc)
            await conn.execute(insert(OrchestrationProgressEventRecord).values(
                job_id=job_id, tenant_id=tenant_id, sequence=value,
                event_type=event_type, payload=payload, created_at=created,
            ))
            await conn.execute(delete(OrchestrationProgressEventRecord).where(
                OrchestrationProgressEventRecord.job_id == job_id,
                OrchestrationProgressEventRecord.sequence <= value - max_events,
            ))
        return {"job_id": job_id, "tenant_id": tenant_id, "sequence": value,
                "event_type": event_type, "payload": payload, "created_at": created}

    async def list_orchestration_progress(self, job_id: str, tenant_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        async with self.sessions() as session:
            rows = await session.execute(select(OrchestrationProgressEventRecord).where(
                OrchestrationProgressEventRecord.job_id == job_id,
                OrchestrationProgressEventRecord.tenant_id == tenant_id,
                OrchestrationProgressEventRecord.sequence > after,
            ).order_by(OrchestrationProgressEventRecord.sequence).limit(max(1, min(limit, 500))))
            return [{"job_id": row.job_id, "tenant_id": row.tenant_id,
                "sequence": row.sequence, "event_type": row.event_type,
                "payload": row.payload, "created_at": row.created_at} for row in rows.scalars()]

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

    async def list_incidents(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None = None, status: str | None = None, query: str = "",
        limit: int = 51, offset: int = 0,
    ) -> list[IncidentSummary]:
        if allowed_clusters is not None and not allowed_clusters:
            return []
        # Select summary fields only, keeping evidence and execution payloads out of list reads.
        statement = select(
            InvestigationRecord.id.label("incident_id"), InvestigationRecord.title,
            InvestigationRecord.tenant_id, InvestigationRecord.cluster,
            InvestigationRecord.namespace, InvestigationRecord.status,
            InvestigationRecord.root_cause, InvestigationRecord.created_at,
            InvestigationRecord.payload["incident"]["severity"].as_string().label("severity"),
            InvestigationRecord.payload["agentic"]["plan_id"].as_string().label("plan_id"),
        )
        if tenant_id is not None:
            statement = statement.where(InvestigationRecord.tenant_id == tenant_id)
        if allowed_clusters is not None:
            statement = statement.where(InvestigationRecord.cluster.in_(allowed_clusters))
        if cluster is not None:
            statement = statement.where(InvestigationRecord.cluster == cluster)
        if status is not None:
            statement = statement.where(InvestigationRecord.status == status)
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(or_(
                InvestigationRecord.title.ilike(pattern, escape="\\"),
                InvestigationRecord.id.ilike(pattern, escape="\\"),
            ))
        statement = statement.order_by(InvestigationRecord.created_at.desc(), InvestigationRecord.id.desc()).offset(offset).limit(limit)
        async with self.sessions() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [IncidentSummary(
            **{key: value for key, value in row.items() if key != "plan_id"},
            investigation_mode="agentic" if row["plan_id"] else "classic",
        ) for row in rows]

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
            .order_by(
                func.greatest(
                    AwarenessEntityRecord.last_seen,
                    func.coalesce(
                        AwarenessEntityRecord.tombstoned_at,
                        AwarenessEntityRecord.last_seen,
                    ),
                ).desc()
            )
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
                    .order_by(
                        func.greatest(
                            AwarenessRelationshipRecord.last_seen,
                            func.coalesce(
                                AwarenessRelationshipRecord.tombstoned_at,
                                AwarenessRelationshipRecord.last_seen,
                            ),
                        ).desc()
                    )
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
                tombstoned_at=row.tombstoned_at,
                tombstone_snapshot_id=row.tombstone_snapshot_id,
                tombstone_source=row.tombstone_source,
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
                tombstoned_at=row.tombstoned_at,
                tombstone_snapshot_id=row.tombstone_snapshot_id,
                tombstone_source=row.tombstone_source,
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
                AwarenessEntityRecord.tombstoned_at.is_(None),
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
                tombstoned_at=row.tombstoned_at,
                tombstone_snapshot_id=row.tombstone_snapshot_id,
                tombstone_source=row.tombstone_source,
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
            tombstoned_at=row.tombstoned_at,
            tombstone_snapshot_id=row.tombstone_snapshot_id,
            tombstone_source=row.tombstone_source,
        )

    async def get_awareness_neighbors(
        self,
        entity_ids: set[str],
        tenant_id: str,
        cluster: str,
        *,
        limit: int,
        include_tombstoned: bool = True,
    ) -> tuple[list[AwarenessEntity], list[AwarenessRelationship]]:
        if not entity_ids:
            return [], []
        bounded_limit = max(1, min(limit, 8001))
        relationship_statement = select(
            AwarenessRelationshipRecord
        ).where(
            AwarenessRelationshipRecord.tenant_id == tenant_id,
            AwarenessRelationshipRecord.cluster == cluster,
            or_(
                AwarenessRelationshipRecord.source_id.in_(entity_ids),
                AwarenessRelationshipRecord.target_id.in_(entity_ids),
            ),
        )
        if not include_tombstoned:
            relationship_statement = relationship_statement.where(
                AwarenessRelationshipRecord.tombstoned_at.is_(None)
            )
        relationship_statement = (
            relationship_statement
            .order_by(
                func.greatest(
                    AwarenessRelationshipRecord.last_seen,
                    func.coalesce(
                        AwarenessRelationshipRecord.tombstoned_at,
                        AwarenessRelationshipRecord.last_seen,
                    ),
                ).desc()
            )
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
                if not include_tombstoned:
                    entity_statement = entity_statement.where(
                        AwarenessEntityRecord.tombstoned_at.is_(None)
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
                tombstoned_at=row.tombstoned_at,
                tombstone_snapshot_id=row.tombstone_snapshot_id,
                tombstone_source=row.tombstone_source,
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
                tombstoned_at=row.tombstoned_at,
                tombstone_snapshot_id=row.tombstone_snapshot_id,
                tombstone_source=row.tombstone_source,
            )
            for row in relationship_rows
        ]
        return entities, relationships

    async def get_awareness_health(
        self,
        tenant_id: str,
        cluster: str,
        *,
        stale_after_minutes: int,
        inventory_max_age_minutes: int,
        authoritative_sources: frozenset[str],
        as_of: datetime | None = None,
    ) -> AwarenessGraphHealth:
        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        stale_cutoff = now - timedelta(minutes=stale_after_minutes)

        def state_statement(record_type):
            temporal_role = record_type.metadata_json[
                "temporal_role"
            ].as_string()
            nonhistorical = func.coalesce(temporal_role, "") != "event"
            active = and_(
                record_type.tombstoned_at.is_(None),
                nonhistorical,
            )
            historical = and_(
                record_type.tombstoned_at.is_(None),
                temporal_role == "event",
            )
            return select(
                func.count(record_type.id),
                func.count(record_type.id).filter(and_(
                    active,
                    record_type.last_seen >= stale_cutoff,
                )),
                func.count(record_type.id).filter(and_(
                    active,
                    record_type.last_seen < stale_cutoff,
                )),
                func.count(record_type.id).filter(historical),
                func.count(record_type.id).filter(
                    record_type.tombstoned_at.is_not(None)
                ),
            ).where(
                record_type.tenant_id == tenant_id,
                record_type.cluster == cluster,
            )

        def counts(row) -> AwarenessStateCounts:
            return AwarenessStateCounts(
                total=int(row[0] or 0),
                current=int(row[1] or 0),
                stale=int(row[2] or 0),
                historical=int(row[3] or 0),
                tombstoned=int(row[4] or 0),
            )

        async with self.sessions() as session:
            entity_row = (
                await session.execute(
                    state_statement(AwarenessEntityRecord)
                )
            ).one()
            relationship_row = (
                await session.execute(
                    state_statement(AwarenessRelationshipRecord)
                )
            ).one()
            observation_row = (
                await session.execute(
                    select(
                        func.count(AwarenessObservationRecord.id),
                        func.max(AwarenessObservationRecord.observed_at),
                    ).where(
                        AwarenessObservationRecord.tenant_id == tenant_id,
                        AwarenessObservationRecord.cluster == cluster,
                    )
                )
            ).one()

            inventory_sources: list[
                AwarenessInventorySourceHealth
            ] = []
            source_expr = AwarenessObservationRecord.payload[
                "source"
            ].as_string()
            for source in sorted(authoritative_sources):
                row = (
                    await session.execute(
                        select(
                            AwarenessObservationRecord.observed_at,
                            AwarenessObservationRecord.payload,
                        )
                        .where(
                            AwarenessObservationRecord.tenant_id == tenant_id,
                            AwarenessObservationRecord.cluster == cluster,
                            AwarenessObservationRecord.target_type
                            == "inventory_snapshot",
                            source_expr == source,
                        )
                        .order_by(
                            AwarenessObservationRecord.observed_at.desc()
                        )
                        .limit(1)
                    )
                ).first()
                payload = row[1] if row is not None else {}
                inventory_sources.append(inventory_source_health(
                    source=source,
                    snapshot_id=(
                        str(payload.get("snapshot_id"))
                        if payload.get("snapshot_id") is not None
                        else None
                    ),
                    observed_at=row[0] if row is not None else None,
                    as_of=now,
                    max_age_minutes=inventory_max_age_minutes,
                ))

        return AwarenessGraphHealth(
            tenant_id=tenant_id,
            cluster=cluster,
            generated_at=now,
            stale_after_minutes=stale_after_minutes,
            inventory_max_age_minutes=inventory_max_age_minutes,
            entities=counts(entity_row),
            relationships=counts(relationship_row),
            observations_total=int(observation_row[0] or 0),
            latest_observation_at=observation_row[1],
            inventory_sources=inventory_sources,
        )

    async def reconcile_authoritative_inventory(
        self,
        snapshot: AuthoritativeInventorySnapshot,
        *,
        actor: str | None = None,
    ) -> InventoryReconciliationResult:
        entities = materialize_inventory_entities(snapshot)
        relationships = materialize_inventory_relationships(snapshot)
        origin = inventory_incident_id(snapshot)
        result = InventoryReconciliationResult(
            snapshot_id=snapshot.snapshot_id,
            source=snapshot.source,
            tenant_id=snapshot.tenant_id,
            cluster=snapshot.cluster,
            observed_at=snapshot.observed_at,
            resources_observed=len(entities),
            relationships_observed=len(relationships),
        )

        async with self.sessions() as session:
            # Serialize authoritative snapshots for one source/scope. This keeps
            # concurrent retries idempotent and prevents two different snapshots
            # from interleaving tombstone/current-state writes.
            lock_scope = "\x1f".join(
                (
                    "awareness-inventory",
                    snapshot.tenant_id,
                    snapshot.cluster,
                    snapshot.source,
                )
            )
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:scope, 0))"
                ),
                {"scope": lock_scope},
            )

            receipt_id = inventory_snapshot_receipt_id(snapshot)
            fingerprint = inventory_snapshot_fingerprint(snapshot)
            existing_receipt = await session.get(
                AwarenessObservationRecord,
                receipt_id,
            )
            if existing_receipt is not None:
                if (
                    (existing_receipt.payload or {}).get("fingerprint")
                    != fingerprint
                ):
                    raise InventorySnapshotConflictError(
                        "authoritative inventory snapshot_id was reused "
                        "with different content"
                    )
            else:
                session.add(AwarenessObservationRecord(
                    id=receipt_id,
                    tenant_id=snapshot.tenant_id,
                    cluster=snapshot.cluster,
                    incident_id=origin,
                    target_type="inventory_snapshot",
                    target_id=receipt_id,
                    observed_at=snapshot.observed_at,
                    payload={
                        "origin": "authoritative_inventory",
                        "source": snapshot.source,
                        "snapshot_id": snapshot.snapshot_id,
                        "fingerprint": fingerprint,
                    },
                ))

            async def append_observation(
                target_type: str,
                target_id: str,
                action: str,
                payload: dict,
            ) -> bool:
                observation_id = inventory_observation_id(
                    snapshot.source,
                    snapshot.snapshot_id,
                    target_type,
                    target_id,
                    action,
                )
                if await session.get(
                    AwarenessObservationRecord,
                    observation_id,
                ):
                    result.duplicate_observations += 1
                    return False
                session.add(AwarenessObservationRecord(
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
                ))
                return True

            for entity in entities.values():
                if not await append_observation(
                    "entity",
                    entity.id,
                    "observed",
                    {"metadata": entity.metadata},
                ):
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
                    result.entities_created += 1
                    continue

                record.first_seen = min(record.first_seen, entity.first_seen)
                record.observation_count += 1
                current_boundary = record.last_seen
                if (
                    record.tombstoned_at is not None
                    and record.tombstoned_at > current_boundary
                ):
                    current_boundary = record.tombstoned_at
                if entity.last_seen < current_boundary:
                    result.skipped_newer_entities += 1
                    continue
                was_tombstoned = record.tombstoned_at is not None
                if (
                    was_tombstoned
                    and record.tombstoned_at is not None
                    and entity.last_seen >= record.tombstoned_at
                ):
                    record.tombstoned_at = None
                    record.tombstone_snapshot_id = None
                    record.tombstone_source = None
                    result.entities_reactivated += 1
                else:
                    result.entities_refreshed += 1
                record.last_seen = entity.last_seen
                record.last_incident_id = entity.last_incident_id
                record.metadata_json = dict(entity.metadata)

            covered_statement = select(AwarenessEntityRecord).where(
                AwarenessEntityRecord.tenant_id == snapshot.tenant_id,
                AwarenessEntityRecord.cluster == snapshot.cluster,
                func.lower(AwarenessEntityRecord.kind).in_(
                    [item.casefold() for item in snapshot.coverage.kinds]
                ),
            )
            if snapshot.coverage.namespaces is not None:
                covered_statement = covered_statement.where(
                    AwarenessEntityRecord.namespace.in_(
                        snapshot.coverage.namespaces
                    )
                )
            covered_rows = list(
                (await session.scalars(covered_statement)).all()
            )
            covered_entity_ids = {
                row.id
                for row in covered_rows
                if (row.metadata_json or {}).get("temporal_role") != "event"
            }
            newly_tombstoned_entity_ids: set[str] = set()
            for row in covered_rows:
                if (
                    row.id in entities
                    or (row.metadata_json or {}).get("temporal_role") == "event"
                ):
                    continue
                if row.last_seen > snapshot.observed_at:
                    result.skipped_newer_entities += 1
                    continue
                if row.tombstoned_at is not None:
                    continue
                if not await append_observation(
                    "entity",
                    row.id,
                    "tombstoned",
                    {
                        "kind": row.kind,
                        "name": row.name,
                        "namespace": row.namespace,
                    },
                ):
                    continue
                row.tombstoned_at = snapshot.observed_at
                row.tombstone_snapshot_id = snapshot.snapshot_id
                row.tombstone_source = snapshot.source
                newly_tombstoned_entity_ids.add(row.id)
                result.entities_tombstoned += 1

            for relationship in relationships.values():
                if not await append_observation(
                    "relationship",
                    relationship.id,
                    "observed",
                    {
                        "metadata": relationship.metadata,
                        "relationship": relationship.relationship,
                    },
                ):
                    continue
                record = await session.get(
                    AwarenessRelationshipRecord,
                    relationship.id,
                )
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
                    result.relationships_created += 1
                    continue

                record.first_seen = min(
                    record.first_seen,
                    relationship.first_seen,
                )
                record.observation_count += 1
                current_boundary = record.last_seen
                if (
                    record.tombstoned_at is not None
                    and record.tombstoned_at > current_boundary
                ):
                    current_boundary = record.tombstoned_at
                if relationship.last_seen < current_boundary:
                    result.skipped_newer_relationships += 1
                    continue
                was_tombstoned = record.tombstoned_at is not None
                if (
                    was_tombstoned
                    and record.tombstoned_at is not None
                    and relationship.last_seen >= record.tombstoned_at
                ):
                    record.tombstoned_at = None
                    record.tombstone_snapshot_id = None
                    record.tombstone_source = None
                    result.relationships_reactivated += 1
                else:
                    result.relationships_refreshed += 1
                record.last_seen = relationship.last_seen
                record.last_incident_id = relationship.last_incident_id
                record.metadata_json = dict(relationship.metadata)
                record.evidence_ids = list(relationship.evidence_ids)

            relationship_filters = []
            if snapshot.coverage.relationship_types:
                relationship_filters.append(
                    AwarenessRelationshipRecord.relationship.in_(
                        snapshot.coverage.relationship_types
                    )
                )
            if newly_tombstoned_entity_ids:
                relationship_filters.append(or_(
                    AwarenessRelationshipRecord.source_id.in_(
                        newly_tombstoned_entity_ids
                    ),
                    AwarenessRelationshipRecord.target_id.in_(
                        newly_tombstoned_entity_ids
                    ),
                ))

            if relationship_filters:
                relationship_statement = select(
                    AwarenessRelationshipRecord
                ).where(
                    AwarenessRelationshipRecord.tenant_id
                    == snapshot.tenant_id,
                    AwarenessRelationshipRecord.cluster == snapshot.cluster,
                    AwarenessRelationshipRecord.tombstoned_at.is_(None),
                    or_(*relationship_filters),
                )
                relationship_rows = list(
                    (await session.scalars(relationship_statement)).all()
                )
                authoritative_relationship_types = set(
                    snapshot.coverage.relationship_types
                )
                for row in relationship_rows:
                    if (
                        row.id in relationships
                        or (row.metadata_json or {}).get("temporal_role")
                        == "event"
                    ):
                        continue
                    cascaded = (
                        row.source_id in newly_tombstoned_entity_ids
                        or row.target_id in newly_tombstoned_entity_ids
                    )
                    explicitly_covered = (
                        row.relationship
                        in authoritative_relationship_types
                        and row.source_id in covered_entity_ids
                        and row.target_id in covered_entity_ids
                    )
                    if not cascaded and not explicitly_covered:
                        continue
                    if row.last_seen > snapshot.observed_at:
                        result.skipped_newer_relationships += 1
                        continue
                    if not await append_observation(
                        "relationship",
                        row.id,
                        "tombstoned",
                        {
                            "relationship": row.relationship,
                            "source_id": row.source_id,
                            "target_id": row.target_id,
                            "reason": (
                                "endpoint_tombstoned"
                                if cascaded
                                else (
                                    "absent_from_authoritative_"
                                    "relationship_inventory"
                                )
                            ),
                        },
                    ):
                        continue
                    row.tombstoned_at = snapshot.observed_at
                    row.tombstone_snapshot_id = snapshot.snapshot_id
                    row.tombstone_source = snapshot.source
                    result.relationships_tombstoned += 1

            if actor is not None:
                audit = _inventory_reconciliation_audit(
                    snapshot,
                    result,
                    actor,
                )
                session.add(AuditEventRecord(
                    id=audit.id,
                    incident_id=audit.incident_id,
                    tenant_id=audit.tenant_id,
                    actor=audit.actor,
                    action=audit.action,
                    detail=audit.detail,
                    created_at=audit.created_at,
                ))

            await session.commit()
        return result

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

    @staticmethod
    def _alert_from_row(row: AlertOccurrenceRecord) -> AlertOccurrence:
        return AlertOccurrence(
            id=row.id, tenant_id=row.tenant_id, cluster=row.cluster, namespace=row.namespace,
            target_kind=row.target_kind, target=row.target, group_key=row.group_key,
            group_id=row.group_id, alert_name=row.alert_name, severity=row.severity,
            fingerprint=row.fingerprint, status=row.status, starts_at=row.starts_at,
            ends_at=row.ends_at, received_at=row.received_at,
            last_received_at=row.last_received_at, notifications=row.notifications,
        )

    async def record_alert_occurrence(self, occurrence: AlertOccurrence, group_window_seconds: int) -> AlertOccurrence:
        async with self.sessions() as session:
            async with session.begin():
                # Serialize group assignment for one scope so concurrent alerts join the same group.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"alert-group:{occurrence.group_key}"},
                )
                row = await session.scalar(select(AlertOccurrenceRecord).where(
                    AlertOccurrenceRecord.tenant_id == occurrence.tenant_id,
                    AlertOccurrenceRecord.cluster == occurrence.cluster,
                    AlertOccurrenceRecord.fingerprint == occurrence.fingerprint,
                    AlertOccurrenceRecord.starts_at == occurrence.starts_at,
                ))
                if row is not None:
                    stored = merge_alert_notification(self._alert_from_row(row), occurrence)
                    row.status = stored.status
                    row.ends_at = stored.ends_at
                    row.last_received_at = stored.last_received_at
                    row.notifications = stored.notifications
                    return stored
                candidates = (await session.scalars(
                    select(AlertOccurrenceRecord).where(
                        AlertOccurrenceRecord.tenant_id == occurrence.tenant_id,
                        AlertOccurrenceRecord.group_key == occurrence.group_key,
                    ).order_by(AlertOccurrenceRecord.starts_at.desc()).limit(200)
                )).all()
                group_id = select_group(
                    [self._alert_from_row(item) for item in candidates],
                    occurrence, timedelta(seconds=group_window_seconds),
                ) or f"grp-{uuid4().hex[:16]}"
                stored = occurrence.model_copy(update={"group_id": group_id})
                session.add(AlertOccurrenceRecord(**stored.model_dump()))
                return stored

    async def list_alert_occurrences(
        self, *, tenant_id: str | None, allowed_clusters: frozenset[str] | None,
        cluster: str | None, since: datetime, limit: int = 5000,
    ) -> list[AlertOccurrence]:
        statement = select(AlertOccurrenceRecord).where(AlertOccurrenceRecord.last_received_at >= since)
        if tenant_id is not None:
            statement = statement.where(AlertOccurrenceRecord.tenant_id == tenant_id)
        if allowed_clusters is not None:
            statement = statement.where(AlertOccurrenceRecord.cluster.in_(sorted(allowed_clusters)))
        if cluster is not None:
            statement = statement.where(AlertOccurrenceRecord.cluster == cluster)
        statement = statement.order_by(
            AlertOccurrenceRecord.last_received_at.desc(), AlertOccurrenceRecord.id.desc(),
        ).limit(limit)
        async with self.sessions() as session:
            rows = (await session.scalars(statement)).all()
        return [self._alert_from_row(row) for row in rows]

    async def purge_awareness_retention(
        self,
        tenant_id: str,
        cluster: str,
        *,
        observation_days: int,
        tombstone_days: int,
        actor: str | None = None,
        dry_run: bool = True,
        as_of: datetime | None = None,
    ) -> AwarenessRetentionResult:
        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        observation_days = max(1, observation_days)
        tombstone_days = max(1, tombstone_days)
        observation_cutoff = now - timedelta(days=observation_days)
        tombstone_cutoff = now - timedelta(days=tombstone_days)

        ranked_observations = (
            select(
                AwarenessObservationRecord.id.label("id"),
                AwarenessObservationRecord.target_type.label("target_type"),
                AwarenessObservationRecord.observed_at.label("observed_at"),
                func.row_number().over(
                    partition_by=(
                        AwarenessObservationRecord.target_type,
                        AwarenessObservationRecord.target_id,
                    ),
                    order_by=(
                        AwarenessObservationRecord.observed_at.desc(),
                        AwarenessObservationRecord.id.desc(),
                    ),
                ).label("retention_rank"),
            )
            .where(
                AwarenessObservationRecord.tenant_id == tenant_id,
                AwarenessObservationRecord.cluster == cluster,
            )
            .subquery()
        )
        observation_candidate_ids = select(
            ranked_observations.c.id
        ).where(
            ranked_observations.c.target_type != "inventory_snapshot",
            ranked_observations.c.observed_at < observation_cutoff,
            ranked_observations.c.retention_rank > 1,
        )
        latest_old_statement = select(func.count()).select_from(
            ranked_observations
        ).where(
            ranked_observations.c.target_type != "inventory_snapshot",
            ranked_observations.c.observed_at < observation_cutoff,
            ranked_observations.c.retention_rank == 1,
        )

        relationship_candidate_ids = select(
            AwarenessRelationshipRecord.id
        ).where(
            AwarenessRelationshipRecord.tenant_id == tenant_id,
            AwarenessRelationshipRecord.cluster == cluster,
            AwarenessRelationshipRecord.tombstoned_at.is_not(None),
            AwarenessRelationshipRecord.tombstoned_at < tombstone_cutoff,
        )
        remaining_relationship_exists = (
            select(AwarenessRelationshipRecord.id)
            .where(
                AwarenessRelationshipRecord.tenant_id == tenant_id,
                AwarenessRelationshipRecord.cluster == cluster,
                or_(
                    AwarenessRelationshipRecord.source_id
                    == AwarenessEntityRecord.id,
                    AwarenessRelationshipRecord.target_id
                    == AwarenessEntityRecord.id,
                ),
                AwarenessRelationshipRecord.id.not_in(
                    relationship_candidate_ids
                ),
            )
            .exists()
        )
        old_tombstoned_entity_scope = (
            AwarenessEntityRecord.tenant_id == tenant_id,
            AwarenessEntityRecord.cluster == cluster,
            AwarenessEntityRecord.tombstoned_at.is_not(None),
            AwarenessEntityRecord.tombstoned_at < tombstone_cutoff,
        )
        entity_candidate_ids = select(AwarenessEntityRecord.id).where(
            *old_tombstoned_entity_scope,
            ~remaining_relationship_exists,
        )

        async with self.sessions() as session:
            observations_eligible = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(
                            observation_candidate_ids.subquery()
                        )
                    )
                )
                or 0
            )
            latest_preserved = int(
                (await session.scalar(latest_old_statement)) or 0
            )
            receipts_preserved = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(
                            AwarenessObservationRecord
                        ).where(
                            AwarenessObservationRecord.tenant_id == tenant_id,
                            AwarenessObservationRecord.cluster == cluster,
                            AwarenessObservationRecord.target_type
                            == "inventory_snapshot",
                        )
                    )
                )
                or 0
            )
            relationships_eligible = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(
                            relationship_candidate_ids.subquery()
                        )
                    )
                )
                or 0
            )
            old_entities = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(
                            AwarenessEntityRecord
                        ).where(*old_tombstoned_entity_scope)
                    )
                )
                or 0
            )
            entities_eligible = int(
                (
                    await session.scalar(
                        select(func.count()).select_from(
                            entity_candidate_ids.subquery()
                        )
                    )
                )
                or 0
            )

            result = AwarenessRetentionResult(
                tenant_id=tenant_id,
                cluster=cluster,
                dry_run=dry_run,
                as_of=now,
                observation_retention_days=observation_days,
                tombstone_retention_days=tombstone_days,
                observation_cutoff=observation_cutoff,
                tombstone_cutoff=tombstone_cutoff,
                observations_eligible=observations_eligible,
                observations_deleted=0,
                latest_observations_preserved=latest_preserved,
                inventory_receipts_preserved=receipts_preserved,
                tombstoned_relationships_eligible=relationships_eligible,
                tombstoned_relationships_deleted=0,
                tombstoned_entities_eligible=entities_eligible,
                tombstoned_entities_deleted=0,
                tombstoned_entities_blocked=max(
                    0,
                    old_entities - entities_eligible,
                ),
            )

            if dry_run:
                return result

            observations_result = await session.execute(
                delete(AwarenessObservationRecord).where(
                    AwarenessObservationRecord.id.in_(
                        observation_candidate_ids
                    )
                )
            )
            relationships_result = await session.execute(
                delete(AwarenessRelationshipRecord).where(
                    AwarenessRelationshipRecord.id.in_(
                        relationship_candidate_ids
                    )
                )
            )
            entities_result = await session.execute(
                delete(AwarenessEntityRecord).where(
                    AwarenessEntityRecord.id.in_(entity_candidate_ids)
                )
            )

            result.observations_deleted = int(
                observations_result.rowcount or 0
            )
            result.tombstoned_relationships_deleted = int(
                relationships_result.rowcount or 0
            )
            result.tombstoned_entities_deleted = int(
                entities_result.rowcount or 0
            )

            if actor is not None:
                audit = _awareness_retention_audit(result, actor)
                session.add(AuditEventRecord(
                    id=audit.id,
                    incident_id=audit.incident_id,
                    tenant_id=audit.tenant_id,
                    actor=audit.actor,
                    action=audit.action,
                    detail=audit.detail,
                    created_at=audit.created_at,
                ))
            await session.commit()
        return result

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
