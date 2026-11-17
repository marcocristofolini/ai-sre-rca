"""Source-neutral telemetry types (ADR-0001).

Callers ask for a signal; a source translates it to its native query and
returns a normalized result that carries the provenance of what it ran.
Requests are a closed catalogue of frozen dataclasses, not free-form strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class UnsupportedSignal(Exception):
    """The source cannot answer this signal. Callers treat it as "no evidence"."""


@dataclass(frozen=True)
class Provenance:
    source: str
    native_query: str
    raw_reference: str
    truncated: bool = False


@dataclass(frozen=True)
class NodeFilesystemAvailable:
    node: str


@dataclass(frozen=True)
class ContainerMemoryWorkingSet:
    namespace: str
    pod: str


@dataclass(frozen=True)
class PodLogs:
    namespace: str
    pod: str
    limit: int = 100


MetricRequest = NodeFilesystemAvailable | ContainerMemoryWorkingSet
LogRequest = PodLogs


@dataclass(frozen=True)
class MetricResult:
    provenance: Provenance
    value: float | None = None
    values: tuple[float, ...] = ()
    budget_denied: bool = False
    # Backend payload kept while call sites migrate; new code should not read it.
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def empty(cls, source: str) -> MetricResult:
        return cls(provenance=Provenance(source=source, native_query="", raw_reference=""))


@dataclass(frozen=True)
class LogResult:
    provenance: Provenance
    entries: tuple[str, ...] = ()
    entry_count: int = 0
    budget_denied: bool = False
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def empty(cls, source: str) -> LogResult:
        return cls(provenance=Provenance(source=source, native_query="", raw_reference=""))
