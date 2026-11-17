"""Read-only Datadog adapter for metrics and logs (ADR-0001, issue #74).

Endpoints and fields follow the official datadog-api-client 2.62.0: GET /api/v1/query,
POST /api/v2/logs/events/search (read-only search) and GET /api/v1/validate, with the
DD-API-KEY / DD-APPLICATION-KEY headers. The site is validated against Datadog's
regional sites so credentials cannot be sent to another host; tag values are validated
instead of escaped; errors never include URLs or credentials.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

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

ALLOWED_SITES = frozenset(
    {
        "datadoghq.com",
        "us3.datadoghq.com",
        "us5.datadoghq.com",
        "ap1.datadoghq.com",
        "ap2.datadoghq.com",
        "uk1.datadoghq.com",
        "datadoghq.eu",
        "ddog-gov.com",
        "us2.ddog-gov.com",
    }
)
BUDGET_METRICS = "datadog_metrics"
BUDGET_LOGS = "datadog_logs"
_TAG_VALUE = re.compile(r"[A-Za-z0-9_./-]{1,253}")
_MAX_LOG_LIMIT = 1000
_MAX_MESSAGE_CHARS = 4000
_INSTANT_LOOKBACK = timedelta(minutes=5)


def api_base_url(site: str) -> str:
    if site not in ALLOWED_SITES:
        raise ValueError(f"datadog_site {site!r} is not a known Datadog site")
    return f"https://api.{site}"


def _tag(value: Any) -> str:
    if not isinstance(value, str) or not _TAG_VALUE.fullmatch(value):
        raise UnsupportedSignal("value cannot be used safely in a Datadog query")
    return value


def _seconds(moment: datetime) -> int:
    return int(moment.astimezone(UTC).timestamp())


def _millis(moment: datetime) -> str:
    return str(int(moment.astimezone(UTC).timestamp() * 1000))


class _Client:
    def __init__(self, tools: Any, budget_source: str):
        self._tools = tools
        self._budget_source = budget_source

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None, budgeted: bool = True,
    ) -> dict[str, Any] | None:
        """JSON body, or None when the query budget denied the call."""
        settings = self._tools.settings
        if not settings.datadog_api_key or not settings.datadog_app_key:
            raise RuntimeError("datadog_api_key and datadog_app_key are required")
        base_url = api_base_url(str(self._tools._cluster_value("datadog_site", settings.datadog_site)))
        if budgeted and not self._tools._allow(self._budget_source):
            return None
        headers = {
            "DD-API-KEY": settings.datadog_api_key,
            "DD-APPLICATION-KEY": settings.datadog_app_key,
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=settings.datadog_timeout_seconds,
            transport=getattr(self._tools, "datadog_transport", None),
        ) as client:
            response = await client.request(method, path, params=params, json=json)
        if response.status_code >= 400:
            raise RuntimeError(f"datadog {method} {path} failed: HTTP {response.status_code}")
        body = response.json()
        return body if isinstance(body, dict) else {}


async def _health(tools: Any) -> dict[str, Any]:
    client = _Client(tools, BUDGET_METRICS)
    try:
        valid = await client.request("GET", "/api/v1/validate", budgeted=False)
        if not (valid or {}).get("valid"):
            return {"status": "unavailable", "detail": "API key rejected"}
        now = datetime.now(UTC)
        await client.request(
            "GET",
            "/api/v1/query",
            params={"from": _seconds(now - timedelta(minutes=1)), "to": _seconds(now), "query": "avg:datadog.agent.running{*}"},
            budgeted=False,
        )
    except Exception as exc:  # noqa: BLE001 - health reports, never raises
        return {"status": "unavailable", "detail": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}
    return {"status": "healthy", "detail": "datadog API and application keys accepted"}


class DatadogMetricsSource:
    source = "datadog"
    capability = "metrics"

    def __init__(self, tools: Any):
        self._tools = tools
        self._client = _Client(tools, BUDGET_METRICS)

    def supports(self, request: Any) -> bool:
        return isinstance(request, ContainerMemoryWorkingSet)

    def native_query(self, request: MetricRequest) -> str:
        if isinstance(request, ContainerMemoryWorkingSet):
            return (
                "sum:kubernetes.memory.working_set"
                f"{{kube_namespace:{_tag(request.namespace)},pod_name:{_tag(request.pod)}}}"
            )
        raise UnsupportedSignal(type(request).__name__)

    async def _points(self, request: MetricRequest, start: datetime, end: datetime):
        query = self.native_query(request)
        provenance = Provenance(self.source, query, "datadog:/api/v1/query")
        body = await self._client.request(
            "GET", "/api/v1/query", params={"from": _seconds(start), "to": _seconds(end), "query": query}
        )
        if body is None:
            return provenance, None, None
        if body.get("error") or body.get("status") not in (None, "ok"):
            raise RuntimeError("datadog metrics query returned an error")
        values: list[float] = []
        series = body.get("series") or []
        pointlist = series[0].get("pointlist") if series and isinstance(series[0], dict) else None
        for point in pointlist or []:
            try:
                if point[1] is not None:
                    values.append(float(point[1]))
            except (IndexError, TypeError, ValueError):
                continue
        return provenance, tuple(values), body

    async def instant(self, request: MetricRequest, at: datetime | None = None) -> MetricResult:
        end = at or datetime.now(UTC)
        provenance, values, body = await self._points(request, end - _INSTANT_LOOKBACK, end)
        if values is None:
            return MetricResult(provenance=provenance, budget_denied=True)
        return MetricResult(provenance=provenance, value=values[-1] if values else None, raw=body)

    async def range(self, request: MetricRequest, start: datetime, end: datetime, step: str = "60s") -> MetricResult:
        provenance, values, body = await self._points(request, start, end)
        if values is None:
            return MetricResult(provenance=provenance, budget_denied=True)
        return MetricResult(provenance=provenance, values=values, raw=body)

    async def dependency(self, request: Any, at: datetime) -> dict[str, Any]:
        raise UnsupportedSignal("datadog adapter has no dependency telemetry")

    async def service_graph(self, request: Any, at: datetime) -> list[dict[str, Any]]:
        raise UnsupportedSignal("datadog adapter has no service graph telemetry")

    async def health(self) -> dict[str, Any]:
        return await _health(self._tools)


class DatadogLogsSource:
    source = "datadog"
    capability = "logs"

    def __init__(self, tools: Any):
        self._tools = tools
        self._client = _Client(tools, BUDGET_LOGS)

    def supports(self, request: Any) -> bool:
        return isinstance(request, PodLogs)

    def native_query(self, request: LogRequest) -> str:
        if isinstance(request, PodLogs):
            return f"kube_namespace:{_tag(request.namespace)} pod_name:{_tag(request.pod)}"
        raise UnsupportedSignal(type(request).__name__)

    async def query(self, request: LogRequest, start: datetime, end: datetime) -> LogResult:
        query = self.native_query(request)
        provenance = Provenance(self.source, query, "datadog:/api/v2/logs/events/search")
        body = await self._client.request(
            "POST",
            "/api/v2/logs/events/search",
            json={
                "filter": {"query": query, "from": _millis(start), "to": _millis(end)},
                "page": {"limit": max(1, min(int(request.limit), _MAX_LOG_LIMIT))},
                "sort": "-timestamp",
            },
        )
        if body is None:
            return LogResult(provenance=provenance, budget_denied=True)
        data = [item for item in (body.get("data") or []) if isinstance(item, dict)]
        entries = tuple(
            message[:_MAX_MESSAGE_CHARS]
            for item in data
            if isinstance(message := (item.get("attributes") or {}).get("message"), str)
        )
        return LogResult(provenance=provenance, entries=entries, entry_count=len(data), raw=body)

    async def health(self) -> dict[str, Any]:
        return await _health(self._tools)


register_source_capability(BUDGET_METRICS, "metrics")
register_source_capability(BUDGET_LOGS, "logs")
register_backend("metrics", "datadog", DatadogMetricsSource)
register_backend("logs", "datadog", DatadogLogsSource)
