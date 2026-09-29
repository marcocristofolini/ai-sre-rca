from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
)
from app.awareness.topology import reconcile_snapshot
from app.db.repository import SQLRepository
from app.schemas import (
    Entity,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is required for PostgreSQL integration coverage",
)


def make_result(
    *,
    tenant_id: str,
    cluster: str,
    incident_id: str,
    created_at: datetime,
) -> InvestigationResult:
    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="SQL inventory reconciliation fixture",
            tenant_id=tenant_id,
            cluster=cluster,
            namespace="payments",
            service="checkout",
            alert_name="HighErrorRate",
        ),
        entities=[
            Entity(
                id="service",
                kind="Service",
                name="checkout",
                namespace="payments",
            ),
            Entity(
                id="deployment",
                kind="Deployment",
                name="checkout",
                namespace="payments",
            ),
        ],
        graph_edges=[
            GraphEdge(
                source="deployment",
                target="service",
                relationship="serves",
            )
        ],
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Confirmed SQL fixture cause",
            summary="SQL inventory reconciliation fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_postgres_inventory_reconciliation_is_idempotent_and_bounded():
    assert TEST_DATABASE_URL is not None
    repo = SQLRepository(TEST_DATABASE_URL)
    await repo.initialize()

    # Simulate a pre-tombstone persistent volume and verify the lightweight
    # forward migration restores both columns and their declared indexes.
    async with repo.engine.begin() as conn:
        for table in ("awareness_entities", "awareness_relationships"):
            for column in (
                "tombstoned_at",
                "tombstone_snapshot_id",
                "tombstone_source",
            ):
                await conn.execute(text(
                    f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column} CASCADE"
                ))
    await repo.initialize()

    async with repo.engine.begin() as conn:
        for table in ("awareness_entities", "awareness_relationships"):
            columns = {
                row[0]
                for row in (
                    await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table"
                    ), {"table": table})
                ).all()
            }
            assert {
                "tombstoned_at",
                "tombstone_snapshot_id",
                "tombstone_source",
            }.issubset(columns)
            index_names = {
                row[0]
                for row in (
                    await conn.execute(text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = :table"
                    ), {"table": table})
                ).all()
            }
            assert {
                f"ix_{table}_tombstoned_at",
                f"ix_{table}_tombstone_snapshot_id",
                f"ix_{table}_tombstone_source",
            }.issubset(index_names)

    suffix = uuid4().hex[:10]
    tenant_id = f"tenant-sql-{suffix}"
    cluster = f"prod-sql-{suffix}"
    now = datetime.now(timezone.utc).replace(microsecond=0)
    incident_id = f"inc-sql-{suffix}"

    try:
        await repo.save(
            make_result(
                tenant_id=tenant_id,
                cluster=cluster,
                incident_id=incident_id,
                created_at=now,
            )
        )

        snapshot_id = "s" * 128
        snapshot = AuthoritativeInventorySnapshot(
            snapshot_id=snapshot_id,
            source="kubernetes",
            observed_at=now + timedelta(minutes=10),
            complete=True,
            coverage=InventoryCoverage(
                kinds=["Deployment"],
                namespaces=["payments"],
                relationship_types=[],
            ),
            resources=[],
            relationships=[],
            tenant_id=tenant_id,
            cluster=cluster,
        )

        first, second = await asyncio.gather(
            repo.reconcile_authoritative_inventory(
                snapshot,
                actor="sql-admin-a",
            ),
            repo.reconcile_authoritative_inventory(
                snapshot,
                actor="sql-admin-b",
            ),
        )

        assert first.entities_tombstoned + second.entities_tombstoned == 1
        assert (
            first.relationships_tombstoned
            + second.relationships_tombstoned
            == 1
        )
        # The snapshot receipt itself provides replay identity. An empty
        # authoritative inventory does not necessarily create a duplicate
        # entity/relationship observation on the serialized retry.
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
        assert receipt_count == 1

        graph = await repo.get_awareness_graph(tenant_id, cluster)
        reconciled = reconcile_snapshot(
            graph,
            stale_after_minutes=60,
            as_of=now + timedelta(minutes=10),
        )
        deployment = next(
            item
            for item in reconciled.entities
            if item.kind == "Deployment"
        )
        serves = next(
            item
            for item in reconciled.relationships
            if item.relationship == "serves"
        )

        assert deployment.freshness == "tombstoned"
        assert serves.freshness == "tombstoned"
        assert deployment.tombstone_snapshot_id == snapshot_id

        limited = await repo.get_awareness_graph(
            tenant_id,
            cluster,
            limit=1,
        )
        assert len(limited.entities) == 1
        assert limited.entities[0].id == deployment.id

        history = await repo.get_awareness_history(
            deployment.id,
            tenant_id,
            cluster,
        )
        inventory_observation = next(
            item
            for item in history
            if item.payload.get("origin") == "authoritative_inventory"
        )
        assert len(inventory_observation.incident_id) <= 64
        assert inventory_observation.payload["snapshot_id"] == snapshot_id

        async with repo.engine.begin() as conn:
            audit_rows = (
                await conn.execute(
                    text(
                        "SELECT actor FROM audit_events "
                        "WHERE tenant_id = :tenant_id "
                        "AND action = 'awareness.inventory.reconcile'"
                    ),
                    {"tenant_id": tenant_id},
                )
            ).all()
        assert {row[0] for row in audit_rows} == {
            "sql-admin-a",
            "sql-admin-b",
        }

        health = await repo.get_awareness_health(
            tenant_id,
            cluster,
            stale_after_minutes=60,
            inventory_max_age_minutes=15,
            authoritative_sources=frozenset({"kubernetes", "cmdb"}),
            as_of=now + timedelta(minutes=11),
        )
        assert health.entities.total == 2
        assert health.entities.current == 1
        assert health.entities.tombstoned == 1
        assert health.relationships.total == 1
        assert health.relationships.tombstoned == 1
        sources = {item.source: item for item in health.inventory_sources}
        assert sources["kubernetes"].freshness == "current"
        assert sources["kubernetes"].snapshot_id == snapshot_id
        assert sources["cmdb"].freshness == "missing"
    finally:
        await repo.close()
