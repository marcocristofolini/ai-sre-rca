from datetime import UTC, datetime

import pytest

from app.core.query_safety import quoted_string
from app.integrations.telemetry import (
    ContainerMemoryWorkingSet,
    LokiLogsSource,
    NodeFilesystemAvailable,
    PodLogs,
    PrometheusMetricsSource,
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
