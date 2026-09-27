from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.schemas import AuditEvent, InvestigationResult


async def append_audit(
    app: FastAPI,
    *,
    tenant_id: str,
    actor: str,
    action: str,
    incident_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        incident_id=incident_id,
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        detail=detail or {},
    )
    await app.state.repo.append_audit(event)
    return event


async def persist_investigation(
    app: FastAPI,
    result: InvestigationResult,
    *,
    actor: str,
    notify: bool = True,
) -> list[dict[str, str]]:
    await app.state.repo.save(result)
    await append_audit(
        app,
        tenant_id=result.incident.tenant_id,
        actor=actor,
        action="investigation.completed",
        incident_id=result.incident_id,
        detail={
            "status": result.status,
            "cluster": result.incident.cluster,
            "root_cause": result.root_cause.title,
            "evidence_count": len(result.evidence),
            "query_budget": result.query_budget,
        },
    )

    outcomes: list[dict[str, str]] = []
    if notify:
        outcomes = await app.state.notifications.send_incident(result)
        for outcome in outcomes:
            await append_audit(
                app,
                tenant_id=result.incident.tenant_id,
                actor="notification-manager",
                action=f"notification.{outcome['status']}",
                incident_id=result.incident_id,
                detail={
                    "channel": outcome["channel"],
                    "detail": outcome["detail"],
                },
            )
    return outcomes
