from __future__ import annotations

import base64
from datetime import datetime
from typing import Any


class DemoTools:
    async def capability_report(self, cluster: str) -> list[dict[str, Any]]:
        return [
            {
                "source": source,
                "status": "healthy",
                "detail": "demo fixture",
                "cluster": cluster,
                "latency_ms": 0.0,
            }
            for source in ("kubernetes", "prometheus", "loki", "tempo", "argocd", "github")
        ]

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

    async def postgres_dependency_metrics(self, database: str, at: datetime, window_minutes: int = 15, instance_hint: str | None = None) -> dict[str, Any]:
        return {
            "kind": "postgresql",
            "database": database,
            "window_minutes": window_minutes,
            "signals": {},
        }

    async def kafka_dependency_metrics(
        self,
        topic: str | None,
        consumer_group: str | None,
        at: datetime,
        window_minutes: int = 15,
    ) -> dict[str, Any]:
        return {
            "kind": "kafka",
            "topic": topic,
            "consumer_group": consumer_group,
            "window_minutes": window_minutes,
            "signals": {},
        }

    async def redis_dependency_metrics(self, instance_hint: str | None, at: datetime, window_minutes: int = 15) -> dict[str, Any]:
        return {
            "kind": "redis",
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": {},
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


def _demo_trace_payload(seed: int, dependency: str = "postgresql") -> dict[str, Any]:
    trace_id = _demo_b64(bytes([seed]) * 16)
    root_id = _demo_b64(bytes([seed, 1, 1, 1, 1, 1, 1, 1]))
    checkout_id = _demo_b64(bytes([seed, 2, 2, 2, 2, 2, 2, 2]))
    db_id = _demo_b64(bytes([seed, 3, 3, 3, 3, 3, 3, 3]))

    if dependency == "postgresql":
        dependency_operation = "SELECT orders"
        dependency_kind = "SPAN_KIND_CLIENT"
        dependency_attributes = [
            {"key": "db.system", "value": {"stringValue": "postgresql"}},
            {"key": "db.namespace", "value": {"stringValue": "orders"}},
            {"key": "server.address", "value": {"stringValue": "orders-db"}},
        ]
    elif dependency == "kafka":
        dependency_operation = "publish orders-events"
        dependency_kind = "SPAN_KIND_PRODUCER"
        dependency_attributes = [
            {"key": "messaging.system", "value": {"stringValue": "kafka"}},
            {"key": "messaging.destination.name", "value": {"stringValue": "orders-events"}},
            {"key": "messaging.consumer.group.name", "value": {"stringValue": "checkout-workers"}},
            {"key": "server.address", "value": {"stringValue": "kafka-prod"}},
        ]
    elif dependency == "redis":
        dependency_operation = "GET cart"
        dependency_kind = "SPAN_KIND_CLIENT"
        dependency_attributes = [
            {"key": "db.system", "value": {"stringValue": "redis"}},
            {"key": "db.namespace", "value": {"stringValue": "cache"}},
            {"key": "server.address", "value": {"stringValue": "redis-cache"}},
        ]
    else:
        raise ValueError(f"unsupported demo dependency: {dependency}")

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
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "checkout"}},
                        {"key": "service.version", "value": {"stringValue": "7d37e39"}},
                        {"key": "k8s.namespace.name", "value": {"stringValue": "checkout"}},
                        {"key": "k8s.deployment.name", "value": {"stringValue": "checkout"}},
                        {"key": "k8s.pod.name", "value": {"stringValue": "checkout-7d37e39-x2k9m"}}
                    ]},
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
                            "name": dependency_operation,
                            "kind": dependency_kind,
                            "startTimeUnixNano": "1100000000",
                            "endTimeUnixNano": "1800000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                            "attributes": dependency_attributes,
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



