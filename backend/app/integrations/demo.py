from __future__ import annotations

import base64
from datetime import datetime
from typing import Any


class DemoTools:
    async def get_pod(self, namespace: str, pod: str) -> dict[str, Any]:
        return {
            "metadata": {
                "name": pod,
                "namespace": namespace,
                "labels": {
                    "app": "landing-page",
                    "app.kubernetes.io/name": "landing-page",
                    "app.kubernetes.io/instance": "landing-page",
                },
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
            "metadata": {"name": node, "labels": {"karpenter.sh/nodepool": "general", "topology.kubernetes.io/zone": "us-east-1a"}},
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

    async def get_cluster_events(self, object_name: str) -> list[dict[str, Any]]:
        return [
            {
                "reason": "DisruptionBlocked",
                "message": "NodeClaim is not blocked from disruption; no consolidation action was active during the incident.",
                "type": "Normal",
                "eventTime": "2026-09-24T20:40:00Z",
                "involvedObject": {"name": object_name, "kind": "NodeClaim"},
            }
        ]

    async def get_replicaset(self, namespace: str, name: str) -> dict[str, Any]:
        return {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "ownerReferences": [{"kind": "Deployment", "name": "landing-page"}],
            }
        }

    async def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {"app.kubernetes.io/instance": "landing-page"},
                "annotations": {"argocd.argoproj.io/tracking-id": "landing-page:apps/Deployment:exchange/landing-page"},
            },
            "status": {"replicas": 6, "availableReplicas": 5},
        }

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        return [
            {"metadata": {"namespace": "exchange", "name": "landing-page-7d8b6d9b-x9k2p"}, "spec": {"nodeName": node}},
            {"metadata": {"namespace": "payments", "name": "payments-api-6bb86f8c8d-2m4zx"}, "spec": {"nodeName": node}},
            {"metadata": {"namespace": "logging", "name": "fluent-bit-zm44q"}, "spec": {"nodeName": node}},
        ]

    async def get_hpa_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        return {}

    async def get_scaledobject_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        return {}

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

    async def service_graph(self, service: str, at: datetime) -> list[dict[str, Any]]:
        return [
            {"client": "web-gateway", "server": service, "requests": 22.0, "failures": 1.0, "error_ratio": 1 / 22, "p95_seconds": 0.18, "connection_type": "unset"},
            {"client": service, "server": "catalog-api", "requests": 16.0, "failures": 2.0, "error_ratio": 0.125, "p95_seconds": 0.42, "connection_type": "unset"},
            {"client": service, "server": "redis", "requests": 9.0, "failures": 0.0, "error_ratio": 0.0, "p95_seconds": 0.03, "connection_type": "database"},
        ]

    async def tempo_search_traces(self, service: str, start: datetime, end: datetime, limit: int = 8) -> list[dict[str, Any]]:
        return []

    async def tempo_get_trace(self, trace_id: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        return {}

    async def tempo_metrics_query(self, query: str, start: datetime, end: datetime, step: str = "30s") -> dict[str, Any]:
        return {}

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
            "spec": {"source": {"repoURL": "https://github.com/example/landing-page.git", "path": "deploy/production"}},
            "status": {
                "sync": {"status": "Synced", "revision": "7d37e39"},
                "health": {"status": "Healthy"},
                "operationState": {"finishedAt": "2026-09-24T17:12:00Z", "phase": "Succeeded"},
                "history": [{"id": 42, "revision": "7d37e39", "deployedAt": "2026-09-24T17:12:00Z"}],
            },
        }

    async def github_commit(self, repo_url: str, revision: str) -> dict[str, Any]:
        return {
            "sha": revision,
            "html_url": f"{repo_url.removesuffix('.git')}/commit/{revision}",
            "commit": {
                "message": "chore: update homepage copy",
                "author": {"name": "demo-engineer", "date": "2026-09-24T17:05:00Z"},
            },
        }

    async def karpenter_nodeclaim_for_node(self, node: str) -> dict[str, Any]:
        return {
            "apiVersion": "karpenter.sh/v1",
            "kind": "NodeClaim",
            "metadata": {
                "name": "general-f8x9m",
                "creationTimestamp": "2026-09-24T11:20:00Z",
                "labels": {"karpenter.sh/nodepool": "general", "karpenter.sh/capacity-type": "on-demand"},
                "ownerReferences": [{"kind": "NodePool", "name": "general"}],
            },
            "status": {
                "nodeName": node,
                "providerID": "aws:///us-east-1a/i-0123456789abcdef0",
                "conditions": [
                    {"type": "Ready", "status": "True", "reason": "Ready", "lastTransitionTime": "2026-09-24T11:21:20Z"},
                    {"type": "Consolidatable", "status": "False", "reason": "PodDisruptionBudget", "lastTransitionTime": "2026-09-24T20:30:00Z"},
                ],
            },
        }



