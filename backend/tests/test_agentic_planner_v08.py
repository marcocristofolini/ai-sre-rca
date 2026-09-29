from datetime import datetime, timezone

import pytest

from app.agentic.planner import (
    SPECIALIST_ALLOWLIST,
    build_investigation_plan,
)
from app.config import Settings
from app.db.repository import MemoryRepository
from app.schemas import (
    Entity,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


def topology_result(now: datetime) -> InvestigationResult:
    return InvestigationResult(
        incident_id="inc-topology",
        status="resolved",
        incident=IncidentInput(
            title="Historical checkout incident",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
        ),
        entities=[
            Entity(
                id="frontend",
                kind="Service",
                name="frontend",
                namespace="payments",
            ),
            Entity(
                id="checkout",
                kind="Service",
                name="checkout",
                namespace="payments",
            ),
        ],
        graph_edges=[
            GraphEdge(
                source="frontend",
                target="checkout",
                relationship="calls",
            )
        ],
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="fixture",
            summary="fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=now,
    )


@pytest.mark.asyncio
async def test_planner_emits_allowlisted_read_only_specialists_with_graph_context():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc)
    await repo.save(topology_result(now))
    settings = Settings(
        agentic_max_tasks=6,
        agentic_max_parallel_tasks=4,
        agentic_specialist_max_queries=9,
        agentic_kubernetes_max_queries=17,
    )
    incident = IncidentInput(
        title="Checkout latency",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        pod="checkout-abc",
        service="checkout",
        alert_name="HighLatency",
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )

    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
        "logs",
        "traces",
        "database",
        "changes",
    ]
    assert all(task.specialist in SPECIALIST_ALLOWLIST for task in plan.tasks)
    assert all(task.read_only is True for task in plan.tasks)
    kubernetes = next(
        task for task in plan.tasks if task.specialist == "kubernetes"
    )
    assert kubernetes.max_queries == 17
    assert all(
        task.max_queries <= 9
        for task in plan.tasks
        if task.specialist != "kubernetes"
    )
    assert plan.max_parallel_tasks == 4
    assert plan.graph_context.root_entity_id is not None
    assert len(plan.graph_context.entity_ids) == 2
@pytest.mark.asyncio
async def test_database_specialist_depends_on_trace_identity():
    repo = MemoryRepository()
    settings = Settings()
    incident = IncidentInput(
        title="Checkout errors",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        service="checkout",
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )
    database = next(
        task for task in plan.tasks if task.specialist == "database"
    )
    assert database.depends_on == ["task-traces"]


@pytest.mark.asyncio
async def test_database_specialist_is_not_planned_without_trace_branch():
    repo = MemoryRepository()
    settings = Settings(trace_analysis_enabled=False)
    incident = IncidentInput(
        title="Checkout errors",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        service="checkout",
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )
    specialists = {task.specialist for task in plan.tasks}
    assert "traces" not in specialists
    assert "database" not in specialists
    assert "metrics" in specialists


@pytest.mark.asyncio
async def test_planner_without_service_context_stays_bounded_and_useful():
    repo = MemoryRepository()
    settings = Settings(
        agentic_max_tasks=3,
        agentic_max_parallel_tasks=2,
    )
    incident = IncidentInput(
        title="Node readiness degraded",
        tenant_id="tenant-a",
        cluster="prod",
        node="node-1",
        alert_name="NodeNotReady",
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )

    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
    ]
    assert plan.graph_context.root_entity_id is None
    assert plan.max_parallel_tasks == 2
    assert len(plan.tasks) <= settings.agentic_max_tasks
@pytest.mark.asyncio
async def test_planner_task_limit_drops_dependencies_that_were_trimmed():
    repo = MemoryRepository()
    settings = Settings(
        agentic_max_tasks=4,
        agentic_max_parallel_tasks=4,
    )
    incident = IncidentInput(
        title="Checkout latency",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        pod="checkout-abc",
        service="checkout",
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )

    assert len(plan.tasks) == 4
    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
        "logs",
        "traces",
    ]
    valid_ids = {task.id for task in plan.tasks}
    assert all(
        set(task.depends_on).issubset(valid_ids)
        for task in plan.tasks
    )


@pytest.mark.asyncio
async def test_global_principal_preserves_concrete_requested_tenant():
    from types import SimpleNamespace

    from app.api.planner import plan_investigation
    from app.auth import Principal

    repo = MemoryRepository()
    settings = Settings()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                repo=repo,
            )
        )
    )
    principal = Principal(
        subject="platform",
        tenant_id="*",
        roles=frozenset({"investigator"}),
        clusters=frozenset({"*"}),
    )
    incident = IncidentInput(
        title="Global scoped plan",
        tenant_id="tenant-a",
        cluster="prod",
        service="checkout",
    )

    plan = await plan_investigation(
        incident=incident,
        request=request,
        principal=principal,
    )

    assert plan.tenant_id == "tenant-a"
    assert plan.incident.tenant_id == "tenant-a"
    assert repo._audit[-1].tenant_id == "tenant-a"


@pytest.mark.asyncio
async def test_global_principal_cannot_plan_for_wildcard_tenant():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.planner import plan_investigation
    from app.auth import Principal

    repo = MemoryRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=Settings(),
                repo=repo,
            )
        )
    )
    principal = Principal(
        subject="platform",
        tenant_id="*",
        roles=frozenset({"investigator"}),
        clusters=frozenset({"*"}),
    )

    with pytest.raises(HTTPException) as exc:
        await plan_investigation(
            incident=IncidentInput(
                title="Invalid wildcard plan",
                tenant_id="*",
                cluster="prod",
            ),
            request=request,
            principal=principal,
        )
    assert exc.value.status_code == 422
