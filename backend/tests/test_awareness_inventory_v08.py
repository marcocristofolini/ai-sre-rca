from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.awareness.inventory import (
    AuthoritativeInventoryRequest,
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
    InventoryRelationship,
    InventoryResource,
    InventorySnapshotConflictError,
    InventoryResourceRef,
)
from app.awareness.topology import (
    AwarenessEntityNotFoundError,
    build_service_topology,
    calculate_blast_radius,
    reconcile_snapshot,
)
from app.db.repository import MemoryRepository
from app.schemas import (
    Entity,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


def make_result(
    incident_id: str,
    *,
    created_at: datetime,
    include_deployment: bool = True,
    include_database: bool = True,
) -> InvestigationResult:
    entities = [
        Entity(
            id="service",
            kind="Service",
            name="checkout",
            namespace="payments",
        ),
    ]
    edges: list[GraphEdge] = []
    if include_deployment:
        entities.append(Entity(
            id="deployment",
            kind="Deployment",
            name="checkout",
            namespace="payments",
            metadata={"generation": incident_id},
        ))
        edges.append(GraphEdge(
            source="deployment",
            target="service",
            relationship="serves",
        ))
    if include_database:
        entities.append(Entity(
            id="database",
            kind="Database",
            name="orders",
            namespace="payments",
        ))
        edges.append(GraphEdge(
            source="service",
            target="database",
            relationship="calls",
        ))

    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="Inventory reconciliation fixture",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
            alert_name="HighErrorRate",
        ),
        entities=entities,
        graph_edges=edges,
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Confirmed test cause",
            summary="Inventory reconciliation fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )


def authoritative_snapshot(
    snapshot_id: str,
    *,
    observed_at: datetime,
    include_deployment: bool,
    include_database: bool = False,
) -> AuthoritativeInventorySnapshot:
    resources = [
        InventoryResource(
            kind="Service",
            name="checkout",
            namespace="payments",
            metadata={"inventory": "kubernetes"},
        ),
    ]
    relationships: list[InventoryRelationship] = []
    kinds = ["Service", "Deployment"]

    if include_deployment:
        resources.append(InventoryResource(
            kind="Deployment",
            name="checkout",
            namespace="payments",
            metadata={"inventory": "kubernetes"},
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

    if include_database:
        kinds.append("Database")
        resources.append(InventoryResource(
            kind="Database",
            name="orders",
            namespace="payments",
        ))

    return AuthoritativeInventorySnapshot(
        snapshot_id=snapshot_id,
        source="kubernetes",
        observed_at=observed_at,
        complete=True,
        coverage=InventoryCoverage(
            kinds=kinds,
            namespaces=["payments"],
            relationship_types=["serves"],
        ),
        resources=resources,
        relationships=relationships,
        tenant_id="tenant-a",
        cluster="prod",
    )


def test_inventory_request_rejects_naive_authoritative_timestamp():
    with pytest.raises(ValidationError):
        AuthoritativeInventoryRequest(
            snapshot_id="snap-naive",
            source="kubernetes",
            observed_at=datetime(2026, 9, 29, 10, 0),
            complete=True,
            coverage=InventoryCoverage(
                kinds=["Service"],
                namespaces=["payments"],
            ),
            resources=[],
        )


def test_inventory_coverage_normalizes_declared_scope():
    coverage = InventoryCoverage(
        kinds=[" Service ", "Deployment"],
        namespaces=[" payments "],
        relationship_types=[" serves "],
    )

    assert coverage.kinds == ["Service", "Deployment"]
    assert coverage.namespaces == ["payments"]
    assert coverage.relationship_types == ["serves"]


def test_inventory_request_rejects_incomplete_snapshot():
    with pytest.raises(ValidationError):
        AuthoritativeInventoryRequest(
            snapshot_id="snap-incomplete",
            source="kubernetes",
            observed_at=datetime.now(timezone.utc),
            complete=False,
            coverage=InventoryCoverage(
                kinds=["Service"],
                namespaces=["payments"],
            ),
            resources=[],
        )


def test_inventory_request_rejects_resources_outside_declared_scope():
    with pytest.raises(ValidationError):
        AuthoritativeInventoryRequest(
            snapshot_id="snap-bad-scope",
            source="kubernetes",
            observed_at=datetime.now(timezone.utc),
            complete=True,
            coverage=InventoryCoverage(
                kinds=["Service"],
                namespaces=["payments"],
            ),
            resources=[
                InventoryResource(
                    kind="Deployment",
                    name="checkout",
                    namespace="payments",
                )
            ],
        )


@pytest.mark.asyncio
async def test_authoritative_snapshot_tombstones_missing_covered_entity_and_edge():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))

    result = await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-1",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    assert result.entities_tombstoned == 1
    assert result.relationships_tombstoned == 1

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    reconciled = reconcile_snapshot(
        graph,
        stale_after_minutes=60,
        as_of=now + timedelta(minutes=10),
    )
    deployment = next(
        item for item in reconciled.entities if item.kind == "Deployment"
    )
    serves = next(
        item
        for item in reconciled.relationships
        if item.relationship == "serves"
    )
    assert deployment.freshness == "tombstoned"
    assert deployment.tombstone_snapshot_id == "snap-1"
    assert deployment.tombstone_source == "kubernetes"
    assert serves.freshness == "tombstoned"

    current = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        name="checkout",
        kind="Deployment",
    )
    assert current == []

    history = await repo.get_awareness_history(
        deployment.id,
        "tenant-a",
        "prod",
    )
    assert history[0].payload["action"] == "tombstoned"
    assert history[0].payload["origin"] == "authoritative_inventory"


