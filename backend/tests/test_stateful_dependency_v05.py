from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from app.core.engine import InvestigationEngine
from app.integrations.demo import DemoTools
from app.schemas import IncidentInput


START = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _attr(key: str, value: str) -> dict:
    return {"key": key, "value": {"stringValue": value}}


def dependency_trace_payload(seed: int, dependency: str) -> dict:
    trace_id = _b64(bytes([seed]) * 16)
    root_id = _b64(bytes([seed, 1, 1, 1, 1, 1, 1, 1]))
    app_id = _b64(bytes([seed, 2, 2, 2, 2, 2, 2, 2]))
    dep_id = _b64(bytes([seed, 3, 3, 3, 3, 3, 3, 3]))

    if dependency == "postgresql":
        operation = "SELECT orders"
        kind = "SPAN_KIND_CLIENT"
        attrs = [
            _attr("db.system", "postgresql"),
            _attr("db.namespace", "orders"),
            _attr("server.address", "orders-db"),
        ]
    elif dependency == "kafka":
        operation = "publish orders-events"
        kind = "SPAN_KIND_PRODUCER"
        attrs = [
            _attr("messaging.system", "kafka"),
            _attr("messaging.destination.name", "orders-events"),
            _attr("messaging.consumer.group.name", "checkout-workers"),
            _attr("server.address", "kafka-prod"),
        ]
    elif dependency == "redis":
        operation = "GET cart"
        kind = "SPAN_KIND_CLIENT"
        attrs = [
            _attr("db.system", "redis"),
            _attr("db.namespace", "cache"),
            _attr("server.address", "redis-cache"),
        ]
    else:
        raise ValueError(dependency)

    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", "frontend")]},
                    "scopeSpans": [{"spans": [{
                        "traceId": trace_id,
                        "spanId": root_id,
                        "name": "POST /checkout",
                        "kind": "SPAN_KIND_SERVER",
                        "startTimeUnixNano": "1000000000",
                        "endTimeUnixNano": "1900000000",
                        "status": {"code": "STATUS_CODE_ERROR"},
                        "attributes": [_attr("http.response.status_code", "500")],
                    }]}],
                },
                {
                    "resource": {"attributes": [
                        _attr("service.name", "checkout"),
                        _attr("service.version", "7d37e39"),
                        _attr("k8s.namespace.name", "checkout"),
                        _attr("k8s.deployment.name", "checkout"),
                    ]},
                    "scopeSpans": [{"spans": [
                        {
                            "traceId": trace_id,
                            "spanId": app_id,
                            "parentSpanId": root_id,
                            "name": "CheckoutService.PlaceOrder",
                            "kind": "SPAN_KIND_SERVER",
                            "startTimeUnixNano": "1050000000",
                            "endTimeUnixNano": "1850000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                        },
                        {
                            "traceId": trace_id,
                            "spanId": dep_id,
                            "parentSpanId": app_id,
                            "name": operation,
                            "kind": kind,
                            "startTimeUnixNano": "1100000000",
                            "endTimeUnixNano": "1800000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                            "attributes": attrs,
                        },
                    ]}],
                },
            ]
        }
    }


def _signals(values: dict[str, float | None]) -> dict:
    return {
        name: {"value": value, "query": f"demo:{name}"}
        for name, value in values.items()
    }


