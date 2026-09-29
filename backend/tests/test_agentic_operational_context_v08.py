from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
)
from app.config import Settings
from app.db.repository import MemoryRepository
from app.integrations.demo import StatefulDemoTools
from app.schemas import (
    Entity,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


def _persistent_fixture(now: datetime) -> InvestigationResult:
    change_at = now + timedelta(minutes=2)
    return InvestigationResult(
        incident_id="inc-operational-context",
        status="resolved",
        incident=IncidentInput(
            title="Historical checkout topology",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
            starts_at=now,
        ),
        entities=[
            Entity(
                id="checkout-service",
                kind="Service",
                name="checkout",
                namespace="payments",
            ),
            Entity(
                id="checkout-deployment",
                kind="Deployment",
                name="checkout",
                namespace="payments",
            ),
            Entity(
                id="checkout-change",
                kind="ChangeEvent",
                name="checkout@abc123",
                namespace="payments",
                metadata={
                    "temporal_role": "event",
                    "application": "checkout",
                    "revision": "abc123",
                    "service_name": "checkout",
                    "workload_kind": "Deployment",
                    "workload_name": "checkout",
                    "occurred_at": change_at.isoformat(),
                },
            ),
        ],
        graph_edges=[
            GraphEdge(
                source="checkout-deployment",
                target="checkout-service",
                relationship="serves",
            ),
            GraphEdge(
                source="checkout-change",
                target="checkout-service",
                relationship="targets_service",
                metadata={
                    "temporal_role": "event",
                    "occurred_at": change_at.isoformat(),
                },
            ),
        ],
        evidence=[],
        hypotheses=[],
        timeline=[],
        root_cause=RootCause(
            title="fixture",
            summary="fixture",
            evidence_strength="strong",
            supporting_signals=1,
            contradicting_signals=0,
        ),
        remediation=[],
        created_at=now,
    )


async def _repo_with_operational_context(
    now: datetime,
) -> MemoryRepository:
    repo = MemoryRepository()
    await repo.save(_persistent_fixture(now))
    await repo.reconcile_authoritative_inventory(
        AuthoritativeInventorySnapshot(
            snapshot_id="snap-remove-old-deployment",
            source="kubernetes",
            observed_at=now + timedelta(minutes=5),
            complete=True,
            coverage=InventoryCoverage(
                kinds=["Deployment"],
                namespaces=["payments"],
                relationship_types=["serves"],
            ),
            resources=[],
            relationships=[],
            tenant_id="tenant-a",
            cluster="prod",
        ),
        actor="collector",
    )
    return repo


def _incident(now: datetime) -> IncidentInput:
    return IncidentInput(
        title="Checkout latency",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        service="checkout",
        alert_name="HighLatency",
        starts_at=now + timedelta(minutes=6),
    )


@pytest.mark.asyncio
async def test_plan_freezes_current_history_tombstones_changes_and_health():
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    repo = await _repo_with_operational_context(now)
    settings = Settings(
        agentic_context_depth=2,
        agentic_context_max_history_targets=12,
        agentic_context_history_per_target=3,
        agentic_context_change_limit=10,
    )

    plan = await build_investigation_plan(
        repo,
        incident=_incident(now),
        settings=settings,
    )
    context = plan.graph_context

    assert context.root_entity_id is not None
    assert context.entities
    deployment = next(
        item for item in context.entities if item.kind == "Deployment"
    )
    change = next(
        item for item in context.entities if item.kind == "ChangeEvent"
    )
    assert deployment.freshness == "tombstoned"
    assert deployment.id not in context.entity_ids
    assert change.freshness == "historical"
    assert context.recent_changes
    assert context.recent_changes[0].revision == "abc123"
    assert any(
        item.target_id == deployment.id and item.action == "tombstoned"
        for item in context.observations
    )

    sources = {item.source: item for item in context.inventory_sources}
    assert sources["kubernetes"].freshness == "current"
    assert context.graph_health.entities.tombstoned >= 1
    assert context.graph_health.entities.historical >= 1
    assert context.graph_health.observations_total >= 1


@pytest.mark.asyncio
async def test_executor_shares_context_and_changes_specialist_consumes_events():
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    repo = await _repo_with_operational_context(now)
    settings = Settings(
        agentic_execution_enabled=True,
        query_budget_enabled=False,
        agentic_context_history_per_target=3,
    )
    plan = await build_investigation_plan(
        repo,
        incident=_incident(now),
        settings=settings,
    )

    execution = await execute_investigation_plan(
        plan,
        tools=StatefulDemoTools("postgresql"),
        settings=settings,
    )

    assert (
        execution.shared_evidence.operational_context
        == plan.graph_context
    )
    changes = next(
        item
        for item in execution.specialist_results
        if item.specialist == "changes"
    )
    persistent = next(
        item
        for item in changes.evidence
        if item.category == "agentic-persistent-change-context"
    )
    assert persistent.source == "awareness"
    assert persistent.facts["context_only"] is True
    assert persistent.facts["change_event_count"] == 1
    assert any(
        ref.startswith("change:")
        for ref in changes.context_references
    )
