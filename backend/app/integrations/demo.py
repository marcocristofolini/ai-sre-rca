from __future__ import annotations

from datetime import datetime
from typing import Any


class DemoTools:
    async def get_pod(self, namespace: str, pod: str) -> dict[str, Any]:
        return {
            "metadata": {
                "name": pod,
                "namespace": namespace,
                "labels": {"app": "landing-page", "app.kubernetes.io/instance": "landing-page"},
                "ownerReferences": [{"kind": "ReplicaSet", "name": "landing-page-7d8b6d9b"}],
            },
            "spec": {
                "nodeName": "ip-10-0-1-42",
                "containers": [{
                    "name": "web",
                    "resources": {
                        "requests": {"cpu": "200m", "memory": "256Mi"},
                        "limits": {"cpu": "1", "memory": "512Mi"},
                    },
                }],
            },
            "status": {
                "phase": "Failed",
                "reason": "Evicted",
                "message": "The node was low on resource: ephemeral-storage. Threshold quantity: 2147483648, available: 534773760Ki.",
                "containerStatuses": [{"name": "web", "restartCount": 0, "state": {"terminated": {"reason": "Error", "exitCode": 137}}}],
            },
        }

    async def get_node(self, node: str) -> dict[str, Any]:
        return {
            "metadata": {"name": node, "labels": {"karpenter.sh/nodepool": "general"}},
            "status": {
                "capacity": {"ephemeral-storage": "100Gi", "memory": "16Gi", "cpu": "4"},
                "allocatable": {"ephemeral-storage": "92Gi", "memory": "15Gi", "cpu": "3900m"},
                "conditions": [
                    {"type": "Ready", "status": "True", "reason": "KubeletReady"},
                    {"type": "MemoryPressure", "status": "False", "reason": "KubeletHasSufficientMemory"},
                    {"type": "DiskPressure", "status": "True", "reason": "KubeletHasDiskPressure"},
                    {"type": "PIDPressure", "status": "False", "reason": "KubeletHasSufficientPID"},
                ],
            },
        }

    async def get_events(self, namespace: str, object_name: str) -> list[dict[str, Any]]:
        return [
            {
                "reason": "Evicted",
                "message": "The node was low on resource: ephemeral-storage.",
                "type": "Warning",
                "eventTime": "2026-09-24T20:47:03Z",
                "involvedObject": {"name": object_name, "kind": "Pod"},
            },
            {
                "reason": "Killing",
                "message": "Stopping container web",
                "type": "Normal",
                "eventTime": "2026-09-24T20:47:04Z",
                "involvedObject": {"name": object_name, "kind": "Pod"},
            },
        ]

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        return [
            {"metadata": {"namespace": "exchange", "name": "landing-page-7d8b6d9b-x9k2p"}, "spec": {"nodeName": node}},
            {"metadata": {"namespace": "payments", "name": "payments-api-6bb86f8c8d-2m4zx"}, "spec": {"nodeName": node}},
            {"metadata": {"namespace": "logging", "name": "fluent-bit-zm44q"}, "spec": {"nodeName": node}},
        ]

    async def prometheus_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        if "node_filesystem_avail_bytes" in query:
            value = "536870912"
        elif "container_fs_usage_bytes" in query:
            value = "17179869184"
        else:
            value = "0"
        return {"status": "success", "data": {"resultType": "vector", "result": [{"metric": {}, "value": [at.timestamp() if at else 0, value]}]}}

    async def prometheus_query_range(self, query: str, start: datetime, end: datetime, step: str = "60s") -> dict[str, Any]:
        mid = start.timestamp() + (end.timestamp() - start.timestamp()) / 2
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{"metric": {"instance": "ip-10-0-1-42"}, "values": [[start.timestamp(), "6442450944"], [mid, "2147483648"], [end.timestamp(), "536870912"]]}],
            },
        }

    async def loki_query_range(self, query: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [{
                    "stream": {"namespace": "exchange", "pod": "landing-page-7d8b6d9b-x9k2p"},
                    "values": [[str(int(end.timestamp() * 1_000_000_000)), "HTTP server shutting down after SIGTERM"]],
                }],
            },
        }

    async def argocd_application(self, app_name: str) -> dict[str, Any]:
        return {
            "metadata": {"name": app_name},
            "status": {
                "sync": {"status": "Synced", "revision": "7d37e39"},
                "health": {"status": "Healthy"},
                "operationState": {"finishedAt": "2026-09-24T17:12:00Z"},
            },
        }
