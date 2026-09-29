from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.schemas import Entity, GraphEdge, InvestigationResult


CHANGE_EVENT_KIND = "ChangeEvent"
CHANGE_EVENT_RELATIONSHIPS = {
    "observed_on",
    "targets",
    "targets_service",
    "revision_from",
}


def _stable_event_key(
    application: str,
    revision: str,
    occurred_at: datetime,
) -> str:
    payload = (
        f"{application}\x1f{revision}\x1f"
        f"{occurred_at.astimezone(timezone.utc).isoformat()}"
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:20]


def _parse_datetime(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    else:
        return fallback
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entity_by_kind(
    entities: list[Entity],
    kind: str,
    *,
    name: str | None = None,
    namespace: str | None = None,
) -> Entity | None:
    for entity in entities:
        if entity.kind.lower() != kind.lower():
            continue
        if name is not None and entity.name != name:
            continue
        if namespace is not None and entity.namespace != namespace:
            continue
        return entity
    return None


def augment_with_change_events(
    result: InvestigationResult,
) -> tuple[list[Entity], list[GraphEdge]]:
    """Derive immutable deployment/change event nodes from verified RCA evidence.

    This transformation is context-only. It does not mutate the InvestigationResult
    and it never creates blast-radius semantics or causal hypotheses.
    """

    entities = [item.model_copy(deep=True) for item in result.entities]
    graph_edges = [item.model_copy(deep=True) for item in result.graph_edges]
    existing_ids = {item.id for item in entities}

    git_evidence = [
        item
        for item in result.evidence
        if item.source == "github" and item.category == "source-change"
    ]

    for change_evidence in result.evidence:
        if not (
            change_evidence.source == "argocd"
            and change_evidence.category == "change-correlation"
        ):
            continue

        facts = change_evidence.facts
        application = str(facts.get("application") or "").strip()
        revision = str(facts.get("revision") or "").strip()
        repo_url = str(facts.get("repo_url") or "").strip()
        if not application:
            continue

        finished_at = facts.get("finished_at")
        if not finished_at:
            # A current Argo CD revision without operation time is correlation,
            # not an authoritative deployment event timestamp.
            continue
        occurred_at = _parse_datetime(
            finished_at,
            change_evidence.observed_at,
        )
        event_key = _stable_event_key(
            application,
            revision or "unknown",
            occurred_at,
        )
        local_id = f"change:{event_key}"
        if local_id in existing_ids:
            continue

        deployment = _entity_by_kind(
            entities,
            "Deployment",
            namespace=result.incident.namespace,
        )
        service = _entity_by_kind(
            entities,
            "Service",
            name=result.incident.service,
            namespace=result.incident.namespace,
        )
        if service is None:
            service = _entity_by_kind(
                entities,
                "Service",
                namespace=result.incident.namespace,
            )
        argo = _entity_by_kind(
            entities,
            "ArgoCDApplication",
            name=application,
        )
        repository = _entity_by_kind(
            entities,
            "GitRepository",
            name=repo_url or None,
        )

        matching_git = next(
            (
                item
                for item in git_evidence
                if not revision
                or str(item.facts.get("sha") or "").strip() == revision
            ),
            None,
        )
        evidence_ids = [change_evidence.id]
        commit_metadata: dict[str, Any] = {}
        if matching_git is not None:
            evidence_ids.append(matching_git.id)
            commit_metadata = {
                "commit_sha": matching_git.facts.get("sha"),
                "commit_message": matching_git.facts.get("message"),
                "commit_author": matching_git.facts.get("author"),
                "commit_url": matching_git.facts.get("url"),
                "commit_at": matching_git.observed_at.isoformat(),
            }

        service_name = service.name if service is not None else result.incident.service
        workload_name = deployment.name if deployment is not None else None
        event_name = (
            f"{application}@{(revision or 'unknown')[:12]}"
            f"#{occurred_at.strftime('%Y%m%dT%H%M%SZ')}"
        )
        event = Entity(
            id=local_id,
            kind=CHANGE_EVENT_KIND,
            name=event_name,
            namespace=result.incident.namespace,
            metadata={
                "temporal_role": "event",
                "change_type": "deployment",
                "occurred_at": occurred_at.isoformat(),
                "application": application,
                "revision": revision or None,
                "repository": repo_url or None,
                "service_name": service_name,
                "workload_kind": "Deployment" if deployment is not None else None,
                "workload_name": workload_name,
                "sync_status": facts.get("sync_status"),
                "health": facts.get("health"),
                "operation_phase": facts.get("operation_phase"),
                "incident_id": result.incident_id,
                "evidence_ids": evidence_ids,
                **commit_metadata,
            },
        )
        entities.append(event)
        existing_ids.add(local_id)

        def add_event_edge(
            target: Entity | None,
            relationship: str,
        ) -> None:
            if target is None:
                return
            graph_edges.append(GraphEdge(
                source=local_id,
                target=target.id,
                relationship=relationship,
                metadata={
                    "temporal_role": "event",
                    "occurred_at": occurred_at.isoformat(),
                },
                evidence_ids=evidence_ids,
            ))

        add_event_edge(argo, "observed_on")
        add_event_edge(deployment, "targets")
        add_event_edge(service, "targets_service")
        add_event_edge(repository, "revision_from")

    return entities, graph_edges


class AwarenessChangeEvent(BaseModel):
    entity_id: str
    tenant_id: str
    cluster: str
    namespace: str | None = None
    application: str
    revision: str | None = None
    repository: str | None = None
    service_name: str | None = None
    workload_kind: str | None = None
    workload_name: str | None = None
    change_type: str = "deployment"
    occurred_at: datetime
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int
    last_incident_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChangeEntityLike(Protocol):
    id: str
    tenant_id: str
    cluster: str
    namespace: str | None
    metadata: dict[str, Any]
    first_seen: datetime
    last_seen: datetime
    last_incident_id: str
    observation_count: int


def change_event_from_entity(
    entity: ChangeEntityLike,
) -> AwarenessChangeEvent | None:
    metadata = entity.metadata or {}
    if metadata.get("temporal_role") != "event":
        return None
    application = str(metadata.get("application") or "").strip()
    occurred_raw = metadata.get("occurred_at")
    if not application or not occurred_raw:
        return None
    occurred_at = _parse_datetime(occurred_raw, entity.first_seen)
    return AwarenessChangeEvent(
        entity_id=entity.id,
        tenant_id=entity.tenant_id,
        cluster=entity.cluster,
        namespace=entity.namespace,
        application=application,
        revision=metadata.get("revision"),
        repository=metadata.get("repository"),
        service_name=metadata.get("service_name"),
        workload_kind=metadata.get("workload_kind"),
        workload_name=metadata.get("workload_name"),
        change_type=str(metadata.get("change_type") or "deployment"),
        occurred_at=occurred_at,
        first_observed_at=entity.first_seen,
        last_observed_at=entity.last_seen,
        observation_count=entity.observation_count,
        last_incident_id=entity.last_incident_id,
        evidence_ids=[
            str(item)
            for item in metadata.get("evidence_ids", [])
            if item
        ],
        metadata=metadata,
    )


async def query_change_events(
    repo,
    *,
    tenant_id: str,
    cluster: str,
    service_name: str | None = None,
    namespace: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[AwarenessChangeEvent]:
    normalized_since = (
        _parse_datetime(since, since) if since is not None else None
    )
    normalized_until = (
        _parse_datetime(until, until) if until is not None else None
    )
    if (
        normalized_since is not None
        and normalized_until is not None
        and normalized_since > normalized_until
    ):
        raise ValueError("since must be earlier than or equal to until")

    fetch_limit = max(1, min(max(limit * 3, 100), 500))
    entities = await repo.find_awareness_entities(
        tenant_id,
        cluster,
        namespace=namespace,
        kind=CHANGE_EVENT_KIND,
        limit=fetch_limit,
    )
    events: list[AwarenessChangeEvent] = []
    for entity in entities:
        event = change_event_from_entity(entity)
        if event is None:
            continue
        if service_name and event.service_name != service_name:
            continue
        if normalized_since and event.occurred_at < normalized_since:
            continue
        if normalized_until and event.occurred_at > normalized_until:
            continue
        events.append(event)

    events.sort(key=lambda item: item.occurred_at, reverse=True)
    return events[:max(1, min(limit, 200))]