@pytest.mark.asyncio
async def test_inventory_scope_does_not_tombstone_uncovered_resource_kinds():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))

    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-1",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    database = next(item for item in graph.entities if item.kind == "Database")
    assert database.tombstoned_at is None


@pytest.mark.asyncio
async def test_older_authoritative_snapshot_cannot_tombstone_newer_graph_fact():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-newer",
        created_at=now + timedelta(minutes=20),
    ))

    result = await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-old",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    deployment = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="checkout",
            kind="Deployment",
        )
    )[0]
    assert deployment.tombstoned_at is None
    assert result.skipped_newer_entities >= 1


@pytest.mark.asyncio
async def test_newer_live_evidence_reactivates_tombstoned_entity_and_relationship():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))
    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-delete",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    await repo.save(make_result(
        "inc-returned",
        created_at=now + timedelta(minutes=20),
    ))

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    deployment = next(item for item in graph.entities if item.kind == "Deployment")
    serves = next(
        item
        for item in graph.relationships
        if item.relationship == "serves"
    )
    assert deployment.tombstoned_at is None
    assert deployment.tombstone_snapshot_id is None
    assert serves.tombstoned_at is None


@pytest.mark.asyncio
async def test_authoritative_snapshot_replay_is_idempotent():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))
    snapshot = authoritative_snapshot(
        "snap-repeat",
        observed_at=now + timedelta(minutes=10),
        include_deployment=False,
    )

    first = await repo.reconcile_authoritative_inventory(snapshot)
    graph_before = await repo.get_awareness_graph("tenant-a", "prod")
    second = await repo.reconcile_authoritative_inventory(snapshot)
    graph_after = await repo.get_awareness_graph("tenant-a", "prod")

    assert first.entities_tombstoned == 1
    assert second.entities_tombstoned == 0
    assert second.relationships_tombstoned == 0
    assert second.duplicate_observations >= 1
    assert [
        (item.id, item.observation_count, item.tombstoned_at)
        for item in graph_before.entities
    ] == [
        (item.id, item.observation_count, item.tombstoned_at)
        for item in graph_after.entities
    ]


@pytest.mark.asyncio
async def test_change_events_are_never_tombstoned_by_inventory_absence():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    result = make_result(
        "inc-event",
        created_at=now,
        include_deployment=False,
        include_database=False,
    )
    result.entities.append(Entity(
        id="change",
        kind="ChangeEvent",
        name="deploy-42",
        namespace="payments",
        metadata={
            "temporal_role": "event",
            "occurred_at": now.isoformat(),
        },
    ))
    await repo.save(result)

    snapshot = AuthoritativeInventorySnapshot(
        snapshot_id="snap-event",
        source="kubernetes",
        observed_at=now + timedelta(minutes=10),
        complete=True,
        coverage=InventoryCoverage(
            kinds=["ChangeEvent"],
            namespaces=["payments"],
        ),
        resources=[],
        tenant_id="tenant-a",
        cluster="prod",
    )
    reconciliation = await repo.reconcile_authoritative_inventory(snapshot)

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    event = next(item for item in graph.entities if item.kind == "ChangeEvent")
    assert event.tombstoned_at is None
    assert reconciliation.entities_tombstoned == 0


