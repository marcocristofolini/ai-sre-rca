from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import asyncio
from fastapi import HTTPException

from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.api.planner import (
    _run_orchestration_job,
    execute_agentic_investigation,
    get_orchestration_job,
    poll_orchestration_progress,
    stream_orchestration_progress,
    submit_orchestration_job,
)
from app.auth import Principal
from app.config import Settings
from app.db.repository import MemoryRepository
from app.integrations.demo import DemoTools, StatefulDemoTools
from app.schemas import IncidentInput
from app.agentic.models import OrchestrationJob


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


@pytest.mark.asyncio
async def test_orchestration_job_runs_and_polling_is_tenant_scoped():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=True, query_budget_enabled=False)
    state = SimpleNamespace(settings=settings, repo=repo, tools=DemoTools(), orchestration_worker_id="worker-test")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    principal = Principal(
        subject="investigator-a", tenant_id="tenant-a",
        roles=frozenset({"investigator"}), clusters=frozenset({"prod"}),
    )
    job = await submit_orchestration_job(
        incident=incident(), request=request, idempotency_key=None, principal=principal,
    )
    assert job.status == "queued"
    await _run_orchestration_job(request.app, job.id)
    completed = await get_orchestration_job(job.id, request, principal)
    assert completed.status == "completed"
    assert completed.result and completed.result.execution.shared_evidence.evidence
    assert repo._audit[0].action == "planner.orchestration_queued"
    progress = await repo.list_orchestration_progress(job.id, "tenant-a")
    assert [item["sequence"] for item in progress] == list(range(1, len(progress) + 1))
    assert {item["event_type"] for item in progress} >= {"queued", "started", "plan_ready", "specialist_update", "evaluation", "completed"}

    other_tenant = principal.__class__(
        subject="other", tenant_id="tenant-b",
        roles=frozenset({"investigator"}), clusters=frozenset({"prod"}),
    )
    with pytest.raises(HTTPException) as exc:
        await get_orchestration_job(job.id, request, other_tenant)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_orchestration_idempotency_is_tenant_scoped_and_capacity_bounded():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=True, orchestration_max_queued_jobs=1)
    state = SimpleNamespace(settings=settings, repo=repo, tools=DemoTools(), orchestration_worker_id="worker-test")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    principal = Principal(subject="investigator-a", tenant_id="tenant-a",
        roles=frozenset({"investigator"}), clusters=frozenset({"prod"}))
    first = await submit_orchestration_job(incident(), request,
        idempotency_key="retry-1", principal=principal)
    duplicate = await submit_orchestration_job(incident(), request,
        idempotency_key="retry-1", principal=principal)
    assert duplicate.id == first.id
    changed = incident().model_copy(update={"title": "different incident"})
    with pytest.raises(HTTPException) as conflict:
        await submit_orchestration_job(changed, request,
            idempotency_key="retry-1", principal=principal)
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await submit_orchestration_job(incident(), request,
            idempotency_key="retry-2", principal=principal)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_orchestration_failure_is_terminal_and_reports_error_type(monkeypatch):
    import app.api.planner as planner_api
    repo = MemoryRepository()
    state = SimpleNamespace(settings=Settings(agentic_execution_enabled=True), repo=repo,
        tools=DemoTools(), orchestration_worker_id="worker-test")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    principal = Principal(subject="investigator-a", tenant_id="tenant-a",
        roles=frozenset({"investigator"}), clusters=frozenset({"prod"}))
    async def fail(*args, **kwargs): raise RuntimeError("sensitive details")
    monkeypatch.setattr(planner_api, "build_investigation_plan", fail)
    job = await submit_orchestration_job(incident(), request,
        idempotency_key=None, principal=principal)
    await _run_orchestration_job(request.app, job.id)
    failed = await get_orchestration_job(job.id, request, principal)
    assert failed.status == "failed"
    assert failed.error_type == "RuntimeError"
    assert "sensitive details" not in failed.error
    assert failed.finished_at is not None


@pytest.mark.asyncio
async def test_synchronous_execute_api_remains_available():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=True, query_budget_enabled=False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, repo=repo, tools=DemoTools())))
    principal = Principal(subject="investigator-a", tenant_id="tenant-a",
        roles=frozenset({"investigator"}), clusters=frozenset({"prod"}))
    result = await execute_agentic_investigation(incident(), request, principal)
    assert result.status in {"completed", "partial", "failed"}


