from datetime import datetime, timedelta, timezone

import pytest

from app.awareness.change_events import query_change_events
from app.awareness.topology import calculate_blast_radius, reconcile_snapshot
from app.db.repository import MemoryRepository
from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


def make_change_result(
    incident_id: str,
    *,
    revision: str,
    occurred_at: datetime,
    created_at: datetime,
    service_name: str = "checkout",
) -> InvestigationResult:
    repo_url = "https://github.com/example/checkout.git"
    argocd = Evidence(
        source="argocd",
        category="change-correlation",
        summary="Argo CD deployment observed.",
        observed_at=occurred_at,
        facts={
            "application": "checkout-prod",
            "revision": revision,
            "repo_url": repo_url,
            "sync_status": "Synced",
            "health": "Healthy",
            "operation_phase": "Succeeded",
            "finished_at": occurred_at.isoformat(),
            "delta_minutes": 5,
        },
        raw_reference="argocd://application/checkout-prod",
        strength="medium",
    )
    github = Evidence(
        source="github",
        category="source-change",
        summary="Git revision resolved.",
        observed_at=occurred_at - timedelta(minutes=1),
        facts={
            "sha": revision,
            "message": f"deploy {revision[:8]}",
            "author": "SRE",
            "date": (occurred_at - timedelta(minutes=1)).isoformat(),
            "url": f"https://github.com/example/checkout/commit/{revision}",
        },
        raw_reference=f"https://github.com/example/checkout/commit/{revision}",
        strength="weak",
    )
    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="Checkout incident",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service=service_name,
            pod="checkout-abc",
            starts_at=created_at,
        ),
        entities=[
            Entity(
                id="deployment",
                kind="Deployment",
                name="checkout",
                namespace="payments",
            ),
            Entity(
                id="service:checkout",
                kind="Service",
                name=service_name,
                namespace="payments",
            ),
            Entity(
                id="argocd",
                kind="ArgoCDApplication",
                name="checkout-prod",
                metadata={"revision": revision, "repo_url": repo_url},
            ),
            Entity(
                id="git-repo",
                kind="GitRepository",
                name=repo_url,
                metadata={"revision": revision},
            ),
        ],
        graph_edges=[
            GraphEdge(
                source="deployment",
                target="service:checkout",
                relationship="serves",
            ),
            GraphEdge(
                source="deployment",
                target="argocd",
                relationship="managed_by",
                evidence_ids=[argocd.id],
            ),
            GraphEdge(
                source="argocd",
                target="git-repo",
                relationship="syncs_from",
                evidence_ids=[github.id],
            ),
        ],
        evidence=[argocd, github],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Fixture root",
            summary="Fixture root",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_change_event_is_persisted_as_first_class_graph_node():
    repo = MemoryRepository()
    occurred_at = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    await repo.save(make_change_result(
        "inc-change-1",
        revision="abcdef1234567890",
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=5),
    ))

    events = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="ChangeEvent",
        limit=20,
    )
    assert len(events) == 1
    event = events[0]
    assert event.metadata["temporal_role"] == "event"
    assert event.metadata["application"] == "checkout-prod"
    assert event.metadata["revision"] == "abcdef1234567890"
    assert event.metadata["service_name"] == "checkout"
    assert event.metadata["workload_name"] == "checkout"
    assert event.metadata["commit_message"] == "deploy abcdef12"
    assert len(event.metadata["evidence_ids"]) == 2

    _, relationships = await repo.get_awareness_neighbors(
        {event.id},
        "tenant-a",
        "prod",
        limit=20,
    )
    assert {item.relationship for item in relationships} == {
        "observed_on",
        "targets",
        "targets_service",
        "revision_from",
    }
    assert all(
        item.metadata["temporal_role"] == "event"
        for item in relationships
    )


