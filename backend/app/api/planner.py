from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agentic.models import InvestigationPlan
from app.agentic.planner import build_investigation_plan
from app.auth import Principal, authorize_cluster, require_role
from app.operations import append_audit
from app.schemas import IncidentInput

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])


@router.post("/plan", response_model=InvestigationPlan)
async def plan_investigation(
    incident: IncidentInput,
    request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    settings = request.app.state.settings
    if not settings.agentic_planner_enabled:
        raise HTTPException(
            status_code=404,
            detail="agentic planner is disabled",
        )

    tenant_id = (
        incident.tenant_id
        if principal.tenant_id == "*"
        else principal.tenant_id
    )
    if tenant_id == "*":
        raise HTTPException(
            status_code=422,
            detail="a concrete tenant_id is required for a global principal",
        )
    scoped_incident = incident.model_copy(
        update={"tenant_id": tenant_id}
    )
    authorize_cluster(principal, scoped_incident.cluster)
    plan = await build_investigation_plan(
        request.app.state.repo,
        incident=scoped_incident,
        settings=settings,
    )

    await append_audit(
        request.app,
        tenant_id=scoped_incident.tenant_id,
        actor=principal.subject,
        action="planner.plan_generated",
        detail={
            "plan_id": plan.id,
            "cluster": plan.cluster,
            "specialists": [
                task.specialist for task in plan.tasks
            ],
            "graph_context_nodes": len(plan.graph_context.entity_ids),
            "graph_context_truncated": plan.graph_context.truncated,
        },
    )
    return plan
