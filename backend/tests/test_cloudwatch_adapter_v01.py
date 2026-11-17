"""CloudWatch Metrics + Logs Insights read-only adapter (issue #75).

Clients are real botocore clients wrapped in Stubber, so every request is validated
against the AWS service model; no network is used.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from botocore.session import get_session
from botocore.stub import Stubber

from app.config import Settings
from app.core.query_budget import capability_for
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.integrations.telemetry import registered_backends
from app.integrations.telemetry.cloudwatch import (
    CloudWatchLogsSource,
    CloudWatchMetricsSource,
    valid_region,
)
from app.integrations.telemetry.types import (
    ContainerMemoryWorkingSet,
    NodeFilesystemAvailable,
    PodLogs,
    UnsupportedSignal,
)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
CLUSTER = {
    "aws_region": "sa-east-1",
    "cloudwatch_cluster_name": "prod-eks",
    "cloudwatch_log_groups": ["/aws/containerinsights/prod-eks/application"],
}


def run(coro):
    return asyncio.run(coro)


class Env:
    """Tools + stubbed clients keyed by service."""

    def __init__(self, **cluster):
        config = {**CLUSTER, **cluster}
        self.tools = ReadOnlyHTTPTools(Settings(cluster_config_json=json.dumps({"prod": config})))
        self.tools.cloudwatch_poll_interval = 0
        self.stubs = {}
        self.regions = []
        session = get_session()

        def factory(service, region):
            self.regions.append((service, region))
            client = session.create_client(
                service, region_name=region, aws_access_key_id="AKIAFAKE", aws_secret_access_key="fake"
            )
            stubber = Stubber(client)
            self.stubs[service] = stubber
            return client

        self.tools.cloudwatch_client_factory = factory
        self.token = self.tools.set_cluster("prod")


def _prepared(service, env, calls):
    """Create the client now so stubs can be queued, then activate."""
    client = env.tools.cloudwatch_client_factory(service, CLUSTER["aws_region"])
    stubber = env.stubs[service]
    for method, response, params in calls:
        stubber.add_response(method, response, params)
    stubber.activate()
    env.tools.cloudwatch_client_factory = lambda s, r, _c=client: _c
    return stubber


def test_backend_registration_and_budget_capabilities():
    assert "cloudwatch" in registered_backends("metrics") and "cloudwatch" in registered_backends("logs")
    assert "cloudwatch" not in registered_backends("traces")
    assert capability_for("cloudwatch_metrics") == "metrics"
    assert capability_for("cloudwatch_logs") == "logs"


def test_region_validation():
    for ok in ("us-east-1", "sa-east-1", "ap-southeast-3", "us-gov-west-1", "cn-north-1"):
        assert valid_region(ok)
    for bad in ("", "evil.example.com", "us-east-1.evil.io", "http://x", "us east 1", "../x"):
        assert not valid_region(bad)


def test_memory_working_set_query_and_last_point():
    env = Env()
    expected = {
        "MetricDataQueries": [
            {
                "Id": "m1",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "ContainerInsights",
                        "MetricName": "pod_memory_working_set",
                        "Dimensions": [
                            {"Name": "ClusterName", "Value": "prod-eks"},
                            {"Name": "Namespace", "Value": "shop"},
                            {"Name": "PodName", "Value": "api-1"},
                        ],
                    },
                    "Period": 60,
                    "Stat": "Maximum",
                },
                "ReturnData": True,
            }
        ],
        "StartTime": NOW - timedelta(minutes=5),
        "EndTime": NOW,
        "ScanBy": "TimestampAscending",
    }
    response = {"MetricDataResults": [{"Id": "m1", "Label": "x", "Timestamps": [NOW - timedelta(minutes=1), NOW], "Values": [10.0, 42.0], "StatusCode": "Complete"}]}
    stubber = _prepared("cloudwatch", env, [("get_metric_data", response, expected)])
    source = CloudWatchMetricsSource(env.tools)
    result = run(source.instant(ContainerMemoryWorkingSet("shop", "api-1"), NOW))
    stubber.assert_no_pending_responses()
    assert result.value == 42.0 and result.provenance.source == "cloudwatch"
    assert result.provenance.native_query == (
        "ContainerInsights/pod_memory_working_set{ClusterName=prod-eks,Namespace=shop,PodName=api-1} Maximum 60s"
    )
    assert result.provenance.raw_reference == "cloudwatch:GetMetricData"
    assert env.regions[0] == ("cloudwatch", "sa-east-1")


def test_range_returns_all_values_and_empty_is_no_value():
    env = Env()
    response = {"MetricDataResults": [{"Id": "m1", "Label": "x", "Timestamps": [NOW, NOW], "Values": [1.0, 3.0], "StatusCode": "Complete"}]}
    _prepared("cloudwatch", env, [("get_metric_data", response, None), ("get_metric_data", {"MetricDataResults": []}, None)])
    source = CloudWatchMetricsSource(env.tools)
    assert run(source.range(ContainerMemoryWorkingSet("a", "b"), NOW - timedelta(hours=1), NOW)).values == (1.0, 3.0)
    assert run(source.instant(ContainerMemoryWorkingSet("a", "b"), NOW)).value is None


def test_metrics_fail_closed_without_any_client_call():
    def forbidden(service, region):
        raise AssertionError("no AWS call expected")

    for cluster in ({}, {"cloudwatch_cluster_name": None}, {"aws_region": "evil.example.com"}):
        env = Env(**cluster)
        env.tools.cloudwatch_client_factory = forbidden
        source = CloudWatchMetricsSource(env.tools)
        with pytest.raises(UnsupportedSignal):
            run(source.instant(NodeFilesystemAvailable("n"), NOW)) if not cluster else run(
                source.instant(ContainerMemoryWorkingSet("a", "b"), NOW)
            )
    env = Env()
    env.tools.cloudwatch_client_factory = forbidden
    source = CloudWatchMetricsSource(env.tools)
    for bad in ("a}) OR (b", 'x"y', "", "a b"):
        with pytest.raises(UnsupportedSignal):
            run(source.instant(ContainerMemoryWorkingSet(bad, "p"), NOW))
    with pytest.raises(UnsupportedSignal):
        run(source.dependency(object(), NOW))
    assert not source.supports(NodeFilesystemAvailable("n"))


def test_logs_insights_start_poll_and_map_results():
    env = Env()
    start = NOW - timedelta(minutes=15)
    query = (
        "fields @timestamp, @message | filter kubernetes.namespace_name = \"shop\" "
        "and kubernetes.pod_name = \"api-1\" | sort @timestamp desc | limit 100"
    )
    rows = [
        [{"field": "@timestamp", "value": "t1"}, {"field": "@message", "value": "boom"}, {"field": "@ptr", "value": "p"}],
        [{"field": "@message", "value": "ok"}],
        [{"field": "@timestamp", "value": "t3"}],
    ]
    stubber = _prepared(
        "logs",
        env,
        [
            ("start_query", {"queryId": "q-1"}, {
                "logGroupNames": CLUSTER["cloudwatch_log_groups"],
                "startTime": int(start.timestamp()),
                "endTime": int(NOW.timestamp()),
                "queryString": query,
                "limit": 100,
            }),
            ("get_query_results", {"status": "Running", "results": []}, {"queryId": "q-1"}),
            ("get_query_results", {"status": "Complete", "results": rows}, {"queryId": "q-1"}),
        ],
    )
    result = run(CloudWatchLogsSource(env.tools).query(PodLogs("shop", "api-1"), start, NOW))
    stubber.assert_no_pending_responses()
    assert result.entries == ("boom", "ok") and result.entry_count == 3
    assert result.provenance.source == "cloudwatch" and result.provenance.native_query == query
    assert result.provenance.raw_reference == "cloudwatch:logs:StartQuery"


def test_logs_failed_or_timed_out_query_raises_and_stops_the_query():
    env = Env()
    _prepared("logs", env, [("start_query", {"queryId": "q-2"}, None), ("get_query_results", {"status": "Failed", "results": []}, None)])
    with pytest.raises(RuntimeError, match="Failed"):
        run(CloudWatchLogsSource(env.tools).query(PodLogs("a", "b"), NOW - timedelta(minutes=5), NOW))

    env = Env()
    env.tools.settings.cloudwatch_query_timeout_seconds = 0.0  # deadline already passed after the first poll
    stubber = _prepared(
        "logs",
        env,
        [("start_query", {"queryId": "q-3"}, None)]
        + [("get_query_results", {"status": "Running", "results": []}, None)]
        + [("stop_query", {"success": True}, {"queryId": "q-3"})],
    )
    with pytest.raises(RuntimeError, match="timed out"):
        run(CloudWatchLogsSource(env.tools).query(PodLogs("a", "b"), NOW - timedelta(minutes=5), NOW))
    stubber.assert_no_pending_responses()


def test_logs_config_and_value_validation_fail_closed():
    def forbidden(service, region):
        raise AssertionError("no AWS call expected")

    for cluster in ({"cloudwatch_log_groups": []}, {"cloudwatch_log_groups": ["bad group!"]}, {"cloudwatch_log_groups": None},
                    {"cloudwatch_log_pod_field": 'x" or 1=1 //'}):
        env = Env(**cluster)
        env.tools.cloudwatch_client_factory = forbidden
        with pytest.raises(UnsupportedSignal):
            run(CloudWatchLogsSource(env.tools).query(PodLogs("a", "b"), NOW, NOW))
    env = Env()
    env.tools.cloudwatch_client_factory = forbidden
    with pytest.raises(UnsupportedSignal):
        run(CloudWatchLogsSource(env.tools).query(PodLogs("a", 'b" | stats count()'), NOW, NOW))


def test_aws_errors_expose_only_the_error_code():
    env = Env()
    stubber = _prepared("cloudwatch", env, [])
    stubber.add_client_error("get_metric_data", service_error_code="AccessDenied", service_message="secret-arn arn:aws:iam::123:role/x")
    with pytest.raises(RuntimeError) as exc:
        run(CloudWatchMetricsSource(env.tools).instant(ContainerMemoryWorkingSet("a", "b"), NOW))
    assert "AccessDenied" in str(exc.value) and "secret-arn" not in str(exc.value) and "arn:aws" not in str(exc.value)


def test_health_reports_each_service_without_raising():
    env = Env()
    _prepared("cloudwatch", env, [("list_metrics", {"Metrics": []}, None)])
    assert run(CloudWatchMetricsSource(env.tools).health())["status"] == "healthy"
    env = Env()
    stubber = _prepared("logs", env, [])
    stubber.add_client_error("describe_log_groups", service_error_code="AccessDeniedException", service_message="nope")
    out = run(CloudWatchLogsSource(env.tools).health())
    assert out["status"] == "unavailable" and "nope" not in json.dumps(out)
    env = Env(aws_region="not a region")
    assert run(CloudWatchLogsSource(env.tools).health())["status"] == "unavailable"
