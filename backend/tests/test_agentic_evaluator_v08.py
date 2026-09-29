from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.api.planner import investigate_agentically
from app.auth import Principal
from app.config import Settings
from app.db.repository import MemoryRepository
from app.integrations.demo import StatefulDemoTools, TraceDemoTools
from app.schemas import IncidentInput


def service_incident() -> IncidentInput:
    return IncidentInput(
        title="Frontend checkout failure",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="checkout",
        service="frontend",
        alert_name="HighErrorRate",
        starts_at=datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc),
    )


def kubernetes_incident() -> IncidentInput:
    return service_incident().model_copy(
        update={"pod": "checkout-abc"}
    )


@pytest.mark.asyncio
async def test_evaluator_prefers_stateful_over_trace_when_both_pass():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=service_incident(),
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )

    evaluation = evaluate_shared_evidence(execution)

    assert evaluation.status == "resolved"
    assert evaluation.root_cause is not None
    assert evaluation.root_cause.hypothesis_id is not None
    assert evaluation.root_cause.hypothesis_id.startswith("postgres-")
    selected = next(
        item
        for item in evaluation.evaluated_hypotheses
        if item.decision == "selected"
    )
    assert selected.specialist == "database"
    trace = next(
        item
        for item in evaluation.evaluated_hypotheses
        if item.id == "trace-causal-propagation"
    )
    assert trace.status.value == "confirmed"
    assert trace.decision == "supported"


@pytest.mark.asyncio
async def test_direct_kubernetes_evidence_blocks_downstream_promotion():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=kubernetes_incident(),
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )

    evaluation = evaluate_shared_evidence(execution)

    assert evaluation.status == "resolved"
    assert evaluation.root_cause is not None
    assert evaluation.root_cause.hypothesis_id == "disk-pressure"
    selected = next(
        item
        for item in evaluation.evaluated_hypotheses
        if item.decision == "selected"
    )
    assert selected.specialist == "kubernetes"
    blocked = [
        item
        for item in evaluation.evaluated_hypotheses
        if item.decision == "blocked_by_precedence"
    ]
    assert blocked
    assert all(
        item.specialist in {"traces", "database"}
        for item in blocked
    )
    gate = next(
        node
        for node in evaluation.explanation_graph.nodes
        if node.id == "gate:kubernetes-precedence"
    )
    assert gate.status == "active"


@pytest.mark.asyncio
async def test_evaluator_fails_closed_when_envelope_integrity_breaks():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=service_incident(),
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )
    assert execution.shared_evidence.evidence

    execution.shared_evidence.evidence[0].facts["tampered"] = True
    evaluation = evaluate_shared_evidence(execution)

    assert evaluation.status == "inconclusive"
    assert evaluation.root_cause is None
    assert all(
        item.decision == "invalid_evidence"
        for item in evaluation.evaluated_hypotheses
    )
    assert any(
        node.id == "gate:evidence-integrity"
        for node in evaluation.explanation_graph.nodes
    )


class SingleTraceTools(TraceDemoTools):
    async def tempo_search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        return [
            {
                "traceID": "trace-1",
                "rootServiceName": "frontend",
                "rootTraceName": "POST /checkout",
                "_source_query": (
                    '{ resource.service.name = "frontend" && status = error }'
                ),
            }
        ]


@pytest.mark.asyncio
async def test_trace_only_candidate_needs_two_representative_traces():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        stateful_dependency_analysis_enabled=False,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=service_incident(),
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=SingleTraceTools(),
        settings=settings,
    )

    evaluation = evaluate_shared_evidence(execution)

    assert evaluation.status == "inconclusive"
    assert evaluation.root_cause is None
    trace = next(
        item
        for item in evaluation.evaluated_hypotheses
        if item.id == "trace-causal-propagation"
    )
    assert trace.status.value == "testing"
    assert trace.decision == "testing"


@pytest.mark.asyncio
async def test_investigate_endpoint_executes_evaluates_and_audits():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                repo=repo,
                tools=StatefulDemoTools("postgresql"),
            )
        )
    )
    principal = Principal(
        subject="investigator-a",
        tenant_id="tenant-a",
        roles=frozenset({"investigator"}),
        clusters=frozenset({"prod"}),
    )

    result = await investigate_agentically(
        incident=service_incident(),
        request=request,
        principal=principal,
    )

    assert result.evaluation.status == "resolved"
    assert result.evaluation.root_cause is not None
    assert repo._audit[-1].action == "planner.investigation_evaluated"
    assert repo._audit[-1].detail["plan_id"] == result.execution.plan.id


@pytest.mark.asyncio
async def test_investigate_endpoint_is_disabled_by_default():
    repo = MemoryRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(),
                repo=repo,
                tools=TraceDemoTools(),
            )
        )
    )
    principal = Principal(
        subject="investigator-a",
        tenant_id="tenant-a",
        roles=frozenset({"investigator"}),
        clusters=frozenset({"prod"}),
    )

    with pytest.raises(HTTPException) as exc:
        await investigate_agentically(
            incident=service_incident(),
            request=request,
            principal=principal,
        )
    assert exc.value.status_code == 404


class BrokenKubernetesStatefulTools(StatefulDemoTools):
    async def get_pod(self, namespace: str, pod: str):
        raise RuntimeError("kubernetes unavailable")


@pytest.mark.asyncio
async def test_downstream_promotion_is_blocked_when_kubernetes_context_failed():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=kubernetes_incident(),
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=BrokenKubernetesStatefulTools("postgresql"),
        settings=settings,
    )
    by_specialist = {
        result.specialist: result
        for result in execution.specialist_results
    }
    assert by_specialist["kubernetes"].status == "failed"
    assert by_specialist["traces"].status == "completed"
    assert by_specialist["database"].status == "completed"

    evaluation = evaluate_shared_evidence(execution)

    assert evaluation.status == "inconclusive"
    assert evaluation.root_cause is None
    assert any(
        item.decision == "blocked_by_incomplete_context"
        for item in evaluation.evaluated_hypotheses
    )
    assert any(
        node.id == "gate:specialist-context"
        for node in evaluation.explanation_graph.nodes
    )
