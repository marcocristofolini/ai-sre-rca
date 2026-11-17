from datetime import UTC, datetime

import pytest

from app.core.query_safety import quoted_string
from app.integrations.telemetry import (
    ContainerMemoryWorkingSet,
    KafkaDependency,
    LokiLogsSource,
    NodeFilesystemAvailable,
    PodLogs,
    PostgresDependency,
    PrometheusMetricsSource,
    RedisDependency,
    ServiceGraph,
    ServiceRed,
    TempoTracesSource,
    UnsupportedSignal,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)


class FakeTools:
    def __init__(self, instant=None, ranged=None, logs=None):
        self.calls = []
        self._instant = instant if instant is not None else {"data": {"result": []}}
        self._ranged = ranged if ranged is not None else {"data": {"result": []}}
        self._logs = logs if logs is not None else {"data": {"result": []}}

    async def prometheus_query(self, query, at=None):
        self.calls.append(("instant", query, at))
        return self._instant

    async def prometheus_query_range(self, query, start, end, step="60s"):
        self.calls.append(("range", query, step))
        return self._ranged

    async def loki_query_range(self, query, start, end, limit=100):
        self.calls.append(("loki", query, limit))
        return self._logs


# Golden strings copied from the pre-migration call sites: evidence hashes depend on them.
def test_native_queries_are_byte_identical_to_legacy():
    prom = PrometheusMetricsSource(FakeTools())
    loki = LokiLogsSource(FakeTools())
    assert prom.native_query(NodeFilesystemAvailable("ip-10-0-1-42.ec2.internal")) == (
        'node_filesystem_avail_bytes{instance=~"^ip-10-0-1-42\\\\.ec2\\\\.internal(:[0-9]+)?$",mountpoint="/"}'
    )
    assert prom.native_query(ContainerMemoryWorkingSet("shop", "web-1")) == (
        'sum(container_memory_working_set_bytes{namespace="shop",pod="web-1",'
        'container!="",image!=""})'
    )
    assert loki.native_query(PodLogs("shop", "web-1")) == '{namespace="shop", pod="web-1"}'


def test_hostile_labels_are_escaped_not_injected():
    prom = PrometheusMetricsSource(FakeTools())
    loki = LokiLogsSource(FakeTools())
    query = prom.native_query(ContainerMemoryWorkingSet('a"b', 'c"d'))
    assert quoted_string('a"b') in query and 'a"b' not in query
    assert 'a"b' not in loki.native_query(PodLogs('a"b', "p"))


async def test_instant_result_carries_provenance_and_value():
    tools = FakeTools(instant={"data": {"result": [{"value": [1, "42.5"]}]}})
    result = await PrometheusMetricsSource(tools).instant(ContainerMemoryWorkingSet("ns", "p"), T0)
    assert result.value == 42.5
    assert result.provenance.source == "prometheus"
    assert result.provenance.raw_reference == "prometheus:/api/v1/query"
    assert result.provenance.native_query == tools.calls[0][1]


async def test_range_result_skips_malformed_points():
    tools = FakeTools(ranged={"data": {"result": [{"values": [[1, "3"], [2], [3, "x"], [4, "5"]]}]}})
    result = await PrometheusMetricsSource(tools).range(NodeFilesystemAvailable("n"), T0, T1, "60s")
    assert result.values == (3.0, 5.0)
    assert result.provenance.raw_reference == "prometheus:/api/v1/query_range"


async def test_missing_or_garbage_payload_yields_no_value():
    for payload in ({}, {"data": {"result": []}}, {"data": {"result": [{"value": [1, "nan-ish"]}]}}):
        result = await PrometheusMetricsSource(FakeTools(instant=payload)).instant(NodeFilesystemAvailable("n"))
        assert result.value is None


async def test_budget_denied_is_visible_and_has_no_value():
    tools = FakeTools(instant={"status": "success", "data": {"result": []}, "_budget_denied": True})
    result = await PrometheusMetricsSource(tools).instant(NodeFilesystemAvailable("n"))
    assert result.budget_denied and result.value is None


