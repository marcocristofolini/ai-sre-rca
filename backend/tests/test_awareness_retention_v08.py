from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.awareness import router as awareness_router
from app.auth import Principal, get_principal
from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
    InventoryRelationship,
    InventoryResource,
    InventoryResourceRef,
    InventorySnapshotConflictError,
)
from app.config import Settings
from app.db.repository import MemoryRepository


def _snapshot(
    snapshot_id: str,
    observed_at: datetime,
    *,
    include_deployment: bool,
) -> AuthoritativeInventorySnapshot:
    service = InventoryResource(
        kind="Service",
        name="checkout",
        namespace="payments",
    )
    resources = [service]
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
        tenant_id="tenant-a",
        cluster="prod",
    )


@pytest.mark.asyncio
async def test_retention_preview_and_purge_preserve_explanation_minimums():
    repo = MemoryRepository()
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    as_of = start + timedelta(days=100)

    await repo.reconcile_authoritative_inventory(
        _snapshot("snap-create", start, include_deployment=True),
        actor="collector",
    )
    await repo.reconcile_authoritative_inventory(
        _snapshot(
            "snap-delete",
            start + timedelta(days=1),
            include_deployment=False,
        ),
        actor="collector",
    )

    before = await repo.get_awareness_graph("tenant-a", "prod")
    deployment = next(
        item for item in before.entities if item.kind == "Deployment"
    )

    preview = await repo.purge_awareness_retention(
        "tenant-a",
        "prod",
        observation_days=30,
        tombstone_days=30,
        actor="platform-admin",
        dry_run=True,
        as_of=as_of,
    )

    assert preview.dry_run is True
    assert preview.observations_eligible >= 3
    assert preview.observations_deleted == 0
    assert preview.latest_observations_preserved >= 3
    assert preview.inventory_receipts_preserved == 2
    assert preview.tombstoned_relationships_eligible == 1
    assert preview.tombstoned_relationships_deleted == 0
    assert preview.tombstoned_entities_eligible == 1
    assert preview.tombstoned_entities_deleted == 0
    assert not any(
        event.action == "awareness.retention.purge"
        for event in repo._audit
    )
    assert any(
        item.id == deployment.id
        for item in (await repo.get_awareness_graph(
            "tenant-a",
            "prod",
        )).entities
    )

    purged = await repo.purge_awareness_retention(
        "tenant-a",
        "prod",
        observation_days=30,
        tombstone_days=30,
        actor="platform-admin",
        dry_run=False,
        as_of=as_of,
    )

    assert purged.observations_deleted == preview.observations_eligible
    assert purged.tombstoned_relationships_deleted == 1
    assert purged.tombstoned_entities_deleted == 1

    after = await repo.get_awareness_graph("tenant-a", "prod")
    assert [item.kind for item in after.entities] == ["Service"]
    assert after.relationships == []

    history = await repo.get_awareness_history(
        deployment.id,
        "tenant-a",
        "prod",
    )
    assert len(history) == 1
    assert history[0].payload["action"] == "tombstoned"

    receipts = [
        item
        for item in repo._awareness_observations.values()
        if item.tenant_id == "tenant-a"
        and item.cluster == "prod"
        and item.target_type == "inventory_snapshot"
    ]
    assert len(receipts) == 2

    purge_audit = [
        event
        for event in repo._audit
        if event.action == "awareness.retention.purge"
    ]
    assert len(purge_audit) == 1
    assert purge_audit[0].actor == "platform-admin"

    with pytest.raises(InventorySnapshotConflictError):
        await repo.reconcile_authoritative_inventory(
            _snapshot(
                "snap-delete",
                start + timedelta(days=1),
                include_deployment=True,
            )
        )


def _api(principal: Principal) -> FastAPI:
    api = FastAPI()
    api.include_router(awareness_router)
    api.state.repo = MemoryRepository()
    api.state.settings = Settings(
        awareness_observation_retention_days=30,
        awareness_tombstone_retention_days=30,
    )

    async def principal_override():
        return principal

    api.dependency_overrides[get_principal] = principal_override
    return api


def test_retention_api_requires_admin_role():
    api = _api(Principal(
        subject="viewer",
        tenant_id="tenant-a",
        roles=frozenset({"viewer"}),
        clusters=frozenset({"prod"}),
    ))

    with TestClient(api) as client:
        response = client.post(
            "/api/v1/awareness/retention/purge",
            params={"cluster": "prod"},
        )

    assert response.status_code == 403


def test_retention_api_defaults_to_dry_run():
    api = _api(Principal(
        subject="platform-admin",
        tenant_id="tenant-a",
        roles=frozenset({"admin"}),
        clusters=frozenset({"prod"}),
    ))

    with TestClient(api) as client:
        response = client.post(
            "/api/v1/awareness/retention/purge",
            params={"cluster": "prod"},
        )

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    assert not any(
        event.action == "awareness.retention.purge"
        for event in api.state.repo._audit
    )
