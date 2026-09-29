from datetime import datetime, timezone

import pytest

from app.agentic.models import ModelPlanningAdvice
from app.agentic.planner import build_investigation_plan
from app.config import Settings
from app.db.repository import MemoryRepository
from app.schemas import IncidentInput


class FakeAdvisor:
    def __init__(self, advice: ModelPlanningAdvice | None):
        self.advice = advice
        self.calls = 0

    async def advise(self, incident, graph_context, eligible_tasks):
        self.calls += 1
        return self.advice


class FailingAdvisor:
    async def advise(self, incident, graph_context, eligible_tasks):
        raise RuntimeError("simulated advisor failure")


def incident() -> IncidentInput:
    return IncidentInput(
        title="Checkout latency",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        pod="checkout-abc",
        service="checkout",
        alert_name="HighLatency",
        starts_at=datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_model_advice_is_compiled_through_allowlist_and_server_templates():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        agentic_max_tasks=6,
        agentic_specialist_max_queries=9,
        agentic_kubernetes_max_queries=17,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=[
            "changes",
            "database",
            "shell",
            "logs",
        ],
        specialist_reasons={
            "changes": "A deploy may have changed behavior.",
            "database": "The service has stateful dependencies.",
            "shell": "Run arbitrary commands.",
        },
        rationale="Prioritize change and stateful branches.",
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert advisor.calls == 1
    assert plan.planner_origin == "model-assisted"
    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
        "traces",
        "changes",
        "database",
        "logs",
    ]
    assert plan.advisory_trace is not None
    assert plan.advisory_trace.requested_specialists == [
        "changes",
        "database",
        "shell",
        "logs",
    ]
    assert plan.advisory_trace.accepted_specialists == [
        "changes",
        "database",
        "logs",
    ]
    assert plan.advisory_trace.rejected_specialists == ["shell"]
    assert plan.advisory_trace.forced_specialists == [
        "kubernetes",
        "metrics",
        "traces",
    ]

    database = next(
        task for task in plan.tasks if task.specialist == "database"
    )
    assert database.depends_on == ["task-traces"]
    assert "Run arbitrary commands." not in {
        task.rationale for task in plan.tasks
    }

    kubernetes = next(
        task for task in plan.tasks if task.specialist == "kubernetes"
    )
    assert kubernetes.max_queries == 17
    assert all(
        task.max_queries <= 9
        for task in plan.tasks
        if task.specialist != "kubernetes"
    )
    assert all(task.read_only is True for task in plan.tasks)


@pytest.mark.asyncio
async def test_invalid_model_advice_falls_back_to_full_deterministic_plan():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        agentic_max_tasks=6,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=["shell", "cloud", "kubectl-exec"],
        rationale="Unsafe and unknown branches.",
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert plan.planner_origin == "deterministic"
    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
        "logs",
        "traces",
        "database",
        "changes",
    ]
    assert plan.advisory_trace is not None
    assert plan.advisory_trace.accepted_specialists == []
    assert plan.advisory_trace.rejected_specialists == [
        "cloud",
        "kubectl-exec",
        "shell",
    ]
    assert plan.advisory_trace.fallback_reason


@pytest.mark.asyncio
async def test_model_cannot_make_ineligible_database_branch_executable():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        trace_analysis_enabled=False,
        agentic_max_tasks=6,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=["database", "changes"],
        rationale="Try stateful analysis without traces.",
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert plan.planner_origin == "model-assisted"
    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
        "changes",
    ]
    assert plan.advisory_trace is not None
    assert "database" in plan.advisory_trace.rejected_specialists
    assert "traces" not in {task.specialist for task in plan.tasks}


@pytest.mark.asyncio
async def test_advisor_failure_preserves_deterministic_plan():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        agentic_max_tasks=6,
    )

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=FailingAdvisor(),
    )

    assert plan.planner_origin == "deterministic"
    assert len(plan.tasks) == 6
    assert plan.advisory_trace is not None
    assert "RuntimeError" in (plan.advisory_trace.fallback_reason or "")


@pytest.mark.asyncio
async def test_model_advisor_is_not_called_when_feature_is_disabled():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=False,
        agentic_max_tasks=6,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=["changes"],
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert advisor.calls == 0
    assert plan.planner_origin == "deterministic"
    assert plan.advisory_trace is None


@pytest.mark.asyncio
async def test_model_path_falls_back_when_task_budget_cannot_fit_baseline():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        agentic_max_tasks=2,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=["changes"],
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert plan.planner_origin == "deterministic"
    assert [task.specialist for task in plan.tasks] == [
        "kubernetes",
        "metrics",
    ]
    assert plan.advisory_trace is not None
    assert "mandatory model-assisted baseline" in (
        plan.advisory_trace.fallback_reason or ""
    )


@pytest.mark.asyncio
async def test_compiler_caps_advice_even_for_nonstandard_advisor():
    repo = MemoryRepository()
    settings = Settings(
        agentic_model_planner_enabled=True,
        agentic_model_planner_max_specialists=2,
        agentic_max_tasks=6,
    )
    advisor = FakeAdvisor(ModelPlanningAdvice(
        requested_specialists=[
            "changes",
            "logs",
            "database",
            "shell",
        ],
    ))

    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
        advisor=advisor,
    )

    assert plan.planner_origin == "model-assisted"
    assert plan.advisory_trace is not None
    assert plan.advisory_trace.requested_specialists == [
        "changes",
        "logs",
    ]
    assert "database" in plan.advisory_trace.rejected_specialists
    assert "shell" in plan.advisory_trace.rejected_specialists
