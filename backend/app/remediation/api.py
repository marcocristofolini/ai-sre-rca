from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.auth import (
    Principal,
    authorize_cluster,
    require_any_exact_configured_role,
)
from app.remediation.service import (
    RemediationApprovalError,
    RemediationPlanNotFound,
    RemediationPlanStateError,
)
from app.schemas import (
    RemediationDecisionRequest,
    RemediationPlan,
    ScaleDeploymentPlanRequest,
)

router = APIRouter(prefix="/api/v1/remediation", tags=["remediation"])

_plan_requester = require_any_exact_configured_role(
    "investigator_role",
    "remediator_role",
)
_remediator = require_any_exact_configured_role("remediator_role")


def _tenant_scope(principal: Principal) -> str | None:
    return None if principal.tenant_id == "*" else principal.tenant_id


async def _plan_for_principal(
    request: Request,
    plan_id: str,
    principal: Principal,
) -> RemediationPlan:
    try:
        plan = await request.app.state.remediation_service.get_plan(
            plan_id,
            tenant_id=_tenant_scope(principal),
        )
    except RemediationPlanNotFound as exc:
        raise HTTPException(status_code=404, detail="remediation plan not found") from exc
    authorize_cluster(principal, plan.cluster)
    return plan


@router.post("/plans", response_model=RemediationPlan)
async def create_plan(
    payload: ScaleDeploymentPlanRequest,
    request: Request,
    principal: Principal = Depends(_plan_requester),
):
    incident = await request.app.state.repo.get(
        payload.incident_id,
        tenant_id=_tenant_scope(principal),
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    authorize_cluster(principal, incident.incident.cluster)
    return await request.app.state.remediation_service.create_plan(
        payload,
        incident,
        actor=principal.subject,
    )


@router.get("/plans/{plan_id}", response_model=RemediationPlan)
async def get_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(_plan_requester),
):
    return await _plan_for_principal(request, plan_id, principal)


@router.get("/plans", response_model=list[RemediationPlan])
async def list_plans(
    request: Request,
    incident_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(_plan_requester),
):
    if principal.tenant_id == "*":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="global remediation plan listing requires an explicit tenant",
        )
    plans = await request.app.state.remediation_service.list_plans(
        tenant_id=principal.tenant_id,
        incident_id=incident_id,
        limit=limit,
    )
    return [plan for plan in plans if principal.can_access_cluster(plan.cluster)]


@router.post("/plans/{plan_id}/approve", response_model=RemediationPlan)
async def approve_plan(
    plan_id: str,
    payload: RemediationDecisionRequest,
    request: Request,
    principal: Principal = Depends(_remediator),
):
    plan = await _plan_for_principal(request, plan_id, principal)
    try:
        return await request.app.state.remediation_service.approve(
            plan,
            actor=principal.subject,
            note=payload.note,
        )
    except RemediationApprovalError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RemediationPlanStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/reject", response_model=RemediationPlan)
async def reject_plan(
    plan_id: str,
    payload: RemediationDecisionRequest,
    request: Request,
    principal: Principal = Depends(_remediator),
):
    plan = await _plan_for_principal(request, plan_id, principal)
    try:
        return await request.app.state.remediation_service.reject(
            plan,
            actor=principal.subject,
            note=payload.note,
        )
    except RemediationPlanStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/execute", response_model=RemediationPlan)
async def execute_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(_remediator),
):
    plan = await _plan_for_principal(request, plan_id, principal)
    incident = await request.app.state.repo.get(
        plan.incident_id,
        tenant_id=_tenant_scope(principal),
    )
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")
    try:
        return await request.app.state.remediation_service.execute(
            plan,
            incident,
            actor=principal.subject,
        )
    except RemediationPlanStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/plans/{plan_id}/rollback", response_model=RemediationPlan)
async def rollback_plan(
    plan_id: str,
    payload: RemediationDecisionRequest,
    request: Request,
    principal: Principal = Depends(_remediator),
):
    plan = await _plan_for_principal(request, plan_id, principal)
    try:
        return await request.app.state.remediation_service.rollback(
            plan,
            actor=principal.subject,
            note=payload.note,
        )
    except RemediationPlanStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
