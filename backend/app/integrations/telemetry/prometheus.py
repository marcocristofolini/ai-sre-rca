from __future__ import annotations

import asyncio
import math
from datetime import datetime
from typing import Any

from app.core.query_safety import prom_regex_literal, quoted_string
from app.integrations.telemetry.types import (
    ContainerMemoryWorkingSet,
    DependencyRequest,
    KafkaDependency,
    LogRequest,
    LogResult,
    MetricRequest,
    MetricResult,
    NodeFilesystemAvailable,
    PodLogs,
    PostgresDependency,
    Provenance,
    RedisDependency,
    ServiceGraph,
    UnsupportedSignal,
)

_INSTANT_REF = "prometheus:/api/v1/query"
_RANGE_REF = "prometheus:/api/v1/query_range"
_LOKI_REF = "loki:/loki/api/v1/query_range"


def _scalar(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _series(payload: dict[str, Any]) -> tuple[float, ...]:
    try:
        values = payload["data"]["result"][0]["values"]
    except (KeyError, IndexError, TypeError):
        return ()
    output: list[float] = []
    for value in values:
        try:
            output.append(float(value[1]))
        except (IndexError, TypeError, ValueError):
            continue
    return tuple(output)


def instance_matcher(value: str) -> str:
    raw = str(value).strip()
    explicit_port = raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit()
    safe = prom_regex_literal(raw)
    suffix = "" if explicit_port else "(:[0-9]+)?"
    return f'instance=~"^{safe}{suffix}$"'


def finite_scalar(payload: dict[str, Any]) -> float | None:
    try:
        value = float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


_POSTGRES_SIGNALS = (
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
_KAFKA_SIGNALS = (
    "consumer_lag",
    "under_replicated_partitions",
    "offline_partitions",
    "consumer_group_members",
)
_REDIS_SIGNALS = (
    "memory_used_bytes",
    "memory_max_bytes",
    "config_maxmemory_bytes",
    "evictions_delta",
    "blocked_clients",
    "connected_clients",
    "max_clients",
    "command_latency_seconds",
)


def _postgres_queries(request: PostgresDependency) -> dict[str, str]:
    db = quoted_string(request.database)
    window_minutes = request.window_minutes
    instance_label_matcher = instance_matcher(request.instance_hint or "")
    instance_matcher_clause = f",{instance_label_matcher}"
    instance_selector = f"{{{instance_label_matcher}}}"
    db_selector = f'{{datname="{db}"{instance_matcher_clause}}}'
    return {
        "connections": f"sum(pg_stat_activity_count{instance_selector})",
        "database_connections": f"sum(pg_stat_activity_count{db_selector})",
        "active_connections": f'sum(pg_stat_activity_count{{datname="{db}",state="active"{instance_matcher_clause}}})',
        "idle_in_transaction": f'sum(pg_stat_activity_count{{datname="{db}",state=~"idle in transaction.*"{instance_matcher_clause}}})',
        "max_connections": f"max(pg_settings_max_connections{instance_selector})",
        "locks": f"sum(pg_locks_count{db_selector})",
        "access_exclusive_locks": f'sum(pg_locks_count{{datname="{db}",mode="accessexclusivelock"{instance_matcher_clause}}})',
        "deadlocks_delta": f"sum(increase(pg_stat_database_deadlocks{db_selector}[{window_minutes}m]))",
        "replication_lag_bytes": f"max(pg_stat_replication_pg_wal_lsn_diff{instance_selector})",
        "long_tx_seconds": f"max(pg_stat_activity_max_tx_duration{db_selector})",
    }


def _kafka_queries(request: KafkaDependency) -> dict[str, str]:
    topic_label = quoted_string(request.topic or "")
    instance_matcher_clause = f",{instance_matcher(request.instance_hint or '')}"
    topic_selector = f'{{topic="{topic_label}"{instance_matcher_clause}}}'
    queries: dict[str, str] = {
        "under_replicated_partitions": (
            f"sum(kafka_topic_partition_under_replicated_partition{topic_selector})"
        ),
        "offline_partitions": (
            f"sum(kafka_topic_partition_leader{topic_selector} < bool 0)"
        ),
    }
    if request.consumer_group:
        group = quoted_string(request.consumer_group)
        group_selector = (
            f'{{topic="{topic_label}",consumergroup="{group}"{instance_matcher_clause}}}'
        )
        member_selector = f'{{consumergroup="{group}"{instance_matcher_clause}}}'
        queries["consumer_lag"] = f"sum(kafka_consumergroup_lag{group_selector})"
        queries["consumer_group_members"] = (
            f"min(kafka_consumergroup_members{member_selector})"
        )
    return queries


def _redis_queries(request: RedisDependency) -> dict[str, str]:
    window_minutes = request.window_minutes
    selector = f"{{{instance_matcher(request.instance_hint or '')}}}"
    return {
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


def _service_graph_queries(request: ServiceGraph) -> dict[str, str]:
    window = request.window_minutes
    escaped = quoted_string(request.service)
    selector_server = f'{{server="{escaped}"}}'
    selector_client = f'{{client="{escaped}"}}'
    grouping = "by (client, server, connection_type)"
    return {
        "requests": (
            f'sum(increase(traces_service_graph_request_total{selector_server}[{window}m])) {grouping} > 0 '
            f'or sum(increase(traces_service_graph_request_total{selector_client}[{window}m])) {grouping} > 0'
        ),
        "failures": (
            f'sum(increase(traces_service_graph_request_failed_total{selector_server}[{window}m])) {grouping} '
            f'or sum(increase(traces_service_graph_request_failed_total{selector_client}[{window}m])) {grouping}'
        ),
        "p95": (
            f'histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{selector_server}[5m])) '
            f'by (client, server, connection_type, le)) '
            f'or histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{selector_client}[5m])) '
            f'by (client, server, connection_type, le))'
        ),
    }


class PrometheusMetricsSource:
    """Translates catalogued signals to PromQL and runs them through existing tools.

    Wraps an ``InvestigationTools`` object, so the existing budget accounting
    and test doubles keep working unchanged.
    """

    source = "prometheus"

    def __init__(self, tools: Any):
        self._tools = tools

    def supports(self, request: Any) -> bool:
        return isinstance(
            request,
            (
                NodeFilesystemAvailable,
                ContainerMemoryWorkingSet,
                PostgresDependency,
                KafkaDependency,
                RedisDependency,
                ServiceGraph,
            ),
        )

    def native_query(self, request: Any) -> str:
        if isinstance(request, NodeFilesystemAvailable):
            node = prom_regex_literal(request.node)
            return f'node_filesystem_avail_bytes{{instance=~"^{node}(:[0-9]+)?$",mountpoint="/"}}'
        if isinstance(request, ContainerMemoryWorkingSet):
            namespace = quoted_string(request.namespace)
            pod = quoted_string(request.pod)
            return (
                f'sum(container_memory_working_set_bytes{{namespace="{namespace}",pod="{pod}",'
                'container!="",image!=""})'
            )
        if isinstance(request, PostgresDependency):
            return "; ".join(_postgres_queries(request).values())
        if isinstance(request, KafkaDependency):
            return "; ".join(_kafka_queries(request).values())
        if isinstance(request, RedisDependency):
            return "; ".join(_redis_queries(request).values())
        if isinstance(request, ServiceGraph):
            return "; ".join(_service_graph_queries(request).values())
        raise UnsupportedSignal(type(request).__name__)

    async def instant(self, request: MetricRequest, at: datetime | None = None) -> MetricResult:
        query = self.native_query(request)
        payload = await self._tools.prometheus_query(query, at)
        return MetricResult(
            provenance=Provenance(self.source, query, _INSTANT_REF),
            value=_scalar(payload),
            budget_denied=bool(payload.get("_budget_denied")),
            raw=payload,
        )

    async def range(self, request: MetricRequest, start: datetime, end: datetime, step: str = "60s") -> MetricResult:
        query = self.native_query(request)
        payload = await self._tools.prometheus_query_range(query, start, end, step)
        return MetricResult(
            provenance=Provenance(self.source, query, _RANGE_REF),
            values=_series(payload),
            budget_denied=bool(payload.get("_budget_denied")),
            raw=payload,
        )


    async def _named_scalars(self, queries: dict[str, str], at: datetime) -> dict[str, Any]:
        names = list(queries)
        payloads = await asyncio.gather(
            *[self._tools.prometheus_query(queries[name], at) for name in names],
            return_exceptions=True,
        )
        output: dict[str, Any] = {}
        for name, payload in zip(names, payloads):
            query = queries[name]
            if isinstance(payload, Exception):
                output[name] = {"value": None, "query": query, "error": str(payload)}
                continue
            output[name] = {"value": finite_scalar(payload), "query": query}
        return output

    @staticmethod
    def _missing_identity(names: tuple[str, ...], error: str) -> dict[str, Any]:
        return {name: {"value": None, "query": None, "error": error} for name in names}

    async def dependency(self, request: DependencyRequest, at: datetime) -> dict[str, Any]:
        """Run a stateful-dependency signal set; returns the shape stateful_dependency consumes."""
        if isinstance(request, PostgresDependency):
            base = {
                "kind": "postgresql",
                "database": request.database,
                "instance_hint": request.instance_hint or None,
                "window_minutes": request.window_minutes,
            }
            if not request.instance_hint:
                base["signals"] = self._missing_identity(
                    _POSTGRES_SIGNALS, "instance identity required to scope PostgreSQL telemetry"
                )
                return base
            base["signals"] = await self._named_scalars(_postgres_queries(request), at)
            return base
        if isinstance(request, KafkaDependency):
            base = {
                "kind": "kafka",
                "topic": request.topic,
                "consumer_group": request.consumer_group,
                "instance_hint": request.instance_hint,
                "window_minutes": request.window_minutes,
            }
            if not request.topic or not request.instance_hint:
                missing = (
                    "topic identity required to scope Kafka telemetry"
                    if not request.topic
                    else "instance identity required to scope Kafka telemetry"
                )
                base["signals"] = self._missing_identity(_KAFKA_SIGNALS, missing)
                return base
            result = await self._named_scalars(_kafka_queries(request), at)
            if not request.consumer_group:
                for name in ("consumer_lag", "consumer_group_members"):
                    result[name] = {
                        "value": None,
                        "query": None,
                        "error": "consumer group identity required for consumer-specific telemetry",
                    }
            base["signals"] = result
            return base
        if isinstance(request, RedisDependency):
            base = {
                "kind": "redis",
                "instance_hint": request.instance_hint or None,
                "window_minutes": request.window_minutes,
            }
            if not request.instance_hint:
                base["signals"] = self._missing_identity(
                    _REDIS_SIGNALS, "instance identity required to scope Redis telemetry"
                )
                return base
            base["signals"] = await self._named_scalars(_redis_queries(request), at)
            return base
        raise UnsupportedSignal(type(request).__name__)

    async def service_graph(self, request: ServiceGraph, at: datetime) -> list[dict[str, Any]]:
        queries = _service_graph_queries(request)
        request_payload, failed_payload, latency_payload = await asyncio.gather(
            self._tools.prometheus_query(queries["requests"], at),
            self._tools.prometheus_query(queries["failures"], at),
            self._tools.prometheus_query(queries["p95"], at),
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
            item["query"] = queries["requests"]
            item["red_queries"] = {
                "requests": queries["requests"],
                "failures": queries["failures"],
                "p95": queries["p95"],
            }
            edges.append(item)
        return edges


class LokiLogsSource:
    source = "loki"

    def __init__(self, tools: Any):
        self._tools = tools

    def supports(self, request: LogRequest) -> bool:
        return isinstance(request, PodLogs)

    def native_query(self, request: LogRequest) -> str:
        if isinstance(request, PodLogs):
            return f'{{namespace="{quoted_string(request.namespace)}", pod="{quoted_string(request.pod)}"}}'
        raise UnsupportedSignal(type(request).__name__)

    async def query(self, request: LogRequest, start: datetime, end: datetime) -> LogResult:
        query = self.native_query(request)
        payload = await self._tools.loki_query_range(query, start, end, request.limit)
        streams = payload.get("data", {}).get("result", [])
        return LogResult(
            provenance=Provenance(self.source, query, _LOKI_REF),
            entries=tuple(v[1] for s in streams for v in s.get("values", []) if len(v) > 1),
            entry_count=sum(len(s.get("values", [])) for s in streams),
            budget_denied=bool(payload.get("_budget_denied")),
            raw=payload,
        )
