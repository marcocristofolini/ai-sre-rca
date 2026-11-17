from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.integrations.telemetry.types import (
    LogRequest,
    LogResult,
    MetricRequest,
    MetricResult,
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
