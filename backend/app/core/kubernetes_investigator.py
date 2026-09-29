from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.core.k8s_signals import (
    condition,
    container_statuses,
    event_text,
    karpenter_capacity_signal,
    resource_memory_limits,
    restart_count,
    scheduler_categories,
    terminated_states,
    waiting_states,
)
from app.core.playbooks import hypotheses_for_scenario, kubernetes_root_priority
from app.core.query_safety import quoted_string
from app.core.scoring import score_hypothesis
from app.integrations.base import InvestigationTools
from app.llm.hypotheses import HypothesisGenerator
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


def _parse_dt(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


def _owner(resource: dict[str, Any], kind: str | None = None) -> dict[str, Any] | None:
    for ref in resource.get("metadata", {}).get("ownerReferences", []):
        if kind is None or ref.get("kind") == kind:
            return ref
    return None


def _condition_status(resource: dict[str, Any], condition_type: str) -> str | None:
    item = condition(resource, condition_type)
    return str(item.get("status")) if item else None


def _prom_value(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _quantity_bytes(value: str | None) -> float | None:
    if not value:
        return None
    value = str(value).strip()
    suffixes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
    }
    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            try:
                return float(value[: -len(suffix)]) * multiplier
            except ValueError:
                return None
    try:
        return float(value)
    except ValueError:
        return None


def _event_evidence(
    source: str,
    category: str,
    item: dict[str, Any],
    raw_reference: str,
    fallback: datetime,
    strength: str = "strong",
) -> Evidence:
    return Evidence(
        source=source,
        category=category,
        summary=f"{item.get('reason', 'Event')}: {item.get('message', '')}".strip(),
        facts={"reason": item.get("reason"), "type": item.get("type"), "message": item.get("message")},
        raw_reference=raw_reference,
        observed_at=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), fallback),
        strength=strength,
    )


def _select_primary(scenario: str, hypotheses: list[Hypothesis]) -> Hypothesis:
    deterministic = [item for item in hypotheses if item.origin == "playbook"]
    confirmed = {item.id: item for item in deterministic if item.status == HypothesisStatus.CONFIRMED}
    for hypothesis_id in kubernetes_root_priority(scenario):
        if hypothesis_id in confirmed:
            return confirmed[hypothesis_id]
    confirmed_values = sorted(confirmed.values(), key=lambda item: item.score, reverse=True)
    if confirmed_values:
        return confirmed_values[0]
    return max(deterministic, key=lambda item: item.score)


def _root_copy(scenario: str, primary: Hypothesis) -> tuple[str, str]:
    copy = {
        "container-memory-limit": (
            "Container memory exhaustion triggered OOM termination",
            "Kubernetes recorded an OOMKilled container termination and the workload had a configured memory limit with memory usage close to that limit. Node-wide memory pressure is evaluated separately so a cgroup-level OOM is not confused with host exhaustion.",
        ),
        "container-oom": (
            "Container was terminated by the OOM killer",
            "The container state directly records OOMKilled. The available evidence confirms the termination mechanism but does not overstate whether the pressure came from a container limit or node-wide exhaustion.",
        ),
        "node-memory-pressure": (
            "Node-wide memory pressure contributed to OOM termination",
            "The OOM event coincided with MemoryPressure on the hosting node, making node-wide memory exhaustion a material contributing signal.",
        ),
        "container-crash-loop": (
            "Container is in Kubernetes restart backoff",
            "The pod reports CrashLoopBackOff with repeated restarts. This confirms the crash-loop mechanism; a more specific application, probe, or OOM cause is preferred when independently confirmed.",
        ),
        "oom-restart": (
            "Repeated OOMKilled terminations caused the crash loop",
            "The container is in CrashLoopBackOff and its previous termination state is OOMKilled, linking the restart loop to memory exhaustion.",
        ),
        "probe-failure": (
            "Health probe failures are restarting the container",
            "Kubernetes emitted probe failure events while the container repeatedly restarted, providing direct evidence that liveness or startup checks are driving the crash loop.",
        ),
        "application-exit": (
            "Application process exits repeatedly with a non-zero status",
            "The previous container state records a non-zero application exit and the pod is in restart backoff without an OOMKilled or probe-failure signal taking precedence.",
        ),
        "insufficient-resources": (
            "Scheduler cannot find sufficient node resources",
            "Kubernetes FailedScheduling events explicitly report insufficient CPU, memory, ephemeral storage, or pod capacity across candidate nodes.",
        ),
        "taint-toleration-mismatch": (
            "Pod does not tolerate required node taints",
            "Kubernetes FailedScheduling events identify an untolerated taint, so otherwise eligible nodes reject the pod.",
        ),
        "affinity-mismatch": (
            "Scheduling constraints exclude all candidate nodes",
            "Kubernetes FailedScheduling events identify node affinity, selector, or topology constraints that prevent placement.",
        ),
        "volume-binding": (
            "Volume constraints prevent pod scheduling",
            "Kubernetes FailedScheduling events identify an unbound PVC or volume topology/binding constraint.",
        ),
        "karpenter-capacity": (
            "Karpenter could not provision compatible capacity",
            "Karpenter or cloud-provider events report provisioning or insufficient-capacity failures while the pod remains pending.",
        ),
        "hpa-max-saturation": (
            "HPA is saturated at maxReplicas",
            "The HorizontalPodAutoscaler reports ScalingLimited and the target is at its configured maximum replica count while additional scaling is desired.",
        ),
        "hpa-metrics-unavailable": (
            "HPA cannot calculate scale because metrics are unavailable",
            "The HPA reports ScalingActive=False and related controller evidence indicates that the required metric cannot be evaluated.",
        ),
        "hpa-scale-blocked": (
            "HPA cannot apply the desired scale",
            "The HPA reports AbleToScale=False, indicating a controller or scale-target limitation rather than simple max-replica saturation.",
        ),
        "keda-scaler-error": (
            "KEDA scaler evaluation is failing",
            "The ScaledObject is not Ready and KEDA warning events identify scaler or status-update failures.",
        ),
        "keda-paused": (
            "KEDA autoscaling is explicitly paused",
            "The ScaledObject pause annotation or KEDA pause event confirms that autoscaling was intentionally suspended.",
        ),
        "keda-fallback": (
            "KEDA fallback mode is active",
            "KEDA reports fallback activation while the primary scaler source is unhealthy, so replica decisions are being made from fallback policy.",
        ),
        "keda-trigger-inactive": (
            "KEDA trigger demand is inactive",
            "The ScaledObject reports Active=False. This is a direct scaling-state signal, but by itself does not prove an infrastructure failure.",
        ),
        "node-unreachable": (
            "Node is unreachable from the control plane",
            "Node Ready is Unknown and the node carries an unreachable signal, indicating that the control plane has lost contact with kubelet.",
        ),
        "node-kubelet-unhealthy": (
            "Kubelet reports the node NotReady",
            "Node Ready is False with kubelet/node health evidence, confirming a node-health failure while the API object remains reachable.",
        ),
        "node-resource-pressure": (
            "Node resource pressure contributed to unavailability",
            "One or more DiskPressure, MemoryPressure, or PIDPressure conditions are active on the unhealthy node.",
        ),
        "karpenter-node-disruption": (
            "Karpenter node lifecycle activity contributed to unavailability",
            "NodeClaim readiness or Karpenter disruption events align with the node-health incident.",
        ),
    }
    return copy.get(primary.id, (primary.statement, primary.rationale))


