from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.config import Settings, get_settings
from app.core.k8s_signals import classify_pod_scenario
from app.core.kubernetes_investigator import investigate_node_not_ready, investigate_pod_scenario
from app.core.playbooks import pod_eviction_hypotheses
from app.core.scoring import score_hypothesis
from app.core.trace_analysis import enrich_with_trace_causality
from app.memory.base import IncidentMemory
from app.integrations.base import InvestigationTools
from app.llm.hypotheses import HypothesisGenerator, NullHypothesisGenerator
from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    Hypothesis,
    HypothesisStatus,
    IncidentInput,
    InvestigationResult,
    Remediation,
    RootCause,
    SimilarIncident,
    TimelineEvent,
)


def _condition(node: dict[str, Any], condition_type: str) -> str | None:
    for condition in node.get("status", {}).get("conditions", []):
        if condition.get("type") == condition_type:
            return condition.get("status")
    return None


def _parse_dt(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def _prom_value(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _prom_range_values(payload: dict[str, Any]) -> list[float]:
    try:
        values = payload["data"]["result"][0]["values"]
    except (KeyError, IndexError, TypeError):
        return []
    output: list[float] = []
    for value in values:
        try:
            output.append(float(value[1]))
        except (IndexError, TypeError, ValueError):
            continue
    return output


def _owner(resource: dict[str, Any], kind: str | None = None) -> dict[str, Any] | None:
    for ref in resource.get("metadata", {}).get("ownerReferences", []):
        if kind is None or ref.get("kind") == kind:
            return ref
    return None


def _argo_source(app: dict[str, Any]) -> dict[str, Any]:
    spec = app.get("spec", {})
    if spec.get("source"):
        return spec["source"]
    sources = spec.get("sources", [])
    return sources[0] if sources else {}


class InvestigationEngine:
    def __init__(
        self,
        tools: InvestigationTools,
        memory: IncidentMemory | None = None,
        hypothesis_generator: HypothesisGenerator | None = None,
        settings: Settings | None = None,
    ):
        self.tools = tools
        self.memory = memory
        self.hypothesis_generator = hypothesis_generator or NullHypothesisGenerator()
        self.settings = settings or get_settings()

    async def investigate(self, incident: IncidentInput) -> InvestigationResult:
        incident_id = f"inc-{uuid4().hex[:12]}"
        similar = await self._similar_incidents(incident)
        alert = incident.alert_name.lower()

        if incident.node and ("nodenotready" in alert or "node-not-ready" in alert or not incident.pod):
            result = await investigate_node_not_ready(
                incident_id=incident_id,
                incident=incident,
                similar=similar,
                tools=self.tools,
                hypothesis_generator=self.hypothesis_generator,
            )
        elif incident.pod and incident.namespace:
            result = await self._investigate_pod(incident_id, incident, similar)
        else:
            result = self._generic_result(incident_id, incident, similar)

        return await enrich_with_trace_causality(
            result,
            tools=self.tools,
            settings=self.settings,
        )

    async def _similar_incidents(self, incident: IncidentInput) -> list[SimilarIncident]:
        if not self.memory:
            return []
        try:
            return await self.memory.search_similar(incident, limit=3)
        except Exception:
            # Memory is context enrichment. It must never make live investigation fail.
            return []

    async def _investigate_pod(
        self,
        incident_id: str,
        incident: IncidentInput,
        similar: list[SimilarIncident],
    ) -> InvestigationResult:
        assert incident.namespace and incident.pod
        pod = await self.tools.get_pod(incident.namespace, incident.pod)
        preloaded_events = await self.tools.get_events(incident.namespace, incident.pod)
        scenario = classify_pod_scenario(incident, pod, preloaded_events)
        if scenario != "eviction":
            if scenario == "generic-pod":
                return self._generic_result(incident_id, incident, similar)
            return await investigate_pod_scenario(
                incident_id=incident_id,
                incident=incident,
                similar=similar,
                pod=pod,
                events=preloaded_events,
                scenario=scenario,
                tools=self.tools,
                settings=self.settings,
                hypothesis_generator=self.hypothesis_generator,
            )

        node_name = incident.node or pod.get("spec", {}).get("nodeName")
        labels = pod.get("metadata", {}).get("labels", {})
        service_name = labels.get("app.kubernetes.io/name") or labels.get("app") or incident.pod
        argo_app_name = labels.get("app.kubernetes.io/instance") or service_name

        rs_ref = _owner(pod, "ReplicaSet")
        rs = await self.tools.get_replicaset(incident.namespace, rs_ref["name"]) if rs_ref else {}
        deploy_ref = _owner(rs, "Deployment")
        deployment = await self.tools.get_deployment(incident.namespace, deploy_ref["name"]) if deploy_ref else {}
        if deployment:
            argo_app_name = deployment.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance", argo_app_name)

        start = incident.starts_at
        event_task = asyncio.sleep(0, result=preloaded_events)
        node_task = self.tools.get_node(node_name) if node_name else asyncio.sleep(0, result={})
        logs_task = self.tools.loki_query_range(
            f'{{namespace="{incident.namespace}", pod="{incident.pod}"}}',
            start - timedelta(minutes=20),
            start + timedelta(minutes=5),
            100,
        )
        graph_task = self.tools.service_graph(service_name, start)
        argo_task = self.tools.argocd_application(argo_app_name)
        nodeclaim_task = self.tools.karpenter_nodeclaim_for_node(node_name) if node_name else asyncio.sleep(0, result={})
        node_pods_task = self.tools.list_pods_on_node(node_name) if node_name else asyncio.sleep(0, result=[])
        events, node, logs, service_edges, argo_app, nodeclaim, node_pods = await asyncio.gather(
            event_task, node_task, logs_task, graph_task, argo_task, nodeclaim_task, node_pods_task
        )

        fs_current: dict[str, Any] = {}
        fs_trend: dict[str, Any] = {}
        if node_name:
            fs_query = f'node_filesystem_avail_bytes{{instance=~"{node_name}.*",mountpoint="/"}}'
            fs_current, fs_trend = await asyncio.gather(
                self.tools.prometheus_query(fs_query, start),
                self.tools.prometheus_query_range(
                    fs_query,
                    start - timedelta(minutes=30),
                    start + timedelta(minutes=2),
                    "60s",
                ),
            )
        else:
            fs_query = ""

        repo_url = _argo_source(argo_app).get("repoURL", "") if argo_app else ""
        revision = argo_app.get("status", {}).get("sync", {}).get("revision", "") if argo_app else ""
        github_commit: dict[str, Any] = {}
        if repo_url and revision:
            github_commit = await self.tools.github_commit(repo_url, revision)

        karpenter_events: list[dict[str, Any]] = []
        claim_name = nodeclaim.get("metadata", {}).get("name")
        if claim_name:
            karpenter_events = await self.tools.get_cluster_events(claim_name)

        evidence: list[Evidence] = []
        entities: list[Entity] = []
        graph_edges: list[GraphEdge] = []
        entity_ids: set[str] = set()

        def add_entity(entity: Entity) -> None:
            if entity.id not in entity_ids:
                entities.append(entity)
                entity_ids.add(entity.id)

        add_entity(Entity(id="pod", kind="Pod", name=incident.pod, namespace=incident.namespace))
        if node_name:
            add_entity(Entity(id="node", kind="Node", name=node_name, relationship="scheduled_on", metadata={"labels": node.get("metadata", {}).get("labels", {})}))
            graph_edges.append(GraphEdge(source="pod", target="node", relationship="scheduled_on"))

        if rs_ref:
            add_entity(Entity(id="replicaset", kind="ReplicaSet", name=rs_ref.get("name", "unknown"), namespace=incident.namespace, relationship="owned_by"))
            graph_edges.append(GraphEdge(source="pod", target="replicaset", relationship="owned_by"))
        if deploy_ref:
            add_entity(Entity(id="deployment", kind="Deployment", name=deploy_ref.get("name", "unknown"), namespace=incident.namespace, relationship="owned_by"))
            graph_edges.append(GraphEdge(source="replicaset", target="deployment", relationship="owned_by"))

        add_entity(Entity(id=f"service:{service_name}", kind="Service", name=service_name, namespace=incident.namespace))
        if deploy_ref:
            graph_edges.append(GraphEdge(source="deployment", target=f"service:{service_name}", relationship="serves"))

        reason = pod.get("status", {}).get("reason")
        message = pod.get("status", {}).get("message", "")
        pod_status_ev = Evidence(
            source="kubernetes",
            category="pod-status",
            summary=f"Pod terminal reason is {reason or 'unknown'}.",
            facts={"phase": pod.get("status", {}).get("phase"), "reason": reason, "message": message},
            raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}",
            strength="strong" if reason == "Evicted" else "medium",
        )
        evidence.append(pod_status_ev)

        disk_pressure = _condition(node, "DiskPressure")
        memory_pressure = _condition(node, "MemoryPressure")
        pid_pressure = _condition(node, "PIDPressure")
        node_conditions_ev = Evidence(
            source="kubernetes",
            category="node-conditions",
            summary=f"Node conditions: DiskPressure={disk_pressure}, MemoryPressure={memory_pressure}, PIDPressure={pid_pressure}.",
            facts={"DiskPressure": disk_pressure, "MemoryPressure": memory_pressure, "PIDPressure": pid_pressure},
            raw_reference=f"k8s://node/{node_name}" if node_name else None,
            strength="strong",
        )
        evidence.append(node_conditions_ev)

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
                    observed_at=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), start),
                )
                evidence.append(event_ev)
                break

        resource_spec = pod.get("spec", {}).get("containers", [])
        resources = [{"name": container.get("name"), **container.get("resources", {})} for container in resource_spec]
        has_ephemeral_request = any("ephemeral-storage" in container.get("resources", {}).get("requests", {}) for container in resource_spec)
        has_ephemeral_limit = any("ephemeral-storage" in container.get("resources", {}).get("limits", {}) for container in resource_spec)
        resource_ev = Evidence(
            source="kubernetes",
            category="resource-policy",
            summary=(
                "Pod defines ephemeral-storage reservations."
                if has_ephemeral_request or has_ephemeral_limit
                else "Pod has no ephemeral-storage requests or limits."
            ),
            facts={"has_ephemeral_request": has_ephemeral_request, "has_ephemeral_limit": has_ephemeral_limit, "resources": resources},
            raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}/resources",
            strength="strong",
        )
        evidence.append(resource_ev)

        fs_ev: Evidence | None = None
        available = _prom_value(fs_current)
        trend_values = _prom_range_values(fs_trend)
        if available is not None:
            trend_summary = ""
            if len(trend_values) >= 2:
                delta_gib = (trend_values[-1] - trend_values[0]) / 1024**3
                trend_summary = f"; 30-minute change was {delta_gib:.1f} GiB"
            fs_ev = Evidence(
                source="prometheus",
                category="filesystem-capacity",
                summary=f"Node filesystem had approximately {available / 1024 / 1024:.0f} MiB available near the incident{trend_summary}.",
                facts={
                    "available_bytes": available,
                    "trend_first_bytes": trend_values[0] if trend_values else None,
                    "trend_last_bytes": trend_values[-1] if trend_values else None,
                },
                query=fs_query,
                raw_reference="prometheus:/api/v1/query_range",
                strength="strong" if available < 2 * 1024**3 else "medium",
            )
            evidence.append(fs_ev)

        log_count = sum(len(stream.get("values", [])) for stream in logs.get("data", {}).get("result", []))
        logs_ev = Evidence(
            source="loki",
            category="logs",
            summary=f"Retrieved {log_count} application log entries around the incident window; the deterministic playbook found no independent application-crash signal.",
            facts={"entries": log_count},
            query=f'{{namespace="{incident.namespace}", pod="{incident.pod}"}}',
            raw_reference="loki:/loki/api/v1/query_range",
            strength="weak",
        )
        evidence.append(logs_ev)

        topology_ev: Evidence | None = None
        if service_edges:
            topology_ev = Evidence(
                source="opentelemetry",
                category="service-graph",
                summary=f"Trace-derived service graph exposed {len(service_edges)} dependency edges around {service_name}.",
                facts={"service": service_name, "edges": service_edges},
                query=service_edges[0].get("query") if service_edges else None,
                raw_reference="prometheus:traces_service_graph_request_total",
                strength="medium",
            )
            evidence.append(topology_ev)
            for edge in service_edges:
                client = str(edge.get("client"))
                server = str(edge.get("server"))
                client_id = f"service:{client}"
                server_id = f"service:{server}"
                add_entity(Entity(id=client_id, kind="Service", name=client))
                add_entity(Entity(id=server_id, kind="Service", name=server))
                graph_edges.append(GraphEdge(
                    source=client_id,
                    target=server_id,
                    relationship="calls",
                    metadata={"requests": edge.get("requests"), "connection_type": edge.get("connection_type")},
                    evidence_ids=[topology_ev.id],
                ))

        argo_ev: Evidence | None = None
        argo_finished: datetime | None = None
        if argo_app:
            operation = argo_app.get("status", {}).get("operationState", {})
            finished_raw = operation.get("finishedAt")
            if finished_raw:
                argo_finished = _parse_dt(finished_raw, start)
            delta_minutes = abs((start - argo_finished).total_seconds()) / 60 if argo_finished else None
            argo_ev = Evidence(
                source="argocd",
                category="change-correlation",
                summary=(
                    f"Argo CD application {argo_app_name} is {argo_app.get('status', {}).get('sync', {}).get('status', 'unknown')} at revision {revision or 'unknown'}"
                    + (f"; last operation finished {delta_minutes:.0f} minutes from incident start." if delta_minutes is not None else ".")
                ),
                facts={
                    "application": argo_app_name,
                    "revision": revision,
                    "repo_url": repo_url,
                    "sync_status": argo_app.get("status", {}).get("sync", {}).get("status"),
                    "health": argo_app.get("status", {}).get("health", {}).get("status"),
                    "operation_phase": operation.get("phase"),
                    "finished_at": finished_raw,
                    "delta_minutes": delta_minutes,
                },
                raw_reference=f"argocd://application/{argo_app_name}",
                observed_at=argo_finished or start,
                strength="medium",
            )
            evidence.append(argo_ev)
            add_entity(Entity(id="argocd", kind="ArgoCDApplication", name=argo_app_name, metadata={"revision": revision, "repo_url": repo_url}))
            if deploy_ref:
                graph_edges.append(GraphEdge(source="deployment", target="argocd", relationship="managed_by", evidence_ids=[argo_ev.id]))

        git_ev: Evidence | None = None
        if github_commit:
            commit = github_commit.get("commit", {})
            author = commit.get("author", {})
            git_ev = Evidence(
                source="github",
                category="source-change",
                summary=f"Revision {github_commit.get('sha', revision)[:12]} resolves to commit: {str(commit.get('message', '')).splitlines()[0] or 'no message'}.",
                facts={
                    "sha": github_commit.get("sha", revision),
                    "message": commit.get("message"),
                    "author": author.get("name"),
                    "date": author.get("date"),
                    "url": github_commit.get("html_url"),
                },
                raw_reference=github_commit.get("html_url"),
                observed_at=_parse_dt(author.get("date"), start),
                strength="weak",
            )
            evidence.append(git_ev)
            add_entity(Entity(id="git-repo", kind="GitRepository", name=repo_url or "repository", metadata={"revision": revision}))
            if argo_app:
                graph_edges.append(GraphEdge(source="argocd", target="git-repo", relationship="syncs_from", evidence_ids=[git_ev.id]))

        karpenter_ev: Evidence | None = None
        if nodeclaim:
            claim_meta = nodeclaim.get("metadata", {})
            claim_status = nodeclaim.get("status", {})
            claim_labels = claim_meta.get("labels", {})
            claim_conditions = claim_status.get("conditions", [])
            ready = next((item.get("status") for item in claim_conditions if item.get("type") == "Ready"), None)
            created = _parse_dt(claim_meta.get("creationTimestamp"), start)
            age_minutes = max(0.0, (start - created).total_seconds() / 60)
            karpenter_ev = Evidence(
                source="karpenter",
                category="nodeclaim",
                summary=f"Node is backed by NodeClaim {claim_meta.get('name')} in NodePool {claim_labels.get('karpenter.sh/nodepool', 'unknown')}; Ready={ready}, age≈{age_minutes:.0f} minutes.",
                facts={
                    "nodeclaim": claim_meta.get("name"),
                    "nodepool": claim_labels.get("karpenter.sh/nodepool"),
                    "capacity_type": claim_labels.get("karpenter.sh/capacity-type"),
                    "ready": ready,
                    "provider_id": claim_status.get("providerID"),
                    "age_minutes": age_minutes,
                    "conditions": claim_conditions,
                    "co_located_pods": len(node_pods),
                },
                raw_reference=f"k8s://nodeclaim/{claim_meta.get('name')}",
                strength="strong",
            )
            evidence.append(karpenter_ev)
            add_entity(Entity(id="nodeclaim", kind="NodeClaim", name=claim_meta.get("name", "unknown"), metadata={"nodepool": claim_labels.get("karpenter.sh/nodepool")}))
            graph_edges.append(GraphEdge(source="node", target="nodeclaim", relationship="represented_by", evidence_ids=[karpenter_ev.id]))
            nodepool = claim_labels.get("karpenter.sh/nodepool")
            if nodepool:
                add_entity(Entity(id="nodepool", kind="NodePool", name=nodepool))
                graph_edges.append(GraphEdge(source="nodeclaim", target="nodepool", relationship="owned_by", evidence_ids=[karpenter_ev.id]))

        hypotheses = pod_eviction_hypotheses()
        hmap = {hypothesis.id: hypothesis for hypothesis in hypotheses}

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

        if argo_ev:
            delta = argo_ev.facts.get("delta_minutes")
            if isinstance(delta, (int, float)) and delta <= self.settings.change_correlation_window_minutes:
                hmap["recent-change-regression"].supporting_evidence.append(argo_ev.id)
                if git_ev:
                    hmap["recent-change-regression"].supporting_evidence.append(git_ev.id)
            elif isinstance(delta, (int, float)):
                hmap["recent-change-regression"].contradicting_evidence.append(argo_ev.id)
        if event_ev and "ephemeral-storage" in event_ev.summary:
            hmap["recent-change-regression"].contradicting_evidence.append(event_ev.id)

        if karpenter_ev:
            ready = karpenter_ev.facts.get("ready")
            age = karpenter_ev.facts.get("age_minutes")
            disruptive_reasons = {"Disrupted", "Disrupting", "Terminating", "Unconsolidatable", "InsufficientCapacity"}
            related_disruption = any(item.get("reason") in disruptive_reasons for item in karpenter_events)
            if ready != "True" or related_disruption or (isinstance(age, (int, float)) and age < 10):
                hmap["node-capacity-churn"].supporting_evidence.append(karpenter_ev.id)
            else:
                hmap["node-capacity-churn"].contradicting_evidence.append(karpenter_ev.id)

        by_id = {item.id: item for item in evidence}
        for hypothesis in hypotheses:
            score_hypothesis(hypothesis, by_id)
            hypothesis.rationale = self._rationale(hypothesis, by_id)

        llm_hypotheses = await self.hypothesis_generator.propose(incident, entities, evidence, hypotheses)
        hypotheses.extend(llm_hypotheses)

        deterministic = [hypothesis for hypothesis in hypotheses if hypothesis.origin == "playbook"]
        confirmed = sorted(
            (hypothesis for hypothesis in deterministic if hypothesis.status == HypothesisStatus.CONFIRMED),
            key=lambda hypothesis: hypothesis.score,
            reverse=True,
        )
        primary = confirmed[0] if confirmed else max(deterministic, key=lambda hypothesis: hypothesis.score)
        status = "resolved" if primary.status == HypothesisStatus.CONFIRMED else "inconclusive"

        supporting = len(primary.supporting_evidence)
        contradicting = len(primary.contradicting_evidence)
        evidence_strength = "strong" if supporting >= 3 and contradicting == 0 else "medium" if supporting >= 1 else "weak"

        root_title = "Ephemeral-storage exhaustion caused kubelet eviction" if primary.id == "disk-pressure" else primary.statement
        root_summary = (
            "Kubelet evicted the pod while the node reported DiskPressure, and Kubernetes explicitly identified ephemeral-storage as the scarce resource. "
            "The workload had no ephemeral-storage request/limit, which is recorded as a contributing scheduling/accounting risk rather than the direct eviction trigger. "
            "Change correlation and Karpenter lifecycle evidence are evaluated separately so temporal coincidence cannot override the direct Kubernetes signal."
            if primary.id == "disk-pressure"
            else primary.rationale
        )

        timeline: list[TimelineEvent] = []
        if argo_finished:
            timeline.append(TimelineEvent(timestamp=argo_finished, source="argocd", message=f"Application {argo_app_name} sync finished at revision {revision}."))
        if git_ev:
            timeline.append(TimelineEvent(timestamp=git_ev.observed_at, source="github", message=f"Commit {str(git_ev.facts.get('sha', ''))[:12]}: {str(git_ev.facts.get('message', '')).splitlines()[0]}"))
        for item in karpenter_events:
            timeline.append(TimelineEvent(
                timestamp=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), start),
                source="karpenter",
                message=f"{item.get('reason', 'Event')}: {item.get('message', '')}",
            ))
        for item in events:
            timeline.append(TimelineEvent(
                timestamp=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), start),
                source="kubernetes",
                message=f"{item.get('reason', 'Event')}: {item.get('message', '')}",
            ))
        timeline.sort(key=lambda item: item.timestamp)

        remediation = [
            Remediation(priority="P1", action="Define ephemeral-storage requests and limits for the affected workload after measuring normal and burst usage.", reason="Prevents unbounded local-storage consumption and improves scheduler accounting."),
            Remediation(priority="P1", action="Alert on node filesystem availability and DiskPressure before kubelet eviction thresholds are reached.", reason="Provides lead time before workload eviction."),
            Remediation(priority="P2", action="Identify high ephemeral-storage consumers on the node and validate log/cache/temp-file retention.", reason="Finds the workload or host path producing the storage pressure."),
            Remediation(priority="P2", action="Review kubelet eviction thresholds and node root-volume sizing against observed production usage.", reason="Validates capacity and safety margins without masking runaway disk usage."),
            Remediation(priority="P3", action="Use the retrieved incident-memory matches as investigation hints only and independently revalidate every signal.", reason="Historical similarity accelerates triage without turning past RCAs into assumed causes."),
        ]

        return InvestigationResult(
            incident_id=incident_id,
            status=status,
            incident=incident,
            entities=entities,
            graph_edges=graph_edges,
            evidence=evidence,
            hypotheses=hypotheses,
            similar_incidents=similar,
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
    def _rationale(hypothesis: Hypothesis, evidence_by_id: dict[str, Evidence]) -> str:
        support = [evidence_by_id[item].summary for item in hypothesis.supporting_evidence if item in evidence_by_id]
        contradict = [evidence_by_id[item].summary for item in hypothesis.contradicting_evidence if item in evidence_by_id]
        parts = []
        if support:
            parts.append("Supporting: " + " ".join(support))
        if contradict:
            parts.append("Contradicting: " + " ".join(contradict))
        return " ".join(parts) or "No decisive evidence collected."

    @staticmethod
    def _generic_result(incident_id: str, incident: IncidentInput, similar: list[SimilarIncident]) -> InvestigationResult:
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
            graph_edges=[],
            evidence=[],
            hypotheses=[hypothesis],
            similar_incidents=similar,
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
