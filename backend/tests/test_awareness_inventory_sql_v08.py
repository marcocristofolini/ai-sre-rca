from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
    InventoryResource,
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
                kinds=["Service", "Deployment"],
                namespaces=["payments"],
                relationship_types=["serves"],
            ),
            resources=[
                InventoryResource(
                    kind="Service",
                    name="checkout",
                    namespace="payments",
                    metadata={"inventory": "kubernetes"},
                )
            ],
            relationships=[],
            tenant_id=tenant_id,
            cluster=cluster,
        )

        first, second = await asyncio.gather(
            repo.reconcile_authoritative_inventory(snapshot),
            repo.reconcile_authoritative_inventory(snapshot),
        )

        assert first.entities_tombstoned + second.entities_tombstoned == 1
        assert (
            first.relationships_tombstoned
            + second.relationships_tombstoned
            == 1
        )
        assert (
            first.duplicate_observations
            + second.duplicate_observations
            >= 1
        )

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

        service = next(
            item for item in graph.entities if item.kind == "Service"
        )
        history = await repo.get_awareness_history(
            service.id,
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
    finally:
        await repo.close()
