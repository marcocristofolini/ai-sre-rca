from datetime import datetime, timedelta, timezone

import pytest

from app.awareness.topology import (
    AmbiguousServiceError,
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
    entities: list[Entity],
    edges: list[GraphEdge],
    tenant_id: str = "tenant-a",
    cluster: str = "prod",
) -> InvestigationResult:
    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="Topology fixture",
            tenant_id=tenant_id,
            cluster=cluster,
            namespace="payments",
            alert_name="HighErrorRate",
        ),
        entities=entities,
        graph_edges=edges,
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Confirmed test cause",
            summary="Topology fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )


def service_graph_entities() -> list[Entity]:
    return [
        Entity(
            id="frontend",
            kind="Service",
            name="frontend",
            namespace="payments",
        ),
        Entity(
            id="checkout",
            kind="Service",
            name="checkout",
            namespace="payments",
        ),
        Entity(
            id="postgres",
            kind="Database",
            name="orders",
            namespace="payments",
        ),
        Entity(
            id="deployment",
            kind="Deployment",
            name="checkout",
            namespace="payments",
        ),
    ]


def service_graph_edges() -> list[GraphEdge]:
    return [
        GraphEdge(
            source="frontend",
            target="checkout",
            relationship="calls",
        ),
        GraphEdge(
            source="checkout",
            target="postgres",
            relationship="calls",
        ),
        GraphEdge(
            source="deployment",
            target="checkout",
            relationship="serves",
        ),
    ]
@pytest.mark.asyncio
async def test_reconciliation_marks_old_observations_stale_without_tombstones():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-old",
        created_at=now - timedelta(hours=3),
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))
    await repo.save(make_result(
        "inc-fresh",
        created_at=now - timedelta(minutes=5),
        entities=[
            Entity(
                id="checkout-new",
                kind="Service",
                name="checkout",
                namespace="payments",
                metadata={"version": "v2"},
            )
        ],
        edges=[],
    ))

    snapshot = await repo.get_awareness_graph("tenant-a", "prod")
    reconciled = reconcile_snapshot(
        snapshot,
        stale_after_minutes=60,
        as_of=now,
    )
    by_name = {
        (item.kind, item.name): item
        for item in reconciled.entities
    }

    assert by_name[("Service", "checkout")].freshness == "fresh"
    assert by_name[("Service", "checkout")].metadata["version"] == "v2"
    assert by_name[("Service", "frontend")].freshness == "stale"
    assert by_name[("Service", "frontend")].stale_since is not None
    assert any(item.freshness == "stale" for item in reconciled.relationships)


@pytest.mark.asyncio
async def test_service_projection_builds_bounded_neighborhood():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-graph",
        created_at=now,
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))

    projection = await build_service_topology(
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
        as_of=now,
    )

    assert projection.root.kind == "Service"
    assert projection.root.name == "checkout"
    assert projection.root.depth == 0
    assert {item.name for item in projection.entities} == {
        "frontend",
        "checkout",
        "orders",
        "checkout",
    }
    assert len(projection.entities) == 4
    assert len(projection.relationships) == 3
    assert {item.depth for item in projection.entities} == {0, 1}
    assert projection.truncated is False
@pytest.mark.asyncio
async def test_service_projection_requires_namespace_when_name_is_ambiguous():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-payments",
        created_at=now,
        entities=[
            Entity(
                id="checkout-payments",
                kind="Service",
                name="checkout",
                namespace="payments",
            )
        ],
        edges=[],
    ))
    await repo.save(make_result(
        "inc-billing",
        created_at=now,
        entities=[
            Entity(
                id="checkout-billing",
                kind="Service",
                name="checkout",
                namespace="billing",
            )
        ],
        edges=[],
    ))

    with pytest.raises(AmbiguousServiceError):
        await build_service_topology(
            repo,
            tenant_id="tenant-a",
            cluster="prod",
            service_name="checkout",
            namespace=None,
            requested_depth=1,
            max_depth=4,
            max_nodes=20,
            max_edges=40,
            stale_after_minutes=60,
            as_of=now,
        )
