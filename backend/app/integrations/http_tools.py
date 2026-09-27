from __future__ import annotations

import asyncio
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.core.query_safety import path_segment, prom_regex_literal as escape_prom_regex_literal, quoted_string


class ReadOnlyHTTPTools:
    """Narrow read-only clients used by the investigator.

    This class intentionally exposes only GET-style infrastructure operations.
    There is no generic shell, kubectl exec, patch, update or delete primitive.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _k8s_headers(self) -> dict[str, str]:
        token_path = Path(self.settings.kubernetes_token_file)
        if not token_path.exists():
            return {}
        return {"Authorization": f"Bearer {token_path.read_text().strip()}"}

    def _k8s_verify(self) -> str | bool:
        ca = Path(self.settings.kubernetes_ca_file)
        return str(ca) if ca.exists() else True

    async def _k8s_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.kubernetes_api_url,
            headers=self._k8s_headers(),
            verify=self._k8s_verify(),
            timeout=10,
        ) as client:
            response = await client.get(path, params=params)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def get_pod(self, namespace: str, pod: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/namespaces/{path_segment(namespace)}/pods/{path_segment(pod)}")

    async def get_node(self, node: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/nodes/{path_segment(node)}")

    async def get_events(self, namespace: str, object_name: str) -> list[dict[str, Any]]:
        data = await self._k8s_get(
            f"/api/v1/namespaces/{path_segment(namespace)}/events",
            {"fieldSelector": f"involvedObject.name={object_name}"},
        )
        return data.get("items", [])

    async def get_cluster_events(self, object_name: str) -> list[dict[str, Any]]:
        data = await self._k8s_get(
            "/api/v1/events",
            {"fieldSelector": f"involvedObject.name={object_name}"},
        )
        return data.get("items", [])

    async def get_replicaset(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{path_segment(namespace)}/replicasets/{path_segment(name)}")

    async def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{path_segment(namespace)}/deployments/{path_segment(name)}")

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        data = await self._k8s_get("/api/v1/pods", {"fieldSelector": f"spec.nodeName={node}"})
        return data.get("items", [])

    async def get_hpa_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/autoscaling/v2/namespaces/{path_segment(namespace)}/horizontalpodautoscalers"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            if target.get("kind", "").lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def get_scaledobject_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/keda.sh/v1alpha1/namespaces/{path_segment(namespace)}/scaledobjects"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            target_kind = target.get("kind") or "Deployment"
            if str(target_kind).lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def prometheus_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query}
        if at:
            params["time"] = at.isoformat()
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=10) as client:
            response = await client.get("/api/v1/query", params=params)
            response.raise_for_status()
            return response.json()

    async def prometheus_query_range(self, query: str, start: datetime, end: datetime, step: str = "60s") -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=15) as client:
            response = await client.get(
                "/api/v1/query_range",
                params={"query": query, "start": start.isoformat(), "end": end.isoformat(), "step": step},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _prometheus_scalar(payload: dict[str, Any]) -> float | None:
        try:
            value = float(payload["data"]["result"][0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @staticmethod
    def _prom_label(value: str) -> str:
        return quoted_string(value)

    @staticmethod
    def _prom_regex_literal(value: str) -> str:
        return escape_prom_regex_literal(value)

    @classmethod
    def _prom_instance_matcher(cls, value: str) -> str:
        raw = str(value).strip()
        explicit_port = raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit()
        safe = cls._prom_regex_literal(raw)
        suffix = "" if explicit_port else "(:[0-9]+)?"
        return f'instance=~"^{safe}{suffix}$"'

    async def _named_prometheus_scalars(
        self,
        queries: dict[str, str],
        at: datetime,
    ) -> dict[str, Any]:
        names = list(queries)
        payloads = await asyncio.gather(
            *[self.prometheus_query(queries[name], at) for name in names],
            return_exceptions=True,
        )
        output: dict[str, Any] = {}
        for name, payload in zip(names, payloads):
            query = queries[name]
            if isinstance(payload, Exception):
                output[name] = {"value": None, "query": query, "error": str(payload)}
                continue
            output[name] = {
                "value": self._prometheus_scalar(payload),
                "query": query,
            }
        return output

    async def postgres_dependency_metrics(
        self,
        database: str,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ) -> dict[str, Any]:
        signal_names = (
            "connections",
            "database_connections",
            "active_connections",
            "idle_in_transaction",
            "max_connections",
            "locks",
            "access_exclusive_locks",
            "deadlocks_delta",
            "replication_lag_bytes",
            "long_tx_seconds",
        )
        if not instance_hint:
            return {
                "kind": "postgresql",
                "database": database,
                "instance_hint": None,
                "window_minutes": window_minutes,
                "signals": {
                    name: {
                        "value": None,
                        "query": None,
                        "error": "instance identity required to scope PostgreSQL telemetry",
                    }
                    for name in signal_names
                },
            }

        db = self._prom_label(database)
        instance_label_matcher = self._prom_instance_matcher(instance_hint)
        instance_matcher = f",{instance_label_matcher}"
        instance_selector = f"{{{instance_label_matcher}}}"
        db_selector = f'{{datname="{db}"{instance_matcher}}}'

        queries = {
            "connections": f"sum(pg_stat_activity_count{instance_selector})",
            "database_connections": f"sum(pg_stat_activity_count{db_selector})",
            "active_connections": f'sum(pg_stat_activity_count{{datname="{db}",state="active"{instance_matcher}}})',
            "idle_in_transaction": f'sum(pg_stat_activity_count{{datname="{db}",state=~"idle in transaction.*"{instance_matcher}}})',
            "max_connections": f"max(pg_settings_max_connections{instance_selector})",
            "locks": f"sum(pg_locks_count{db_selector})",
            "access_exclusive_locks": f'sum(pg_locks_count{{datname="{db}",mode="accessexclusivelock"{instance_matcher}}})',
            "deadlocks_delta": f"sum(increase(pg_stat_database_deadlocks{db_selector}[{window_minutes}m]))",
            "replication_lag_bytes": f"max(pg_stat_replication_pg_wal_lsn_diff{instance_selector})",
            "long_tx_seconds": f"max(pg_stat_activity_max_tx_duration{db_selector})",
        }
        result = await self._named_prometheus_scalars(queries, at)
        return {
            "kind": "postgresql",
            "database": database,
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": result,
        }

    async def kafka_dependency_metrics(
        self,
        topic: str | None,
        consumer_group: str | None,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ) -> dict[str, Any]:
        signal_names = (
            "consumer_lag",
            "under_replicated_partitions",
            "offline_partitions",
            "consumer_group_members",
        )
        if not topic or not instance_hint:
            missing = (
                "topic identity required to scope Kafka telemetry"
                if not topic
                else "instance identity required to scope Kafka telemetry"
            )
            return {
                "kind": "kafka",
                "topic": topic,
                "consumer_group": consumer_group,
                "instance_hint": instance_hint,
                "window_minutes": window_minutes,
                "signals": {
                    name: {"value": None, "query": None, "error": missing}
                    for name in signal_names
                },
            }

        topic_label = self._prom_label(topic)
        instance_label_matcher = self._prom_instance_matcher(instance_hint)
        instance_matcher = f",{instance_label_matcher}"
        topic_selector = f'{{topic="{topic_label}"{instance_matcher}}}'
        queries: dict[str, str] = {
            "under_replicated_partitions": (
                f"sum(kafka_topic_partition_under_replicated_partition{topic_selector})"
            ),
            "offline_partitions": (
                f"sum(kafka_topic_partition_leader{topic_selector} < bool 0)"
            ),
        }

        if consumer_group:
            group = self._prom_label(consumer_group)
            group_selector = (
                f'{{topic="{topic_label}",consumergroup="{group}"{instance_matcher}}}'
            )
            member_selector = f'{{consumergroup="{group}"{instance_matcher}}}'
            queries["consumer_lag"] = f"sum(kafka_consumergroup_lag{group_selector})"
            queries["consumer_group_members"] = (
                f"min(kafka_consumergroup_members{member_selector})"
            )

        result = await self._named_prometheus_scalars(queries, at)
        if not consumer_group:
            for name in ("consumer_lag", "consumer_group_members"):
                result[name] = {
                    "value": None,
                    "query": None,
                    "error": "consumer group identity required for consumer-specific telemetry",
                }

        return {
            "kind": "kafka",
            "topic": topic,
            "consumer_group": consumer_group,
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": result,
        }

    async def redis_dependency_metrics(
        self,
        instance_hint: str | None,
        at: datetime,
        window_minutes: int = 15,
    ) -> dict[str, Any]:
        signal_names = (
            "memory_used_bytes",
            "memory_max_bytes",
            "config_maxmemory_bytes",
            "evictions_delta",
            "blocked_clients",
            "connected_clients",
            "max_clients",
            "command_latency_seconds",
        )
        if not instance_hint:
            return {
                "kind": "redis",
                "instance_hint": None,
                "window_minutes": window_minutes,
                "signals": {
                    name: {
                        "value": None,
                        "query": None,
                        "error": "instance identity required to scope Redis telemetry",
                    }
                    for name in signal_names
                },
            }

        selector = f"{{{self._prom_instance_matcher(instance_hint)}}}"
        queries = {
            "memory_used_bytes": f"max(redis_memory_used_bytes{selector})",
            "memory_max_bytes": f"max(redis_memory_max_bytes{selector})",
            "config_maxmemory_bytes": f"max(redis_config_maxmemory{selector})",
            "evictions_delta": f"sum(increase(redis_evicted_keys_total{selector}[{window_minutes}m]))",
            "blocked_clients": f"max(redis_blocked_clients{selector})",
            "connected_clients": f"max(redis_connected_clients{selector})",
            "max_clients": f"max(redis_config_maxclients{selector})",
            "command_latency_seconds": (
                f"max(rate(redis_commands_duration_seconds_total{selector}[5m]) "
                f"/ clamp_min(rate(redis_commands_total{selector}[5m]), 0.001))"
            ),
        }
        result = await self._named_prometheus_scalars(queries, at)
        return {
            "kind": "redis",
            "instance_hint": instance_hint,
            "window_minutes": window_minutes,
            "signals": result,
        }

    async def service_graph(self, service: str, at: datetime) -> list[dict[str, Any]]:
        window = self.settings.service_graph_window_minutes
        escaped = self._prom_label(service)
        selector_server = f'{{server="{escaped}"}}'
        selector_client = f'{{client="{escaped}"}}'
        grouping = "by (client, server, connection_type)"

        request_query = (
            f'sum(increase(traces_service_graph_request_total{selector_server}[{window}m])) {grouping} > 0 '
            f'or sum(increase(traces_service_graph_request_total{selector_client}[{window}m])) {grouping} > 0'
        )
        failed_query = (
            f'sum(increase(traces_service_graph_request_failed_total{selector_server}[{window}m])) {grouping} '
            f'or sum(increase(traces_service_graph_request_failed_total{selector_client}[{window}m])) {grouping}'
        )
        latency_query = (
            f'histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{selector_server}[5m])) '
            f'by (client, server, connection_type, le)) '
            f'or histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{selector_client}[5m])) '
            f'by (client, server, connection_type, le))'
        )
        request_payload, failed_payload, latency_payload = await asyncio.gather(
            self.prometheus_query(request_query, at),
            self.prometheus_query(failed_query, at),
            self.prometheus_query(latency_query, at),
        )

        merged: dict[tuple[str, str, str], dict[str, Any]] = {}

        def merge_payload(payload: dict[str, Any], field: str) -> None:
            for row in payload.get("data", {}).get("result", []):
                metric = row.get("metric", {})
                client = metric.get("client")
                server = metric.get("server")
                if not client or not server:
                    continue
                connection_type = metric.get("connection_type", "unset")
                key = (str(client), str(server), str(connection_type))
                try:
                    value = float(row.get("value", [0, "0"])[1])
                except (TypeError, ValueError, IndexError):
                    value = 0.0
                merged.setdefault(
                    key,
                    {
                        "client": str(client),
                        "server": str(server),
                        "connection_type": str(connection_type),
                        "requests": 0.0,
                        "failures": 0.0,
                        "p95_seconds": None,
                    },
                )[field] = value

        merge_payload(request_payload, "requests")
        merge_payload(failed_payload, "failures")
        merge_payload(latency_payload, "p95_seconds")

        edges: list[dict[str, Any]] = []
        for item in merged.values():
            requests = float(item.get("requests") or 0.0)
            failures = float(item.get("failures") or 0.0)
            item["error_ratio"] = failures / requests if requests > 0 else 0.0
            item["query"] = request_query
            item["red_queries"] = {
                "requests": request_query,
                "failures": failed_query,
                "p95": latency_query,
            }
            edges.append(item)
        return edges

    def _tempo_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.settings.tempo_tenant_id:
            headers["X-Scope-OrgID"] = self.settings.tempo_tenant_id
        return headers

    async def tempo_search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        escaped = quoted_string(service)
        queries = [
            f'{{ resource.service.name = "{escaped}" && status = error }}',
            f'{{ resource.service.name = "{escaped}" }}',
        ]
        traces: list[dict[str, Any]] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(
            base_url=self.settings.tempo_url,
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            for query in queries:
                if len(traces) >= limit:
                    break
                response = await client.get(
                    "/api/search",
                    params={
                        "q": query,
                        "start": int(start.timestamp()),
                        "end": int(end.timestamp()),
                        "limit": max(1, limit - len(traces)),
                        "spss": 5,
                    },
                )
                if response.status_code in {404, 501}:
                    return traces
                response.raise_for_status()
                for item in response.json().get("traces", []):
                    trace_id = str(item.get("traceID") or "")
                    if not trace_id or trace_id in seen:
                        continue
                    seen.add(trace_id)
                    copy = dict(item)
                    copy["_source_query"] = query
                    traces.append(copy)
                    if len(traces) >= limit:
                        break
        return traces

    async def tempo_get_trace(
        self,
        trace_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if start:
            params["start"] = int(start.timestamp())
        if end:
            params["end"] = int(end.timestamp())
        async with httpx.AsyncClient(
            base_url=self.settings.tempo_url,
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            response = await client.get(f"/api/v2/traces/{path_segment(trace_id)}", params=params)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def tempo_metrics_query(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.tempo_url,
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            response = await client.get(
                "/api/metrics/query_range",
                params={
                    "q": query,
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                    "step": step,
                },
            )
            if response.status_code in {404, 501}:
                return {}
            response.raise_for_status()
            return response.json()

    async def loki_query_range(self, query: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.loki_url, timeout=15) as client:
            response = await client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(int(start.timestamp() * 1_000_000_000)),
                    "end": str(int(end.timestamp() * 1_000_000_000)),
                    "limit": limit,
                    "direction": "backward",
                },
            )
            response.raise_for_status()
            return response.json()

    async def argocd_application(self, app_name: str) -> dict[str, Any]:
        headers = {}
        if self.settings.argocd_token:
            headers["Authorization"] = f"Bearer {self.settings.argocd_token}"
        async with httpx.AsyncClient(base_url=self.settings.argocd_url, headers=headers, timeout=10) as client:
            response = await client.get(f"/api/v1/applications/{path_segment(app_name)}")
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def github_commit(self, repo_url: str, revision: str) -> dict[str, Any]:
        parsed = urlparse(repo_url)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            return {}
        parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
        if len(parts) < 2:
            return {}
        owner, repo = parts[0], parts[1]
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        async with httpx.AsyncClient(base_url=self.settings.github_api_url, headers=headers, timeout=10) as client:
            response = await client.get(f"/repos/{path_segment(owner)}/{path_segment(repo)}/commits/{path_segment(revision)}")
            if response.status_code in {401, 403, 404}:
                return {}
            response.raise_for_status()
            return response.json()

    async def karpenter_nodeclaim_for_node(self, node: str) -> dict[str, Any]:
        data = await self._k8s_get("/apis/karpenter.sh/v1/nodeclaims")
        for item in data.get("items", []):
            if item.get("status", {}).get("nodeName") == node:
                return item
        return {}
