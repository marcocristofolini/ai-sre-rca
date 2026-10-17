"""Scope-bound, deterministic compilation of conversational investigation requests."""
import hashlib
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agentic.models import OrchestrationJob
from app.api.incidents import _authorized_result
from app.api.planner import submit_orchestration_job
from app.auth import Principal, require_role
from app.operations import append_audit
from app.schemas import IncidentInput
from app.core.copilot_scope import ScopeSelection, resolve_scope, scope_options, validate_queued_scope

router = APIRouter(prefix="/api/v1/incidents", tags=["copilot"])


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)
    scope: ScopeSelection | None = None

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("describe the investigation")
        return value


class InvestigationConfirmation(InvestigationRequest):
    proposal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit confirmation is required")
        return value


class InvestigationProposal(BaseModel):
    proposal_id: str
    incident: IncidentInput
    resolved_identity: dict | None = None
    query_limits: dict | None = None
    scope_notice: str = "Reinvestigate the saved target and time window only. Read-only collection; deterministic RCA."


async def compile_proposal(incident_id: str, question: str, request: Request, principal: Principal, selection: ScopeSelection | None = None):
    settings = request.app.state.settings
    if not settings.agentic_planner_enabled or not settings.agentic_execution_enabled:
        raise HTTPException(status_code=404, detail="agentic execution is disabled")
    source = await _authorized_result(incident_id, request, principal)
    # Free text is a display label, never a query, identifier, permission or tool argument.
    # Logs, evidence and LLM responses are deliberately excluded from scope compilation.
    context = source.incident
    incident = IncidentInput(
        title=f"Copilot: {question}"[:200], tenant_id=context.tenant_id,
        cluster=context.cluster, namespace=context.namespace, pod=context.pod,
        node=context.node, service=context.service, alert_name=context.alert_name,
        severity=context.severity, starts_at=context.starts_at, ends_at=context.ends_at, workload=context.workload,
        labels={key: context.labels[key] for key in ("copilot_target_kind", "copilot_target_id", "copilot_target_uid") if key in context.labels},
    )
    if selection is None:
        await validate_queued_scope(request.app.state.repo, settings, incident)
    identity, budget = None, None
    if selection is not None:
        incident, identity, budget = await resolve_scope(source, selection, request, principal, question)
    # This digest checks the reviewed scope; it never grants authorization.
    fingerprint = hashlib.sha256(json.dumps({
        "source": source.incident_id, "actor": principal.subject,
        "question": question, "incident": incident.model_dump(mode="json", exclude=({"ends_at"} if incident.ends_at is None else set()) | ({"workload"} if incident.workload is None else set())),
        **({"identity": identity, "budget": budget} if selection is not None else {}),
    }, sort_keys=True).encode()).hexdigest()
    return InvestigationProposal(proposal_id=fingerprint, incident=incident, resolved_identity=identity, query_limits=budget,
        **({"scope_notice": "Authorized inventory scope. Historical telemetry uses the selected window; Kubernetes state is live. Identical requests reuse the same job."} if selection is not None else {}))


@router.post("/{incident_id}/copilot/proposals", response_model=InvestigationProposal)
async def propose_investigation(
    incident_id: str, body: InvestigationRequest, request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    proposal = await compile_proposal(incident_id, body.question, request, principal, body.scope)
    await append_audit(request.app, tenant_id=proposal.incident.tenant_id,
        actor=principal.subject, action="copilot.investigation_proposed", incident_id=incident_id,
        detail={"proposal_id": proposal.proposal_id})
    return proposal


@router.post("/{incident_id}/copilot/confirm", response_model=OrchestrationJob, status_code=202,
    response_model_exclude={"idempotency_key", "request_fingerprint"})
async def confirm_investigation(
    incident_id: str, body: InvestigationConfirmation, request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    proposal = await compile_proposal(incident_id, body.question, request, principal, body.scope)
    if not secrets.compare_digest(proposal.proposal_id, body.proposal_id):
        raise HTTPException(status_code=409, detail="proposal changed; review a new proposal")
    await append_audit(request.app, tenant_id=proposal.incident.tenant_id,
        actor=principal.subject, action="copilot.investigation_confirmed", incident_id=incident_id,
        detail={"proposal_id": proposal.proposal_id})
    return await submit_orchestration_job(proposal.incident, request,
        idempotency_key=f"copilot:{proposal.proposal_id}", principal=principal)


@router.get("/{incident_id}/copilot/scope-options")
async def get_scope_options(incident_id: str, request: Request, cluster: str | None = Query(default=None, min_length=1, max_length=255),
    principal: Principal = Depends(require_role("investigator"))):
    source = await _authorized_result(incident_id, request, principal)
    return await scope_options(source, request, principal, cluster)
