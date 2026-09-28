from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import Principal, authorize_cluster, authorize_tenant, require_role
from app.awareness.models import AwarenessGraphSnapshot, AwarenessObservation

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