async def test_log_counts_match_legacy_semantics():
    payload = {"data": {"result": [{"values": [[1, "a"], [2, "b"], [3]]}, {"values": [[4, "c"]]}]}}
    result = await LokiLogsSource(FakeTools(logs=payload)).query(PodLogs("ns", "p", 200), T0, T1)
    assert result.entry_count == 4  # engine counted every value
    assert result.entries == ("a", "b", "c")  # investigator kept only well-formed entries
    assert result.provenance.raw_reference == "loki:/loki/api/v1/query_range"


async def test_limit_is_forwarded_to_tools():
    tools = FakeTools()
    await LokiLogsSource(tools).query(PodLogs("ns", "p", 200), T0, T1)
    assert tools.calls == [("loki", '{namespace="ns", pod="p"}', 200)]


def test_unsupported_signal_fails_closed():
    prom = PrometheusMetricsSource(FakeTools())
    assert not prom.supports(PodLogs("ns", "p"))
    with pytest.raises(UnsupportedSignal):
        prom.native_query(PodLogs("ns", "p"))
    with pytest.raises(UnsupportedSignal):
        LokiLogsSource(FakeTools()).native_query(NodeFilesystemAvailable("n"))


class FakeTempo:
    def __init__(self, payloads=None, fail=False):
        self.calls = []
        self._payloads = payloads or {}
        self._fail = fail

    async def tempo_metrics_query(self, query, start, end, step="30s"):
        self.calls.append(("metrics", query, step))
        if self._fail:
            raise RuntimeError("tempo down")
        key = "errors" if "status = error" in query else "p95" if "quantile" in query else "rate"
        return self._payloads.get(key, {})

    async def tempo_search_traces(self, service, start, end, limit=8):
        self.calls.append(("search", service, limit))
        return [{"traceID": "abc"}]

    async def tempo_get_trace(self, trace_id, start=None, end=None):
        self.calls.append(("get", trace_id))
        return {"batches": []}


def test_trace_red_query_is_byte_identical_to_legacy():
    assert TempoTracesSource(FakeTempo()).native_query(ServiceRed("checkout")) == (
        '{ resource.service.name = "checkout" } | rate(); '
        '{ resource.service.name = "checkout" && status = error } | rate(); '
        '{ resource.service.name = "checkout" } | quantile_over_time(span:duration, .95)'
    )


async def test_trace_red_values_and_provenance():
    tools = FakeTempo(
        payloads={
            "rate": {"series": [{"samples": [{"value": "2"}, {"value": "x"}]}]},
            "errors": {"data": {"result": [{"values": [[1, "0.5"]]}]}},
        }
    )
    red = await TempoTracesSource(tools).red(ServiceRed("checkout"), T0, T1)
    assert red.rate == (2.0,) and red.errors == (0.5,) and red.p95 == ()
    assert red.provenance.source == "tempo"
    assert red.provenance.raw_reference == "tempo:/api/metrics/query_range"
    assert len(tools.calls) == 3 and all(call[2] == "30s" for call in tools.calls)


async def test_trace_red_propagates_backend_errors_so_callers_can_fail_closed():
    with pytest.raises(RuntimeError):
        await TempoTracesSource(FakeTempo(fail=True)).red(ServiceRed("s"), T0, T1)


async def test_trace_search_and_get_forward_arguments():
    tools = FakeTempo()
    source = TempoTracesSource(tools)
    assert await source.search("svc", T0, T1, 5) == [{"traceID": "abc"}]
    assert await source.get_trace("abc", T0, T1) == {"batches": []}
    assert tools.calls == [("search", "svc", 5), ("get", "abc")]


def test_tempo_rejects_unsupported_signal():
    source = TempoTracesSource(FakeTempo())
    assert not source.supports(PodLogs("ns", "p"))
    with pytest.raises(UnsupportedSignal):
        source.native_query(PodLogs("ns", "p"))