@pytest.mark.asyncio
async def test_blast_radius_uses_explicit_causal_direction():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-graph",
        created_at=now,
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))
    postgres = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="orders",
            kind="Database",
        )
    )[0]

    result = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=postgres.id,
        requested_depth=3,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        as_of=now,
    )

    assert [item.entity.name for item in result.impacts] == [
        "checkout",
        "frontend",
    ]
    assert [item.depth for item in result.impacts] == [1, 2]
    assert result.traversed_relationship_types == ["calls"]
    assert result.truncated is False
    frontend = result.impacts[1]
    assert [step.relationship for step in frontend.path] == [
        "calls",
        "calls",
    ]
@pytest.mark.asyncio
async def test_blast_radius_serves_propagates_deployment_to_service_only():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-graph",
        created_at=now,
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))
    deployment = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="checkout",
            kind="Deployment",
        )
    )[0]

    result = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=deployment.id,
        requested_depth=3,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        as_of=now,
    )

    assert [item.entity.name for item in result.impacts] == [
        "checkout",
        "frontend",
    ]
    assert "serves" in result.traversed_relationship_types
    assert "calls" in result.traversed_relationship_types
    assert "orders" not in {item.entity.name for item in result.impacts}
@pytest.mark.asyncio
async def test_blast_radius_ignores_unknown_relationship_types():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-unknown",
        created_at=now,
        entities=[
            Entity(
                id="source",
                kind="External",
                name="source",
                namespace="payments",
            ),
            Entity(
                id="target",
                kind="Service",
                name="target",
                namespace="payments",
            ),
        ],
        edges=[
            GraphEdge(
                source="target",
                target="source",
                relationship="mirrors",
            )
        ],
    ))
    source = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="source",
        )
    )[0]

    result = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=source.id,
        requested_depth=2,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        as_of=now,
    )

    assert result.impacts == []
    assert result.ignored_relationship_types == ["mirrors"]
@pytest.mark.asyncio
async def test_blast_radius_truncates_on_node_budget():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-graph",
        created_at=now,
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))
    postgres = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="orders",
            kind="Database",
        )
    )[0]

    result = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=postgres.id,
        requested_depth=8,
        max_depth=4,
        max_nodes=2,
        max_edges=40,
        stale_after_minutes=60,
        as_of=now,
    )

    assert result.effective_depth == 4
    assert result.truncated is True
    assert [item.entity.name for item in result.impacts] == ["checkout"]


@pytest.mark.asyncio
async def test_service_projection_reports_edge_budget_truncation():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-wide",
        created_at=now,
        entities=[
            Entity(id="root", kind="Service", name="root", namespace="payments"),
            Entity(id="a", kind="Service", name="a", namespace="payments"),
            Entity(id="b", kind="Service", name="b", namespace="payments"),
            Entity(id="c", kind="Service", name="c", namespace="payments"),
        ],
        edges=[
            GraphEdge(source="a", target="root", relationship="calls"),
            GraphEdge(source="b", target="root", relationship="calls"),
            GraphEdge(source="c", target="root", relationship="calls"),
        ],
    ))

    projection = await build_service_topology(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        service_name="root",
        namespace="payments",
        requested_depth=1,
        max_depth=4,
        max_nodes=20,
        max_edges=2,
        stale_after_minutes=60,
        as_of=now,
    )

    assert projection.truncated is True
    assert len(projection.relationships) == 2


@pytest.mark.asyncio
async def test_blast_radius_does_not_propagate_through_stale_relationships():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 21, 0, tzinfo=timezone.utc)
    await repo.save(make_result(
        "inc-stale-path",
        created_at=now - timedelta(hours=3),
        entities=service_graph_entities(),
        edges=service_graph_edges(),
    ))
    postgres = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            name="orders",
            kind="Database",
        )
    )[0]

    result = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=postgres.id,
        requested_depth=3,
        max_depth=4,
        max_nodes=20,
        max_edges=40,
        stale_after_minutes=60,
        as_of=now,
    )

    assert result.impacts == []
    assert result.skipped_stale_relationship_ids
    assert result.traversed_relationship_types == []
