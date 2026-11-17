from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.core.query_safety import quoted_string
from app.integrations.telemetry.types import (
    Provenance,
    ServiceRed,
    TraceRedResult,
    UnsupportedSignal,
)

_RED_REF = "tempo:/api/metrics/query_range"


def metric_values(payload: dict[str, Any]) -> list[float]:
    """Flatten a Tempo metrics payload into floats; malformed samples are skipped."""
    output: list[float] = []
    for series in payload.get("series", []):
        for sample in series.get("samples", []):
            try:
                output.append(float(sample.get("value")))
            except (TypeError, ValueError):
                pass
        for value in series.get("values", []):
            try:
                output.append(float(value[1] if isinstance(value, list) else value))
            except (TypeError, ValueError, IndexError):
                pass
    for row in payload.get("data", {}).get("result", []):
        values = row.get("values") or ([row.get("value")] if row.get("value") else [])
        for value in values:
            try:
                output.append(float(value[1]))
            except (TypeError, ValueError, IndexError):
                pass
    return output


class TempoTracesSource:
    """Trace signals backed by Tempo, run through the existing tools (budget included)."""

    source = "tempo"

    def __init__(self, tools: Any):
        self._tools = tools

    def supports(self, request: Any) -> bool:
        return isinstance(request, ServiceRed)

    def _red_queries(self, request: ServiceRed) -> dict[str, str]:
        escaped = quoted_string(request.service)
        return {
            "rate": f'{{ resource.service.name = "{escaped}" }} | rate()',
            "errors": f'{{ resource.service.name = "{escaped}" && status = error }} | rate()',
            "p95": f'{{ resource.service.name = "{escaped}" }} | quantile_over_time(span:duration, .95)',
        }

    def native_query(self, request: ServiceRed) -> str:
        if not isinstance(request, ServiceRed):
            raise UnsupportedSignal(type(request).__name__)
        return "; ".join(self._red_queries(request).values())

    async def red(self, request: ServiceRed, start: datetime, end: datetime, step: str = "30s") -> TraceRedResult:
        queries = self._red_queries(request) if isinstance(request, ServiceRed) else self._unsupported(request)
        rate, errors, p95 = await asyncio.gather(
            self._tools.tempo_metrics_query(queries["rate"], start, end, step),
            self._tools.tempo_metrics_query(queries["errors"], start, end, step),
            self._tools.tempo_metrics_query(queries["p95"], start, end, step),
        )
        return TraceRedResult(
            provenance=Provenance(self.source, "; ".join(queries.values()), _RED_REF),
            rate=tuple(metric_values(rate)),
            errors=tuple(metric_values(errors)),
            p95=tuple(metric_values(p95)),
        )

    @staticmethod
    def _unsupported(request: Any) -> dict[str, str]:
        raise UnsupportedSignal(type(request).__name__)

    async def search(self, service: str, start: datetime, end: datetime, limit: int = 8) -> list[dict[str, Any]]:
        return await self._tools.tempo_search_traces(service, start, end, limit)

    async def get_trace(
        self, trace_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> dict[str, Any]:
        return await self._tools.tempo_get_trace(trace_id, start, end)
