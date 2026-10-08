from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.auth import Principal, authorize_cluster, require_role
from app.schemas import AuditEvent, CapabilityReport, SourceCapability

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/me")
async def me(
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    return {
        "subject": principal.subject,
        "tenant_id": principal.tenant_id,
        "roles": sorted(principal.roles),
        "clusters": sorted(principal.clusters),
        "auth_enabled": request.app.state.settings.auth_enabled,
        "can_investigate": principal.has_role("investigator", request.app.state.settings),
        "agentic_execution_enabled": request.app.state.settings.agentic_planner_enabled and request.app.state.settings.agentic_execution_enabled,
        "demo_mode": request.app.state.settings.demo_mode,
    }


@router.get("/capabilities", response_model=CapabilityReport)
async def capabilities(
    cluster: str,
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    authorize_cluster(principal, cluster)
    raw = await request.app.state.tools.capability_report(cluster)
    sources = [SourceCapability.model_validate(item) for item in raw]
    return CapabilityReport(
        cluster=cluster,
        tenant_id=principal.tenant_id,
        auth_enabled=request.app.state.settings.auth_enabled,
        notifications_enabled=request.app.state.settings.notifications_enabled,
        persistence=(
            "postgres+pgvector"
            if request.app.state.settings.database_url
            else "memory"
        ),
        sources=sources,
    )


@router.post("/retention/purge")
async def purge_retention(
    request: Request,
    principal: Principal = Depends(require_role("admin")),
):
    settings = request.app.state.settings
    tenant_scope = None if principal.tenant_id == "*" else principal.tenant_id
    deleted = await request.app.state.repo.purge_expired(
        settings.incident_retention_days,
        settings.audit_retention_days,
        tenant_id=tenant_scope,
    )
    await request.app.state.repo.append_audit(
        AuditEvent(
            tenant_id=principal.tenant_id,
            actor=principal.subject,
            action="retention.purge",
            detail={
                **deleted,
                "scope": "all" if tenant_scope is None else tenant_scope,
            },
        )
    )
    return {
        "retention": {
            "incident_days": settings.incident_retention_days,
            "audit_days": settings.audit_retention_days,
        },
        "deleted": deleted,
    }
