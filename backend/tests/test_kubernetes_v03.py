from datetime import datetime, timezone

import pytest

from app.core.engine import InvestigationEngine
from app.integrations.demo import DemoTools
from app.schemas import IncidentInput


START = datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc)


class HealthyBaseTools(DemoTools):
    async def get_node(self, node: str):
        data = await super().get_node(node)
        data["status"]["conditions"] = [
            {"type": "Ready", "status": "True", "reason": "KubeletReady"},
            {"type": "MemoryPressure", "status": "False", "reason": "KubeletHasSufficientMemory"},
            {"type": "DiskPressure", "status": "False", "reason": "KubeletHasNoDiskPressure"},
            {"type": "PIDPressure", "status": "False", "reason": "KubeletHasSufficientPID"},
        ]
        return data

    async def get_cluster_events(self, object_name: str):
        return []


class OOMTools(HealthyBaseTools):
    async def get_pod(self, namespace: str, pod: str):
        data = await super().get_pod(namespace, pod)
        data["spec"]["containers"][0]["resources"]["limits"]["memory"] = "512Mi"
        data["status"] = {
            "phase": "Running",
            "containerStatuses": [{
                "name": "web",
                "restartCount": 3,
                "state": {"running": {"startedAt": "2026-09-24T20:47:05Z"}},
                "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137, "finishedAt": "2026-09-24T20:47:04Z"}},
            }],
        }
        return data

    async def get_events(self, namespace: str, object_name: str):
        return [{"reason": "OOMKilling", "message": "Memory cgroup out of memory", "type": "Warning", "eventTime": "2026-09-24T20:47:04Z"}]

    async def prometheus_query(self, query: str, at=None):
        if "container_memory_working_set_bytes" in query:
            return {"status": "success", "data": {"result": [{"value": [START.timestamp(), str(500 * 1024**2)]}]}}
        return await super().prometheus_query(query, at)


class CrashLoopTools(HealthyBaseTools):
    async def get_pod(self, namespace: str, pod: str):
        data = await super().get_pod(namespace, pod)
        data["status"] = {
            "phase": "Running",
            "containerStatuses": [{
                "name": "web",
                "restartCount": 8,
                "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "back-off restarting failed container"}},
                "lastState": {"terminated": {"reason": "Error", "exitCode": 1, "finishedAt": "2026-09-24T20:46:55Z"}},
            }],
        }
        return data

    async def get_events(self, namespace: str, object_name: str):
        return [{"reason": "BackOff", "message": "Back-off restarting failed container web", "type": "Warning", "eventTime": "2026-09-24T20:47:00Z"}]


class FailedSchedulingTools(HealthyBaseTools):
    async def get_pod(self, namespace: str, pod: str):
        data = await super().get_pod(namespace, pod)
        data["spec"].pop("nodeName", None)
        data["status"] = {"phase": "Pending", "message": "pod is unschedulable"}
        return data

    async def get_events(self, namespace: str, object_name: str):
        return [{
            "reason": "FailedScheduling",
            "message": "0/3 nodes are available: 3 Insufficient cpu.",
            "type": "Warning",
            "eventTime": "2026-09-24T20:47:00Z",
        }]


class KarpenterCapacityTools(FailedSchedulingTools):
    async def get_events(self, namespace: str, object_name: str):
        return [{
            "reason": "FailedScheduling",
            "message": "0/0 nodes are available.",
            "type": "Warning",
            "eventTime": "2026-09-24T20:47:00Z",
        }]

    async def get_cluster_events(self, object_name: str):
        if "landing-page" in object_name:
            return [{
                "reason": "FailedProvisioning",
                "message": "Insufficient Capacity: no instance type satisfied the pod requirements",
                "type": "Warning",
                "eventTime": "2026-09-24T20:47:02Z",
            }]
        return []


class HPATools(HealthyBaseTools):
    async def get_pod(self, namespace: str, pod: str):
        data = await super().get_pod(namespace, pod)
        data["status"] = {
            "phase": "Running",
            "containerStatuses": [{"name": "web", "restartCount": 0, "state": {"running": {}}}],
        }
        return data

    async def get_events(self, namespace: str, object_name: str):
        return []

    async def get_hpa_for_target(self, namespace: str, kind: str, name: str):
        return {
            "metadata": {"name": "landing-page"},
            "spec": {"maxReplicas": 10, "scaleTargetRef": {"kind": kind, "name": name}},
            "status": {
                "currentReplicas": 10,
                "desiredReplicas": 10,
                "conditions": [
                    {"type": "AbleToScale", "status": "True", "reason": "ReadyForNewScale"},
                    {"type": "ScalingActive", "status": "True", "reason": "ValidMetricFound"},
                    {"type": "ScalingLimited", "status": "True", "reason": "TooManyReplicas"},
                ],
            },
        }


