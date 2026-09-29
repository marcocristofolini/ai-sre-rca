from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
    InventoryRelationship,
    InventoryResource,
    InventoryResourceRef,
    InventorySnapshotConflictError,
)
from app.db.repository import SQLRepository


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def _snapshot(
    *,
    tenant_id: str,
    cluster: str,
    snapshot_id: str,
    observed_at: datetime,
    include_deployment: bool,
) -> AuthoritativeInventorySnapshot:
    resources = [
        InventoryResource(
            kind="Service",
            name="checkout",
            namespace="payments",
        )
    ]
    relationships: list[InventoryRelationship] = []
    if include_deployment:
        resources.append(InventoryResource(
            kind="Deployment",
            name="checkout",
            namespace="payments",
        ))
        relationships.append(InventoryRelationship(
            source=InventoryResourceRef(
                kind="Deployment",
                name="checkout",
                namespace="payments",
            ),
            target=InventoryResourceRef(
                kind="Service",
                name="checkout",
                namespace="payments",
            ),
            relationship="serves",
        ))
    return AuthoritativeInventorySnapshot(
        snapshot_id=snapshot_id,
        source="kubernetes",
        observed_at=observed_at,
        complete=True,
        coverage=InventoryCoverage(
            kinds=["Service", "Deployment"],
            namespaces=["payments"],
            relationship_types=["serves"],
        ),
        resources=resources,
        relationships=relationships,
        tenant_id=tenant_id,
        cluster=cluster,
    )


@pytest.mark.asyncio
async def test_postgres_awareness_retention_preserves_receipts_and_latest_state():
    assert TEST_DATABASE_URL is not None
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()

    suffix = uuid4().hex[:10]
    tenant_id = f"tenant-retention-{suffix}"
    cluster = f"prod-retention-{suffix}"
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    as_of = start + timedelta(days=100)

    create_snapshot = _snapshot(
        tenant_id=tenant_id,
        cluster=cluster,
        snapshot_id="snap-create",
        observed_at=start,
        include_deployment=True,
    )
    delete_snapshot = _snapshot(
        tenant_id=tenant_id,
        cluster=cluster,
        snapshot_id="snap-delete",
        observed_at=start + timedelta(days=1),
        include_deployment=False,
    )

    try:
        await repo.reconcile_authoritative_inventory(
            create_snapshot,
            actor="collector",
        )
        await repo.reconcile_authoritative_inventory(
            delete_snapshot,
            actor="collector",
        )

        before = await repo.get_awareness_graph(tenant_id, cluster)
        deployment = next(
            item for item in before.entities if item.kind == "Deployment"
        )

        preview = await repo.purge_awareness_retention(
            tenant_id,
            cluster,
            observation_days=30,
            tombstone_days=30,
            actor="sql-admin",
            dry_run=True,
            as_of=as_of,
        )
        assert preview.observations_eligible >= 3
        assert preview.observations_deleted == 0
        assert preview.inventory_receipts_preserved == 2
        assert preview.latest_observations_preserved >= 3
        assert preview.tombstoned_relationships_eligible == 1
        assert preview.tombstoned_entities_eligible == 1

        purged = await repo.purge_awareness_retention(
            tenant_id,
            cluster,
            observation_days=30,
            tombstone_days=30,
            actor="sql-admin",
            dry_run=False,
            as_of=as_of,
        )
        assert purged.observations_deleted == preview.observations_eligible
        assert purged.tombstoned_relationships_deleted == 1
        assert purged.tombstoned_entities_deleted == 1

        after = await repo.get_awareness_graph(tenant_id, cluster)
        assert [item.kind for item in after.entities] == ["Service"]
        assert after.relationships == []

        history = await repo.get_awareness_history(
            deployment.id,
            tenant_id,
            cluster,
        )
        assert len(history) == 1
        assert history[0].payload["action"] == "tombstoned"

        async with repo.engine.begin() as conn:
            receipt_count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM awareness_observations "
                        "WHERE tenant_id = :tenant_id "
                        "AND cluster = :cluster "
                        "AND target_type = 'inventory_snapshot'"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "cluster": cluster,
                    },
                )
            ).scalar_one()
            purge_audit = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM audit_events "
                        "WHERE tenant_id = :tenant_id "
                        "AND action = 'awareness.retention.purge'"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).scalar_one()
        assert receipt_count == 2
        assert purge_audit == 1

        changed_replay = _snapshot(
            tenant_id=tenant_id,
            cluster=cluster,
            snapshot_id="snap-delete",
            observed_at=start + timedelta(days=1),
            include_deployment=True,
        )
        with pytest.raises(InventorySnapshotConflictError):
            await repo.reconcile_authoritative_inventory(changed_replay)
    finally:
        await repo.close()
