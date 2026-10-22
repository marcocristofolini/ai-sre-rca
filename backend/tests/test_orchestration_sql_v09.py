import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, update

from app.db.models import OrchestrationJobRecord
from app.db.repository import SQLRepository


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is required")


def job_record(tenant: str, key: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    job_id = f"job-{uuid4().hex}"
    return {
        "id": job_id, "tenant_id": tenant, "idempotency_key": key,
        "request_fingerprint": "a" * 64, "status": "queued", "cluster": "prod",
        "incident_id": f"inc-{uuid4().hex[:12]}", "actor": "test",
        "payload": {"incident": {"title": "test"}}, "result": None,
        "error": None, "error_type": None, "submitted_at": now.isoformat(),
        "started_at": None, "finished_at": None, "lease_owner": None,
        "lease_expires_at": None,
    }


@pytest.mark.asyncio
async def test_sql_jobs_persist_idempotently_and_are_tenant_scoped():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f"orch-{uuid4().hex}"
    record = job_record(tenant, "request-1")
    try:
        one, created = await repo.create_orchestration_job(record)
        two, duplicate_created = await repo.create_orchestration_job(record | {"id": f"job-{uuid4().hex}"})
        assert created and not duplicate_created and two["id"] == one["id"]
        assert await repo.get_orchestration_job(one["id"], tenant) is not None
        assert await repo.get_orchestration_job(one["id"], "another-tenant") is None
        restarted_repo = SQLRepository(TEST_DATABASE_URL)
        assert (await restarted_repo.get_orchestration_job(one["id"], tenant))["id"] == one["id"]
        await restarted_repo.close()
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(OrchestrationJobRecord).where(OrchestrationJobRecord.tenant_id == tenant))
            await session.commit()
        await repo.close()


@pytest.mark.asyncio
async def test_sql_claim_is_atomic_bounded_and_expired_leases_recover():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f"orch-{uuid4().hex}"
    records = [job_record(tenant), job_record(tenant)]
    try:
        for record in records:
            await repo.create_orchestration_job(record)
        claims = await asyncio.gather(*(
            repo.claim_orchestration_job(record["id"], f"worker-{i}", 30, 1)
            for i, record in enumerate(records)
        ))
        assert sum(claim is not None for claim in claims) == 1
        claimed = next(claim for claim in claims if claim is not None)
        other_id = next(record["id"] for record in records if record["id"] != claimed["id"])
        assert await repo.claim_orchestration_job(other_id, "worker-extra", 30, 1) is None
        async with repo.sessions() as session:
            await session.execute(update(OrchestrationJobRecord).where(
                OrchestrationJobRecord.id == claimed["id"],
            ).values(lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
            await session.commit()
        recoverable = await repo.recover_orchestration_jobs()
        assert set(recoverable) == {record["id"] for record in records}
        reclaimed = await repo.claim_orchestration_job(claimed["id"], "recovery-worker", 30, 1)
        assert reclaimed and reclaimed["lease_owner"] == "recovery-worker"
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(OrchestrationJobRecord).where(OrchestrationJobRecord.tenant_id == tenant))
            await session.commit()
        await repo.close()


@pytest.mark.asyncio
async def test_sql_duplicate_workers_cannot_claim_same_job_concurrently():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f"orch-{uuid4().hex}"
    record = job_record(tenant)
    try:
        await repo.create_orchestration_job(record)
        claims = await asyncio.gather(
            repo.claim_orchestration_job(record["id"], "worker-a", 30, 2),
            repo.claim_orchestration_job(record["id"], "worker-b", 30, 2),
        )
        assert sum(item is not None for item in claims) == 1
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(OrchestrationJobRecord).where(OrchestrationJobRecord.tenant_id == tenant))
            await session.commit()
        await repo.close()


@pytest.mark.asyncio
async def test_sql_job_lease_and_terminal_updates_accept_iso_timestamps():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f"orch-{uuid4().hex}"
    record = job_record(tenant)
    try:
        await repo.create_orchestration_job(record)
        claimed = await repo.claim_orchestration_job(record["id"], "worker", 30, 1)
        assert claimed is not None
        renewed_until = datetime.now(timezone.utc) + timedelta(minutes=2)
        assert await repo.update_orchestration_job(record["id"], {
            "lease_expires_at": renewed_until.isoformat(),
        }, owner="worker")
        finished_at = datetime.now(timezone.utc)
        assert await repo.update_orchestration_job(record["id"], {
            "status": "completed", "finished_at": finished_at.isoformat(),
            "lease_owner": None, "lease_expires_at": None,
        }, owner="worker")
        saved = await repo.get_orchestration_job(record["id"], tenant)
        assert saved["status"] == "completed"
        assert saved["finished_at"].replace(microsecond=0) == finished_at.replace(microsecond=0)
        assert saved["lease_expires_at"] is None
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(OrchestrationJobRecord).where(OrchestrationJobRecord.tenant_id == tenant))
            await session.commit()
        await repo.close()


@pytest.mark.asyncio
async def test_sql_progress_sequence_is_atomic_replayable_and_bounded():
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()
    tenant = f"orch-{uuid4().hex}"
    record = job_record(tenant)
    try:
        await repo.create_orchestration_job(record)
        await asyncio.gather(*(repo.append_orchestration_progress(
            record["id"], tenant, "specialist_update", {"index": index}, 7,
        ) for index in range(20)))
        events = await repo.list_orchestration_progress(record["id"], tenant, after=0, limit=100)
        assert [event["sequence"] for event in events] == list(range(14, 21))
        assert len({event["payload"]["index"] for event in events}) == 7
        assert await repo.list_orchestration_progress(record["id"], "other-tenant") == []
    finally:
        async with repo.sessions() as session:
            await session.execute(delete(OrchestrationJobRecord).where(OrchestrationJobRecord.tenant_id == tenant))
            await session.commit()
        await repo.close()
