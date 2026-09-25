from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas import IncidentInput, InvestigationResult

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


def get_engine(request: Request):
    return request.app.state.engine


def get_repo(request: Request):
    return request.app.state.repo


@router.get("/demo", response_model=InvestigationResult)
async def demo(request: Request):
    incident = IncidentInput(
        title="Pod evicted from node: ephemeral-storage exhaustion",
        cluster="prod-india-exchange",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        severity="warning",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    )
    result = await request.app.state.engine.investigate(incident)
    await request.app.state.repo.save(result)
    return result


@router.post("/investigate", response_model=InvestigationResult)
async def investigate(incident: IncidentInput, request: Request):
    result = await request.app.state.engine.investigate(incident)
    await request.app.state.repo.save(result)
    return result


@router.get("/{incident_id}", response_model=InvestigationResult)
async def get_incident(incident_id: str, request: Request):
    result = await request.app.state.repo.get(incident_id)
    if not result:
        raise HTTPException(status_code=404, detail="incident not found")
    return result
