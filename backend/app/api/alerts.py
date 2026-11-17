"""Read-only alert grouping and noise report."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import Principal, authorize_cluster, require_role
from app.core.alert_grouping import build_report

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

MAX_REPORT_OCCURRENCES = 5000


@router.get("/report")
async def alert_report(
    request: Request,
    principal: Principal = Depends(require_role("viewer")),
    cluster: str | None = Query(default=None, min_length=1, max_length=255),
    hours: int = Query(default=168, ge=1, le=720),
):
    settings = request.app.state.settings
    if not settings.alert_grouping_enabled:
        raise HTTPException(status_code=404, detail="alert grouping is disabled")
    if cluster is not None:
        authorize_cluster(principal, cluster)
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    rows = await request.app.state.repo.list_alert_occurrences(
        tenant_id=None if principal.tenant_id == "*" else principal.tenant_id,
        allowed_clusters=None if "*" in principal.clusters else principal.clusters,
        cluster=cluster,
        since=since,
        limit=MAX_REPORT_OCCURRENCES + 1,
    )
    report = build_report(
        rows[:MAX_REPORT_OCCURRENCES],
        now=now,
        since=since,
        noise_resolve_seconds=settings.alert_noise_resolve_seconds,
    )
    report["truncated"] = len(rows) > MAX_REPORT_OCCURRENCES
    return report
