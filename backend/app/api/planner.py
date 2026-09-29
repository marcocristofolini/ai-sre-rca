from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.models import (
    AgenticExecutionResult,
    AgenticInvestigationResult,
    InvestigationPlan,
)
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
        advisor=getattr(
            request.app.state,
            "planning_advisor",
            None,
        ),
    )

    await append_audit(
        request.app,
        tenant_id=scoped_incident.tenant_id,
        actor=principal.subject,
        action="planner.plan_generated",
        detail={
            "plan_id": plan.id,
            "cluster": plan.cluster,
            "planner_origin": plan.planner_origin,
            "model_requested_specialists": (
                plan.advisory_trace.requested_specialists
                if plan.advisory_trace
                else []
            ),
            "model_rejected_specialists": (
                plan.advisory_trace.rejected_specialists
                if plan.advisory_trace
                else []
            ),
            "specialists": [
                task.specialist for task in plan.tasks
            ],
            "graph_context_nodes": len(plan.graph_context.entity_ids),
            "graph_context_truncated": plan.graph_context.truncated,
        },
    )
    return plan


@router.post("/execute", response_model=AgenticExecutionResult)
async def execute_agentic_investigation(
    incident: IncidentInput,
    request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    settings = request.app.state.settings
    if (
        not settings.agentic_planner_enabled
        or not settings.agentic_execution_enabled
    ):
        raise HTTPException(
            status_code=404,
            detail="agentic execution is disabled",
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

    # Recompile server-side immediately before execution. The caller cannot
    # submit arbitrary tasks, budgets, tools or dependencies for execution.
    plan = await build_investigation_plan(
        request.app.state.repo,
        incident=scoped_incident,
        settings=settings,
        advisor=getattr(
            request.app.state,
            "planning_advisor",
            None,
        ),
    )
    execution = await execute_investigation_plan(
        plan,
        tools=request.app.state.tools,
        settings=settings,
    )

    await append_audit(
        request.app,
        tenant_id=scoped_incident.tenant_id,
        actor=principal.subject,
        action="planner.execution_completed",
        detail={
            "plan_id": plan.id,
            "cluster": plan.cluster,
            "status": execution.status,
            "specialists": {
                result.specialist: result.status
                for result in execution.specialist_results
            },
            "evidence_count": len(execution.shared_evidence.evidence),
            "evidence_integrity_ok": (
                execution.shared_evidence.evidence_integrity_ok
            ),
            "query_budget": execution.query_budget,
        },
    )
    return execution


@router.post("/investigate", response_model=AgenticInvestigationResult)
async def investigate_agentically(
    incident: IncidentInput,
    request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    settings = request.app.state.settings
    if (
        not settings.agentic_planner_enabled
        or not settings.agentic_execution_enabled
    ):
        raise HTTPException(
            status_code=404,
            detail="agentic investigation is disabled",
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
        advisor=getattr(
            request.app.state,
            "planning_advisor",
            None,
        ),
    )
    execution = await execute_investigation_plan(
        plan,
        tools=request.app.state.tools,
        settings=settings,
    )
    evaluation = evaluate_shared_evidence(execution)

    await append_audit(
        request.app,
        tenant_id=scoped_incident.tenant_id,
        actor=principal.subject,
        action="planner.investigation_evaluated",
        detail={
            "plan_id": plan.id,
            "cluster": plan.cluster,
            "execution_status": execution.status,
            "evaluation_status": evaluation.status,
            "root_hypothesis_id": (
                evaluation.root_cause.hypothesis_id
                if evaluation.root_cause
                else None
            ),
            "evaluated_hypotheses": len(
                evaluation.evaluated_hypotheses
            ),
            "explanation_nodes": len(
                evaluation.explanation_graph.nodes
            ),
            "evidence_integrity_ok": (
                execution.shared_evidence.evidence_integrity_ok
            ),
        },
    )

    return AgenticInvestigationResult(
        execution=execution,
        evaluation=evaluation,
    )
