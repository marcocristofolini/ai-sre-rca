from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings

# Telemetry capability each budget source belongs to. Additive reporting only:
# limits still apply per source and in total. Adapters for new backends register
# their source here so snapshots can show usage per capability (ADR-0001).
_CAPABILITY_BY_SOURCE: dict[str, str] = {
    "prometheus": "metrics",
    "loki": "logs",
    "tempo": "traces",
}


def register_source_capability(source: str, capability: str) -> None:
    existing = _CAPABILITY_BY_SOURCE.get(source)
    if existing is not None and existing != capability:
        raise ValueError(f"source {source!r} is already registered as {existing!r}")
    _CAPABILITY_BY_SOURCE[source] = capability


def capability_for(source: str) -> str | None:
    return _CAPABILITY_BY_SOURCE.get(source)


@dataclass
class QueryBudgetState:
    enabled: bool
    max_calls: int
    max_calls_per_source: int
    max_wall_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    total_calls: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    per_capability: dict[str, int] = field(default_factory=dict)
    denied: list[dict[str, Any]] = field(default_factory=list)

    def consume(self, source: str, cost: int = 1) -> bool:
        if not self.enabled:
            return True

        elapsed = time.monotonic() - self.started_at
        source_calls = self.per_source.get(source, 0)

        if elapsed > self.max_wall_seconds:
            self.denied.append({
                "source": source,
                "reason": "wall_time",
                "elapsed_seconds": round(elapsed, 3),
            })
            return False

        if self.total_calls + cost > self.max_calls:
            self.denied.append({
                "source": source,
                "reason": "total_calls",
                "total_calls": self.total_calls,
            })
            return False

        if source_calls + cost > self.max_calls_per_source:
            self.denied.append({
                "source": source,
                "reason": "source_calls",
                "source_calls": source_calls,
            })
            return False

        self.total_calls += cost
        self.per_source[source] = source_calls + cost
        capability = capability_for(source)
        if capability:
            self.per_capability[capability] = self.per_capability.get(capability, 0) + cost
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "total_calls": self.total_calls,
            "per_source": dict(sorted(self.per_source.items())),
            "per_capability": dict(sorted(self.per_capability.items())),
            "denied": list(self.denied),
            "elapsed_seconds": round(time.monotonic() - self.started_at, 3),
            "limits": {
                "max_calls": self.max_calls,
                "max_calls_per_source": self.max_calls_per_source,
                "max_wall_seconds": self.max_wall_seconds,
            },
        }


_budget_var: ContextVar[QueryBudgetState | None] = ContextVar("query_budget", default=None)


def begin_query_budget(settings: Settings) -> object:
    state = QueryBudgetState(
        enabled=settings.query_budget_enabled,
        max_calls=settings.query_budget_max_calls,
        max_calls_per_source=settings.query_budget_max_calls_per_source,
        max_wall_seconds=settings.query_budget_max_wall_seconds,
    )
    return _budget_var.set(state)


def consume_query(source: str, cost: int = 1) -> bool:
    state = _budget_var.get()
    if state is None:
        return True
    return state.consume(source, cost)


def query_budget_snapshot() -> dict[str, Any] | None:
    state = _budget_var.get()
    return state.snapshot() if state else None


def end_query_budget(token: object) -> dict[str, Any] | None:
    state = _budget_var.get()
    snapshot = state.snapshot() if state else None
    _budget_var.reset(token)
    return snapshot
