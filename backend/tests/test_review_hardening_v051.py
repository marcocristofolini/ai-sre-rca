from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.core.dependency_identity import dependency_instance
from app.core.engine import InvestigationEngine
from app.core.query_safety import path_segment, prom_regex_literal, quoted_string
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

    result = await tools.postgres_dependency_metrics("orders", START, 15, "orders-db")

    assert tools.queries
    assert all("instance=~" in query for query in tools.queries)
    assert any('mode="accessexclusivelock"' in query for query in tools.queries)
    assert "datname=" not in result["signals"]["connections"]["query"]
    assert 'datname="orders"' in result["signals"]["database_connections"]["query"]
    assert "orders\\-db" not in " ".join(tools.queries)
    assert "orders-db" in " ".join(tools.queries)
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

    result = await tools.kafka_dependency_metrics(
        "orders-events",
        "checkout-workers",
        START,
        15,
        hint,
    )

    assert tools.queries
    assert all("kafka.*(prod)" not in query for query in tools.queries)
    assert all(r"kafka\\.\\*\\(prod\\)" in query for query in tools.queries)
    assert "< bool 0" in result["signals"]["offline_partitions"]["query"]


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
    matching = [
        item
        for item in result.entities
        if item.kind == "TraceDependency" and item.name == "postgresql/orders"
    ]
    assert [item.id for item in matching] == ["dependency:postgresql/orders@orders-db"]
    entity = matching[0]

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


def test_dependency_instance_includes_port_and_handles_ipv6():
    assert dependency_instance({"server.address": "orders-db", "server.port": 5432}) == "orders-db:5432"
    assert dependency_instance({"server.address": "2001:db8::1", "server.port": 6379}) == "[2001:db8::1]:6379"
    assert dependency_instance({"dependency.instance": "explicit:9092"}) == "explicit:9092"


def test_query_safety_escapes_regex_control_and_path_segments():
    assert prom_regex_literal("node.prod-1") == r"node\\.prod-1"
    assert quoted_string('svc"\nnext') == 'svc\\"\\nnext'
    assert path_segment("namespace/pod") == "namespace%2Fpod"
    matcher = ReadOnlyHTTPTools._prom_instance_matcher("orders-db")
    assert matcher == 'instance=~"^orders-db(:[0-9]+)?$"'


def test_non_finite_prometheus_values_are_unknown():
    payload = {"data": {"result": [{"value": [START.timestamp(), str(math.nan)]}]}}

    assert ReadOnlyHTTPTools._prometheus_scalar(payload) is None
