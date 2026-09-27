from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.core.engine import InvestigationEngine
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.schemas import IncidentInput
from tests.test_stateful_dependency_v05 import StatefulTools, dependency_trace_payload


START = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)


class CapturingHTTPTools(ReadOnlyHTTPTools):
    def __init__(self):
        super().__init__(Settings())
        self.queries: list[str] = []

    async def prometheus_query(self, query: str, at: datetime) -> dict:
        self.queries.append(query)
        return {"data": {"result": [{"value": [at.timestamp(), "0"]}]}}


class SplitInstanceKafkaTools(StatefulTools):
    def __init__(self):
        super().__init__("kafka")

    async def tempo_get_trace(self, trace_id, start=None, end=None):
        seed = int(trace_id.rsplit("-", 1)[1])
        payload = dependency_trace_payload(seed, "kafka")
        if seed == 2:
            for resource in payload["trace"]["resourceSpans"]:
                for scope in resource.get("scopeSpans", []):
                    for span in scope.get("spans", []):
                        for attr in span.get("attributes", []):
                            if attr.get("key") == "server.address":
                                attr["value"]["stringValue"] = "kafka-canary"
        return payload


class ExistingDependencyGraphTools(StatefulTools):
    def __init__(self):
        super().__init__("postgresql")

    async def service_graph(self, service: str, at: datetime):
        return [
            {
                "client": "checkout",
                "server": "postgresql/orders",
                "requests": 12.0,
                "failures": 4.0,
                "error_ratio": 1 / 3,
                "p95_seconds": 0.8,
                "connection_type": "database",
                "query": "demo:service-graph",
            }
        ]


@pytest.mark.asyncio
async def test_postgres_queries_are_instance_scoped_and_use_exporter_labels():
    tools = CapturingHTTPTools()

    await tools.postgres_dependency_metrics("orders", START, 15, "orders-db")

    assert tools.queries
    assert all("instance=~" in query for query in tools.queries)
    assert any('mode="accessexclusivelock"' in query for query in tools.queries)
    long_tx_queries = [query for query in tools.queries if "pg_stat_activity_max_tx_duration" in query]
    assert len(long_tx_queries) == 1
    assert 'datname="orders"' in long_tx_queries[0]
    assert "instance=~" in long_tx_queries[0]


@pytest.mark.asyncio
async def test_postgres_without_instance_identity_does_not_query_global_metrics():
    tools = CapturingHTTPTools()

    result = await tools.postgres_dependency_metrics("orders", START, 15, None)

    assert tools.queries == []
    assert all(signal["value"] is None for signal in result["signals"].values())
    assert all("instance identity required" in signal["error"] for signal in result["signals"].values())


@pytest.mark.asyncio
async def test_kafka_without_instance_identity_does_not_query_global_metrics():
    tools = CapturingHTTPTools()

    result = await tools.kafka_dependency_metrics(
        "orders-events",
        "checkout-workers",
        START,
        15,
        None,
    )

    assert tools.queries == []
    assert all(signal["value"] is None for signal in result["signals"].values())
    assert all("instance identity required" in signal["error"] for signal in result["signals"].values())


@pytest.mark.asyncio
async def test_kafka_instance_regex_is_escaped():
    tools = CapturingHTTPTools()
    hint = "kafka.*(prod)"

    await tools.kafka_dependency_metrics(
        "orders-events",
        "checkout-workers",
        START,
        15,
        hint,
    )

    assert tools.queries
    assert all("kafka.*(prod)" not in query for query in tools.queries)
    assert all(r"kafka\\.\\*\\(prod\\)" in query for query in tools.queries)


@pytest.mark.asyncio
async def test_redis_without_instance_identity_does_not_query_global_metrics():
    tools = CapturingHTTPTools()

    result = await tools.redis_dependency_metrics(None, START, 15)

    assert tools.queries == []
    assert all(signal["value"] is None for signal in result["signals"].values())


@pytest.mark.asyncio
async def test_redis_instance_regex_is_escaped():
    tools = CapturingHTTPTools()
    hint = "redis.*(prod)"

    await tools.redis_dependency_metrics(hint, START, 15)

    assert tools.queries
    assert all("redis.*(prod)" not in query for query in tools.queries)
    assert all(r"redis\\.\\*\\(prod\\)" in query for query in tools.queries)


@pytest.mark.asyncio
async def test_trace_metadata_merges_into_existing_dependency_entity():
    incident = IncidentInput(
        title="Checkout dependency errors",
        cluster="prod",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=START,
    )

    result = await InvestigationEngine(ExistingDependencyGraphTools()).investigate(incident)
    entity = next(item for item in result.entities if item.id == "dependency:postgresql/orders")

    assert entity.metadata["db.system"] == "postgresql"
    assert entity.metadata["db.namespace"] == "orders"
    assert entity.metadata["server.address"] == "orders-db"


@pytest.mark.asyncio
async def test_trace_confirmation_does_not_merge_same_dependency_across_instances():
    incident = IncidentInput(
        title="Checkout dependency errors",
        cluster="prod",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=START,
    )
    settings = Settings(stateful_dependency_analysis_enabled=False)

    result = await InvestigationEngine(
        SplitInstanceKafkaTools(),
        settings=settings,
    ).investigate(incident)

    trace_hypothesis = next(item for item in result.hypotheses if item.id == "trace-causal-propagation")
    assert trace_hypothesis.status != "confirmed"
    dependency_ids = {
        entity.id
        for entity in result.entities
        if entity.kind == "TraceDependency" and entity.name == "kafka/orders-events"
    }
    assert "dependency:kafka/orders-events@kafka-prod" in dependency_ids
    assert "dependency:kafka/orders-events@kafka-canary" in dependency_ids


def test_non_finite_prometheus_values_are_unknown():
    payload = {"data": {"result": [{"value": [START.timestamp(), str(math.nan)]}]}}

    assert ReadOnlyHTTPTools._prometheus_scalar(payload) is None
