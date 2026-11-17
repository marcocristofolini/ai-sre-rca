"""Datadog read-only adapter (issue #74). Fixtures use field names from the official client."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import Settings
from app.core.query_budget import capability_for
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.integrations.telemetry import registered_backends
from app.integrations.telemetry.datadog import (
    ALLOWED_SITES,
    DatadogLogsSource,
    DatadogMetricsSource,
    api_base_url,
)
from app.integrations.telemetry.types import (
    ContainerMemoryWorkingSet,
    NodeFilesystemAvailable,
    PodLogs,
    UnsupportedSignal,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _tools(handler, **cluster):
    settings = Settings(
        datadog_api_key="api-secret",
        datadog_app_key="app-secret",
        cluster_config_json=json.dumps({"prod": cluster}),
    )
    tools = ReadOnlyHTTPTools(settings)
    tools.datadog_transport = httpx.MockTransport(handler)
    return tools


def run(coro):
    return asyncio.run(coro)


def test_backend_is_registered_for_metrics_and_logs():
    assert "datadog" in registered_backends("metrics")
    assert "datadog" in registered_backends("logs")


def test_budget_sources_map_to_capabilities():
    assert capability_for("datadog_metrics") == "metrics"
    assert capability_for("datadog_logs") == "logs"


def test_site_allow_list_blocks_unknown_hosts():
    assert api_base_url("datadoghq.eu") == "https://api.datadoghq.eu"
    assert "datadoghq.com" in ALLOWED_SITES
    for bad in ("evil.example.com", "datadoghq.com.evil.io", "", "api.datadoghq.com"):
        with pytest.raises(ValueError):
            api_base_url(bad)


def test_memory_query_uses_verified_endpoint_headers_and_returns_last_point():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            json={"status": "ok", "series": [{"pointlist": [[1.0, 10.0], [2.0, None], [3.0, 42.0]]}]},
        )

    source = DatadogMetricsSource(_tools(handler))
    result = run(source.instant(ContainerMemoryWorkingSet("shop", "api-1"), NOW))
    assert result.value == 42.0
    assert result.provenance.source == "datadog"
    assert result.provenance.native_query == "sum:kubernetes.memory.working_set{kube_namespace:shop,pod_name:api-1}"
    assert seen["url"].host == "api.datadoghq.com" and seen["url"].path == "/api/v1/query"
    assert seen["url"].params["query"] == result.provenance.native_query
    assert seen["headers"]["DD-API-KEY"] == "api-secret"
    assert seen["headers"]["DD-APPLICATION-KEY"] == "app-secret"


def test_range_returns_all_non_null_points():
    handler = lambda r: httpx.Response(200, json={"status": "ok", "series": [{"pointlist": [[1, 1.0], [2, None], [3, 3.0]]}]})
    result = run(DatadogMetricsSource(_tools(handler)).range(ContainerMemoryWorkingSet("a", "b"), NOW - timedelta(hours=1), NOW))
    assert result.values == (1.0, 3.0)


def test_empty_series_yields_no_value():
    handler = lambda r: httpx.Response(200, json={"status": "ok", "series": []})
    result = run(DatadogMetricsSource(_tools(handler)).instant(ContainerMemoryWorkingSet("a", "b"), NOW))
    assert result.value is None


def test_unsupported_and_unsafe_signals_fail_closed_without_http():
    def handler(request):
        raise AssertionError("no HTTP call expected")

    source = DatadogMetricsSource(_tools(handler))
    assert not source.supports(NodeFilesystemAvailable("n"))
    with pytest.raises(UnsupportedSignal):
        run(source.instant(NodeFilesystemAvailable("n"), NOW))
    for bad in ("a}) OR (b", "ns pod_name:x", 'x"y', ""):
        with pytest.raises(UnsupportedSignal):
            run(source.instant(ContainerMemoryWorkingSet(bad, "p"), NOW))
    with pytest.raises(UnsupportedSignal):
        run(source.dependency(object(), NOW))


def test_http_error_hides_credentials_and_url():
    handler = lambda r: httpx.Response(403, json={"errors": ["Forbidden api-secret"]})
    with pytest.raises(RuntimeError) as exc:
        run(DatadogMetricsSource(_tools(handler)).instant(ContainerMemoryWorkingSet("a", "b"), NOW))
    text = str(exc.value)
    assert "403" in text and "api-secret" not in text and "app-secret" not in text and "api.datadoghq" not in text


def test_missing_keys_raise_without_http():
    tools = ReadOnlyHTTPTools(Settings())
    with pytest.raises(RuntimeError, match="datadog_api_key"):
        run(DatadogMetricsSource(tools).instant(ContainerMemoryWorkingSet("a", "b"), NOW))


def test_cluster_site_override_is_validated():
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        return httpx.Response(200, json={"status": "ok", "series": []})

    token = None
    tools = _tools(handler, datadog_site="datadoghq.eu")
    token = tools.set_cluster("prod")
    try:
        run(DatadogMetricsSource(tools).instant(ContainerMemoryWorkingSet("a", "b"), NOW))
        assert seen["host"] == "api.datadoghq.eu"
    finally:
        tools.reset_cluster(token)
    bad = _tools(handler, datadog_site="evil.example.com")
    token = bad.set_cluster("prod")
    try:
        with pytest.raises(ValueError):
            run(DatadogMetricsSource(bad).instant(ContainerMemoryWorkingSet("a", "b"), NOW))
    finally:
        bad.reset_cluster(token)


def test_logs_search_request_and_response_mapping():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"attributes": {"message": "boom", "status": "error"}}, {"attributes": {"message": "ok"}}, {"attributes": {}}]},
        )

    result = run(DatadogLogsSource(_tools(handler)).query(PodLogs("shop", "api-1", limit=5000), NOW - timedelta(minutes=15), NOW))
    assert seen["method"] == "POST" and seen["path"] == "/api/v2/logs/events/search"
    assert seen["body"]["filter"]["query"] == "kube_namespace:shop pod_name:api-1"
    assert seen["body"]["page"]["limit"] == 1000
    assert seen["body"]["sort"] == "-timestamp"
    assert result.entries == ("boom", "ok") and result.entry_count == 3
    assert result.provenance.raw_reference == "datadog:/api/v2/logs/events/search"


def test_logs_reject_unsafe_values():
    source = DatadogLogsSource(_tools(lambda r: httpx.Response(200, json={})))
    with pytest.raises(UnsupportedSignal):
        run(source.query(PodLogs("ns", "p OR *"), NOW, NOW))


def test_health_validates_api_key_then_app_key():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/validate":
            return httpx.Response(200, json={"valid": True})
        return httpx.Response(200, json={"status": "ok", "series": []})

    assert run(DatadogMetricsSource(_tools(handler)).health())["status"] == "healthy"
    assert calls == ["/api/v1/validate", "/api/v1/query"]
    rejected = lambda r: httpx.Response(403, json={})
    out = run(DatadogMetricsSource(_tools(rejected)).health())
    assert out["status"] == "unavailable" and "secret" not in json.dumps(out)


# ---------------------------------------------------------------- APM traces
from app.core.trace_analysis import _normalize_spans, analyze_trace
from app.integrations.telemetry.datadog import DatadogTracesSource
from app.integrations.telemetry.types import ServiceRed

TRACE = "5b8efff798038103d269b633813fc60c"


def _span(span_id, parent, service, name, start, end, status=None):
    attrs = {"status": status} if status else {}
    return {
        "attributes": {
            "trace_id": TRACE,
            "span_id": span_id,
            "parent_id": parent,
            "service": service,
            "resource_name": name,
            "start_timestamp": start,
            "end_timestamp": end,
            "attributes": attrs,
        }
    }


SPANS = [
    _span("1", "0", "frontend", "POST /checkout", "2026-11-17T12:00:00.000Z", "2026-11-17T12:00:02.000Z", "error"),
    _span("2", "1", "payments", "charge", "2026-11-17T12:00:00.100Z", "2026-11-17T12:00:01.900Z", "error"),
    _span("3", "2", "postgres", "SELECT", "2026-11-17T12:00:00.200Z", "2026-11-17T12:00:01.800Z", "ok"),
]


def test_traces_backend_registration_and_labels():
    assert "datadog" in registered_backends("traces")
    assert capability_for("datadog_traces") == "traces"
    source = DatadogTracesSource(_tools(lambda r: httpx.Response(200, json={})))
    assert (source.source, source.display_name, source.red_label) == ("datadog", "Datadog", "Datadog span RED metrics")
    assert source.search_query("frontend") == "service:frontend"
    assert source.search_reference == "datadog:/api/v2/spans/events/search"


def test_trace_search_prefers_errors_dedupes_and_returns_tempo_shaped_hits():
    queries = []

    def handler(request):
        body = json.loads(request.content)
        attrs = body["data"]["attributes"]
        queries.append(attrs["filter"]["query"])
        assert request.url.path == "/api/v2/spans/events/search" and body["data"]["type"] == "search_request"
        if "status:error" in attrs["filter"]["query"]:
            return httpx.Response(200, json={"data": [{"attributes": {"trace_id": "aaa"}}, {"attributes": {"trace_id": "aaa"}}]})
        return httpx.Response(200, json={"data": [{"attributes": {"trace_id": "aaa"}}, {"attributes": {"trace_id": "bbb"}}]})

    hits = run(DatadogTracesSource(_tools(handler)).search("frontend", NOW - timedelta(minutes=15), NOW, 8))
    assert [h["traceID"] for h in hits] == ["aaa", "bbb"]
    assert hits[0]["_source_query"] == "service:frontend status:error"
    assert queries == ["service:frontend status:error", "service:frontend"]


def test_trace_search_limit_and_unsafe_service():
    handler = lambda r: httpx.Response(200, json={"data": [{"attributes": {"trace_id": c}} for c in "abcdef"]})
    assert len(run(DatadogTracesSource(_tools(handler)).search("frontend", NOW, NOW, 3))) == 3
    with pytest.raises(UnsupportedSignal):
        run(DatadogTracesSource(_tools(handler)).search("a b OR c", NOW, NOW, 3))


def test_get_trace_converts_spans_to_the_shape_the_analyzer_reads():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": SPANS})

    payload = run(DatadogTracesSource(_tools(handler)).get_trace(TRACE, NOW - timedelta(hours=1), NOW))
    assert seen["body"]["data"]["attributes"]["filter"]["query"] == f"trace_id:{TRACE}"
    spans = _normalize_spans(payload)
    assert {s["service"] for s in spans} == {"frontend", "payments", "postgres"}
    by = {s["operation"]: s for s in spans}
    assert by["POST /checkout"]["status"] == "error" and by["SELECT"]["status"] == "ok"
    assert by["POST /checkout"]["duration_ms"] == 2000.0
    assert by["charge"]["parent_span_id"] == by["POST /checkout"]["span_id"]
    assert all(s["trace_id"] == TRACE for s in spans)
    analysis = analyze_trace(payload, trace_id=TRACE, source_query="q", incident_service="frontend", settings=Settings())
    assert analysis is not None and analysis.error_count == 2


def test_get_trace_skips_spans_without_usable_timestamps_and_rejects_unsafe_ids():
    bad = _span("9", "1", "x", "bad", "not-a-date", "2026-11-17T12:00:00Z")
    payload = run(DatadogTracesSource(_tools(lambda r: httpx.Response(200, json={"data": [bad, *SPANS]}))).get_trace(TRACE))
    assert len(_normalize_spans(payload)) == 3
    with pytest.raises(UnsupportedSignal):
        run(DatadogTracesSource(_tools(lambda r: httpx.Response(200, json={}))).get_trace("x OR y"))


def test_get_trace_with_no_spans_is_empty():
    assert run(DatadogTracesSource(_tools(lambda r: httpx.Response(200, json={"data": []}))).get_trace(TRACE)) == {}


def test_red_uses_span_aggregate_totals_and_leaves_p95_unreported():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert request.url.path == "/api/v2/spans/analytics/aggregate"
        assert body["data"]["type"] == "aggregate_request"
        error = "status:error" in body["data"]["attributes"]["filter"]["query"]
        return httpx.Response(200, json={"data": [{"attributes": {"by": {}, "computes": {"c0": 30 if error else 600}}}]})

    result = run(
        DatadogTracesSource(_tools(handler)).red(ServiceRed("frontend"), NOW - timedelta(minutes=10), NOW)
    )
    assert result.rate == (1.0,) and result.errors == (0.05,) and result.p95 == ()
    assert result.provenance.source == "datadog"
    assert result.provenance.raw_reference == "datadog:/api/v2/spans/analytics/aggregate"
    assert {b["data"]["attributes"]["compute"][0]["aggregation"] for b in bodies} == {"count"}
    assert {b["data"]["attributes"]["compute"][0]["type"] for b in bodies} == {"total"}


def test_red_empty_response_has_no_values_and_other_signals_unsupported():
    source = DatadogTracesSource(_tools(lambda r: httpx.Response(200, json={"data": []})))
    out = run(source.red(ServiceRed("frontend"), NOW - timedelta(minutes=5), NOW))
    assert out.rate == () and out.errors == ()
    assert not source.supports(object())
