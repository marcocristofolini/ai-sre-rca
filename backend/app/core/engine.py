from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.core.playbooks import pod_eviction_hypotheses
from app.core.scoring import score_hypothesis
from app.integrations.base import InvestigationTools
from app.schemas import (
    Entity,
    Evidence,
    Hypothesis,
    HypothesisStatus,
    IncidentInput,
    InvestigationResult,
    Remediation,
    RootCause,
    TimelineEvent,
)


def _condition(node: dict[str, Any], condition_type: str) -> str | None:
    for c in node.get("status", {}).get("conditions", []):
        if c.get("type") == condition_type:
            return c.get("status")
    return None


def _parse_dt(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


def _prom_value(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


class InvestigationEngine:
    def __init__(self, tools: InvestigationTools):
        self.tools = tools

    async def investigate(self, incident: IncidentInput) -> InvestigationResult:
        incident_id = f"inc-{uuid4().hex[:12]}"
        if incident.pod and incident.namespace:
            return await self._investigate_pod(incident_id, incident)
        return self._generic_result(incident_id, incident)

    async def _investigate_pod(self, incident_id: str, incident: IncidentInput) -> InvestigationResult:
        assert incident.namespace and incident.pod
        pod = await self.tools.get_pod(incident.namespace, incident.pod)
        node_name = incident.node or pod.get("spec", {}).get("nodeName")

        event_task = self.tools.get_events(incident.namespace, incident.pod)
        node_task = self.tools.get_node(node_name) if node_name else asyncio.sleep(0, result={})
        logs_task = self.tools.loki_query_range(
            f'{{namespace="{incident.namespace}", pod="{incident.pod}"}}',
            incident.starts_at - timedelta(minutes=20),
            incident.starts_at + timedelta(minutes=5),
            100,
        )
        events, node, logs = await asyncio.gather(event_task, node_task, logs_task)

        evidence: list[Evidence] = []
        entities: list[Entity] = [Entity(id="pod", kind="Pod", name=incident.pod, namespace=incident.namespace)]
        if node_name:
            entities.append(Entity(id="node", kind="Node", name=node_name, relationship="scheduled_on"))

        owner_refs = pod.get("metadata", {}).get("ownerReferences", [])
        for idx, owner in enumerate(owner_refs):
            entities.append(Entity(id=f"owner-{idx}", kind=owner.get("kind", "Owner"), name=owner.get("name", "unknown"), namespace=incident.namespace, relationship="owned_by"))

        reason = pod.get("status", {}).get("reason")
        message = pod.get("status", {}).get("message", "")
        evidence.append(Evidence(
            source="kubernetes",
            category="pod-status",
            summary=f"Pod terminal reason is {reason or 'unknown'}.",
            facts={"phase": pod.get("status", {}).get("phase"), "reason": reason, "message": message},
            raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}",
            strength="strong" if reason == "Evicted" else "medium",
        ))
        pod_status_ev = evidence[-1]

        disk_pressure = _condition(node, "DiskPressure")
        memory_pressure = _condition(node, "MemoryPressure")
        pid_pressure = _condition(node, "PIDPressure")
        evidence.append(Evidence(
            source="kubernetes",
            category="node-conditions",
            summary=f"Node conditions: DiskPressure={disk_pressure}, MemoryPressure={memory_pressure}, PIDPressure={pid_pressure}.",
            facts={"DiskPressure": disk_pressure, "MemoryPressure": memory_pressure, "PIDPressure": pid_pressure},
            raw_reference=f"k8s://node/{node_name}" if node_name else None,
            strength="strong",
        ))
        node_conditions_ev = evidence[-1]

        event_ev: Evidence | None = None
        for item in events:
            if item.get("reason") == "Evicted" or "ephemeral-storage" in item.get("message", ""):
                event_ev = Evidence(
                    source="kubernetes",
                    category="event",
                    summary=item.get("message", "Kubernetes eviction event"),
                    facts={"reason": item.get("reason"), "type": item.get("type")},
                    raw_reference=f"k8s://event/{incident.namespace}/{incident.pod}",
                    strength="strong",
                    observed_at=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), incident.starts_at),
                )
                evidence.append(event_ev)
                break

        resource_spec = pod.get("spec", {}).get("containers", [])
        resources = [{"name": c.get("name"), **c.get("resources", {})} for c in resource_spec]
        has_ephemeral_request = any("ephemeral-storage" in c.get("resources", {}).get("requests", {}) for c in resource_spec)
        has_ephemeral_limit = any("ephemeral-storage" in c.get("resources", {}).get("limits", {}) for c in resource_spec)
        evidence.append(Evidence(
            source="kubernetes",
            category="resource-policy",
            summary=("Pod defines ephemeral-storage reservations." if has_ephemeral_request or has_ephemeral_limit else "Pod has no ephemeral-storage requests or limits."),
            facts={"has_ephemeral_request": has_ephemeral_request, "has_ephemeral_limit": has_ephemeral_limit, "resources": resources},
            raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}/resources",
            strength="strong",
        ))
        resource_ev = evidence[-1]

        fs_ev: Evidence | None = None
        if node_name:
            query = f'node_filesystem_avail_bytes{{instance=~"{node_name}.*",mountpoint="/"}}'
            prom = await self.tools.prometheus_query(query, incident.starts_at)
            available = _prom_value(prom)
            if available is not None:
                fs_ev = Evidence(
                    source="prometheus",
                    category="metric",
                    summary=f"Node filesystem had approximately {available / 1024 / 1024:.0f} MiB available near the incident.",
                    facts={"available_bytes": available},
                    query=query,
                    raw_reference="prometheus:/api/v1/query",
                    strength="strong" if available < 2 * 1024**3 else "medium",
                )
                evidence.append(fs_ev)

        log_count = sum(len(stream.get("values", [])) for stream in logs.get("data", {}).get("result", []))
        evidence.append(Evidence(
            source="loki",
            category="logs",
            summary=f"Retrieved {log_count} application log entries around the incident window; no independent crash signature was established by the playbook.",
            facts={"entries": log_count},
            query=f'{{namespace="{incident.namespace}", pod="{incident.pod}"}}',
            raw_reference="loki:/loki/api/v1/query_range",
            strength="weak",
        ))
        logs_ev = evidence[-1]

        hypotheses = pod_eviction_hypotheses()
        hmap = {h.id: h for h in hypotheses}

        if reason == "Evicted":
            hmap["app-crash"].contradicting_evidence.append(pod_status_ev.id)
            if event_ev:
                hmap["app-crash"].contradicting_evidence.append(event_ev.id)
        else:
            hmap["app-crash"].supporting_evidence.append(logs_ev.id)

        if memory_pressure == "False":
            hmap["memory-pressure"].contradicting_evidence.append(node_conditions_ev.id)
        elif memory_pressure == "True":
            hmap["memory-pressure"].supporting_evidence.append(node_conditions_ev.id)

        if pid_pressure == "False":
            hmap["pid-pressure"].contradicting_evidence.append(node_conditions_ev.id)
        elif pid_pressure == "True":
            hmap["pid-pressure"].supporting_evidence.append(node_conditions_ev.id)

        if disk_pressure == "True":
            hmap["disk-pressure"].supporting_evidence.append(node_conditions_ev.id)
        if "ephemeral-storage" in message:
            hmap["disk-pressure"].supporting_evidence.append(pod_status_ev.id)
        if event_ev:
            hmap["disk-pressure"].supporting_evidence.append(event_ev.id)
        if fs_ev:
            hmap["disk-pressure"].supporting_evidence.append(fs_ev.id)

        if not has_ephemeral_request and not has_ephemeral_limit:
            hmap["missing-storage-reservations"].supporting_evidence.append(resource_ev.id)
        else:
            hmap["missing-storage-reservations"].contradicting_evidence.append(resource_ev.id)

        by_id = {e.id: e for e in evidence}
        for h in hypotheses:
            score_hypothesis(h, by_id)
            h.rationale = self._rationale(h, by_id)

        confirmed = sorted((h for h in hypotheses if h.status == HypothesisStatus.CONFIRMED), key=lambda h: h.score, reverse=True)
        primary = confirmed[0] if confirmed else max(hypotheses, key=lambda h: h.score)
        status = "resolved" if primary.status == HypothesisStatus.CONFIRMED else "inconclusive"

        supporting = len(primary.supporting_evidence)
        contradicting = len(primary.contradicting_evidence)
        evidence_strength = "strong" if supporting >= 3 and contradicting == 0 else "medium" if supporting >= 1 else "weak"

        root_title = "Ephemeral-storage exhaustion caused kubelet eviction" if primary.id == "disk-pressure" else primary.statement
        root_summary = (
            "The pod was evicted by kubelet while its node reported DiskPressure. Kubernetes explicitly identified ephemeral-storage as the scarce resource. "
            "The workload did not declare ephemeral-storage requests/limits, which can permit storage overcommit; that condition is treated as a contributing factor rather than the direct eviction trigger."
            if primary.id == "disk-pressure"
            else primary.rationale
        )

        timeline: list[TimelineEvent] = []
        for item in events:
            timeline.append(TimelineEvent(
                timestamp=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), incident.starts_at),
                source="kubernetes",
                message=f"{item.get('reason', 'Event')}: {item.get('message', '')}",
            ))
        timeline.sort(key=lambda x: x.timestamp)

        remediation = [
            Remediation(priority="P1", action="Define ephemeral-storage requests and limits for the affected workload after measuring normal and burst usage.", reason="Prevents unbounded local-storage consumption and improves scheduler accounting."),
            Remediation(priority="P1", action="Alert on node filesystem availability and DiskPressure before kubelet eviction thresholds are reached.", reason="Provides lead time before workload eviction."),
            Remediation(priority="P2", action="Identify high ephemeral-storage consumers on the node and validate log/cache/temp-file retention.", reason="Finds the workload or host path producing the storage pressure."),
            Remediation(priority="P2", action="Review kubelet eviction thresholds and node root-volume sizing against observed production usage.", reason="Validates capacity and safety margins without masking runaway disk usage."),
        ]

        return InvestigationResult(
            incident_id=incident_id,
            status=status,
            incident=incident,
            entities=entities,
            evidence=evidence,
            hypotheses=hypotheses,
            timeline=timeline,
            root_cause=RootCause(
                title=root_title,
                summary=root_summary,
                hypothesis_id=primary.id,
                evidence_strength=evidence_strength,
                supporting_signals=supporting,
                contradicting_signals=contradicting,
            ),
            remediation=remediation,
        )

    @staticmethod
    def _rationale(h: Hypothesis, evidence_by_id: dict[str, Evidence]) -> str:
        support = [evidence_by_id[i].summary for i in h.supporting_evidence if i in evidence_by_id]
        contradict = [evidence_by_id[i].summary for i in h.contradicting_evidence if i in evidence_by_id]
        parts = []
        if support:
            parts.append("Supporting: " + " ".join(support))
        if contradict:
            parts.append("Contradicting: " + " ".join(contradict))
        return " ".join(parts) or "No decisive evidence collected."

    @staticmethod
    def _generic_result(incident_id: str, incident: IncidentInput) -> InvestigationResult:
        hypothesis = Hypothesis(
            id="generic",
            statement="The current alert type does not yet have a deterministic playbook.",
            status=HypothesisStatus.INCONCLUSIVE,
            rationale="Add a playbook and typed checks for this alert family.",
        )
        return InvestigationResult(
            incident_id=incident_id,
            status="inconclusive",
            incident=incident,
            entities=[],
            evidence=[],
            hypotheses=[hypothesis],
            timeline=[],
            root_cause=RootCause(
                title="Investigation inconclusive",
                summary="No deterministic playbook matched the supplied entity set.",
                evidence_strength="weak",
                supporting_signals=0,
                contradicting_signals=0,
            ),
            remediation=[Remediation(priority="P3", action="Create a typed investigation playbook for this alert family.", reason="Unknown alerts should not be diagnosed through unconstrained guessing.")],
        )