def _remediation_for(scenario: str, primary_id: str) -> list[Remediation]:
    common_memory = [
        Remediation(priority="P1", action="Measure peak and steady-state memory working set before changing limits.", reason="Avoids masking leaks or creating an unnecessarily large memory reservation."),
        Remediation(priority="P1", action="Review heap/cache sizing and memory leak indicators for the affected container.", reason="OOMKilled is a termination signal; the durable fix is usually inside workload memory behavior or sizing."),
        Remediation(priority="P2", action="Define alerts on memory working set versus limit and restart/OOM rates.", reason="Creates lead time before user-visible restart loops."),
    ]
    if scenario == "oomkilled":
        return common_memory
    if scenario == "crashloop":
        return [
            Remediation(priority="P1", action="Inspect the previous container termination reason and previous logs before restarting or deleting the pod.", reason="CrashLoopBackOff is a backoff state, not the underlying failure."),
            Remediation(priority="P1", action="Validate startup/liveness probes against real application startup behavior.", reason="Aggressive probes can create self-sustaining restart loops."),
            Remediation(priority="P2", action="Correlate the first restart with the latest deployment and configuration revision.", reason="A recent change is useful supporting evidence but should not override direct container-state evidence."),
        ]
    if scenario == "failed-scheduling":
        return [
            Remediation(priority="P1", action="Fix the scheduler constraint named in the FailedScheduling event before increasing generic cluster capacity.", reason="Scheduler events identify whether the blocker is resources, taints, affinity, or storage."),
            Remediation(priority="P1", action="Validate workload requests against NodePool instance options and cloud quotas/capacity.", reason="Prevents repeated provisioning attempts that can never satisfy the pod."),
            Remediation(priority="P2", action="Alert on sustained Pending pods and Karpenter provisioning failures.", reason="Separates transient scheduling delay from capacity incidents."),
        ]
    if scenario == "hpa":
        return [
            Remediation(priority="P1", action="Validate whether maxReplicas still represents safe production capacity and downstream limits.", reason="Raising maxReplicas without dependency capacity can amplify an incident."),
            Remediation(priority="P1", action="Verify the HPA metric source and adapter when ScalingActive is false.", reason="Autoscaling cannot react to demand if metrics are unavailable."),
            Remediation(priority="P2", action="Review scale-up behavior, stabilization windows, and workload startup latency.", reason="Ensures scaling policy matches how quickly the service can add useful capacity."),
        ]
    if scenario == "keda":
        return [
            Remediation(priority="P1", action="Validate KEDA trigger connectivity, authentication, and source health.", reason="Scaler-source errors prevent normal demand evaluation."),
            Remediation(priority="P1", action="Review pause and fallback configuration before changing replica counts manually.", reason="Manual scaling can fight KEDA policy or hide the original scaler failure."),
            Remediation(priority="P2", action="Alert on ScaledObject Ready=False, fallback activation, and repeated KEDA warning events.", reason="Makes autoscaling-control-plane failures visible before saturation."),
        ]
    if scenario == "node-not-ready":
        return [
            Remediation(priority="P1", action="Verify kubelet, container runtime, network reachability, and underlying VM health for the node.", reason="Ready=False/Unknown is a node-health signal and must be traced to the host or connectivity layer."),
            Remediation(priority="P1", action="Check whether the node is part of a wider zonal or network failure before draining or replacing additional nodes.", reason="Prevents remediation from increasing blast radius during a shared infrastructure event."),
            Remediation(priority="P2", action="Correlate Karpenter NodeClaim lifecycle and cloud-provider events with the Ready transition.", reason="Distinguishes node failure from intentional or failed lifecycle activity."),
        ]
    return [Remediation(priority="P3", action="Collect more typed evidence before taking remediation action.", reason="The deterministic playbook did not reach a confirmed cause.")]


