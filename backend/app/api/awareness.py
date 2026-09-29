from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import Principal, authorize_cluster, authorize_tenant, require_role
from app.awareness.models import (
    AwarenessChangeEvent,
    AwarenessGraphSnapshot,
    AwarenessObservation,
)
from app.awareness.topology import (
    AmbiguousServiceError,
    AwarenessEntityNotFoundError,
    BlastRadiusResult,
    ReconciledGraphSnapshot,
    ServiceTopologyProjection,
    build_service_topology,
    calculate_blast_radius,
    reconcile_snapshot,
)

router = APIRouter(prefix="/api/v1/awareness", tags=["awareness"])


def _tenant_scope(principal: Principal, requested_tenant: str | None) -> str:
    if principal.tenant_id == "*":
        if not requested_tenant:
            raise HTTPException(
                status_code=422,
                detail="tenant_id is required for a global principal",
            )
        return requested_tenant

    if requested_tenant:
        authorize_tenant(principal, requested_tenant)
    return principal.tenant_id


@router.get("/graph", response_model=AwarenessGraphSnapshot)
async def get_graph(
    cluster: str,
    request: Request,
    namespace: str | None = None,
    kind: str | None = None,
    tenant_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    return await request.app.state.repo.get_awareness_graph(
        tenant_id=tenant,
        cluster=cluster,
        namespace=namespace,
        kind=kind,
        limit=limit,
    )


@router.get(
    "/history/{target_id}",
    response_model=list[AwarenessObservation],
)
async def get_history(
    target_id: str,
    cluster: str,
    request: Request,
    tenant_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    return await request.app.state.repo.get_awareness_history(
        target_id=target_id,
        tenant_id=tenant,
        cluster=cluster,
        limit=limit,
    )




@router.get("/changes", response_model=list[AwarenessChangeEvent])
async def list_change_events(
    cluster: str,
    request: Request,
    namespace: str | None = None,
    service: str | None = None,
    subject_entity_id: str | None = None,
    event_type: Literal["deployment_sync", "source_commit"] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    tenant_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    for label, value in (("since", since), ("until", until)):
        if value is not None and value.tzinfo is None:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must include a timezone offset",
            )
    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=422,
            detail="since must be earlier than or equal to until",
        )

    resolved_subject_id = subject_entity_id
    if service is not None:
        candidates = await request.app.state.repo.find_awareness_entities(
            tenant,
            cluster,
            name=service,
            namespace=namespace,
            kind="Service",
            limit=3,
        )
        if not candidates:
            raise HTTPException(
                status_code=404,
                detail=f"service not found: {service}",
            )
        if len(candidates) > 1 and namespace is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "service name is ambiguous across namespaces: "
                    f"{service}"
                ),
            )
        service_id = candidates[0].id
        if (
            resolved_subject_id is not None
            and resolved_subject_id != service_id
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "subject_entity_id does not match the resolved service"
                ),
            )
        resolved_subject_id = service_id

    return await request.app.state.repo.list_awareness_change_events(
        tenant,
        cluster,
        namespace=namespace,
        event_type=event_type,
        subject_entity_id=resolved_subject_id,
        since=since,
        until=until,
        limit=limit,
    )


@router.get("/topology", response_model=ReconciledGraphSnapshot)
async def get_reconciled_topology(
    cluster: str,
    request: Request,
    namespace: str | None = None,
    kind: str | None = None,
    tenant_id: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    snapshot = await request.app.state.repo.get_awareness_graph(
        tenant_id=tenant,
        cluster=cluster,
        namespace=namespace,
        kind=kind,
        limit=limit,
    )
    return reconcile_snapshot(
        snapshot,
        stale_after_minutes=(
            request.app.state.settings.awareness_stale_after_minutes
        ),
    )


@router.get(
    "/services/{service_name}",
    response_model=ServiceTopologyProjection,
)
async def get_service_topology(
    service_name: str,
    cluster: str,
    request: Request,
    namespace: str | None = None,
    tenant_id: str | None = None,
    depth: int = Query(default=2, ge=1, le=8),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    settings = request.app.state.settings
    try:
        return await build_service_topology(
            request.app.state.repo,
            tenant_id=tenant,
            cluster=cluster,
            service_name=service_name,
            namespace=namespace,
            requested_depth=depth,
            max_depth=settings.awareness_max_traversal_depth,
            max_nodes=settings.awareness_max_traversal_nodes,
            max_edges=settings.awareness_max_traversal_edges,
            stale_after_minutes=settings.awareness_stale_after_minutes,
        )
    except AwarenessEntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AmbiguousServiceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/blast-radius/{target_id}",
    response_model=BlastRadiusResult,
)
async def get_blast_radius(
    target_id: str,
    cluster: str,
    request: Request,
    tenant_id: str | None = None,
    depth: int = Query(default=3, ge=1, le=8),
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    tenant = _tenant_scope(principal, tenant_id)
    settings = request.app.state.settings
    try:
        return await calculate_blast_radius(
            request.app.state.repo,
            tenant_id=tenant,
            cluster=cluster,
            source_id=target_id,
            requested_depth=depth,
            max_depth=settings.awareness_max_traversal_depth,
            max_nodes=settings.awareness_max_traversal_nodes,
            max_edges=settings.awareness_max_traversal_edges,
            stale_after_minutes=settings.awareness_stale_after_minutes,
        )
    except AwarenessEntityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
