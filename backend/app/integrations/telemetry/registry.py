"""Backend selection for telemetry capabilities (ADR-0001).

The backend per capability comes from trusted cluster configuration
(``metrics_backend``, ``logs_backend``, ``traces_backend``), never from request
or model input. Unknown backends fail closed: the returned source answers
nothing, so callers record "no evidence" instead of guessing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.integrations.telemetry.prometheus import (
    LokiLogsSource,
    PrometheusMetricsSource,
)
from app.integrations.telemetry.tempo import TempoTracesSource
from app.integrations.telemetry.types import (
    DependencyRequest,
    KafkaDependency,
    PostgresDependency,
    RedisDependency,
    UnsupportedSignal,
)

DEFAULT_BACKENDS = {"metrics": "prometheus", "logs": "loki", "traces": "tempo"}
CONFIG_KEYS = {"metrics": "metrics_backend", "logs": "logs_backend", "traces": "traces_backend"}

_FACTORIES: dict[str, dict[str, Callable[[Any], Any]]] = {
    "metrics": {"prometheus": PrometheusMetricsSource},
    "logs": {"loki": LokiLogsSource},
    "traces": {"tempo": TempoTracesSource},
}


def register_backend(capability: str, name: str, factory: Callable[[Any], Any]) -> None:
    """Register a backend factory. ``factory(tools)`` returns a source for ``capability``."""
    if capability not in _FACTORIES:
        raise ValueError(f"unknown capability: {capability!r}")
    existing = _FACTORIES[capability].get(name)
    if existing is not None and existing is not factory:
        raise ValueError(f"{capability} backend {name!r} is already registered")
    _FACTORIES[capability][name] = factory


def registered_backends(capability: str) -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES[capability]))


class UnavailableSource:
    """Stands in for a configured-but-unregistered backend. Every query fails closed."""

    def __init__(self, capability: str, backend: str):
        self.capability = capability
        self.source = backend

    def supports(self, request: Any) -> bool:
        return False

    def native_query(self, request: Any) -> str:
        return ""

    def _fail(self) -> UnsupportedSignal:
        return UnsupportedSignal(f"{self.capability} backend {self.source!r} is not registered")

    async def instant(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def range(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def query(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def red(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def get_trace(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def dependency(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()

    async def service_graph(self, *args: Any, **kwargs: Any) -> Any:
        raise self._fail()


def build_source(capability: str, tools: Any, backend: str | None = None) -> Any:
    name = backend or DEFAULT_BACKENDS[capability]
    factory = _FACTORIES[capability].get(name)
    if factory is None:
        return UnavailableSource(capability, name)
    return factory(tools)


def _source_for(capability: str, tools: Any, fallback: Callable[[Any], Any]) -> Any:
    configured = getattr(tools, f"{capability}_source", None)
    return configured if configured is not None else fallback(tools)


def metrics_source_for(tools: Any) -> Any:
    """The tools' configured metrics source; test doubles without one get Prometheus."""
    return _source_for("metrics", tools, PrometheusMetricsSource)


def logs_source_for(tools: Any) -> Any:
    return _source_for("logs", tools, LokiLogsSource)


def traces_source_for(tools: Any) -> Any:
    return _source_for("traces", tools, TempoTracesSource)


def unsupported_dependency_result(request: DependencyRequest, backend: str, window: int | None = None) -> dict[str, Any]:
    """Result shape stateful_dependency already handles: no values, explicit reason per signal."""
    from app.integrations.telemetry.prometheus import (
        _KAFKA_SIGNALS,
        _POSTGRES_SIGNALS,
        _REDIS_SIGNALS,
    )

    reason = f"{backend} backend does not provide this dependency telemetry"
    if isinstance(request, PostgresDependency):
        names, base = _POSTGRES_SIGNALS, {"kind": "postgresql", "database": request.database}
    elif isinstance(request, KafkaDependency):
        names, base = _KAFKA_SIGNALS, {"kind": "kafka", "topic": request.topic, "consumer_group": request.consumer_group}
    elif isinstance(request, RedisDependency):
        names, base = _REDIS_SIGNALS, {"kind": "redis"}
    else:
        raise UnsupportedSignal(type(request).__name__)
    return {
        **base,
        "instance_hint": request.instance_hint or None,
        "window_minutes": request.window_minutes,
        "signals": {name: {"value": None, "query": None, "error": reason} for name in names},
    }


__all__ = [
    "CONFIG_KEYS",
    "DEFAULT_BACKENDS",
    "UnavailableSource",
    "build_source",
    "logs_source_for",
    "metrics_source_for",
    "register_backend",
    "registered_backends",
    "traces_source_for",
    "unsupported_dependency_result",
]