class StatefulDemoTools(TraceDemoTools):
    def __init__(self, dependency: str = "postgresql"):
        if dependency not in {"postgresql", "kafka", "redis"}:
            raise ValueError(f"unsupported demo dependency: {dependency}")
        self.dependency = dependency

    async def tempo_get_trace(self, trace_id: str, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        seed = int(trace_id.rsplit("-", 1)[1])
        return _demo_trace_payload(seed, self.dependency)

    async def postgres_dependency_metrics(self, database: str, at: datetime, window_minutes: int = 15, instance_hint: str | None = None) -> dict[str, Any]:
        if self.dependency != "postgresql":
            return await super().postgres_dependency_metrics(database, at, window_minutes, instance_hint)
        return {
            "kind": "postgresql",
            "database": database,
            "window_minutes": window_minutes,
            "signals": {
                "connections": {"value": 97.0, "query": 'sum(pg_stat_activity_count{instance=~".*orders-db.*"})'},
                "database_connections": {"value": 88.0, "query": 'sum(pg_stat_activity_count{datname="orders",instance=~".*orders-db.*"})'},
                "active_connections": {"value": 82.0, "query": 'sum(pg_stat_activity_count{datname="orders",state="active",instance=~".*orders-db.*"})'},
                "idle_in_transaction": {"value": 8.0, "query": 'sum(pg_stat_activity_count{datname="orders",state=~"idle in transaction.*"})'},
                "max_connections": {"value": 100.0, "query": "max(pg_settings_max_connections)"},
                "locks": {"value": 12.0, "query": 'sum(pg_locks_count{datname="orders"})'},
                "access_exclusive_locks": {"value": 0.0, "query": 'sum(pg_locks_count{datname="orders",mode="accessexclusivelock"})'},
                "deadlocks_delta": {"value": 0.0, "query": 'sum(increase(pg_stat_database_deadlocks{datname="orders"}[15m]))'},
                "replication_lag_bytes": {"value": 0.0, "query": "max(pg_stat_replication_pg_wal_lsn_diff)"},
                "long_tx_seconds": {"value": 18.0, "query": 'max(pg_stat_activity_max_tx_duration{datname="orders",instance=~".*orders-db.*"})'},
            },
        }

    async def kafka_dependency_metrics(
        self,
        topic: str | None,
        consumer_group: str | None,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ) -> dict[str, Any]:
        if self.dependency != "kafka":
            return await super().kafka_dependency_metrics(topic, consumer_group, at, window_minutes, instance_hint)
        return {
            "kind": "kafka",
            "topic": topic,
            "consumer_group": consumer_group,
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": {
                "consumer_lag": {"value": 25000.0, "query": 'sum(kafka_consumergroup_lag{topic="orders-events",consumergroup="checkout-workers",instance=~".*kafka-prod.*"})'},
                "under_replicated_partitions": {"value": 0.0, "query": 'sum(kafka_topic_partition_under_replicated_partition{topic="orders-events",instance=~".*kafka-prod.*"})'},
                "offline_partitions": {"value": 0.0, "query": 'count(kafka_topic_partition_leader{topic="orders-events",instance=~".*kafka-prod.*"} < 0)'},
                "consumer_group_members": {"value": 4.0, "query": 'min(kafka_consumergroup_members{consumergroup="checkout-workers",instance=~".*kafka-prod.*"})'},
            },
        }

    async def redis_dependency_metrics(self, instance_hint: str | None, at: datetime, window_minutes: int = 15) -> dict[str, Any]:
        if self.dependency != "redis":
            return await super().redis_dependency_metrics(instance_hint, at, window_minutes)
        mib = 1024 * 1024
        return {
            "kind": "redis",
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": {
                "memory_used_bytes": {"value": 950.0 * mib, "query": "max(redis_memory_used_bytes)"},
                "memory_max_bytes": {"value": 1000.0 * mib, "query": "max(redis_memory_max_bytes)"},
                "config_maxmemory_bytes": {"value": 1000.0 * mib, "query": "max(redis_config_maxmemory)"},
                "evictions_delta": {"value": 37.0, "query": "sum(increase(redis_evicted_keys_total[15m]))"},
                "blocked_clients": {"value": 0.0, "query": "max(redis_blocked_clients)"},
                "connected_clients": {"value": 280.0, "query": "max(redis_connected_clients)"},
                "max_clients": {"value": 1000.0, "query": "max(redis_config_maxclients)"},
                "command_latency_seconds": {"value": 0.008, "query": "redis command duration / command rate"},
            },
        }
