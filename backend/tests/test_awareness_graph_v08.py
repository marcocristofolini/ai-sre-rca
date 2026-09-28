from datetime import datetime, timedelta, timezone

import pytest

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
    tenant_id: str = "tenant-a",
    cluster: str = "prod",
    local_suffix: str = "a",
    created_at: datetime,
) -> InvestigationResult:
    pod_local = f"pod-{local_suffix}"
    deploy_local = f"deployment-{local_suffix}"
    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="Checkout incident",
            tenant_id=tenant_id,
            cluster=cluster,
            namespace="payments",
            pod="checkout-abc",
        ),
        entities=[
            Entity(
                id=pod_local,
                kind="Pod",
                name="checkout-abc",
                namespace="payments",
                metadata={"phase": "Running"},
            ),
            Entity(
                id=deploy_local,
                kind="Deployment",
                name="checkout",
                namespace="payments",
                metadata={"generation": local_suffix},
            ),
        ],
        graph_edges=[
            GraphEdge(
                source=pod_local,
                target=deploy_local,
                relationship="owned_by",
                evidence_ids=[f"ev-{incident_id}"],
            )
        ],
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Confirmed test cause",
            summary="Test fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )
@pytest.mark.asyncio
async def test_awareness_graph_accumulates_same_logical_entities_across_incidents():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)

    first = make_result("inc-1", local_suffix="a", created_at=now)
    second = make_result(
        "inc-2",
        local_suffix="b",
        created_at=now + timedelta(minutes=10),
    )
    await repo.save(first)
    await repo.save(second)

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    assert len(graph.entities) == 2
    assert len(graph.relationships) == 1

    deployment = next(item for item in graph.entities if item.kind == "Deployment")
    assert deployment.name == "checkout"
    assert deployment.observation_count == 2
    assert deployment.first_seen == now
    assert deployment.last_seen == now + timedelta(minutes=10)
    assert deployment.last_incident_id == "inc-2"
    assert deployment.metadata["generation"] == "b"

    relationship = graph.relationships[0]
    assert relationship.observation_count == 2
    assert relationship.evidence_ids == ["ev-inc-2"]

    relationship_history = await repo.get_awareness_history(
        relationship.id,
        "tenant-a",
        "prod",
    )
    assert [item.incident_id for item in relationship_history] == ["inc-2", "inc-1"]
    assert relationship_history[0].payload["evidence_ids"] == ["ev-inc-2"]
    assert relationship_history[1].payload["evidence_ids"] == ["ev-inc-1"]
@pytest.mark.asyncio
async def test_awareness_ingestion_is_idempotent_for_same_incident():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
    result = make_result("inc-repeat", created_at=now)

    await repo.save(result)
    await repo.save(result)

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    assert all(item.observation_count == 1 for item in graph.entities)
    assert graph.relationships[0].observation_count == 1

    deployment = next(item for item in graph.entities if item.kind == "Deployment")
    history = await repo.get_awareness_history(
        deployment.id,
        "tenant-a",
        "prod",
    )
    assert len(history) == 1
    assert history[0].incident_id == "inc-repeat"
@pytest.mark.asyncio
async def test_awareness_graph_is_tenant_and_cluster_scoped():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-a", created_at=now))
    await repo.save(
        make_result(
            "inc-b",
            tenant_id="tenant-b",
            local_suffix="b",
            created_at=now,
        )
    )
    await repo.save(
        make_result(
            "inc-c",
            cluster="staging",
            local_suffix="c",
            created_at=now,
        )
    )

    tenant_a = await repo.get_awareness_graph("tenant-a", "prod")
    tenant_b = await repo.get_awareness_graph("tenant-b", "prod")
    staging = await repo.get_awareness_graph("tenant-a", "staging")

    assert len(tenant_a.entities) == 2
    assert len(tenant_b.entities) == 2
    assert len(staging.entities) == 2
    assert {item.id for item in tenant_a.entities}.isdisjoint(
        {item.id for item in tenant_b.entities}
    )
@pytest.mark.asyncio
async def test_awareness_graph_filters_and_temporal_history():
    repo = MemoryRepository()
    now = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)
    await repo.save(make_result("inc-1", local_suffix="a", created_at=now))
    await repo.save(
        make_result(
            "inc-2",
            local_suffix="b",
            created_at=now + timedelta(minutes=5),
        )
    )

    deployments = await repo.get_awareness_graph(
        "tenant-a",
        "prod",
        namespace="payments",
        kind="deployment",
    )
    assert len(deployments.entities) == 1
    assert deployments.entities[0].kind == "Deployment"
    assert deployments.relationships == []

    deployment = deployments.entities[0]
    history = await repo.get_awareness_history(
        deployment.id,
        "tenant-a",
        "prod",
    )
    assert [item.incident_id for item in history] == ["inc-2", "inc-1"]
    assert all(item.target_type == "entity" for item in history)


def test_awareness_tenant_scope_rejects_cross_tenant_queries():
    from fastapi import HTTPException

    from app.api.awareness import _tenant_scope
    from app.auth import Principal

    tenant_principal = Principal(
        subject="viewer-a",
        tenant_id="tenant-a",
        roles=frozenset({"viewer"}),
        clusters=frozenset({"prod"}),
    )
    assert _tenant_scope(tenant_principal, None) == "tenant-a"
    assert _tenant_scope(tenant_principal, "tenant-a") == "tenant-a"
    with pytest.raises(HTTPException) as exc:
        _tenant_scope(tenant_principal, "tenant-b")
    assert exc.value.status_code == 403


def test_awareness_global_principal_requires_explicit_tenant():
    from fastapi import HTTPException

    from app.api.awareness import _tenant_scope
    from app.auth import Principal

    platform_principal = Principal(
        subject="platform-admin",
        tenant_id="*",
        roles=frozenset({"admin"}),
        clusters=frozenset({"*"}),
    )
    with pytest.raises(HTTPException) as exc:
        _tenant_scope(platform_principal, None)
    assert exc.value.status_code == 422
    assert _tenant_scope(platform_principal, "tenant-a") == "tenant-a"
