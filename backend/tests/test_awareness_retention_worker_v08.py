from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.awareness.retention_worker import RETENTION_ACTOR, run_retention
from app.config import Settings
from app.db.repository import MemoryRepository


@pytest.mark.asyncio
async def test_retention_worker_requires_explicit_clusters():
    repo = MemoryRepository()
    settings = Settings(awareness_retention_clusters="")

    with pytest.raises(RuntimeError, match="AWARENESS_RETENTION_CLUSTERS"):
        await run_retention(repo, settings)


@pytest.mark.asyncio
async def test_retention_worker_runs_each_cluster_with_system_actor():
    repo = MemoryRepository()
    settings = Settings(
        awareness_retention_tenant_id="tenant-a",
        awareness_retention_clusters=" prod-a,prod-b,prod-a ",
        awareness_observation_retention_days=90,
        awareness_tombstone_retention_days=30,
    )

    results = await run_retention(
        repo,
        settings,
        as_of=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc),
    )

    assert [item.cluster for item in results] == ["prod-a", "prod-b"]
    assert all(item.dry_run is False for item in results)
    audits = [
        event
        for event in repo._audit
        if event.action == "awareness.retention.purge"
    ]
    assert len(audits) == 2
    assert {event.actor for event in audits} == {RETENTION_ACTOR}