async def _workload_context(
    tools: InvestigationTools,
    incident: IncidentInput,
    pod: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    assert incident.namespace and incident.pod
    labels = pod.get("metadata", {}).get("labels", {})
    service_name = labels.get("app.kubernetes.io/name") or labels.get("app") or incident.pod
    argo_app_name = labels.get("app.kubernetes.io/instance") or service_name
    rs_ref = _owner(pod, "ReplicaSet")
    rs = (
        await _optional(tools.get_replicaset(incident.namespace, rs_ref["name"]), {})
        if rs_ref
        else {}
    )
    deploy_ref = _owner(rs, "Deployment")
    deployment = (
        await _optional(tools.get_deployment(incident.namespace, deploy_ref["name"]), {})
        if deploy_ref
        else {}
    )
    if deployment:
        argo_app_name = deployment.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance", argo_app_name)
    return rs, deployment, service_name, argo_app_name


async def investigate_pod_scenario(
    *,
    incident_id: str,
    incident: IncidentInput,
    similar: list[SimilarIncident],
    pod: dict[str, Any],
    events: list[dict[str, Any]],
    scenario: str,
    tools: InvestigationTools,
    settings: Settings,
    hypothesis_generator: HypothesisGenerator,
) -> InvestigationResult:
    assert incident.namespace and incident.pod
    start = incident.starts_at
    rs, deployment, service_name, argo_app_name = await _workload_context(tools, incident, pod)
    deploy_ref = _owner(rs, "Deployment")
    target_kind = "Deployment"
    target_name = deploy_ref.get("name") if deploy_ref else ""

    node_name = incident.node or pod.get("spec", {}).get("nodeName")
    node_task = _optional(tools.get_node(node_name), {}) if node_name else _empty_dict()
    namespace_label = quoted_string(incident.namespace)
    pod_label = quoted_string(incident.pod)
    logs_query = f'{{namespace="{namespace_label}", pod="{pod_label}"}}'
    logs_task = _optional(
        tools.loki_query_range(
            logs_query,
            start - timedelta(minutes=20),
            start + timedelta(minutes=5),
            200,
        ),
        {"data": {"result": []}},
    )
    argo_task = _optional(tools.argocd_application(argo_app_name), {})
    hpa_task = (
        _optional(tools.get_hpa_for_target(incident.namespace, target_kind, target_name), {})
        if target_name
        else _empty_dict()
    )
    keda_task = (
        _optional(tools.get_scaledobject_for_target(incident.namespace, target_kind, target_name), {})
        if target_name
        else _empty_dict()
    )
    pod_cluster_events_task = _optional(tools.get_cluster_events(incident.pod), [])
    node, logs, argo_app, hpa, scaledobject, cluster_events = await _gather(
        node_task, logs_task, argo_task, hpa_task, keda_task, pod_cluster_events_task
    )

    nodeclaim: dict[str, Any] = {}
    karpenter_events: list[dict[str, Any]] = []
    if node_name:
        nodeclaim = await _optional(tools.karpenter_nodeclaim_for_node(node_name), {})
        claim_name = nodeclaim.get("metadata", {}).get("name")
        if claim_name:
            karpenter_events = await _optional(tools.get_cluster_events(claim_name), [])

    evidence: list[Evidence] = []
    entities: list[Entity] = []
    graph_edges: list[GraphEdge] = []

    entities.append(Entity(id="pod", kind="Pod", name=incident.pod, namespace=incident.namespace))
    if node_name:
        entities.append(Entity(id="node", kind="Node", name=node_name))
        graph_edges.append(GraphEdge(source="pod", target="node", relationship="scheduled_on"))
    rs_ref = _owner(pod, "ReplicaSet")
    if rs_ref:
        entities.append(Entity(id="replicaset", kind="ReplicaSet", name=rs_ref["name"], namespace=incident.namespace))
        graph_edges.append(GraphEdge(source="pod", target="replicaset", relationship="owned_by"))
    if deploy_ref:
        entities.append(Entity(id="deployment", kind="Deployment", name=deploy_ref["name"], namespace=incident.namespace))
        graph_edges.append(GraphEdge(source="replicaset", target="deployment", relationship="owned_by"))
    entities.append(Entity(id=f"service:{service_name}", kind="Service", name=service_name, namespace=incident.namespace))
    if deploy_ref:
        graph_edges.append(GraphEdge(source="deployment", target=f"service:{service_name}", relationship="serves"))

    pod_ev = Evidence(
        source="kubernetes",
        category="pod-status",
        summary=f"Pod phase={pod.get('status', {}).get('phase', 'unknown')} reason={pod.get('status', {}).get('reason', 'unknown') or 'none'}.",
        facts={
            "phase": pod.get("status", {}).get("phase"),
            "reason": pod.get("status", {}).get("reason"),
            "message": pod.get("status", {}).get("message"),
        },
        raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}",
        strength="medium",
    )
    evidence.append(pod_ev)

    statuses = container_statuses(pod)
    waits = waiting_states(pod)
    terminations = terminated_states(pod)
    restarts = restart_count(pod)
    container_ev = Evidence(
        source="kubernetes",
        category="container-state",
        summary=f"Container state shows {restarts} restarts, waiting={waits or 'none'}, terminated={terminations or 'none'}.",
        facts={"restart_count": restarts, "waiting": waits, "terminated": terminations, "statuses": statuses},
        raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}/status",
        strength="strong",
    )
    evidence.append(container_ev)

    restart_ev = Evidence(
        source="kubernetes",
        category="restart-count",
        summary=f"Aggregate container restart count is {restarts}.",
        facts={"restart_count": restarts},
        raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}/status",
        strength="strong" if restarts >= 2 else "weak",
    )
    evidence.append(restart_ev)

    node_ev: Evidence | None = None
    if node:
        node_ev = Evidence(
            source="kubernetes",
            category="node-conditions",
            summary=(
                f"Node Ready={_condition_status(node, 'Ready')}, MemoryPressure={_condition_status(node, 'MemoryPressure')}, "
                f"DiskPressure={_condition_status(node, 'DiskPressure')}, PIDPressure={_condition_status(node, 'PIDPressure')}."
            ),
            facts={
                "Ready": _condition_status(node, "Ready"),
                "MemoryPressure": _condition_status(node, "MemoryPressure"),
                "DiskPressure": _condition_status(node, "DiskPressure"),
                "PIDPressure": _condition_status(node, "PIDPressure"),
            },
            raw_reference=f"k8s://node/{node_name}",
            strength="strong",
        )
        evidence.append(node_ev)

    event_evidence: list[Evidence] = []
    for item in events:
        if item.get("type") == "Warning" or item.get("reason") in {"FailedScheduling", "Unhealthy", "BackOff", "OOMKilling"}:
            ev = _event_evidence(
                "kubernetes",
                "event",
                item,
                f"k8s://event/{incident.namespace}/{incident.pod}",
                start,
            )
            evidence.append(ev)
            event_evidence.append(ev)

    log_entries = [
        value[1]
        for stream in logs.get("data", {}).get("result", [])
        for value in stream.get("values", [])
        if len(value) > 1
    ]
    logs_ev = Evidence(
        source="loki",
        category="logs",
        summary=f"Retrieved {len(log_entries)} log entries around the incident window.",
        facts={"entries": len(log_entries), "samples": log_entries[:5]},
        query=logs_query,
        raw_reference="loki:/loki/api/v1/query_range",
        strength="medium" if log_entries else "weak",
    )
    evidence.append(logs_ev)

    limits = resource_memory_limits(pod)
    memory_ev = Evidence(
        source="kubernetes",
        category="memory-policy",
        summary=f"Container memory limits: {limits or 'none configured'}.",
        facts={"limits": limits},
        raw_reference=f"k8s://pod/{incident.namespace}/{incident.pod}/resources",
        strength="strong",
    )
    evidence.append(memory_ev)

    working_set_ev: Evidence | None = None
    if scenario in {"oomkilled", "crashloop"}:
        query = (
            f'sum(container_memory_working_set_bytes{{namespace="{namespace_label}",pod="{pod_label}",'
            'container!="",image!=""})'
        )
        payload = await _optional(tools.prometheus_query(query, start), {})
        working_set = _prom_value(payload)
        if working_set is not None:
            working_set_ev = Evidence(
                source="prometheus",
                category="memory-working-set",
                summary=f"Pod memory working set near the incident was approximately {working_set / 1024**2:.0f} MiB.",
                facts={"working_set_bytes": working_set},
                query=query,
                raw_reference="prometheus:/api/v1/query",
                strength="strong",
            )
            evidence.append(working_set_ev)

    argo_ev: Evidence | None = None
    git_ev: Evidence | None = None
    if scenario in {"oomkilled", "crashloop"} and argo_app:
        operation = argo_app.get("status", {}).get("operationState", {})
        finished_raw = operation.get("finishedAt")
        finished = _parse_dt(finished_raw, start)
        delta = abs((start - finished).total_seconds()) / 60 if finished_raw else None
        revision = argo_app.get("status", {}).get("sync", {}).get("revision", "")
        source = argo_app.get("spec", {}).get("source", {})
        repo_url = source.get("repoURL", "")
        argo_ev = Evidence(
            source="argocd",
            category="change-correlation",
            summary=f"Argo CD revision={revision or 'unknown'}; last sync was {delta:.0f} minutes from the incident." if delta is not None else f"Argo CD revision={revision or 'unknown'}.",
            facts={"revision": revision, "delta_minutes": delta, "repo_url": repo_url},
            raw_reference=f"argocd://application/{argo_app_name}",
            observed_at=finished,
            strength="medium",
        )
        evidence.append(argo_ev)
        entities.append(Entity(id="argocd", kind="ArgoCDApplication", name=argo_app_name, metadata={"revision": revision}))
        if deploy_ref:
            graph_edges.append(GraphEdge(source="deployment", target="argocd", relationship="managed_by", evidence_ids=[argo_ev.id]))
        if repo_url and revision:
            commit = await _optional(tools.github_commit(repo_url, revision), {})
            if commit:
                author = commit.get("commit", {}).get("author", {})
                git_ev = Evidence(
                    source="github",
                    category="source-change",
                    summary=f"Git revision {commit.get('sha', revision)[:12]}: {str(commit.get('commit', {}).get('message', '')).splitlines()[0]}.",
                    facts={"sha": commit.get("sha", revision), "message": commit.get("commit", {}).get("message")},
                    raw_reference=commit.get("html_url"),
                    observed_at=_parse_dt(author.get("date"), start),
                    strength="weak",
                )
                evidence.append(git_ev)

    hpa_ev: Evidence | None = None
    hpa_replica_ev: Evidence | None = None
    hpa_events: list[dict[str, Any]] = []
    if hpa:
        name = hpa.get("metadata", {}).get("name", "hpa")
        entities.append(Entity(id="hpa", kind="HorizontalPodAutoscaler", name=name, namespace=incident.namespace))
        if deploy_ref:
            graph_edges.append(GraphEdge(source="hpa", target="deployment", relationship="scales"))
        hpa_ev = Evidence(
            source="kubernetes",
            category="hpa-conditions",
            summary=f"HPA conditions: {hpa.get('status', {}).get('conditions', [])}.",
            facts={"conditions": hpa.get("status", {}).get("conditions", [])},
            raw_reference=f"k8s://hpa/{incident.namespace}/{name}",
            strength="strong",
        )
        evidence.append(hpa_ev)
        hpa_replica_ev = Evidence(
            source="kubernetes",
            category="hpa-replicas",
            summary=(
                f"HPA replicas current={hpa.get('status', {}).get('currentReplicas')}, "
                f"desired={hpa.get('status', {}).get('desiredReplicas')}, max={hpa.get('spec', {}).get('maxReplicas')}."
            ),
            facts={
                "current": hpa.get("status", {}).get("currentReplicas"),
                "desired": hpa.get("status", {}).get("desiredReplicas"),
                "max": hpa.get("spec", {}).get("maxReplicas"),
            },
            raw_reference=f"k8s://hpa/{incident.namespace}/{name}",
            strength="strong",
        )
        evidence.append(hpa_replica_ev)
        hpa_events = await _optional(tools.get_cluster_events(name), [])
        for item in hpa_events:
            if item.get("type") == "Warning":
                evidence.append(_event_evidence("kubernetes", "hpa-event", item, f"k8s://hpa/{incident.namespace}/{name}", start))

    keda_ev: Evidence | None = None
    keda_annotation_ev: Evidence | None = None
    keda_events: list[dict[str, Any]] = []
    if scaledobject:
        name = scaledobject.get("metadata", {}).get("name", "scaledobject")
        entities.append(Entity(id="scaledobject", kind="ScaledObject", name=name, namespace=incident.namespace))
        if deploy_ref:
            graph_edges.append(GraphEdge(source="scaledobject", target="deployment", relationship="scales"))
        keda_ev = Evidence(
            source="keda",
            category="scaledobject-conditions",
            summary=f"ScaledObject conditions: {scaledobject.get('status', {}).get('conditions', [])}.",
            facts={"conditions": scaledobject.get("status", {}).get("conditions", [])},
            raw_reference=f"k8s://scaledobject/{incident.namespace}/{name}",
            strength="strong",
        )
        evidence.append(keda_ev)
        annotations = scaledobject.get("metadata", {}).get("annotations", {})
        keda_annotation_ev = Evidence(
            source="keda",
            category="scaledobject-policy",
            summary=f"ScaledObject autoscaling annotations: {annotations}.",
            facts={"annotations": annotations, "fallback": scaledobject.get("spec", {}).get("fallback")},
            raw_reference=f"k8s://scaledobject/{incident.namespace}/{name}",
            strength="strong",
        )
        evidence.append(keda_annotation_ev)
        keda_events = await _optional(tools.get_cluster_events(name), [])
        for item in keda_events:
            evidence.append(_event_evidence("keda", "scaledobject-event", item, f"k8s://scaledobject/{incident.namespace}/{name}", start))

    karpenter_ev: Evidence | None = None
    if nodeclaim:
        ready = _condition_status(nodeclaim, "Ready")
        karpenter_ev = Evidence(
            source="karpenter",
            category="nodeclaim",
            summary=f"NodeClaim {nodeclaim.get('metadata', {}).get('name')} Ready={ready}.",
            facts={"ready": ready, "conditions": nodeclaim.get("status", {}).get("conditions", [])},
            raw_reference=f"k8s://nodeclaim/{nodeclaim.get('metadata', {}).get('name')}",
            strength="strong",
        )
        evidence.append(karpenter_ev)

    if scenario == "failed-scheduling" and cluster_events:
        for item in cluster_events:
            if karpenter_capacity_signal([item]):
                evidence.append(_event_evidence("karpenter", "provisioning-event", item, "k8s://events/karpenter", start))

    hypotheses = hypotheses_for_scenario(scenario)
    hmap = {item.id: item for item in hypotheses}

    if scenario == "oomkilled":
        oom = any(str(item.get("reason", "")).lower() == "oomkilled" for item in terminations)
        if oom:
            hmap["container-oom"].supporting_evidence.extend([container_ev.id, pod_ev.id])
        if node_ev:
            if node_ev.facts.get("MemoryPressure") == "True":
                hmap["node-memory-pressure"].supporting_evidence.extend([node_ev.id, container_ev.id] if oom else [node_ev.id])
            elif node_ev.facts.get("MemoryPressure") == "False":
                hmap["node-memory-pressure"].contradicting_evidence.append(node_ev.id)
        if limits:
            hmap["missing-memory-limit"].contradicting_evidence.append(memory_ev.id)
            limit_total = sum(value for value in (_quantity_bytes(v) for v in limits.values()) if value is not None)
            working_set = working_set_ev.facts.get("working_set_bytes") if working_set_ev else None
            if oom and limit_total and isinstance(working_set, (int, float)) and working_set >= limit_total * 0.85:
                hmap["container-memory-limit"].supporting_evidence.extend([container_ev.id, working_set_ev.id])
        else:
            hmap["missing-memory-limit"].supporting_evidence.extend([memory_ev.id, container_ev.id] if oom else [memory_ev.id])

    elif scenario == "crashloop":
        crash_wait = any(str(item.get("reason", "")).lower() == "crashloopbackoff" for item in waits)
        if crash_wait:
            hmap["container-crash-loop"].supporting_evidence.append(container_ev.id)
        if restarts >= 2:
            hmap["container-crash-loop"].supporting_evidence.append(restart_ev.id)
        if any(str(item.get("reason", "")).lower() == "oomkilled" for item in terminations):
            hmap["oom-restart"].supporting_evidence.extend([container_ev.id, restart_ev.id])
        probe_events = [ev for ev in event_evidence if "probe" in ev.summary.lower() or ev.facts.get("reason") == "Unhealthy"]
        if probe_events:
            hmap["probe-failure"].supporting_evidence.extend([probe_events[0].id, restart_ev.id])
        nonzero = [
            item for item in terminations
            if int(item.get("exitCode", 0) or 0) != 0 and str(item.get("reason", "")).lower() != "oomkilled"
        ]
        if nonzero and not probe_events:
            hmap["application-exit"].supporting_evidence.extend([container_ev.id, logs_ev.id])
        if argo_ev:
            delta = argo_ev.facts.get("delta_minutes")
            if isinstance(delta, (int, float)) and delta <= settings.change_correlation_window_minutes:
                hmap["recent-change-regression"].supporting_evidence.append(argo_ev.id)
                if git_ev:
                    hmap["recent-change-regression"].supporting_evidence.append(git_ev.id)
            elif isinstance(delta, (int, float)):
                hmap["recent-change-regression"].contradicting_evidence.append(argo_ev.id)

    elif scenario == "failed-scheduling":
        pending_ev = pod_ev
        for ev in event_evidence:
            if ev.facts.get("reason") != "FailedScheduling":
                continue
            for category in scheduler_categories(str(ev.facts.get("message", ""))):
                hmap[category].supporting_evidence.extend([ev.id, pending_ev.id])
        capacity_evidence = [ev for ev in evidence if ev.source == "karpenter" and ev.category == "provisioning-event"]
        if capacity_evidence:
            hmap["karpenter-capacity"].supporting_evidence.extend([capacity_evidence[0].id, pending_ev.id])

    elif scenario == "hpa" and hpa_ev and hpa_replica_ev:
        scaling_limited = condition(hpa, "ScalingLimited")
        scaling_active = condition(hpa, "ScalingActive")
        able = condition(hpa, "AbleToScale")
        current = hpa_replica_ev.facts.get("current")
        desired = hpa_replica_ev.facts.get("desired")
        maximum = hpa_replica_ev.facts.get("max")
        if scaling_limited and str(scaling_limited.get("status")) == "True" and maximum is not None and current == maximum:
            hmap["hpa-max-saturation"].supporting_evidence.extend([hpa_ev.id, hpa_replica_ev.id])
        if scaling_active and str(scaling_active.get("status")) == "False":
            hmap["hpa-metrics-unavailable"].supporting_evidence.append(hpa_ev.id)
            warning = next((ev for ev in evidence if ev.category == "hpa-event"), None)
            if warning:
                hmap["hpa-metrics-unavailable"].supporting_evidence.append(warning.id)
        if able and str(able.get("status")) == "False":
            hmap["hpa-scale-blocked"].supporting_evidence.append(hpa_ev.id)
            warning = next((ev for ev in evidence if ev.category == "hpa-event"), None)
            if warning:
                hmap["hpa-scale-blocked"].supporting_evidence.append(warning.id)
        if desired is not None and maximum is not None and desired < maximum:
            hmap["hpa-max-saturation"].contradicting_evidence.append(hpa_replica_ev.id)

    elif scenario == "keda" and keda_ev and keda_annotation_ev:
        ready = condition(scaledobject, "Ready")
        active = condition(scaledobject, "Active")
        annotations = keda_annotation_ev.facts.get("annotations", {})
        event_blob = event_text(keda_events)
        if ready and str(ready.get("status")) == "False":
            hmap["keda-scaler-error"].supporting_evidence.append(keda_ev.id)
            warning = next((ev for ev in evidence if ev.source == "keda" and ev.facts.get("type") == "Warning"), None)
            if warning:
                hmap["keda-scaler-error"].supporting_evidence.append(warning.id)
        if str(annotations.get("autoscaling.keda.sh/paused", "")).lower() == "true" or "scaledobjectpaused" in event_blob:
            hmap["keda-paused"].supporting_evidence.append(keda_annotation_ev.id)
            pause_event = next((ev for ev in evidence if "paused" in ev.summary.lower()), None)
            if pause_event:
                hmap["keda-paused"].supporting_evidence.append(pause_event.id)
        if "fallbackactivated" in event_blob or "fallback activated" in event_blob:
            fallback_event = next((ev for ev in evidence if "fallback" in ev.summary.lower()), None)
            if fallback_event:
                hmap["keda-fallback"].supporting_evidence.extend([keda_annotation_ev.id, fallback_event.id])
        if active and str(active.get("status")) == "False":
            hmap["keda-trigger-inactive"].supporting_evidence.append(keda_ev.id)

    by_id = {item.id: item for item in evidence}
    for hypothesis in hypotheses:
        score_hypothesis(hypothesis, by_id)
        hypothesis.rationale = _rationale(hypothesis, by_id)

    llm_hypotheses = await hypothesis_generator.propose(incident, entities, evidence, hypotheses)
    hypotheses.extend(llm_hypotheses)
    primary = _select_primary(scenario, hypotheses)
    status = "resolved" if primary.status == HypothesisStatus.CONFIRMED else "inconclusive"
    title, summary = _root_copy(scenario, primary)

    timeline: list[TimelineEvent] = []
    for item in [*events, *cluster_events, *karpenter_events, *hpa_events, *keda_events]:
        timestamp = _parse_dt(item.get("eventTime") or item.get("lastTimestamp"), start)
        timeline.append(TimelineEvent(
            timestamp=timestamp,
            source="keda" if item in keda_events else "karpenter" if item in karpenter_events or item in cluster_events and karpenter_capacity_signal([item]) else "kubernetes",
            message=f"{item.get('reason', 'Event')}: {item.get('message', '')}",
        ))
    if argo_ev:
        timeline.append(TimelineEvent(timestamp=argo_ev.observed_at, source="argocd", message=argo_ev.summary))
    timeline.sort(key=lambda item: item.timestamp)

    supporting = len(primary.supporting_evidence)
    contradicting = len(primary.contradicting_evidence)
    strength = "strong" if primary.status == HypothesisStatus.CONFIRMED and supporting >= 2 and not contradicting else "medium" if supporting else "weak"

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
            title=title,
            summary=summary if status == "resolved" else primary.rationale,
            hypothesis_id=primary.id if status == "resolved" else None,
            evidence_strength=strength,
            supporting_signals=supporting,
            contradicting_signals=contradicting,
        ),
        remediation=_remediation_for(scenario, primary.id),
    )


