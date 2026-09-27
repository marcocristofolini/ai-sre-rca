from fastapi import APIRouter, Request

from app.schemas import AlertmanagerWebhook, IncidentInput

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/alertmanager")
async def alertmanager(payload: AlertmanagerWebhook, request: Request):
    results = []
    for alert in payload.alerts:
        labels = {**payload.commonLabels, **alert.labels}
        annotations = {**payload.commonAnnotations, **alert.annotations}
        if alert.status == "resolved":
            continue

        incident = IncidentInput(
            title=annotations.get("summary") or labels.get("alertname", "Prometheus alert"),
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
        result = await request.app.state.engine.investigate(incident)
        await request.app.state.repo.save(result)
        results.append({"incident_id": result.incident_id, "status": result.status, "root_cause": result.root_cause.title})

    return {"accepted": len(results), "investigations": results}
