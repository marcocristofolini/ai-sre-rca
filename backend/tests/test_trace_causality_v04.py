from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest

from app.core.engine import InvestigationEngine
from app.integrations.demo import DemoTools
from app.schemas import IncidentInput


START = datetime(2026, 9, 27, 4, 30, tzinfo=timezone.utc)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def trace_payload(seed: int) -> dict:
    trace_id = _b64(bytes([seed]) * 16)
    root_id = _b64(bytes([seed, 1, 1, 1, 1, 1, 1, 1]))
    checkout_id = _b64(bytes([seed, 2, 2, 2, 2, 2, 2, 2]))
    db_id = _b64(bytes([seed, 3, 3, 3, 3, 3, 3, 3]))

    return {
        "trace": {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "frontend"}}
                        ]
                    },
                    "scopeSpans": [{
                        "spans": [{
                            "traceId": trace_id,
                            "spanId": root_id,
                            "name": "POST /checkout",
                            "kind": "SPAN_KIND_SERVER",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "1900000000",
                            "status": {"code": "STATUS_CODE_ERROR"},
                            "attributes": [
                                {"key": "http.response.status_code", "value": {"intValue": "500"}}
                            ],
                        }]
                    }],
                },
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "checkout"}}
                        ]
                    },
                    "scopeSpans": [{
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": checkout_id,
                                "parentSpanId": root_id,
                                "name": "CheckoutService.PlaceOrder",
                                "kind": "SPAN_KIND_SERVER",
                                "startTimeUnixNano": "1050000000",
                                "endTimeUnixNano": "1850000000",
                                "status": {"code": "STATUS_CODE_ERROR"},
                            },
                            {
                                "traceId": trace_id,
                                "spanId": db_id,
                                "parentSpanId": checkout_id,
                                "name": "SELECT orders",
                                "kind": "SPAN_KIND_CLIENT",
                                "startTimeUnixNano": "1100000000",
                                "endTimeUnixNano": "1800000000",
                                "status": {"code": "STATUS_CODE_ERROR"},
                                "attributes": [
                                    {"key": "db.system", "value": {"stringValue": "postgresql"}},
                                    {"key": "db.namespace", "value": {"stringValue": "orders"}},
                                ],
                            },
                        ]
                    }],
                },
            ]
        }
    }


class TraceTools(DemoTools):
    def __init__(self, trace_count: int = 2):
        self.trace_count = trace_count

    async def tempo_search_traces(self, service, start, end, limit=8):
        return [
            {
                "traceID": f"trace-{index}",
                "rootServiceName": "frontend",
                "rootTraceName": "POST /checkout",
                "_source_query": '{ resource.service.name = "frontend" && status = error }',
            }
            for index in range(1, self.trace_count + 1)
        ]

    async def tempo_get_trace(self, trace_id, start=None, end=None):
        seed = int(trace_id.rsplit("-", 1)[1])
        return trace_payload(seed)

    async def tempo_metrics_query(self, query, start, end, step="30s"):
        if "status = error" in query:
            value = 1.5
        elif "quantile_over_time" in query:
            value = 0.9
        else:
            value = 12.0
        return {"series": [{"values": [[start.timestamp(), value], [end.timestamp(), value]]}]}


@pytest.mark.asyncio
async def test_repeated_trace_origin_resolves_generic_service_incident():
    incident = IncidentInput(
        title="Checkout 5xx spike",
        cluster="prod",
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=START,
    )

    result = await InvestigationEngine(TraceTools(trace_count=2)).investigate(incident)

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "trace-causal-propagation"
    assert "postgresql/orders" in result.root_cause.title
    assert len(result.trace_analyses) == 2
    assert all(item.candidate_service == "postgresql/orders" for item in result.trace_analyses)
    assert any(edge.relationship == "trace_calls" for edge in result.graph_edges)
    assert any(item.category == "trace-red" for item in result.evidence)


@pytest.mark.asyncio
async def test_single_trace_is_not_enough_to_confirm_generic_root_cause():
    incident = IncidentInput(
        title="Checkout 5xx spike",
        cluster="prod",
        service="frontend",
        alert_name="HighErrorRate",
        starts_at=START,
    )

    result = await InvestigationEngine(TraceTools(trace_count=1)).investigate(incident)

    hypothesis = next(item for item in result.hypotheses if item.id == "trace-causal-propagation")
    assert result.status == "inconclusive"
    assert hypothesis.status.value == "testing"
    assert result.root_cause.title == "Investigation inconclusive"


@pytest.mark.asyncio
async def test_trace_evidence_does_not_override_confirmed_kubernetes_root_cause():
    incident = IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        service="frontend",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    )

    result = await InvestigationEngine(TraceTools(trace_count=2)).investigate(incident)

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "disk-pressure"
    trace_hypothesis = next(item for item in result.hypotheses if item.id == "trace-causal-propagation")
    assert trace_hypothesis.status.value == "confirmed"
