from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import (
    Principal,
    authorize_cluster,
    authorize_tenant,
    require_role,
)
from app.core.engine import InvestigationEngine
from app.core.integrity import verify_evidence_hash
from app.integrations.demo import StatefulDemoTools, TraceDemoTools
from app.operations import append_audit, persist_investigation
from app.schemas import IncidentExport, IncidentInput, InvestigationResult

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


def _require_demo(request: Request) -> None:
    if not request.app.state.settings.demo_mode:
        raise HTTPException(status_code=404, detail="demo endpoints are disabled")


def _tenant_incident(incident: IncidentInput, principal: Principal) -> IncidentInput:
    return incident.model_copy(update={"tenant_id": principal.tenant_id})


async def _authorized_result(
    incident_id: str,
    request: Request,
    principal: Principal,
) -> InvestigationResult:
    tenant_scope = None if principal.tenant_id == "*" else principal.tenant_id
    result = await request.app.state.repo.get(
        incident_id,
        tenant_id=tenant_scope,
    )
    if not result:
        raise HTTPException(status_code=404, detail="incident not found")
    authorize_tenant(principal, result.incident.tenant_id)
    authorize_cluster(principal, result.incident.cluster)
    return result


@router.get("/demo", response_model=InvestigationResult)
async def demo(
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    _require_demo(request)
    incident = IncidentInput(
        title="Pod evicted from node: ephemeral-storage exhaustion",
        tenant_id=principal.tenant_id,
        cluster="prod-india-exchange",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        severity="warning",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    )
    authorize_cluster(principal, incident.cluster)
    result = await request.app.state.engine.investigate(incident)
    await persist_investigation(
        request.app,
        result,
        actor=principal.subject,
        notify=False,
    )
    return result


@router.get("/demo/trace", response_model=InvestigationResult)
async def demo_trace(
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    _require_demo(request)
    incident = IncidentInput(
        title="Checkout 5xx propagated from downstream database dependency",
        tenant_id=principal.tenant_id,
        cluster="prod-us-east-1",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=datetime(2026, 9, 27, 4, 30, tzinfo=timezone.utc),
        labels={"service": "frontend", "environment": "production"},
    )
    authorize_cluster(principal, incident.cluster)
    engine = InvestigationEngine(
        tools=TraceDemoTools(),
        memory=request.app.state.repo,
        hypothesis_generator=request.app.state.engine.hypothesis_generator,
        settings=request.app.state.engine.settings,
    )
    result = await engine.investigate(incident)
    await persist_investigation(
        request.app,
        result,
        actor=principal.subject,
        notify=False,
    )
    return result


@router.get("/demo/stateful/{dependency}", response_model=InvestigationResult)
async def demo_stateful(
    dependency: Literal["postgresql", "kafka", "redis"],
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    _require_demo(request)
    titles = {
        "postgresql": "Checkout failures caused by PostgreSQL connection exhaustion",
        "kafka": "Checkout event processing delayed by Kafka consumer lag",
        "redis": "Checkout cache failures caused by Redis memory pressure",
    }
    incident = IncidentInput(
        title=titles[dependency],
        tenant_id=principal.tenant_id,
        cluster="prod-us-east-1",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc),
        labels={"service": "frontend", "environment": "production"},
    )
    authorize_cluster(principal, incident.cluster)
    engine = InvestigationEngine(
        tools=StatefulDemoTools(dependency),
        memory=request.app.state.repo,
        hypothesis_generator=request.app.state.engine.hypothesis_generator,
        settings=request.app.state.engine.settings,
    )
    result = await engine.investigate(incident)
    await persist_investigation(
        request.app,
        result,
        actor=principal.subject,
        notify=False,
    )
    return result


@router.post("/investigate", response_model=InvestigationResult)
async def investigate(
    incident: IncidentInput,
    request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    incident = _tenant_incident(incident, principal)
    authorize_cluster(principal, incident.cluster)
    await append_audit(
        request.app,
        tenant_id=principal.tenant_id,
        actor=principal.subject,
        action="investigation.requested",
        detail={
            "cluster": incident.cluster,
            "alert_name": incident.alert_name,
        },
    )
    result = await request.app.state.engine.investigate(incident)
    await persist_investigation(
        request.app,
        result,
        actor=principal.subject,
        notify=True,
    )
    return result


@router.get("/{incident_id}", response_model=InvestigationResult)
async def get_incident(
    incident_id: str,
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    result = await _authorized_result(incident_id, request, principal)
    await append_audit(
        request.app,
        tenant_id=result.incident.tenant_id,
        actor=principal.subject,
        action="incident.viewed",
        incident_id=result.incident_id,
    )
    return result


@router.get("/{incident_id}/audit")
async def get_incident_audit(
    incident_id: str,
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    result = await _authorized_result(incident_id, request, principal)
    return await request.app.state.repo.list_audit(
        result.incident_id,
        result.incident.tenant_id,
    )


@router.get("/{incident_id}/export", response_model=IncidentExport)
async def export_incident(
    incident_id: str,
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
):
    result = await _authorized_result(incident_id, request, principal)
    integrity = {
        evidence.id: bool(verify_evidence_hash(evidence))
        for evidence in result.evidence
    }
    export = IncidentExport(
        incident_id=result.incident_id,
        tenant_id=result.incident.tenant_id,
        evidence_integrity_ok=all(integrity.values()),
        evidence_hashes={
            evidence.id: evidence.integrity_sha256 or ""
            for evidence in result.evidence
        },
        investigation=result,
    )
    await append_audit(
        request.app,
        tenant_id=result.incident.tenant_id,
        actor=principal.subject,
        action="incident.exported",
        incident_id=result.incident_id,
        detail={
            "schema_version": export.schema_version,
            "evidence_integrity_ok": export.evidence_integrity_ok,
        },
    )
    return export
