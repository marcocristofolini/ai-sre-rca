from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.api.planner import execute_agentic_investigation
from app.auth import Principal
from app.config import Settings
from app.db.repository import MemoryRepository
from app.integrations.demo import DemoTools, StatefulDemoTools
from app.schemas import IncidentInput


def incident() -> IncidentInput:
    return IncidentInput(
        title="Frontend checkout failure",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="checkout",
        pod="checkout-abc",
        service="frontend",
        alert_name="HighErrorRate",
        starts_at=datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_parallel_executor_builds_shared_evidence_without_root_cause():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
    )

    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )

    by_specialist = {
        result.specialist: result
        for result in execution.specialist_results
    }
    assert execution.status == "completed"
    assert set(by_specialist) == {
        "kubernetes",
        "metrics",
        "logs",
        "traces",
        "database",
        "changes",
    }
    assert all(
        result.status == "completed"
        for result in execution.specialist_results
    )
    assert by_specialist["database"].stateful_dependencies
    assert by_specialist["database"].query_count >= 1
    assert by_specialist["database"].hypotheses_advisory_only is True
    assert execution.shared_evidence.evidence
    assert execution.shared_evidence.evidence_integrity_ok is True
    assert "root_cause" not in execution.model_dump()
@pytest.mark.asyncio
async def test_database_branch_is_skipped_when_trace_identity_is_missing():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
    )

    execution = await execute_investigation_plan(
        plan,
        tools=DemoTools(),
        settings=settings,
    )
    by_specialist = {
        result.specialist: result
        for result in execution.specialist_results
    }

    assert by_specialist["traces"].status == "skipped"
    assert by_specialist["database"].status == "skipped"
    assert "task-traces" in by_specialist["database"].reason
    assert execution.status == "partial"


@pytest.mark.asyncio
async def test_specialist_budget_exhaustion_blocks_dependent_database_branch():
    repo = MemoryRepository()
    settings = Settings(
        agentic_execution_enabled=True,
        agentic_specialist_max_queries=2,
        query_budget_enabled=False,
    )
    plan = await build_investigation_plan(
        repo,
        incident=incident(),
        settings=settings,
    )

    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )
    by_specialist = {
        result.specialist: result
        for result in execution.specialist_results
    }

    assert by_specialist["traces"].status == "budget_exhausted"
    assert by_specialist["traces"].query_count == 2
    assert by_specialist["database"].status == "skipped"
    assert execution.status == "partial"
@pytest.mark.asyncio
async def test_execute_endpoint_is_disabled_by_default():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=False)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                repo=repo,
                tools=DemoTools(),
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
        await execute_agentic_investigation(
            incident=incident(),
            request=request,
            principal=principal,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_execute_endpoint_recompiles_server_plan_and_audits_result():
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

    execution = await execute_agentic_investigation(
        incident=incident(),
        request=request,
        principal=principal,
    )

    assert execution.plan.tenant_id == "tenant-a"
    assert execution.plan.tasks
    assert repo._audit[-1].action == "planner.execution_completed"
    assert repo._audit[-1].detail["plan_id"] == execution.plan.id
    assert repo._audit[-1].detail["evidence_integrity_ok"] is True
