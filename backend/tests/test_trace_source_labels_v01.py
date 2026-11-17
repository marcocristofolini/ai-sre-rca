"""Trace evidence must carry the label of the traces backend that produced it (#74)."""

import re

import pytest
from test_trace_causality_v04 import START, TraceTools

from app.core.engine import InvestigationEngine
from app.integrations.telemetry.tempo import TempoTracesSource
from app.integrations.telemetry.types import Provenance, TraceRedResult
from app.schemas import IncidentInput


class OtherTraces(TempoTracesSource):
    source = "datadog"
    display_name = "Datadog"
    red_label = "Datadog span RED metrics"
    search_reference = "datadog:/api/v2/spans/events/search"

    def search_query(self, service: str) -> str:
        return f"service:{service}"

    async def red(self, request, start, end, step="30s"):
        base = await super().red(request, start, end, step)
        return TraceRedResult(
            provenance=Provenance("datadog", "count{service:x}", "datadog:/api/v2/spans/analytics/aggregate"),
            rate=base.rate,
            errors=base.errors,
            p95=base.p95,
        )


def _incident():
    return IncidentInput(title="Trace labels", cluster="prod", service="frontend", starts_at=START)


async def _run(tools):
    from app.core.trace_analysis import enrich_with_trace_causality

    engine = InvestigationEngine(tools)
    result = engine._generic_result("labels", _incident(), [])
    return await enrich_with_trace_causality(result, tools=tools, settings=engine.settings)


def _trace_evidence(result):
    return [e for e in result.evidence if e.category.startswith("trace-")]


@pytest.mark.asyncio
async def test_default_tempo_labels_are_unchanged():
    result = await _run(TraceTools(trace_count=2))
    found = _trace_evidence(result)
    assert {"trace-causal-chain", "trace-search", "trace-red"} <= {e.category for e in found}
    assert {e.source for e in found} == {"tempo"}
    search = next(e for e in found if e.category == "trace-search")
    assert search.raw_reference == "tempo:/api/search"
    assert search.summary.startswith("Tempo returned ")
    assert search.query == '{ resource.service.name = "frontend" }'
    assert next(e for e in found if e.category == "trace-red").summary.startswith("TraceQL RED metrics for frontend")
    assert all(e.raw_reference.startswith("tempo") for e in found if e.category == "trace-causal-chain")


@pytest.mark.asyncio
async def test_other_backend_is_not_labelled_tempo():
    tools = TraceTools(trace_count=2)
    tools.traces_source = OtherTraces(tools)
    result = await _run(tools)
    found = _trace_evidence(result)
    assert found and {e.source for e in found} == {"datadog"}
    search = next(e for e in found if e.category == "trace-search")
    assert search.raw_reference == "datadog:/api/v2/spans/events/search"
    assert search.summary.startswith("Datadog returned ")
    assert search.query == "service:frontend"
    chain = next(e for e in found if e.category == "trace-causal-chain")
    assert chain.raw_reference.startswith("datadog://trace/")
    red = next(e for e in found if e.category == "trace-red")
    assert red.summary.startswith("Datadog span RED metrics for frontend")
    word = re.compile(r"\btempo\b", re.IGNORECASE)
    assert not any(word.search(e.raw_reference or "") or word.search(e.summary) for e in found)