@pytest.mark.asyncio
async def test_orchestration_expired_lease_is_recovered_and_reclaimed():
    from datetime import timedelta
    repo = MemoryRepository()
    now = datetime.now(timezone.utc)
    job = OrchestrationJob(tenant_id="tenant-a", incident_id="inc-recover", cluster="prod")
    stored = job.model_dump(mode="json") | {
        "actor": "worker", "payload": {"incident": incident().model_dump(mode="json")},
        "submitted_at": now.isoformat(), "started_at": now.isoformat(),
        "finished_at": None, "lease_owner": "dead-worker",
        "lease_expires_at": (now-timedelta(seconds=1)).isoformat(),
    }
    await repo.create_orchestration_job(stored)
    assert await repo.recover_orchestration_jobs() == [job.id]
    claimed = await repo.claim_orchestration_job(job.id, "new-worker", 30, 1)
    assert claimed and claimed["lease_owner"] == "new-worker"


@pytest.mark.asyncio
async def test_orchestration_execution_semaphore_caps_concurrent_workers(monkeypatch):
    import app.api.planner as planner_api
    repo = MemoryRepository()
    jobs = []
    for i in range(4):
        job = OrchestrationJob(tenant_id="tenant-a", incident_id=f"inc-{i}", cluster="prod")
        await repo.create_orchestration_job(job.model_dump(mode="json") | {
            "actor": "test", "payload": {"incident": {}}, "submitted_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None, "finished_at": None, "lease_owner": None, "lease_expires_at": None,
        })
        jobs.append(job.id)
    state = SimpleNamespace(repo=repo, orchestration_worker_id="test-worker",
        settings=Settings(orchestration_max_concurrent_jobs=2, orchestration_job_lease_seconds=30))
    app = SimpleNamespace(state=state)
    running = peak = 0

    async def fake_execute(_app, stored, _owner):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1

    monkeypatch.setattr(planner_api, "_execute_orchestration_job", fake_execute)
    await asyncio.gather(*(planner_api._run_orchestration_job(app, key) for key in jobs))
    assert peak == 2


@pytest.mark.asyncio
async def test_progress_replay_is_ordered_and_scoped_by_tenant_and_cluster():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, repo=repo, tools=DemoTools(), orchestration_worker_id="worker-test")))
    owner = Principal(subject="owner", tenant_id="tenant-a", roles=frozenset({"investigator"}), clusters=frozenset({"prod"}))
    job = await submit_orchestration_job(incident(), request, principal=owner, idempotency_key=None)
    await repo.append_orchestration_progress(job.id, "tenant-a", "specialist_update", {"specialist": "metrics"}, 10)
    await repo.append_orchestration_progress(job.id, "tenant-a", "evaluation", {"status": "inconclusive"}, 10)
    await repo.update_orchestration_job(job.id, {"status": "completed", "finished_at": datetime.now(timezone.utc).isoformat()})

    replay = await poll_orchestration_progress(job.id, request, after=1, principal=owner)
    assert [event["sequence"] for event in replay["events"]] == [2, 3]
    response = await stream_orchestration_progress(job.id, request, after=0,
        last_event_id="1", principal=owner)
    chunks = [chunk async for chunk in response.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert "id: 2\nevent: specialist_update" in body
    assert "id: 3\nevent: evaluation" in body
    assert "id: 1\n" not in body

    for principal in (
        Principal(subject="other", tenant_id="tenant-b", roles=frozenset({"investigator"}), clusters=frozenset({"prod"})),
        Principal(subject="wrong-cluster", tenant_id="tenant-a", roles=frozenset({"investigator"}), clusters=frozenset({"staging"})),
    ):
        with pytest.raises(HTTPException) as exc:
            await stream_orchestration_progress(job.id, request, principal=principal)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_progress_event_retention_keeps_monotonic_sequence():
    repo = MemoryRepository()
    settings = Settings(agentic_execution_enabled=True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=settings, repo=repo, tools=DemoTools(), orchestration_worker_id="worker-test")))
    principal = Principal(subject="owner", tenant_id="tenant-a", roles=frozenset({"investigator"}), clusters=frozenset({"prod"}))
    job = await submit_orchestration_job(incident(), request, principal=principal, idempotency_key=None)
    for index in range(5):
        await repo.append_orchestration_progress(job.id, "tenant-a", "tick", {"index": index}, 3)
    events = await repo.list_orchestration_progress(job.id, "tenant-a", after=0)
    assert [event["sequence"] for event in events] == [4, 5, 6]
