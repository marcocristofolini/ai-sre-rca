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
        deployment = InventoryResource(
            kind="Deployment",
            name="checkout",
            namespace="payments",
        )
        resources.append(deployment)
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
async def test_graph_health_counts_current_tombstoned_and_inventory_sources():
    repo = MemoryRepository()
    start = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)

    await repo.reconcile_authoritative_inventory(
        _snapshot("snap-initial", start, include_deployment=True),
        actor="collector",
    )
    await repo.reconcile_authoritative_inventory(
        _snapshot(
            "snap-delete-deployment",
            start + timedelta(minutes=10),
            include_deployment=False,
        ),
        actor="collector",
    )

    health = await repo.get_awareness_health(
        "tenant-a",
        "prod",
        stale_after_minutes=60,
        inventory_max_age_minutes=15,
        authoritative_sources=frozenset({"kubernetes", "cmdb"}),
        as_of=start + timedelta(minutes=20),
    )

    assert health.entities.total == 2
    assert health.entities.current == 1
    assert health.entities.stale == 0
    assert health.entities.historical == 0
    assert health.entities.tombstoned == 1
    assert health.relationships.total == 1
    assert health.relationships.tombstoned == 1
    assert health.observations_total >= 1
    assert health.latest_observation_at == start + timedelta(minutes=10)

    sources = {item.source: item for item in health.inventory_sources}
    assert sources["kubernetes"].freshness == "current"
    assert sources["kubernetes"].snapshot_id == "snap-delete-deployment"
    assert sources["kubernetes"].age_seconds == 600
    assert sources["cmdb"].freshness == "missing"


@pytest.mark.asyncio
async def test_graph_health_marks_old_authoritative_source_stale():
    repo = MemoryRepository()
    start = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
    await repo.reconcile_authoritative_inventory(
        _snapshot("snap-old", start, include_deployment=True),
        actor="collector",
    )

    health = await repo.get_awareness_health(
        "tenant-a",
        "prod",
        stale_after_minutes=60,
        inventory_max_age_minutes=15,
        authoritative_sources=frozenset({"kubernetes"}),
        as_of=start + timedelta(minutes=16),
    )

    assert health.inventory_sources[0].freshness == "stale"
    assert health.inventory_sources[0].age_seconds == 960
    assert health.entities.current == 2


def test_graph_health_api_is_viewer_scoped_and_reports_missing_source():
    api = FastAPI()
    api.include_router(awareness_router)
    api.state.repo = MemoryRepository()
    api.state.settings = Settings(
        awareness_authoritative_inventory_sources="kubernetes",
        awareness_inventory_max_age_minutes=15,
    )

    async def principal_override():
        return Principal(
            subject="viewer",
            tenant_id="tenant-a",
            roles=frozenset({"viewer"}),
            clusters=frozenset({"prod"}),
        )

    api.dependency_overrides[get_principal] = principal_override

    with TestClient(api) as client:
        response = client.get(
            "/api/v1/awareness/health",
            params={"cluster": "prod"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-a"
    assert body["cluster"] == "prod"
    assert body["entities"]["total"] == 0
    assert body["inventory_sources"] == [{
        "source": "kubernetes",
        "freshness": "missing",
        "snapshot_id": None,
        "observed_at": None,
        "age_seconds": None,
    }]