class StatefulTools(DemoTools):
    def __init__(self, dependency: str):
        self.dependency = dependency

    async def tempo_search_traces(self, service, start, end, limit=8):
        return [
            {
                "traceID": f"trace-{index}",
                "_source_query": f'{{ resource.service.name = "{service}" && status = error }}',
            }
            for index in (1, 2)
        ]

    async def tempo_get_trace(self, trace_id, start=None, end=None):
        seed = int(trace_id.rsplit("-", 1)[1])
        return dependency_trace_payload(seed, self.dependency)

    async def tempo_metrics_query(self, query, start, end, step="30s"):
        value = 1.0 if "status = error" in query else 8.0
        return {"series": [{"values": [[start.timestamp(), value], [end.timestamp(), value]]}]}

    async def postgres_dependency_metrics(self, database, at, window_minutes=15, instance_hint=None):
        if self.dependency != "postgresql":
            return await super().postgres_dependency_metrics(database, at, window_minutes, instance_hint)
        return {
            "kind": "postgresql",
            "database": database,
            "instance_hint": instance_hint,
            "signals": _signals({
                "connections": 97,
                "database_connections": 88,
                "active_connections": 82,
                "idle_in_transaction": 8,
                "max_connections": 100,
                "locks": 12,
                "access_exclusive_locks": 0,
                "deadlocks_delta": 0,
                "replication_lag_bytes": 0,
                "long_tx_seconds": 18,
            }),
        }

    async def kafka_dependency_metrics(self, topic, consumer_group, at, window_minutes=15, instance_hint=None):
        if self.dependency != "kafka":
            return await super().kafka_dependency_metrics(topic, consumer_group, at, window_minutes, instance_hint)
        return {
            "kind": "kafka",
            "topic": topic,
            "consumer_group": consumer_group,
            "instance_hint": instance_hint,
            "signals": _signals({
                "consumer_lag": 25000,
                "under_replicated_partitions": 0,
                "offline_partitions": 0,
                "consumer_group_members": 4,
            }),
        }

    async def redis_dependency_metrics(self, instance_hint, at, window_minutes=15):
        if self.dependency != "redis":
            return await super().redis_dependency_metrics(instance_hint, at, window_minutes)
        return {
            "kind": "redis",
            "instance_hint": instance_hint,
            "signals": _signals({
                "memory_used_bytes": 950 * 1024 * 1024,
                "memory_max_bytes": 1000 * 1024 * 1024,
                "config_maxmemory_bytes": 1000 * 1024 * 1024,
                "evictions_delta": 37,
                "blocked_clients": 0,
                "connected_clients": 280,
                "max_clients": 1000,
                "command_latency_seconds": 0.008,
            }),
        }


def service_incident() -> IncidentInput:
    return IncidentInput(
        title="Checkout dependency errors",
        cluster="prod",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=START,
    )


@pytest.mark.asyncio
async def test_postgres_connection_exhaustion_promotes_trace_origin_to_specific_rca():
    result = await InvestigationEngine(StatefulTools("postgresql")).investigate(service_incident())

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id.startswith("postgres-connection-exhaustion:")
    assert "connection capacity" in result.root_cause.title
    pg = next(item for item in result.stateful_dependencies if item.kind == "postgresql")
    assert pg.health == "critical"
    assert pg.causal_role == "origin"
    assert pg.hypothesis_id == result.root_cause.hypothesis_id
    utilization = next(item for item in pg.signals if item.name == "connection_utilization")
    assert utilization.status == "critical"
    assert utilization.value == pytest.approx(0.97)
    database_connections = next(item for item in pg.signals if item.name == "database_connections")
    assert database_connections.value == 88


@pytest.mark.asyncio
async def test_kafka_consumer_lag_promotes_trace_origin():
    result = await InvestigationEngine(StatefulTools("kafka")).investigate(service_incident())

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id.startswith("kafka-consumer-lag:")
    kafka = next(item for item in result.stateful_dependencies if item.kind == "kafka")
    assert kafka.target == "orders-events"
    assert kafka.metadata["consumer_group"] == "checkout-workers"
    assert kafka.metadata["instance_hint"] == "kafka-prod"
    assert kafka.health == "critical"
    lag = next(item for item in kafka.signals if item.name == "consumer_lag")
    assert lag.value == 25000
    assert lag.status == "critical"


@pytest.mark.asyncio
async def test_redis_memory_pressure_and_evictions_promote_trace_origin():
    result = await InvestigationEngine(StatefulTools("redis")).investigate(service_incident())

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id.startswith("redis-memory-pressure:")
    redis = next(item for item in result.stateful_dependencies if item.kind == "redis")
    assert redis.target == "cache"
    assert redis.metadata["instance_hint"] == "redis-cache"
    assert redis.health == "critical"
    memory = next(item for item in redis.signals if item.name == "memory_utilization")
    evictions = next(item for item in redis.signals if item.name == "evictions_delta")
    assert memory.value == pytest.approx(0.95)
    assert evictions.value == 37
    assert memory.status == "critical"
    assert evictions.status == "critical"


@pytest.mark.asyncio
async def test_stateful_dependency_does_not_override_explicit_kubernetes_root_cause():
    incident = IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        service="frontend",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    )

    result = await InvestigationEngine(StatefulTools("postgresql")).investigate(incident)

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "disk-pressure"
    assert any(
        item.kind == "postgresql" and item.hypothesis_id and item.hypothesis_id.startswith("postgres-connection-exhaustion:")
        for item in result.stateful_dependencies
    )
