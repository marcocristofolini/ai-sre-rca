from datetime import datetime, timezone
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.core.alert_grouping import occurrence_from_alert
from app.operations import append_audit, persist_investigation
from app.schemas import AlertmanagerWebhook, IncidentInput

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


def _authorize_webhook(request: Request, token: str | None) -> None:
    expected = request.app.state.settings.alertmanager_webhook_token
    if not expected:
        return
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid webhook token")


@router.post("/alertmanager")
async def alertmanager(
    payload: AlertmanagerWebhook,
    request: Request,
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
):
    _authorize_webhook(request, x_webhook_token)

    settings = request.app.state.settings
    results = []
    recorded = 0
    received_at = datetime.now(timezone.utc)
    for alert in payload.alerts:
        labels = {**payload.commonLabels, **alert.labels}
        annotations = {**payload.commonAnnotations, **alert.annotations}
        group_id = None
        if settings.alert_grouping_enabled:
            # Recording is bookkeeping for the noise report: resolved alerts are kept,
            # and a storage failure must not stop the investigation below.
            try:
                stored = await request.app.state.repo.record_alert_occurrence(
                    occurrence_from_alert(
                        tenant_id=settings.alertmanager_tenant_id, labels=labels,
                        status=alert.status, starts_at=alert.startsAt, ends_at=alert.endsAt,
                        fingerprint=alert.fingerprint, received_at=received_at,
                    ),
                    settings.alert_group_window_seconds,
                )
                group_id = stored.group_id
                recorded += 1
            except Exception:
                logger.exception("alert occurrence could not be recorded")
        if alert.status == "resolved":
            continue

        incident = IncidentInput(
            title=annotations.get("summary") or labels.get("alertname", "Prometheus alert"),
            tenant_id=settings.alertmanager_tenant_id,
            alert_name=labels.get("alertname", "PrometheusAlert"),
            severity=labels.get("severity", "warning"),
            cluster=labels.get("cluster", "unknown"),
            namespace=labels.get("namespace"),
            pod=labels.get("pod"),
            node=labels.get("node"),
            service=labels.get("service") or labels.get("service_name") or labels.get("service.name") or labels.get("app"),
            starts_at=alert.startsAt or IncidentInput.model_fields["starts_at"].default_factory(),
            labels=labels,
            annotations=annotations,
        )
        await append_audit(
            request.app,
            tenant_id=incident.tenant_id,
            actor="alertmanager",
            action="alert.received",
            detail={
                "cluster": incident.cluster,
                "alert_name": incident.alert_name,
                "severity": incident.severity,
            },
        )
        result = await request.app.state.engine.investigate(incident)
        await persist_investigation(
            request.app,
            result,
            actor="alertmanager",
            notify=True,
        )
        results.append({
            "incident_id": result.incident_id,
            "status": result.status,
            "root_cause": result.root_cause.title,
            **({"group_id": group_id} if group_id else {}),
        })

    response = {"accepted": len(results), "investigations": results}
    if settings.alert_grouping_enabled:
        response["recorded"] = recorded
    return response