class KEDATools(HPATools):
    async def get_scaledobject_for_target(self, namespace: str, kind: str, name: str):
        return {
            "metadata": {"name": "landing-page-events", "annotations": {}},
            "spec": {"scaleTargetRef": {"kind": kind, "name": name}},
            "status": {
                "conditions": [
                    {"type": "Ready", "status": "False", "reason": "TriggerError"},
                    {"type": "Active", "status": "False", "reason": "ScalerNotActive"},
                ]
            },
        }

    async def get_cluster_events(self, object_name: str):
        if object_name == "landing-page-events":
            return [{
                "reason": "ScaledObjectCheckFailed",
                "message": "error getting scaler state: authentication failed",
                "type": "Warning",
                "eventTime": "2026-09-24T20:47:01Z",
            }]
        return []


class NodeNotReadyTools(HealthyBaseTools):
    async def get_node(self, node: str):
        data = await super().get_node(node)
        data["spec"] = {"taints": [{"key": "node.kubernetes.io/unreachable", "effect": "NoExecute"}]}
        data["status"]["conditions"][0] = {
            "type": "Ready",
            "status": "Unknown",
            "reason": "NodeStatusUnknown",
            "message": "Kubelet stopped posting node status.",
            "lastHeartbeatTime": "2026-09-24T20:45:00Z",
        }
        return data

    async def get_cluster_events(self, object_name: str):
        return [{
            "reason": "NodeNotReady",
            "message": "Node status is now: NodeNotReady",
            "type": "Warning",
            "eventTime": "2026-09-24T20:47:00Z",
        }]

    async def karpenter_nodeclaim_for_node(self, node: str):
        return {}


def pod_incident(alert_name: str) -> IncidentInput:
    return IncidentInput(
        title=alert_name,
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name=alert_name,
        starts_at=START,
    )


@pytest.mark.asyncio
async def test_oomkilled_prefers_memory_limit_evidence():
    result = await InvestigationEngine(OOMTools()).investigate(pod_incident("KubePodOOMKilled"))
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "container-memory-limit"
    assert result.root_cause.evidence_strength == "strong"


@pytest.mark.asyncio
async def test_crashloop_identifies_nonzero_application_exit():
    result = await InvestigationEngine(CrashLoopTools()).investigate(pod_incident("KubePodCrashLooping"))
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "application-exit"


@pytest.mark.asyncio
async def test_failed_scheduling_identifies_resource_shortage():
    incident = pod_incident("KubePodFailedScheduling")
    incident.node = None
    result = await InvestigationEngine(FailedSchedulingTools()).investigate(incident)
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "insufficient-resources"


@pytest.mark.asyncio
async def test_failed_scheduling_identifies_karpenter_capacity_failure():
    incident = pod_incident("KubePodFailedScheduling")
    incident.node = None
    result = await InvestigationEngine(KarpenterCapacityTools()).investigate(incident)
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "karpenter-capacity"


@pytest.mark.asyncio
async def test_hpa_max_saturation_is_confirmed():
    result = await InvestigationEngine(HPATools()).investigate(pod_incident("KubeHpaMaxedOut"))
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "hpa-max-saturation"


@pytest.mark.asyncio
async def test_keda_scaler_failure_is_confirmed():
    result = await InvestigationEngine(KEDATools()).investigate(pod_incident("KedaScalerErrors"))
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "keda-scaler-error"


@pytest.mark.asyncio
async def test_node_not_ready_identifies_unreachable_node():
    incident = IncidentInput(
        title="Node not ready",
        cluster="prod",
        node="ip-10-0-1-42",
        alert_name="KubeNodeNotReady",
        starts_at=START,
    )
    result = await InvestigationEngine(NodeNotReadyTools()).investigate(incident)
    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "node-unreachable"
    assert any(item.category == "blast-radius" for item in result.evidence)
