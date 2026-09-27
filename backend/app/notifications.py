from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.schemas import InvestigationResult


_SEVERITY_ORDER = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


@dataclass
class NotificationOutcome:
    channel: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "channel": self.channel,
            "status": self.status,
            "detail": self.detail,
        }


class NotificationManager:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _should_send(self, result: InvestigationResult) -> bool:
        if not self.settings.notifications_enabled:
            return False
        current = _SEVERITY_ORDER.get(result.incident.severity.lower(), 1)
        minimum = _SEVERITY_ORDER.get(
            self.settings.notification_min_severity.lower(),
            1,
        )
        return current >= minimum

    @staticmethod
    def _summary(result: InvestigationResult) -> str:
        return (
            f"[{result.incident.severity.upper()}] {result.incident.title} — "
            f"{result.root_cause.title} "
            f"(cluster={result.incident.cluster}, incident={result.incident_id})"
        )

    def slack_payload(self, result: InvestigationResult) -> dict[str, Any]:
        summary = self._summary(result)
        return {
            "text": summary,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*AI SRE RCA*\n{summary}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Status*\n{result.status}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Root cause*\n{result.root_cause.title}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Tenant*\n{result.incident.tenant_id}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Cluster*\n{result.incident.cluster}",
                        },
                    ],
                },
            ],
        }

    def pagerduty_payload(self, result: InvestigationResult) -> dict[str, Any]:
        severity = result.incident.severity.lower()
        pd_severity = {
            "info": "info",
            "warning": "warning",
            "error": "error",
            "critical": "critical",
        }.get(severity, "warning")
        return {
            "routing_key": self.settings.pagerduty_routing_key,
            "event_action": "trigger",
            "dedup_key": result.incident_id,
            "payload": {
                "summary": self._summary(result),
                "source": result.incident.cluster,
                "severity": pd_severity,
                "component": result.incident.service or result.incident.namespace or "unknown",
                "group": result.incident.tenant_id,
                "class": "ai-sre-rca",
                "custom_details": {
                    "incident_id": result.incident_id,
                    "status": result.status,
                    "root_cause": result.root_cause.model_dump(mode="json"),
                    "alert_name": result.incident.alert_name,
                },
            },
        }

    async def _send_slack(self, result: InvestigationResult) -> NotificationOutcome:
        if not self.settings.slack_webhook_url:
            return NotificationOutcome("slack", "skipped", "webhook not configured")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.settings.slack_webhook_url,
                    json=self.slack_payload(result),
                )
                response.raise_for_status()
            return NotificationOutcome("slack", "sent")
        except Exception as exc:
            return NotificationOutcome(
                "slack",
                "failed",
                exc.__class__.__name__,
            )

    async def _send_pagerduty(self, result: InvestigationResult) -> NotificationOutcome:
        if not self.settings.pagerduty_routing_key:
            return NotificationOutcome("pagerduty", "skipped", "routing key not configured")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.settings.pagerduty_events_url,
                    json=self.pagerduty_payload(result),
                )
                response.raise_for_status()
            return NotificationOutcome("pagerduty", "sent")
        except Exception as exc:
            return NotificationOutcome(
                "pagerduty",
                "failed",
                exc.__class__.__name__,
            )

    async def send_incident(self, result: InvestigationResult) -> list[dict[str, str]]:
        if not self._should_send(result):
            return []

        outcomes = await asyncio.gather(
            self._send_slack(result),
            self._send_pagerduty(result),
        )
        return [item.as_dict() for item in outcomes]