async def investigate_node_not_ready(
    *,
    incident_id: str,
    incident: IncidentInput,
    similar: list[SimilarIncident],
    tools: InvestigationTools,
    hypothesis_generator: HypothesisGenerator,
) -> InvestigationResult:
    assert incident.node
    start = incident.starts_at
    node = await tools.get_node(incident.node)
    events, pods, nodeclaim = await _gather(
        _optional(tools.get_cluster_events(incident.node), []),
        _optional(tools.list_pods_on_node(incident.node), []),
        _optional(tools.karpenter_nodeclaim_for_node(incident.node), {}),
    )

    evidence: list[Evidence] = []
    entities = [Entity(id="node", kind="Node", name=incident.node)]
    graph_edges: list[GraphEdge] = []

    ready = condition(node, "Ready")
    ready_status = str(ready.get("status")) if ready else None
    ready_ev = Evidence(
        source="kubernetes",
        category="node-ready",
        summary=f"Node Ready={ready_status}; reason={ready.get('reason') if ready else 'unknown'}; message={ready.get('message') if ready else 'unknown'}.",
        facts={"condition": ready},
        raw_reference=f"k8s://node/{incident.node}",
        strength="strong",
    )
    evidence.append(ready_ev)

    taints = node.get("spec", {}).get("taints", [])
    taint_ev = Evidence(
        source="kubernetes",
        category="node-taints",
        summary=f"Node taints: {taints or 'none'}.",
        facts={"taints": taints},
        raw_reference=f"k8s://node/{incident.node}",
        strength="strong",
    )
    evidence.append(taint_ev)

    pressure = {
        name: _condition_status(node, name)
        for name in ("MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable")
    }
    pressure_ev = Evidence(
        source="kubernetes",
        category="node-pressure",
        summary=f"Node pressure/network conditions: {pressure}.",
        facts=pressure,
        raw_reference=f"k8s://node/{incident.node}",
        strength="strong",
    )
    evidence.append(pressure_ev)

    event_evidence: list[Evidence] = []
    for item in events:
        ev = _event_evidence("kubernetes", "node-event", item, f"k8s://node/{incident.node}", start)
        evidence.append(ev)
        event_evidence.append(ev)

    nodeclaim_ev: Evidence | None = None
    karpenter_events: list[dict[str, Any]] = []
    if nodeclaim:
        claim_name = nodeclaim.get("metadata", {}).get("name", "unknown")
        entities.append(Entity(id="nodeclaim", kind="NodeClaim", name=claim_name))
        graph_edges.append(GraphEdge(source="node", target="nodeclaim", relationship="represented_by"))
        nodeclaim_ev = Evidence(
            source="karpenter",
            category="nodeclaim",
            summary=f"NodeClaim {claim_name} Ready={_condition_status(nodeclaim, 'Ready')}.",
            facts={"conditions": nodeclaim.get("status", {}).get("conditions", [])},
            raw_reference=f"k8s://nodeclaim/{claim_name}",
            strength="strong",
        )
        evidence.append(nodeclaim_ev)
        karpenter_events = await _optional(tools.get_cluster_events(claim_name), [])
        for item in karpenter_events:
            evidence.append(_event_evidence("karpenter", "nodeclaim-event", item, f"k8s://nodeclaim/{claim_name}", start))

    pod_ev = Evidence(
        source="kubernetes",
        category="blast-radius",
        summary=f"{len(pods)} pods are assigned to the unhealthy node.",
        facts={"pod_count": len(pods), "pods": [item.get("metadata", {}).get("name") for item in pods[:20]]},
        raw_reference=f"k8s://node/{incident.node}/pods",
        strength="medium",
    )
    evidence.append(pod_ev)

    hypotheses = hypotheses_for_scenario("node-not-ready")
    hmap = {item.id: item for item in hypotheses}
    taint_keys = {str(item.get("key")) for item in taints}

    if ready_status == "Unknown":
        hmap["node-unreachable"].supporting_evidence.append(ready_ev.id)
        if "node.kubernetes.io/unreachable" in taint_keys:
            hmap["node-unreachable"].supporting_evidence.append(taint_ev.id)
        matching = next((ev for ev in event_evidence if "unreach" in ev.summary.lower() or "notready" in ev.summary.lower()), None)
        if matching:
            hmap["node-unreachable"].supporting_evidence.append(matching.id)
        hmap["node-kubelet-unhealthy"].contradicting_evidence.append(ready_ev.id)
    elif ready_status == "False":
        hmap["node-kubelet-unhealthy"].supporting_evidence.append(ready_ev.id)
        if "node.kubernetes.io/not-ready" in taint_keys:
            hmap["node-kubelet-unhealthy"].supporting_evidence.append(taint_ev.id)
        matching = next((ev for ev in event_evidence if "notready" in ev.summary.lower() or "kubelet" in ev.summary.lower()), None)
        if matching:
            hmap["node-kubelet-unhealthy"].supporting_evidence.append(matching.id)
        hmap["node-unreachable"].contradicting_evidence.append(ready_ev.id)

    active_pressure = [key for key, value in pressure.items() if value == "True"]
    if active_pressure:
        hmap["node-resource-pressure"].supporting_evidence.extend([pressure_ev.id, ready_ev.id])
    else:
        hmap["node-resource-pressure"].contradicting_evidence.append(pressure_ev.id)

    if nodeclaim_ev:
        claim_ready = _condition_status(nodeclaim, "Ready")
        disruption = next(
            (ev for ev in evidence if ev.source == "karpenter" and any(token in ev.summary.lower() for token in ("disrupt", "terminat", "insufficient"))),
            None,
        )
        if claim_ready != "True":
            hmap["karpenter-node-disruption"].supporting_evidence.append(nodeclaim_ev.id)
            if disruption:
                hmap["karpenter-node-disruption"].supporting_evidence.append(disruption.id)
        elif disruption:
            hmap["karpenter-node-disruption"].supporting_evidence.extend([nodeclaim_ev.id, disruption.id])
        else:
            hmap["karpenter-node-disruption"].contradicting_evidence.append(nodeclaim_ev.id)

    by_id = {item.id: item for item in evidence}
    for hypothesis in hypotheses:
        score_hypothesis(hypothesis, by_id)
        hypothesis.rationale = _rationale(hypothesis, by_id)

    llm_hypotheses = await hypothesis_generator.propose(incident, entities, evidence, hypotheses)
    hypotheses.extend(llm_hypotheses)
    primary = _select_primary("node-not-ready", hypotheses)
    status = "resolved" if primary.status == HypothesisStatus.CONFIRMED else "inconclusive"
    title, summary = _root_copy("node-not-ready", primary)

    timeline = [
        TimelineEvent(
            timestamp=_parse_dt(item.get("eventTime") or item.get("lastTimestamp"), start),
            source="karpenter" if item in karpenter_events else "kubernetes",
            message=f"{item.get('reason', 'Event')}: {item.get('message', '')}",
        )
        for item in [*events, *karpenter_events]
    ]
    timeline.sort(key=lambda item: item.timestamp)

    supporting = len(primary.supporting_evidence)
    contradicting = len(primary.contradicting_evidence)
    strength = "strong" if status == "resolved" and supporting >= 2 and not contradicting else "medium" if supporting else "weak"

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
            title=title,
            summary=summary if status == "resolved" else primary.rationale,
            hypothesis_id=primary.id if status == "resolved" else None,
            evidence_strength=strength,
            supporting_signals=supporting,
            contradicting_signals=contradicting,
        ),
        remediation=_remediation_for("node-not-ready", primary.id),
    )


async def _empty_dict() -> dict[str, Any]:
    return {}


async def _optional(operation, default):
    try:
        return await operation
    except Exception:
        return default


async def _gather(*tasks):
    import asyncio

    return await asyncio.gather(*tasks)


def _rationale(hypothesis: Hypothesis, evidence_by_id: dict[str, Evidence]) -> str:
    support = [evidence_by_id[item].summary for item in hypothesis.supporting_evidence if item in evidence_by_id]
    contradict = [evidence_by_id[item].summary for item in hypothesis.contradicting_evidence if item in evidence_by_id]
    parts: list[str] = []
    if support:
        parts.append("Supporting: " + " ".join(support))
    if contradict:
        parts.append("Contradicting: " + " ".join(contradict))
    return " ".join(parts) or "No decisive evidence collected."