@pytest.mark.parametrize(
    "source,supported,unsupported",
    [
        (PrometheusMetricsSource(FakeTools()), NodeFilesystemAvailable("n"), PodLogs("ns", "p")),
        (LokiLogsSource(FakeTools()), PodLogs("ns", "p"), NodeFilesystemAvailable("n")),
        (TempoTracesSource(FakeTempo()), ServiceRed("s"), PodLogs("ns", "p")),
    ],
)
def test_source_contract(source, supported, unsupported):
    """Every source: names itself, renders a non-empty native query, fails closed on foreign signals."""
    assert source.source
    assert source.supports(supported) and not source.supports(unsupported)
    assert source.native_query(supported)
    with pytest.raises(UnsupportedSignal):
        source.native_query(unsupported)


# Golden queries captured from the pre-migration implementation in http_tools.py.
GOLDEN_POSTGRES = {
    'connections': 'sum(pg_stat_activity_count{instance=~"^pg-1(:[0-9]+)?$"})',
    'database_connections': 'sum(pg_stat_activity_count{datname="orders",instance=~"^pg-1(:[0-9]+)?$"})',
    'active_connections': 'sum(pg_stat_activity_count{datname="orders",state="active",instance=~"^pg-1(:[0-9]+)?$"})',
    'idle_in_transaction': 'sum(pg_stat_activity_count{datname="orders",state=~"idle in transaction.*",instance=~"^pg-1(:[0-9]+)?$"})',
    'max_connections': 'max(pg_settings_max_connections{instance=~"^pg-1(:[0-9]+)?$"})',
    'locks': 'sum(pg_locks_count{datname="orders",instance=~"^pg-1(:[0-9]+)?$"})',
    'access_exclusive_locks': 'sum(pg_locks_count{datname="orders",mode="accessexclusivelock",instance=~"^pg-1(:[0-9]+)?$"})',
    'deadlocks_delta': 'sum(increase(pg_stat_database_deadlocks{datname="orders",instance=~"^pg-1(:[0-9]+)?$"}[15m]))',
    'replication_lag_bytes': 'max(pg_stat_replication_pg_wal_lsn_diff{instance=~"^pg-1(:[0-9]+)?$"})',
    'long_tx_seconds': 'max(pg_stat_activity_max_tx_duration{datname="orders",instance=~"^pg-1(:[0-9]+)?$"})',
}
GOLDEN_KAFKA = {
    'under_replicated_partitions': 'sum(kafka_topic_partition_under_replicated_partition{topic="t1",instance=~"^k-0:9092$"})',
    'offline_partitions': 'sum(kafka_topic_partition_leader{topic="t1",instance=~"^k-0:9092$"} < bool 0)',
    'consumer_lag': 'sum(kafka_consumergroup_lag{topic="t1",consumergroup="g1",instance=~"^k-0:9092$"})',
    'consumer_group_members': 'min(kafka_consumergroup_members{consumergroup="g1",instance=~"^k-0:9092$"})',
}
GOLDEN_REDIS = {
    'memory_used_bytes': 'max(redis_memory_used_bytes{instance=~"^r-1(:[0-9]+)?$"})',
    'memory_max_bytes': 'max(redis_memory_max_bytes{instance=~"^r-1(:[0-9]+)?$"})',
    'config_maxmemory_bytes': 'max(redis_config_maxmemory{instance=~"^r-1(:[0-9]+)?$"})',
    'evictions_delta': 'sum(increase(redis_evicted_keys_total{instance=~"^r-1(:[0-9]+)?$"}[7m]))',
    'blocked_clients': 'max(redis_blocked_clients{instance=~"^r-1(:[0-9]+)?$"})',
    'connected_clients': 'max(redis_connected_clients{instance=~"^r-1(:[0-9]+)?$"})',
    'max_clients': 'max(redis_config_maxclients{instance=~"^r-1(:[0-9]+)?$"})',
    'command_latency_seconds': 'max(rate(redis_commands_duration_seconds_total{instance=~"^r-1(:[0-9]+)?$"}[5m]) / clamp_min(rate(redis_commands_total{instance=~"^r-1(:[0-9]+)?$"}[5m]), 0.001))',
}
GOLDEN_SERVICE_GRAPH = ['histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{server="web"}[5m])) by (client, server, connection_type, le)) or histogram_quantile(0.95, sum(rate(traces_service_graph_request_server_seconds_bucket{client="web"}[5m])) by (client, server, connection_type, le))', 'sum(increase(traces_service_graph_request_failed_total{server="web"}[15m])) by (client, server, connection_type) or sum(increase(traces_service_graph_request_failed_total{client="web"}[15m])) by (client, server, connection_type)', 'sum(increase(traces_service_graph_request_total{server="web"}[15m])) by (client, server, connection_type) > 0 or sum(increase(traces_service_graph_request_total{client="web"}[15m])) by (client, server, connection_type) > 0']