def _demo_b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _demo_trace_payload(seed: int) -> dict[str, Any]:
    trace_id = _demo_b64(bytes([seed]) * 16)
    root_id = _demo_b64(bytes([seed, 1, 1, 1, 1, 1, 1, 1]))
    checkout_id = _demo_b64(bytes([seed, 2, 2, 2, 2, 2, 2, 2]))
    db_id = _demo_b64(bytes([seed, 3, 3, 3, 3, 3, 3, 3]))
    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "frontend"}}]},
                    "scopeSpans": [{"spans": [{
                        "traceId": trace_id,
                        "spanId": root_id,
                        "name": "POST /checkout",
                        "kind": "SPAN_KIND_SERVER",
                        "startTimeUnixNano": "1000000000",
                        "endTimeUnixNano": "1900000000",
                        "status": {"code": "STATUS_CODE_ERROR"},
                        "attributes": [{"key": "http.response.status_code", "value": {"intValue": "500"}}],
                    }]}],
                },
                {
                    "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "checkout"}}]},
                    "scopeSpans": [{"spans": [
                        {
                            "traceId": trace_id,
                            "spanId": checkout_id,
                            "parentSpanId": root_id,
                            "name": "CheckoutService.PlaceOrder",
                            "kind": "SPAN_KIND_SERVER",
                            "startTimeUnixNano": "1050000000",
                            "endTimeUnixNano": "1850000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                        },
                        {
                            "traceId": trace_id,
                            "spanId": db_id,
                            "parentSpanId": checkout_id,
                            "name": "SELECT orders",
                            "kind": "SPAN_KIND_CLIENT",
                            "startTimeUnixNano": "1100000000",
                            "endTimeUnixNano": "1800000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                            "attributes": [
                                {"key": "db.system", "value": {"stringValue": "postgresql"}},
                                {"key": "db.namespace", "value": {"stringValue": "orders"}},
                            ],
                        },
                    ]}],
                },
            ]
        }
    }


class TraceDemoTools(DemoTools):
    async def tempo_search_traces(self, service: str, start: datetime, end: datetime, limit: int = 8) -> list[dict[str, Any]]:
        return [
            {
                "traceID": f"trace-{index}",
                "rootServiceName": "frontend",
                "rootTraceName": "POST /checkout",
                "_source_query": '{ resource.service.name = "frontend" && status = error }',
            }
            for index in range(1, min(limit, 2) + 1)
        ]

    async def tempo_get_trace(self, trace_id: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        seed = int(trace_id.rsplit("-", 1)[1])
        return _demo_trace_payload(seed)

    async def tempo_metrics_query(self, query: str, start: datetime, end: datetime, step: str = "30s") -> dict[str, Any]:
        if "status = error" in query:
            value = 1.5
        elif "quantile_over_time" in query:
            value = 0.9
        else:
            value = 12.0
        return {"series": [{"values": [[start.timestamp(), value], [end.timestamp(), value]]}]}
