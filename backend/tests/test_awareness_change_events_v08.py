from datetime import datetime, timedelta, timezone

import pytest

from app.awareness.topology import calculate_blast_radius
from app.db.repository import MemoryRepository
from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


OCCURRED = datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc)


def make_change_result(
    incident_id: str,
    *,
    tenant_id: str = "tenant-a",
    cluster: str = "prod",
    created_at: datetime,
    namespace: str = "payments",
    revision: str = "abc123",
    include_git: bool = True,
) -> InvestigationResult:
    evidence = [
        Evidence(
            id=f"ev-argo-{incident_id}",
            source="argocd",
            category="change-correlation",
            summary="Argo CD sync completed.",
            observed_at=OCCURRED,
            facts={
                "application": "checkout",
                "revision": revision,
                "repo_url": "https://github.com/acme/checkout",
                "sync_status": "Synced",
                "health": "Healthy",
                "operation_phase": "Succeeded",
                "delta_minutes": 5.0,
            },
            raw_reference="argocd://application/checkout",
            strength="medium",
        )
    ]
    if include_git:
        evidence.append(Evidence(
            id=f"ev-git-{incident_id}",
            source="github",
            category="source-change",
            summary="Git commit resolved.",
            observed_at=OCCURRED - timedelta(hours=1),
            facts={
                "sha": revision,
                "message": "Deploy checkout",
                "author": "SRE",
                "date": (OCCURRED - timedelta(hours=1)).isoformat(),
                "url": "https://github.com/acme/checkout/commit/abc123",
            },
            raw_reference="https://github.com/acme/checkout/commit/abc123",
            strength="weak",
        ))

    return InvestigationResult(
        incident_id=incident_id,
        status="resolved",
        incident=IncidentInput(
            title="Checkout incident",
            tenant_id=tenant_id,
            cluster=cluster,
            namespace=namespace,
            pod="checkout-abc",
            service="checkout",
            starts_at=OCCURRED + timedelta(minutes=5),
        ),
        entities=[
            Entity(
                id="deployment",
                kind="Deployment",
                name="checkout",
                namespace=namespace,
            ),
            Entity(
                id="service:checkout",
                kind="Service",
                name="checkout",
                namespace=namespace,
            ),
            Entity(
                id="argocd",
                kind="ArgoCDApplication",
                name="checkout",
                metadata={"revision": revision},
            ),
            Entity(
                id="git-repo",
                kind="GitRepository",
                name="https://github.com/acme/checkout",
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
                evidence_ids=[f"ev-argo-{incident_id}"],
            ),
            GraphEdge(
                source="argocd",
                target="git-repo",
                relationship="syncs_from",
                evidence_ids=[f"ev-git-{incident_id}"] if include_git else [],
            ),
        ],
        evidence=evidence,
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="Test cause",
            summary="fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_same_deployment_event_is_deduplicated_across_incidents():
    repo = MemoryRepository()
    await repo.save(make_change_result(
        "inc-1",
        created_at=OCCURRED + timedelta(minutes=10),
    ))
    await repo.save(make_change_result(
        "inc-2",
        created_at=OCCURRED + timedelta(minutes=20),
    ))

    events = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
        event_type="deployment_sync",
    )

    assert len(events) == 1
    event = events[0]
    assert event.revision == "abc123"
    assert event.occurred_at == OCCURRED
    assert event.observation_count == 2
    assert event.incident_ids == ["inc-1", "inc-2"]
    assert set(event.evidence_ids) == {
        "ev-argo-inc-1",
        "ev-argo-inc-2",
    }
    assert event.first_observed_at == OCCURRED + timedelta(minutes=10)
    assert event.last_observed_at == OCCURRED + timedelta(minutes=20)


@pytest.mark.asyncio
async def test_source_commit_is_first_class_and_linked_to_service_subject():
    repo = MemoryRepository()
    await repo.save(make_change_result(
        "inc-1",
        created_at=OCCURRED + timedelta(minutes=10),
    ))

    services = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        name="checkout",
        namespace="payments",
        kind="Service",
    )
    assert len(services) == 1

    events = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
        event_type="source_commit",
        subject_entity_id=services[0].id,
    )

    assert len(events) == 1
    assert events[0].source == "github"
    assert events[0].revision == "abc123"
    assert services[0].id in events[0].subject_entity_ids


@pytest.mark.asyncio
async def test_change_events_are_tenant_cluster_and_time_scoped():
    repo = MemoryRepository()
    await repo.save(make_change_result(
        "inc-a",
        created_at=OCCURRED + timedelta(minutes=10),
    ))
    await repo.save(make_change_result(
        "inc-b",
        tenant_id="tenant-b",
        created_at=OCCURRED + timedelta(minutes=10),
    ))
    await repo.save(make_change_result(
        "inc-c",
        cluster="staging",
        created_at=OCCURRED + timedelta(minutes=10),
    ))

    prod_a = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
        since=OCCURRED - timedelta(minutes=1),
        until=OCCURRED + timedelta(minutes=1),
    )
    prod_b = await repo.list_awareness_change_events(
        "tenant-b",
        "prod",
    )
    staging = await repo.list_awareness_change_events(
        "tenant-a",
        "staging",
    )

    assert len(prod_a) == 1
    assert prod_a[0].event_type == "deployment_sync"
    assert len(prod_b) == 2
    assert len(staging) == 2