@pytest.mark.asyncio
async def test_blast_radius_rejects_tombstoned_source_and_edges():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))
    deployment = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="checkout",
            kind="Deployment",
        )
    )[0]

    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-delete",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    with pytest.raises(AwarenessEntityNotFoundError):
        await calculate_blast_radius(
            repo,
            tenant_id="tenant-a",
            cluster="prod",
            source_id=deployment.id,
            requested_depth=2,
            max_depth=4,
            max_nodes=20,
            max_edges=40,
            stale_after_minutes=60,
            as_of=now + timedelta(minutes=10),
        )



@pytest.mark.asyncio
async def test_snapshot_id_reuse_with_different_content_is_rejected():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))

    original = authoritative_snapshot(
        "snap-immutable",
        observed_at=now + timedelta(minutes=10),
        include_deployment=True,
    )
    await repo.reconcile_authoritative_inventory(original)

    conflicting = authoritative_snapshot(
        "snap-immutable",
        observed_at=now + timedelta(minutes=10),
        include_deployment=False,
    )
    with pytest.raises(InventorySnapshotConflictError):
        await repo.reconcile_authoritative_inventory(conflicting)



@pytest.mark.asyncio
async def test_long_snapshot_identity_stays_within_sql_incident_id_boundary():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))
    snapshot_id = "s" * 128

    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            snapshot_id,
            observed_at=now + timedelta(minutes=10),
            include_deployment=True,
        )
    )

    service = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="checkout",
            kind="Service",
        )
    )[0]
    history = await repo.get_awareness_history(
        service.id,
        "tenant-a",
        "prod",
    )
    inventory_observation = next(
        item
        for item in history
        if item.payload.get("origin") == "authoritative_inventory"
    )

    assert len(inventory_observation.incident_id) <= 64
    assert service.last_incident_id == inventory_observation.incident_id
    assert inventory_observation.payload["snapshot_id"] == snapshot_id



@pytest.mark.asyncio
async def test_current_service_projection_excludes_authoritative_tombstones():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))
    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-delete-deployment",
            observed_at=now + timedelta(minutes=10),
            include_deployment=False,
        )
    )

    historical = await build_service_topology(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        service_name="checkout",
        namespace="payments",
        requested_depth=1,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        include_tombstoned=True,
        as_of=now + timedelta(minutes=10),
    )
    current = await build_service_topology(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        service_name="checkout",
        namespace="payments",
        requested_depth=1,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        include_tombstoned=False,
        as_of=now + timedelta(minutes=10),
    )

    assert any(
        item.kind == "Deployment" and item.freshness == "tombstoned"
        for item in historical.entities
    )
    assert not any(item.kind == "Deployment" for item in current.entities)
    assert not any(
        item.relationship == "serves"
        for item in current.relationships
    )



@pytest.mark.asyncio
async def test_older_authoritative_presence_cannot_reactivate_newer_tombstone():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-initial", created_at=now))

    await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-delete-newer",
            observed_at=now + timedelta(minutes=20),
            include_deployment=False,
        )
    )
    replay = await repo.reconcile_authoritative_inventory(
        authoritative_snapshot(
            "snap-presence-older",
            observed_at=now + timedelta(minutes=10),
            include_deployment=True,
        )
    )

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    deployment = next(item for item in graph.entities if item.kind == "Deployment")
    serves = next(
        item
        for item in graph.relationships
        if item.relationship == "serves"
    )

    assert deployment.tombstoned_at == now + timedelta(minutes=20)
    assert serves.tombstoned_at == now + timedelta(minutes=20)
    assert replay.entities_reactivated == 0
    assert replay.relationships_reactivated == 0
    assert replay.skipped_newer_entities >= 1
    assert replay.skipped_newer_relationships >= 1
