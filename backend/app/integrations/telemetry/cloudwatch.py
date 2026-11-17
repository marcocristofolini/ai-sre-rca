"""Read-only CloudWatch Metrics and Logs Insights adapter (ADR-0001, issue #75).

API shapes follow botocore's service models (cloudwatch 2010-08-01 GetMetricData /
ListMetrics, logs 2014-03-28 StartQuery / GetQueryResults / StopQuery / DescribeLogGroups).
Only read operations are used. Safety:
- credentials come from the AWS default chain, never from settings or cluster config;
- the endpoint is derived by botocore from a validated region (no URL is configurable);
- cluster name, log groups and field names come from trusted cluster config and are
  validated; namespace and pod values are validated, not escaped;
- AWS errors are reported by error code only (messages can contain ARNs);
- every metrics call and every Logs Insights query consumes query budget.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.session import get_session

from app.core.query_budget import register_source_capability
from app.integrations.telemetry.registry import register_backend
from app.integrations.telemetry.types import (
    ContainerMemoryWorkingSet,
    LogRequest,
    LogResult,
    MetricRequest,
    MetricResult,
    PodLogs,
    Provenance,
    UnsupportedSignal,
)

BUDGET_METRICS = "cloudwatch_metrics"
BUDGET_LOGS = "cloudwatch_logs"
_REGION = re.compile(r"[a-z]{2,5}(-[a-z]+)+-\d{1,2}")
_VALUE = re.compile(r"[A-Za-z0-9_./-]{1,253}")
_LOG_GROUP = re.compile(r"[A-Za-z0-9_./#-]{1,512}")
_FIELD = re.compile(r"[A-Za-z0-9_.@]{1,128}")
_MAX_LOG_GROUPS = 10
_MAX_LOG_LIMIT = 1000
_MAX_MESSAGE_CHARS = 4000
_INSTANT_LOOKBACK = timedelta(minutes=5)
_POD_MEMORY_NAMESPACE = "ContainerInsights"
_POD_MEMORY_METRIC = "pod_memory_working_set"
_clients: dict[tuple[str, str], Any] = {}


def valid_region(region: Any) -> bool:
    return isinstance(region, str) and _REGION.fullmatch(region) is not None


def _value(value: Any) -> str:
    if not isinstance(value, str) or not _VALUE.fullmatch(value):
        raise UnsupportedSignal("value cannot be used safely in a CloudWatch query")
    return value


def _default_client(service: str, region: str) -> Any:
    key = (service, region)
    if key not in _clients:
        _clients[key] = get_session().create_client(
            service,
            region_name=region,
            config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"}),
        )
    return _clients[key]


class _Aws:
    """Region/config resolution and error-safe, thread-offloaded AWS calls."""

    def __init__(self, tools: Any, service: str, budget_source: str):
        self._tools = tools
        self._service = service
        self._budget_source = budget_source

    def cluster_value(self, key: str, default: Any = None) -> Any:
        return self._tools._cluster_value(key, default)

    def region(self) -> str:
        region = self.cluster_value("aws_region", self._tools.settings.aws_region)
        if not valid_region(region):
            raise UnsupportedSignal("cloudwatch requires a valid aws_region")
        return region

    def allowed(self) -> bool:
        return bool(self._tools._allow(self._budget_source))

    def client(self) -> Any:
        factory = getattr(self._tools, "cloudwatch_client_factory", _default_client)
        return factory(self._service, self.region())

    async def call(self, operation: str, **params: Any) -> dict[str, Any]:
        client = self.client()
        try:
            return await asyncio.to_thread(getattr(client, operation), **params)
        except ClientError as exc:
            code = str((exc.response.get("Error") or {}).get("Code") or "Unknown")
            raise RuntimeError(f"cloudwatch {operation} failed: {code}") from None
        except BotoCoreError as exc:
            raise RuntimeError(f"cloudwatch {operation} failed: {type(exc).__name__}") from None


class CloudWatchMetricsSource:
    source = "cloudwatch"
    capability = "metrics"

    def __init__(self, tools: Any):
        self._aws = _Aws(tools, "cloudwatch", BUDGET_METRICS)

    def supports(self, request: Any) -> bool:
        return isinstance(request, ContainerMemoryWorkingSet)

    def _dimensions(self, request: ContainerMemoryWorkingSet) -> list[dict[str, str]]:
        cluster = self._aws.cluster_value("cloudwatch_cluster_name")
        if not cluster:
            raise UnsupportedSignal("cloudwatch_cluster_name is required for Container Insights metrics")
        return [
            {"Name": "ClusterName", "Value": _value(cluster)},
            {"Name": "Namespace", "Value": _value(request.namespace)},
            {"Name": "PodName", "Value": _value(request.pod)},
        ]

    def native_query(self, request: MetricRequest) -> str:
        if not isinstance(request, ContainerMemoryWorkingSet):
            raise UnsupportedSignal(type(request).__name__)
        dims = ",".join(f"{d['Name']}={d['Value']}" for d in self._dimensions(request))
        return f"{_POD_MEMORY_NAMESPACE}/{_POD_MEMORY_METRIC}{{{dims}}} Maximum 60s"

    async def _points(self, request: MetricRequest, start: datetime, end: datetime):
        query = self.native_query(request)
        self._aws.region()  # fail closed on a bad region before spending budget
        provenance = Provenance(self.source, query, "cloudwatch:GetMetricData")
        if not self._aws.allowed():
            return provenance, None
        response = await self._aws.call(
            "get_metric_data",
            MetricDataQueries=[
                {
                    "Id": "m1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": _POD_MEMORY_NAMESPACE,
                            "MetricName": _POD_MEMORY_METRIC,
                            "Dimensions": self._dimensions(request),
                        },
                        "Period": 60,
                        "Stat": "Maximum",
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=start,
            EndTime=end,
            ScanBy="TimestampAscending",
        )
        values: list[float] = []
        for result in response.get("MetricDataResults") or []:
            if result.get("Id") == "m1":
                values.extend(float(v) for v in result.get("Values") or [])
        return provenance, tuple(values)

    async def instant(self, request: MetricRequest, at: datetime | None = None) -> MetricResult:
        end = at or datetime.now(UTC)
        provenance, values = await self._points(request, end - _INSTANT_LOOKBACK, end)
        if values is None:
            return MetricResult(provenance=provenance, budget_denied=True)
        return MetricResult(provenance=provenance, value=values[-1] if values else None)

    async def range(self, request: MetricRequest, start: datetime, end: datetime, step: str = "60s") -> MetricResult:
        provenance, values = await self._points(request, start, end)
        if values is None:
            return MetricResult(provenance=provenance, budget_denied=True)
        return MetricResult(provenance=provenance, values=values)

    async def dependency(self, request: Any, at: datetime) -> dict[str, Any]:
        raise UnsupportedSignal("cloudwatch adapter has no dependency telemetry")

    async def service_graph(self, request: Any, at: datetime) -> list[dict[str, Any]]:
        raise UnsupportedSignal("cloudwatch adapter has no service graph telemetry")

    async def health(self) -> dict[str, Any]:
        try:
            self._aws.region()
            await self._aws.call("list_metrics", Namespace=_POD_MEMORY_NAMESPACE, MetricName=_POD_MEMORY_METRIC)
        except UnsupportedSignal as exc:
            return {"status": "unavailable", "detail": str(exc)}
        except RuntimeError as exc:
            return {"status": "unavailable", "detail": str(exc)}
        return {"status": "healthy", "detail": "cloudwatch metrics reachable"}


class CloudWatchLogsSource:
    source = "cloudwatch"
    capability = "logs"

    def __init__(self, tools: Any):
        self._tools = tools
        self._aws = _Aws(tools, "logs", BUDGET_LOGS)

    def supports(self, request: Any) -> bool:
        return isinstance(request, PodLogs)

    def _log_groups(self) -> list[str]:
        groups = self._aws.cluster_value("cloudwatch_log_groups")
        if (
            not isinstance(groups, list)
            or not 1 <= len(groups) <= _MAX_LOG_GROUPS
            or not all(isinstance(g, str) and _LOG_GROUP.fullmatch(g) for g in groups)
        ):
            raise UnsupportedSignal("cloudwatch_log_groups must list 1-10 valid log group names")
        return groups

    def _field(self, key: str, default: str) -> str:
        field = self._aws.cluster_value(key, default)
        if not isinstance(field, str) or not _FIELD.fullmatch(field):
            raise UnsupportedSignal(f"{key} is not a valid Logs Insights field name")
        return field

    def native_query(self, request: LogRequest) -> str:
        if not isinstance(request, PodLogs):
            raise UnsupportedSignal(type(request).__name__)
        namespace_field = self._field("cloudwatch_log_namespace_field", "kubernetes.namespace_name")
        pod_field = self._field("cloudwatch_log_pod_field", "kubernetes.pod_name")
        limit = max(1, min(int(request.limit), _MAX_LOG_LIMIT))
        return (
            f'fields @timestamp, @message | filter {namespace_field} = "{_value(request.namespace)}" '
            f'and {pod_field} = "{_value(request.pod)}" | sort @timestamp desc | limit {limit}'
        )

    async def query(self, request: LogRequest, start: datetime, end: datetime) -> LogResult:
        query = self.native_query(request)
        groups = self._log_groups()
        self._aws.region()
        provenance = Provenance(self.source, query, "cloudwatch:logs:StartQuery")
        if not self._aws.allowed():
            return LogResult(provenance=provenance, budget_denied=True)
        started = await self._aws.call(
            "start_query",
            logGroupNames=groups,
            startTime=int(start.timestamp()),
            endTime=int(end.timestamp()),
            queryString=query,
            limit=max(1, min(int(request.limit), _MAX_LOG_LIMIT)),
        )
        query_id = started["queryId"]
        deadline = time.monotonic() + float(self._tools.settings.cloudwatch_query_timeout_seconds)
        interval = float(getattr(self._tools, "cloudwatch_poll_interval", 1.0))
        while True:
            response = await self._aws.call("get_query_results", queryId=query_id)
            status = response.get("status")
            if status == "Complete":
                break
            if status in {"Failed", "Cancelled", "Timeout"}:
                raise RuntimeError(f"cloudwatch logs query ended with status {status}")
            if time.monotonic() >= deadline:
                try:
                    await self._aws.call("stop_query", queryId=query_id)
                except RuntimeError:
                    pass
                raise RuntimeError("cloudwatch logs query timed out")
            await asyncio.sleep(interval)
        rows = response.get("results") or []
        entries = []
        for row in rows:
            for cell in row:
                if cell.get("field") == "@message" and isinstance(cell.get("value"), str):
                    entries.append(cell["value"][:_MAX_MESSAGE_CHARS])
        return LogResult(provenance=provenance, entries=tuple(entries), entry_count=len(rows))

    async def health(self) -> dict[str, Any]:
        try:
            self._aws.region()
            await self._aws.call("describe_log_groups", limit=1)
        except (UnsupportedSignal, RuntimeError) as exc:
            return {"status": "unavailable", "detail": str(exc)}
        return {"status": "healthy", "detail": "cloudwatch logs reachable"}


register_source_capability(BUDGET_METRICS, "metrics")
register_source_capability(BUDGET_LOGS, "logs")
register_backend("metrics", "cloudwatch", CloudWatchMetricsSource)
register_backend("logs", "cloudwatch", CloudWatchLogsSource)