@pytest.mark.asyncio
async def test_same_change_observed_by_multiple_incidents_keeps_one_identity():
    repo = MemoryRepository()
    occurred_at = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    revision = "abcdef1234567890"
    await repo.save(make_change_result(
        "inc-change-1",
        revision=revision,
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=5),
    ))
    await repo.save(make_change_result(
        "inc-change-2",
        revision=revision,
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=20),
    ))

    events = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="ChangeEvent",
        limit=20,
    )
    assert len(events) == 1
    assert events[0].observation_count == 2
    assert events[0].last_incident_id == "inc-change-2"


@pytest.mark.asyncio
async def test_different_revision_creates_distinct_change_event():
    repo = MemoryRepository()
    occurred_at = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    await repo.save(make_change_result(
        "inc-a",
        revision="aaaaaaaaaaaaaaaa",
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=5),
    ))
    await repo.save(make_change_result(
        "inc-b",
        revision="bbbbbbbbbbbbbbbb",
        occurred_at=occurred_at + timedelta(hours=1),
        created_at=occurred_at + timedelta(hours=1, minutes=5),
    ))

    events = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="ChangeEvent",
        limit=20,
    )
    assert len(events) == 2
    assert len({item.id for item in events}) == 2


@pytest.mark.asyncio
async def test_change_event_reconciles_as_historical_not_stale():
    repo = MemoryRepository()
    occurred_at = datetime(2026, 9, 20, 1, 0, tzinfo=timezone.utc)
    as_of = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    await repo.save(make_change_result(
        "inc-old-change",
        revision="abcdef1234567890",
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=5),
    ))

    snapshot = await repo.get_awareness_graph(
        "tenant-a",
        "prod",
        kind="ChangeEvent",
    )
    reconciled = reconcile_snapshot(
        snapshot,
        stale_after_minutes=60,
        as_of=as_of,
    )
    assert len(reconciled.entities) == 1
    assert reconciled.entities[0].freshness == "historical"
    assert reconciled.entities[0].stale_since is None
    assert reconciled.entities[0].age_seconds > 8 * 24 * 3600


@pytest.mark.asyncio
async def test_change_query_filters_service_and_time_window():
    repo = MemoryRepository()
    base = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    await repo.save(make_change_result(
        "inc-old",
        revision="aaaaaaaaaaaaaaaa",
        occurred_at=base - timedelta(hours=5),
        created_at=base - timedelta(hours=4, minutes=55),
    ))
    await repo.save(make_change_result(
        "inc-new",
        revision="bbbbbbbbbbbbbbbb",
        occurred_at=base - timedelta(minutes=30),
        created_at=base - timedelta(minutes=25),
    ))

    events = await query_change_events(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        service_name="checkout",
        namespace="payments",
        since=base - timedelta(hours=1),
        until=base,
        limit=20,
    )
    assert len(events) == 1
    assert events[0].revision == "bbbbbbbbbbbbbbbb"
    assert events[0].occurred_at == base - timedelta(minutes=30)


@pytest.mark.asyncio
async def test_change_event_edges_never_become_blast_radius_paths():
    repo = MemoryRepository()
    now = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    await repo.save(make_change_result(
        "inc-change",
        revision="abcdef1234567890",
        occurred_at=now - timedelta(minutes=5),
        created_at=now,
    ))
    event = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            kind="ChangeEvent",
            limit=20,
        )
    )[0]

    blast = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=event.id,
        requested_depth=3,
        max_depth=4,
        max_nodes=50,
        max_edges=100,
        stale_after_minutes=60,
        as_of=now,
    )
    assert blast.impacts == []
    assert {
        "observed_on",
        "targets",
        "targets_service",
        "revision_from",
    }.issubset(set(blast.ignored_relationship_types))


@pytest.mark.asyncio
async def test_missing_argocd_finished_at_does_not_create_temporal_event():
    repo = MemoryRepository()
    occurred_at = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
    result = make_change_result(
        "inc-no-finished-at",
        revision="abcdef1234567890",
        occurred_at=occurred_at,
        created_at=occurred_at + timedelta(minutes=5),
    )
    argocd = next(
        item
        for item in result.evidence
        if item.source == "argocd"
    )
    argocd.facts["finished_at"] = None

    await repo.save(result)

    events = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="ChangeEvent",
        limit=20,
    )
    assert events == []
