from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.query_safety import prom_regex_literal, quoted_string
from app.integrations.telemetry.types import (
    ContainerMemoryWorkingSet,
    LogRequest,
    LogResult,
    MetricRequest,
    MetricResult,
    NodeFilesystemAvailable,
    PodLogs,
    Provenance,
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


class PrometheusMetricsSource:
    """Translates catalogued signals to PromQL and runs them through existing tools.

    Wraps an ``InvestigationTools`` object, so the existing budget accounting
    and test doubles keep working unchanged.
    """

    source = "prometheus"

    def __init__(self, tools: Any):
        self._tools = tools

    def supports(self, request: MetricRequest) -> bool:
        return isinstance(request, (NodeFilesystemAvailable, ContainerMemoryWorkingSet))

    def native_query(self, request: MetricRequest) -> str:
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