class RecordingTools:
    def __init__(self):
        self.queries = []

    async def prometheus_query(self, query, at=None):
        self.queries.append(query)
        return {"data": {"result": []}}


def _queries(result):
    return {name: item["query"] for name, item in result["signals"].items()}


async def test_postgres_dependency_queries_match_legacy():
    result = await PrometheusMetricsSource(RecordingTools()).dependency(
        PostgresDependency("orders", "pg-1", 15), T0
    )
    assert _queries(result) == GOLDEN_POSTGRES
    assert result["kind"] == "postgresql" and result["instance_hint"] == "pg-1"


async def test_kafka_dependency_queries_match_legacy():
    result = await PrometheusMetricsSource(RecordingTools()).dependency(
        KafkaDependency("t1", "g1", "k-0:9092", 15), T0
    )
    assert _queries(result) == GOLDEN_KAFKA


async def test_redis_dependency_queries_match_legacy():
    result = await PrometheusMetricsSource(RecordingTools()).dependency(RedisDependency("r-1", 7), T0)
    assert _queries(result) == GOLDEN_REDIS


async def test_service_graph_queries_match_legacy():
    tools = RecordingTools()
    assert await PrometheusMetricsSource(tools).service_graph(ServiceGraph("web", 15), T0) == []
    assert sorted(tools.queries) == GOLDEN_SERVICE_GRAPH


async def test_missing_identity_runs_no_queries_and_explains_why():
    tools = RecordingTools()
    source = PrometheusMetricsSource(tools)
    pg = await source.dependency(PostgresDependency("orders", None), T0)
    kafka = await source.dependency(KafkaDependency(None, "g", "k"), T0)
    redis = await source.dependency(RedisDependency(""), T0)
    assert tools.queries == []
    for result in (pg, kafka, redis):
        assert all(
            item["value"] is None and item["query"] is None and item["error"]
            for item in result["signals"].values()
        )
    assert "instance identity" in pg["signals"]["connections"]["error"]
    assert "topic identity" in kafka["signals"]["consumer_lag"]["error"]


async def test_kafka_without_consumer_group_withholds_consumer_signals():
    result = await PrometheusMetricsSource(RecordingTools()).dependency(KafkaDependency("t1", None, "k-0"), T0)
    assert result["signals"]["consumer_lag"]["query"] is None
    assert result["signals"]["offline_partitions"]["query"]


async def test_backend_error_is_recorded_per_signal_not_raised():
    class Failing(RecordingTools):
        async def prometheus_query(self, query, at=None):
            raise RuntimeError("down")

    result = await PrometheusMetricsSource(Failing()).dependency(RedisDependency("r-1"), T0)
    assert all(item["value"] is None and item["error"] == "down" for item in result["signals"].values())


def test_dependency_signals_are_supported_and_render_native_queries():
    source = PrometheusMetricsSource(RecordingTools())
    requests = (PostgresDependency("d", "i"), KafkaDependency("t", None, "i"), RedisDependency("i"), ServiceGraph("s"))
    for request in requests:
        assert source.supports(request) and source.native_query(request)
