from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.agentic.models import (
    PlannerGraphContext,
    SpecialistScope,
    SpecialistTask,
)
from app.agentic.planner import build_investigation_plan
from app.agentic.specialists import execute_specialist
from app.config import Settings
from app.db.repository import MemoryRepository
from app.schemas import (
    Entity,
    GraphEdge,
    IncidentInput,
    InvestigationResult,
    RootCause,
)


def _topology_result(
    now: datetime,
    *,
    second_pod: bool = False,
    second_application: bool = False,
) -> InvestigationResult:
    entities = [
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
        Entity(
            id="replicaset",
            kind="ReplicaSet",
            name="checkout-rs",
            namespace="payments",
        ),
        Entity(
            id="pod-a",
            kind="Pod",
            name="checkout-rs-abc",
            namespace="payments",
        ),
        Entity(
            id="argo-a",
            kind="ArgoCDApplication",
            name="checkout-prod",
            namespace="payments",
        ),
    ]
    edges = [
        GraphEdge(
            source="deployment",
            target="service",
            relationship="serves",
        ),
        GraphEdge(
            source="replicaset",
            target="deployment",
            relationship="owned_by",
        ),
        GraphEdge(
            source="pod-a",
            target="replicaset",
            relationship="owned_by",
        ),
        GraphEdge(
            source="deployment",
            target="argo-a",
            relationship="managed_by",
        ),
    ]
    if second_pod:
        entities.append(Entity(
            id="pod-b",
            kind="Pod",
            name="checkout-rs-def",
            namespace="payments",
        ))
        edges.append(GraphEdge(
            source="pod-b",
            target="replicaset",
            relationship="owned_by",
        ))
    if second_application:
        entities.append(Entity(
            id="argo-b",
            kind="ArgoCDApplication",
            name="checkout-secondary",
            namespace="payments",
        ))
        edges.append(GraphEdge(
            source="deployment",
            target="argo-b",
            relationship="managed_by",
        ))

    return InvestigationResult(
        incident_id="inc-specialist-scope",
        status="resolved",
        incident=IncidentInput(
            title="Historical checkout topology",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
            starts_at=now,
        ),
        entities=entities,
        graph_edges=edges,
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


def _incident(now: datetime) -> IncidentInput:
    return IncidentInput(
        title="Checkout latency",
        tenant_id="tenant-a",
        cluster="prod",
        namespace="payments",
        service="checkout",
        alert_name="HighLatency",
        starts_at=now,
    )


@pytest.mark.asyncio
async def test_unique_fresh_graph_targets_scope_kubernetes_logs_and_changes():
    now = datetime.now(timezone.utc)
    repo = MemoryRepository()
    await repo.save(_topology_result(now))
    settings = Settings(agentic_context_depth=3)

    plan = await build_investigation_plan(
        repo,
        incident=_incident(now),
        settings=settings,
    )

    by_kind = {task.specialist: task for task in plan.tasks}
    assert "kubernetes" in by_kind
    assert "logs" in by_kind

    kubernetes = by_kind["kubernetes"]
    logs = by_kind["logs"]
    changes = by_kind["changes"]

    assert kubernetes.scope.pod == "checkout-rs-abc"
    assert logs.scope.pod == "checkout-rs-abc"
    assert "pod" in kubernetes.scope.graph_resolved_fields
    assert "pod" in logs.scope.graph_resolved_fields

    assert changes.scope.application == "checkout-prod"
    assert "application" in changes.scope.graph_resolved_fields
    assert changes.scope.graph_ambiguous_fields == []


@pytest.mark.asyncio
async def test_multiple_fresh_pods_fail_closed_without_runtime_target_guess():
    now = datetime.now(timezone.utc)
    repo = MemoryRepository()
    await repo.save(_topology_result(now, second_pod=True))
    settings = Settings(agentic_context_depth=3)

    plan = await build_investigation_plan(
        repo,
        incident=_incident(now),
        settings=settings,
    )

    by_kind = {task.specialist: task for task in plan.tasks}
    assert "kubernetes" not in by_kind
    assert "logs" not in by_kind
    assert by_kind["metrics"].scope.pod is None
    assert "pod" in by_kind["metrics"].scope.graph_ambiguous_fields


@pytest.mark.asyncio
async def test_multiple_fresh_argocd_apps_fail_closed_for_live_app_lookup():
    now = datetime.now(timezone.utc)
    repo = MemoryRepository()
    await repo.save(
        _topology_result(now, second_application=True)
    )
    settings = Settings(agentic_context_depth=3)

    plan = await build_investigation_plan(
        repo,
        incident=_incident(now),
        settings=settings,
    )

    changes = next(
        task for task in plan.tasks if task.specialist == "changes"
    )
    assert changes.scope.application is None
    assert "application" in changes.scope.graph_ambiguous_fields
    assert "application" not in changes.scope.graph_resolved_fields


class RecordingLogTools:
    def __init__(self):
        self.query: str | None = None

    async def loki_query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ):
        self.query = query
        return {
            "data": {
                "result": [{
                    "stream": {
                        "namespace": "payments",
                        "pod": "checkout-rs-abc",
                    },
                    "values": [["1", "error"]],
                }]
            }
        }


@pytest.mark.asyncio
async def test_log_specialist_uses_typed_graph_resolved_pod_scope():
    tools = RecordingLogTools()
    task = SpecialistTask(
        id="task-logs",
        specialist="logs",
        objective="bounded logs",
        rationale="test",
        max_queries=2,
        scope=SpecialistScope(
            namespace="payments",
            service="checkout",
            pod="checkout-rs-abc",
            graph_resolved_fields=["pod"],
        ),
    )

    result = await execute_specialist(
        task,
        incident=IncidentInput(
            title="Checkout errors",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
        ),
        tools=tools,
        settings=Settings(),
        prior_results={},
        operational_context=PlannerGraphContext(),
    )

    assert result.status == "completed"
    assert tools.query is not None
    assert 'namespace="payments"' in tools.query
    assert 'pod="checkout-rs-abc"' in tools.query
    assert "scope:pod:checkout-rs-abc" in result.context_references


class RecordingChangeTools:
    def __init__(self):
        self.application: str | None = None

    async def argocd_application(self, app_name: str):
        self.application = app_name
        return {
            "status": {
                "sync": {
                    "status": "Synced",
                    "revision": "",
                },
                "health": {"status": "Healthy"},
            },
            "spec": {"source": {}},
        }


@pytest.mark.asyncio
async def test_changes_specialist_verifies_graph_resolved_application_live():
    tools = RecordingChangeTools()
    task = SpecialistTask(
        id="task-changes",
        specialist="changes",
        objective="verify change",
        rationale="test",
        max_queries=2,
        scope=SpecialistScope(
            namespace="payments",
            service="checkout",
            application="checkout-prod",
            graph_resolved_fields=["application"],
        ),
    )

    result = await execute_specialist(
        task,
        incident=IncidentInput(
            title="Checkout errors",
            tenant_id="tenant-a",
            cluster="prod",
            namespace="payments",
            service="checkout",
        ),
        tools=tools,
        settings=Settings(),
        prior_results={},
        operational_context=PlannerGraphContext(),
    )

    assert result.status == "completed"
    assert tools.application == "checkout-prod"
    assert "scope:application:checkout-prod" in result.context_references