@pytest.mark.asyncio
async def test_tampered_change_evidence_does_not_create_event():
    repo = MemoryRepository()
    result = make_change_result(
        "inc-tampered",
        created_at=OCCURRED + timedelta(minutes=10),
        include_git=False,
    )
    result.evidence[0].facts["revision"] = "tampered-after-hash"

    await repo.save(result)

    events = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
    )
    assert events == []


@pytest.mark.asyncio
async def test_change_events_do_not_become_blast_radius_edges():
    repo = MemoryRepository()
    await repo.save(make_change_result(
        "inc-1",
        created_at=OCCURRED + timedelta(minutes=10),
    ))

    git_repo = (
        await repo.find_awareness_entities(
            "tenant-a",
            "prod",
            kind="GitRepository",
        )
    )[0]

    blast = await calculate_blast_radius(
        repo,
        tenant_id="tenant-a",
        cluster="prod",
        source_id=git_repo.id,
        requested_depth=4,
        max_depth=4,
        max_nodes=50,
        max_edges=100,
        stale_after_minutes=1440,
        as_of=OCCURRED + timedelta(minutes=10),
    )

    # Existing syncs_from semantics may expose current topology impact, but
    # temporal change-event records themselves never add graph edges.
    event_ids = {
        event.id
        for event in await repo.list_awareness_change_events(
            "tenant-a",
            "prod",
        )
    }
    assert all(
        step.relationship_id not in event_ids
        for impact in blast.impacts
        for step in impact.path
    )


@pytest.mark.asyncio
async def test_change_event_save_is_idempotent_for_same_incident():
    repo = MemoryRepository()
    result = make_change_result(
        "inc-repeat",
        created_at=OCCURRED + timedelta(minutes=10),
    )

    await repo.save(result)
    await repo.save(result)

    events = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
    )
    assert len(events) == 2
    assert all(event.observation_count == 1 for event in events)
    assert all(event.incident_ids == ["inc-repeat"] for event in events)


@pytest.mark.asyncio
async def test_same_revision_at_different_sync_times_is_distinct_deployment_event():
    repo = MemoryRepository()
    first = make_change_result(
        "inc-first",
        created_at=OCCURRED + timedelta(minutes=10),
    )
    second = make_change_result(
        "inc-second",
        created_at=OCCURRED + timedelta(hours=2),
    )
    second.evidence[0] = Evidence(
        id="ev-argo-inc-second",
        source="argocd",
        category="change-correlation",
        summary="Later Argo CD sync completed.",
        observed_at=OCCURRED + timedelta(hours=1),
        facts={
            "application": "checkout",
            "revision": "abc123",
            "repo_url": "https://github.com/acme/checkout",
            "sync_status": "Synced",
            "health": "Healthy",
            "operation_phase": "Succeeded",
            "delta_minutes": 5.0,
        },
        raw_reference="argocd://application/checkout",
        strength="medium",
    )

    await repo.save(first)
    await repo.save(second)

    events = await repo.list_awareness_change_events(
        "tenant-a",
        "prod",
        event_type="deployment_sync",
    )
    assert len(events) == 2
    assert {event.occurred_at for event in events} == {
        OCCURRED,
        OCCURRED + timedelta(hours=1),
    }


@pytest.mark.asyncio
async def test_planner_context_includes_recent_service_change_events():
    from app.agentic.planner import build_investigation_plan
    from app.config import Settings

    repo = MemoryRepository()
    await repo.save(make_change_result(
        "inc-history",
        created_at=OCCURRED + timedelta(minutes=10),
    ))

    plan = await build_investigation_plan(
        repo,
        incident=IncidentInput(
            title="New checkout latency",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            pod="checkout-new",
            service="checkout",
            starts_at=OCCURRED + timedelta(minutes=15),
        ),
        settings=Settings(
            change_correlation_window_minutes=180,
        ),
    )

    assert plan.graph_context.root_entity_id is not None
    assert {
        item.event_type
        for item in plan.graph_context.recent_changes
    } == {"deployment_sync", "source_commit"}
    assert all(
        item.revision == "abc123"
        for item in plan.graph_context.recent_changes
    )


@pytest.mark.asyncio
async def test_changes_api_rejects_naive_time_filters():
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.awareness import list_change_events
    from app.auth import Principal

    repo = MemoryRepository()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(repo=repo)
        )
    )
    principal = Principal(
        subject="viewer-a",
        tenant_id="tenant-a",
        roles=frozenset({"viewer"}),
        clusters=frozenset({"prod"}),
    )

    with pytest.raises(HTTPException) as exc:
        await list_change_events(
            cluster="prod",
            request=request,
            since=datetime(2026, 9, 28, 20, 0),
            principal=principal,
        )
    assert exc.value.status_code == 422
    assert "timezone" in str(exc.value.detail)
