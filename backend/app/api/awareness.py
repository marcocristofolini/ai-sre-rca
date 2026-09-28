from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import Principal, authorize_cluster, authorize_tenant, require_role
from app.awareness.models import AwarenessGraphSnapshot, AwarenessObservation
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
