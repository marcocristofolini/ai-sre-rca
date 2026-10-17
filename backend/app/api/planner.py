from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import asyncio
import logging
from datetime import timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.models import (
    AgenticExecutionResult,
    AgenticInvestigationResult,
    InvestigationPlan,
    OrchestrationJob,
)
from app.agentic.planner import build_investigation_plan
from app.agentic.persistence import materialize_agentic_diagnosis
from app.auth import Principal, authorize_cluster, require_role
from app.operations import append_audit, persist_investigation
from app.schemas import IncidentInput
from app.core.copilot_scope import validate_queued_scope

router = APIRouter(prefix="/api/v1/planner", tags=["planner"])
logger = logging.getLogger(__name__)


def _job_response(data: dict) -> OrchestrationJob:
    return OrchestrationJob.model_validate(data)


async def _emit_progress(app, job: OrchestrationJob, event_type: str, payload: dict) -> None:
    try:
        await app.state.repo.append_orchestration_progress(
            job.id, job.tenant_id, event_type, payload,
            app.state.settings.orchestration_progress_events_per_job,
        )
    except Exception:
        # Progress transport is observational and cannot change RCA execution.
        pass


async def _renew_orchestration_lease(app, job_id: str, owner: str) -> None:
    interval = max(1, app.state.settings.orchestration_job_lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        expires = datetime.now(timezone.utc) + timedelta(seconds=app.state.settings.orchestration_job_lease_seconds)
        if not await app.state.repo.update_orchestration_job(
            job_id, {"lease_expires_at": expires.isoformat()}, owner=owner,
        ):
            return


async def _run_orchestration_job(app, job_id: str) -> None:
    owner = app.state.orchestration_worker_id
    claimed = await app.state.repo.claim_orchestration_job(
        job_id, owner, app.state.settings.orchestration_job_lease_seconds,
        app.state.settings.orchestration_max_concurrent_jobs,
    )
    if not claimed:
        return
    job = _job_response(claimed)
    await _emit_progress(app, job, "started", {"status": "running"})
    heartbeat = asyncio.create_task(_renew_orchestration_lease(app, job_id, owner))
    try:
        await _execute_orchestration_job(app, claimed, owner)
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


async def _execute_orchestration_job(app, stored: dict, owner: str) -> None:
    job = _job_response(stored)
    try:
        incident = IncidentInput.model_validate(stored["payload"]["incident"])
        settings = app.state.settings
        await validate_queued_scope(app.state.repo, settings, incident)
        plan = await build_investigation_plan(
            app.state.repo, incident=incident, settings=settings,
            advisor=getattr(app.state, "planning_advisor", None),
        )
        await _emit_progress(app, job, "plan_ready", {
            "planner_origin": plan.planner_origin,
            "specialists": [task.specialist for task in plan.tasks],
            "task_count": len(plan.tasks),
        })

        async def specialist_progress(item):
            await _emit_progress(app, job, "specialist_update", {
                "specialist": item.specialist, "status": item.status,
                "reason": item.reason, "query_count": item.query_count,
                "query_limit": item.query_limit, "integrity_ok": item.integrity_ok,
                "evidence": [e.model_dump(mode="json") for e in item.evidence],
                "hypotheses": [h.model_dump(mode="json") for h in item.hypotheses],
                "hypotheses_advisory_only": True,
            })
        execution = await execute_investigation_plan(
            plan, tools=app.state.tools, settings=settings,
            progress_callback=specialist_progress,
        )
        evaluation = evaluate_shared_evidence(execution)
        await _emit_progress(app, job, "evaluation", {
            "status": evaluation.status,
            "decisions": [item.model_dump(mode="json") for item in evaluation.evaluated_hypotheses],
            "root_cause_selected": evaluation.root_cause.hypothesis_id if evaluation.root_cause else None,
        })
        diagnosis = materialize_agentic_diagnosis(execution, evaluation)
        actor = stored["actor"]
        await persist_investigation(app, diagnosis, actor=actor, notify=False)
        result = AgenticInvestigationResult(
            incident_id=diagnosis.incident_id,
            investigation_url=f"/incidents/{diagnosis.incident_id}",
            execution=execution,
            evaluation=evaluation,
        )
        await app.state.repo.update_orchestration_job(job.id, {
            "status": "completed", "result": result.model_dump(mode="json"),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "lease_owner": None, "lease_expires_at": None,
        }, owner=owner)
        await append_audit(
            app, tenant_id=job.tenant_id, actor=actor,
            action="planner.orchestration_completed", incident_id=diagnosis.incident_id,
            detail={"job_id": job.id, "status": "completed"},
        )
        await _emit_progress(app, job, "completed", {
            "status": "completed", "incident_id": diagnosis.incident_id,
            "evidence_count": len(execution.shared_evidence.evidence),
            "integrity_ok": execution.shared_evidence.evidence_integrity_ok,
        })
    except Exception as exc:
        logger.exception("orchestration job %s failed", job.id)
        error_type = type(exc).__name__
        error = "Investigation execution failed; see audit record for details."
        await app.state.repo.update_orchestration_job(job.id, {
            "status": "failed", "error": error, "error_type": error_type,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "lease_owner": None, "lease_expires_at": None,
        }, owner=owner)
        await _emit_progress(app, job, "failed", {"error": error, "error_type": error_type})
        try:
            await append_audit(
                app, tenant_id=job.tenant_id, actor=stored["actor"],
                action="planner.orchestration_failed", incident_id=job.incident_id,
                detail={"job_id": job.id, "error_type": error_type},
            )
        except Exception:
            pass
async def orchestration_worker(app) -> None:
    next_cleanup = 0.0
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if now >= next_cleanup:
                await app.state.repo.purge_orchestration_jobs(
                    app.state.settings.orchestration_job_retention_seconds,
                    app.state.settings.orchestration_max_retained_jobs,
                )
                next_cleanup = now + 60
            ids = await app.state.repo.recover_orchestration_jobs()
            if not ids:
                await asyncio.sleep(0.5)
                continue
            await asyncio.gather(*(_run_orchestration_job(app, job_id) for job_id in ids))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("orchestration worker loop failed")
            await asyncio.sleep(1)


@router.post(
    "/jobs", response_model=OrchestrationJob, status_code=202,
    response_model_exclude={"idempotency_key", "request_fingerprint"},
)
async def submit_orchestration_job(
    incident: IncidentInput,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=1, max_length=128),
    principal: Principal = Depends(require_role("investigator")),
):
    settings = request.app.state.settings
    if not settings.agentic_planner_enabled or not settings.agentic_execution_enabled:
        raise HTTPException(status_code=404, detail="agentic execution is disabled")
    tenant_id = incident.tenant_id if principal.tenant_id == "*" else principal.tenant_id
    if tenant_id == "*":
        raise HTTPException(status_code=422, detail="a concrete tenant_id is required")
    scoped = incident.model_copy(update={"tenant_id": tenant_id})
    request_fingerprint = hashlib.sha256(
        json.dumps(scoped.model_dump(mode="json", exclude={"ends_at"} if scoped.ends_at is None else set()), sort_keys=True).encode()
    ).hexdigest()
    authorize_cluster(principal, scoped.cluster)
    request_id = f"request-{uuid4().hex[:12]}"
    submitted = datetime.now(timezone.utc)
    job = OrchestrationJob(
        tenant_id=tenant_id, incident_id=request_id, cluster=scoped.cluster,
        idempotency_key=idempotency_key, request_fingerprint=request_fingerprint,
    )
    stored = job.model_dump(mode="json") | {
        "actor": principal.subject,
        "payload": {"incident": scoped.model_dump(mode="json")},
        "submitted_at": submitted.isoformat(), "started_at": None,
        "finished_at": None, "lease_owner": None, "lease_expires_at": None,
    }
    try:
        stored, created = await request.app.state.repo.create_orchestration_job(
            stored, settings.orchestration_max_queued_jobs,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="idempotency key already used for a different request") from exc
    except OverflowError as exc:
        raise HTTPException(status_code=429, detail="orchestration queue is full") from exc
    if created:
        try:
            await append_audit(request.app, tenant_id=tenant_id, actor=principal.subject,
                action="planner.orchestration_queued", incident_id=request_id,
                detail={"job_id": job.id, "cluster": scoped.cluster})
        except Exception:
            await request.app.state.repo.update_orchestration_job(job.id, {
                "status": "failed", "error": "Job could not be queued because audit persistence failed.",
                "error_type": "AuditPersistenceError", "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            raise
        await _emit_progress(request.app, _job_response(stored), "queued", {"status": "queued"})
    return _job_response(stored)


@router.get(
    "/jobs/{job_id}", response_model=OrchestrationJob,
    response_model_exclude={"idempotency_key", "request_fingerprint"},
)
async def get_orchestration_job(
    job_id: str, request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    tenant_id = None if principal.tenant_id == "*" else principal.tenant_id
    stored = await request.app.state.repo.get_orchestration_job(job_id, tenant_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="orchestration job not found")
    job = _job_response(stored)
    cluster = job.cluster
    if cluster is None or not principal.can_access_cluster(cluster):
        raise HTTPException(status_code=404, detail="orchestration job not found")
    return job


async def _authorized_job(job_id: str, request: Request, principal: Principal):
    tenant_id = None if principal.tenant_id == "*" else principal.tenant_id
    stored = await request.app.state.repo.get_orchestration_job(job_id, tenant_id)
    if stored is None or not principal.can_access_cluster(stored["cluster"]):
        raise HTTPException(status_code=404, detail="orchestration job not found")
    return stored


@router.get("/jobs/{job_id}/progress")
async def poll_orchestration_progress(
    job_id: str, request: Request, after: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_role("investigator")),
):
    job = await _authorized_job(job_id, request, principal)
    return {"job": _job_response(job), "source_mode": "demo" if request.app.state.settings.demo_mode else "live",
        "events": await request.app.state.repo.list_orchestration_progress(
        job_id, job["tenant_id"], after=after, limit=200,
    )}


@router.get("/jobs/{job_id}/events")
async def stream_orchestration_progress(
    job_id: str, request: Request, after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    principal: Principal = Depends(require_role("investigator")),
):
    stored = await _authorized_job(job_id, request, principal)
    try:
        cursor = max(after, int(last_event_id)) if last_event_id else after
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

    async def events():
        nonlocal cursor
        heartbeat_at = asyncio.get_running_loop().time()
        while True:
            rows = await request.app.state.repo.list_orchestration_progress(
                job_id, stored["tenant_id"], after=cursor, limit=100,
            )
            if rows and rows[0]["sequence"] > cursor + 1:
                yield f"event: replay_gap\ndata: {json.dumps({'oldest_available': rows[0]['sequence']})}\n\n"
            for row in rows:
                cursor = row["sequence"]
                yield f"id: {cursor}\nevent: {row['event_type']}\ndata: {json.dumps(row['payload'], separators=(',', ':'))}\n\n"
            current = await request.app.state.repo.get_orchestration_job(
                job_id, stored["tenant_id"],
            )
            if current is None or (current["status"] in {"completed", "failed", "cancelled"} and not rows):
                break
            now = asyncio.get_running_loop().time()
            if now - heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                heartbeat_at = now
            await asyncio.sleep(0.5)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


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
            "specialist_scopes": {
                task.specialist: task.scope.model_dump(mode="json")
                for task in plan.tasks
            },
            "graph_context_nodes": len(plan.graph_context.entity_ids),
            "graph_context_tombstones": sum(
                item.freshness == "tombstoned"
                for item in plan.graph_context.entities
            ),
            "graph_context_changes": len(
                plan.graph_context.recent_changes
            ),
            "graph_context_observations": len(
                plan.graph_context.observations
            ),
            "graph_context_inventory_sources": {
                item.source: item.freshness
                for item in plan.graph_context.inventory_sources
            },
            "graph_context_truncated": plan.graph_context.truncated,
            "graph_context_history_truncated": (
                plan.graph_context.history_truncated
            ),
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
            "planner_origin": plan.planner_origin,
            "status": execution.status,
            "specialists": {
                result.specialist: result.status
                for result in execution.specialist_results
            },
            "evidence_count": len(execution.shared_evidence.evidence),
            "operational_context": {
                "entities": len(plan.graph_context.entities),
                "relationships": len(plan.graph_context.relationships),
                "observations": len(plan.graph_context.observations),
                "recent_changes": len(plan.graph_context.recent_changes),
            },
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
    diagnosis = materialize_agentic_diagnosis(execution, evaluation)
    await persist_investigation(
        request.app, diagnosis, actor=principal.subject, notify=False,
    )

    await append_audit(
        request.app,
        tenant_id=scoped_incident.tenant_id,
        actor=principal.subject,
        action="planner.investigation_evaluated",
        incident_id=diagnosis.incident_id,
        detail={
            "plan_id": plan.id,
            "cluster": plan.cluster,
            "planner_origin": plan.planner_origin,
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
            "operational_context": {
                "entities": len(plan.graph_context.entities),
                "relationships": len(plan.graph_context.relationships),
                "observations": len(plan.graph_context.observations),
                "recent_changes": len(plan.graph_context.recent_changes),
            },
            "evidence_integrity_ok": (
                execution.shared_evidence.evidence_integrity_ok
            ),
        },
    )

    return AgenticInvestigationResult(
        incident_id=diagnosis.incident_id,
        investigation_url=f"/incidents/{diagnosis.incident_id}",
        execution=execution,
        evaluation=evaluation,
    )
