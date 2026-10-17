"""Resolve dynamic Copilot scope from authorized authoritative inventory only."""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import authorize_cluster
from app.schemas import IncidentInput

MAX_WINDOW_MINUTES = 60


def scope_error(code: str, message: str, status: int = 409):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


class ScopeSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cluster: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_.-]+$")
    kind: Literal["Service", "Pod"]
    name: str = Field(min_length=1, max_length=253, pattern=r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")
    namespace: str | None = Field(default=None, min_length=1, max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def validate_window(self):
        if any(dt.tzinfo is None or dt.utcoffset() is None for dt in (self.starts_at, self.ends_at)):
            raise ValueError("time bounds require a timezone")
        self.starts_at = self.starts_at.astimezone(timezone.utc)
        self.ends_at = self.ends_at.astimezone(timezone.utc)
        seconds = (self.ends_at - self.starts_at).total_seconds()
        if not 300 <= seconds <= MAX_WINDOW_MINUTES * 60 or seconds % 60:
            raise ValueError("select a positive whole-minute window of 5 to 60 minutes")
        return self


def authorized_clusters(settings, principal, source):
    # An empty registry is the existing single-cluster configuration, not a wildcard.
    configured = set(settings.cluster_config) or {source.incident.cluster}
    return sorted(cluster for cluster in configured if principal.can_access_cluster(cluster))


async def inventory_ready(repo, settings, tenant, cluster):
    health = await repo.get_awareness_health(tenant, cluster,
        stale_after_minutes=settings.awareness_stale_after_minutes,
        inventory_max_age_minutes=settings.awareness_inventory_max_age_minutes,
        authoritative_sources=settings.awareness_authoritative_inventory_source_set)
    if not any(item.freshness == 'current' for item in health.inventory_sources):
        scope_error('inventory_unavailable', 'Authoritative inventory is missing or stale. Refresh inventory before selecting a target.')


async def verified_identity(repo, settings, entity):
    if entity.tombstoned_at is not None:
        scope_error('target_unavailable', 'Target is no longer present in authoritative inventory.')
    history = await repo.get_awareness_history(entity.id, entity.tenant_id, entity.cluster, limit=20)
    now = datetime.now(timezone.utc)
    for item in history:
        data = item.payload
        if data.get('origin') != 'authoritative_inventory' or data.get('source') not in settings.awareness_authoritative_inventory_source_set:
            continue
        if data.get('action') != 'observed' or not 0 <= (now - item.observed_at).total_seconds() <= settings.awareness_inventory_max_age_minutes * 60:
            break
        uid = data.get('metadata', {}).get('kubernetes_uid')
        if isinstance(uid, str) and 0 < len(uid) <= 255:
            return {'entity_id': entity.id, 'uid': uid, 'telemetry_service': data.get('metadata', {}).get('telemetry_service')}
        break
    scope_error('target_unverified', 'Target identity is missing or stale. Refresh authoritative inventory.')


async def scope_options(source, request, principal, cluster=None):
    repo, settings = request.app.state.repo, request.app.state.settings
    clusters = authorized_clusters(settings, principal, source)
    cluster = cluster or source.incident.cluster
    authorize_cluster(principal, cluster)
    if cluster not in clusters:
        scope_error('cluster_unavailable', 'Cluster is not configured for investigation.', 422)
    await inventory_ready(repo, settings, source.incident.tenant_id, cluster)
    choices = []
    truncated = False
    for kind in ('Service', 'Pod'):
        entities = await repo.find_awareness_entities(source.incident.tenant_id, cluster, kind=kind, limit=11)
        truncated |= len(entities) > 10
        for entity in entities[:10]:
            try:
                await verified_identity(repo, settings, entity)
            except HTTPException as exc:
                if exc.status_code != 409: raise
                continue
            choices.append({'kind': kind, 'name': entity.name, 'namespace': entity.namespace})
    return {'clusters': clusters, 'cluster': cluster, 'targets': choices, 'truncated': truncated,
        'min_window_minutes': max(5, settings.service_graph_window_minutes, settings.dependency_window_minutes),
        'max_window_minutes': MAX_WINDOW_MINUTES}


async def resolve_scope(source, selection, request, principal, question):
    settings, repo = request.app.state.settings, request.app.state.repo
    authorize_cluster(principal, selection.cluster)
    if selection.cluster not in authorized_clusters(settings, principal, source):
        scope_error('cluster_unavailable', 'Cluster is not configured for investigation.', 422)
    now = datetime.now(timezone.utc)
    minutes = int((selection.ends_at - selection.starts_at).total_seconds() / 60)
    minimum = max(5, settings.service_graph_window_minutes, settings.dependency_window_minutes)
    if minutes < minimum or selection.ends_at > now or selection.starts_at < now - timedelta(days=settings.incident_retention_days):
        scope_error('invalid_time_window', f'Select {minimum} to 60 minutes within the configured retention period, ending in the past.', 422)
    if not settings.query_budget_enabled or min(settings.query_budget_max_calls, settings.query_budget_max_calls_per_source, settings.query_budget_max_wall_seconds) <= 0:
        scope_error('budgets_unavailable', 'Bounded query budgets are required for dynamic scope.', 503)
    await inventory_ready(repo, settings, source.incident.tenant_id, selection.cluster)
    entities = await repo.find_awareness_entities(source.incident.tenant_id, selection.cluster,
        name=selection.name, namespace=selection.namespace, kind=selection.kind, limit=2)
    if not entities:
        scope_error('target_unavailable', 'No authorized inventory target matches this selection.', 404)
    if len(entities) != 1:
        scope_error('ambiguous_target', 'More than one target matches. Select an explicit namespace.')
    entity = entities[0]
    identity = await verified_identity(repo, settings, entity)
    if not entity.namespace:
        scope_error('target_unverified', 'A namespaced target is required.')
    # Capability probes are bounded, read-only and checked again at confirmation.
    try:
        capabilities = await asyncio.wait_for(request.app.state.tools.capability_report(selection.cluster), timeout=8)
    except Exception:
        scope_error('telemetry_unavailable', 'Telemetry readiness could not be verified.', 503)
    needed = {'prometheus', 'loki'} | ({'kubernetes'} if selection.kind == 'Pod' else set())
    healthy = {item.get('source') for item in capabilities if isinstance(item, dict) and item.get('status') == 'healthy' and item.get('cluster') == selection.cluster}
    if not needed <= healthy:
        scope_error('telemetry_unavailable', 'Required telemetry is unavailable. No investigation was started.', 503)
    service = None
    if selection.kind == 'Service':
        service = identity.get('telemetry_service')
        if not isinstance(service, str) or not re.fullmatch(r'[a-zA-Z0-9_.-]{1,253}', service):
            scope_error('telemetry_identity_missing', 'Service requires an explicit telemetry_service identity in authoritative inventory.')
    incident = IncidentInput(title=f'Copilot: {question}'[:200], tenant_id=source.incident.tenant_id,
        cluster=entity.cluster, namespace=entity.namespace,
        pod=entity.name if entity.kind.casefold() == 'pod' else None,
        service=service, labels={'copilot_target_kind': selection.kind, 'copilot_target_id': entity.id, 'copilot_target_uid': identity['uid']},
        alert_name='ManualInvestigation', severity=source.incident.severity,
        starts_at=selection.starts_at, ends_at=selection.ends_at)
    budget = {'max_calls': settings.query_budget_max_calls, 'max_calls_per_source': settings.query_budget_max_calls_per_source,
        'max_wall_seconds': settings.query_budget_max_wall_seconds}
    return incident, identity, budget


async def validate_queued_scope(repo, settings, incident):
    target_id = incident.labels.get('copilot_target_id')
    if not target_id:
        return
    if not settings.query_budget_enabled or min(settings.query_budget_max_calls, settings.query_budget_max_calls_per_source, settings.query_budget_max_wall_seconds) <= 0:
        scope_error('budgets_unavailable', 'Bounded query budgets are required for dynamic scope.', 503)
    await inventory_ready(repo, settings, incident.tenant_id, incident.cluster)
    entity = await repo.get_awareness_entity(target_id, incident.tenant_id, incident.cluster)
    if entity is None:
        scope_error('target_unavailable', 'Confirmed target is no longer available.')
    identity = await verified_identity(repo, settings, entity)
    if identity['uid'] != incident.labels.get('copilot_target_uid') or entity.namespace != incident.namespace:
        scope_error('target_changed', 'Confirmed target identity changed before execution.')
    if entity.kind == 'Pod' and entity.name != incident.pod:
        scope_error('target_changed', 'Confirmed workload changed before execution.')
    if entity.kind == 'Service' and identity.get('telemetry_service') != incident.service:
        scope_error('target_changed', 'Confirmed telemetry identity changed before execution.')
