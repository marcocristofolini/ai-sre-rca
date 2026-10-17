"""Scope-bound, deterministic compilation of conversational investigation requests."""
import hashlib
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agentic.models import OrchestrationJob
from app.api.incidents import _authorized_result
from app.api.planner import submit_orchestration_job
from app.auth import Principal, require_role
from app.operations import append_audit
from app.schemas import IncidentInput

router = APIRouter(prefix="/api/v1/incidents", tags=["copilot"])


class InvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=500)

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
    scope_notice: str = "Reinvestigate the saved target and time window only. Read-only collection; deterministic RCA."


async def compile_proposal(incident_id: str, question: str, request: Request, principal: Principal):
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
        severity=context.severity, starts_at=context.starts_at,
    )
    # This digest checks the reviewed scope; it never grants authorization.
    fingerprint = hashlib.sha256(json.dumps({
        "source": source.incident_id, "actor": principal.subject,
        "question": question, "incident": incident.model_dump(mode="json"),
    }, sort_keys=True).encode()).hexdigest()
    return InvestigationProposal(proposal_id=fingerprint, incident=incident)


@router.post("/{incident_id}/copilot/proposals", response_model=InvestigationProposal)
async def propose_investigation(
    incident_id: str, body: InvestigationRequest, request: Request,
    principal: Principal = Depends(require_role("investigator")),
):
    proposal = await compile_proposal(incident_id, body.question, request, principal)
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
    proposal = await compile_proposal(incident_id, body.question, request, principal)
    if not secrets.compare_digest(proposal.proposal_id, body.proposal_id):
        raise HTTPException(status_code=409, detail="proposal changed; review a new proposal")
    await append_audit(request.app, tenant_id=proposal.incident.tenant_id,
        actor=principal.subject, action="copilot.investigation_confirmed", incident_id=incident_id,
        detail={"proposal_id": proposal.proposal_id})
    return await submit_orchestration_job(proposal.incident, request,
        idempotency_key=f"copilot:{proposal.proposal_id}", principal=principal)
