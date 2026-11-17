from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.integrations.telemetry.types import (
    LogRequest,
    LogResult,
    MetricRequest,
    MetricResult,
    ServiceRed,
    TraceRedResult,
)


class MetricsSource(Protocol):
    source: str

    def supports(self, request: MetricRequest) -> bool: ...
    def native_query(self, request: MetricRequest) -> str: ...
    async def instant(self, request: MetricRequest, at: datetime | None = None) -> MetricResult: ...
    async def range(self, request: MetricRequest, start: datetime, end: datetime, step: str = "60s") -> MetricResult: ...


class LogsSource(Protocol):
    source: str

    def supports(self, request: LogRequest) -> bool: ...
    def native_query(self, request: LogRequest) -> str: ...
    async def query(self, request: LogRequest, start: datetime, end: datetime) -> LogResult: ...


class TracesSource(Protocol):
    source: str

    def supports(self, request: ServiceRed) -> bool: ...
    def native_query(self, request: ServiceRed) -> str: ...
    async def red(self, request: ServiceRed, start: datetime, end: datetime, step: str = "30s") -> TraceRedResult: ...
    async def search(self, service: str, start: datetime, end: datetime, limit: int = 8) -> list[dict[str, Any]]: ...
    async def get_trace(
        self, trace_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> dict[str, Any]: ...
