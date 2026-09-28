from __future__ import annotations

from app.agentic.models import (
    InvestigationPlan,
    PlannerGraphContext,
    SpecialistKind,
    SpecialistTask,
)
from app.awareness.topology import (
    AmbiguousServiceError,
    AwarenessEntityNotFoundError,
    build_service_topology,
)
from app.config import Settings
from app.schemas import IncidentInput


SPECIALIST_ALLOWLIST: tuple[SpecialistKind, ...] = (
    "kubernetes",
    "metrics",
    "logs",
    "traces",
    "database",
    "changes",
)


def _task_id(kind: SpecialistKind) -> str:
    return f"task-{kind}"


def _append_task(
    tasks: list[SpecialistTask],
    *,
    specialist: SpecialistKind,
    objective: str,
    rationale: str,
    max_queries: int,
    depends_on: list[str] | None = None,
    required_entity_ids: list[str] | None = None,
) -> None:
    if specialist not in SPECIALIST_ALLOWLIST:
        raise ValueError(f"unsupported specialist: {specialist}")
    if any(task.specialist == specialist for task in tasks):
        return
    tasks.append(SpecialistTask(
        id=_task_id(specialist),
        specialist=specialist,
        objective=objective,
        rationale=rationale,
        max_queries=max_queries,
        depends_on=list(depends_on or []),
        required_entity_ids=list(required_entity_ids or []),
    ))


async def _graph_context(
    repo,
    incident: IncidentInput,
    settings: Settings,
) -> PlannerGraphContext:
    if not incident.service:
        return PlannerGraphContext()

    try:
        projection = await build_service_topology(
            repo,
            tenant_id=incident.tenant_id,
            cluster=incident.cluster,
            service_name=incident.service,
            namespace=incident.namespace,
            requested_depth=1,
            max_depth=min(1, settings.awareness_max_traversal_depth),
            max_nodes=min(
                settings.agentic_max_context_nodes,
                settings.awareness_max_traversal_nodes,
            ),
            max_edges=settings.awareness_max_traversal_edges,
            stale_after_minutes=settings.awareness_stale_after_minutes,
        )
    except (AwarenessEntityNotFoundError, AmbiguousServiceError):
        return PlannerGraphContext()

    return PlannerGraphContext(
        root_entity_id=projection.root.id,
        entity_ids=[item.id for item in projection.entities],
        relationship_ids=[
            item.id for item in projection.relationships
        ],
        truncated=projection.truncated,
    )


async def build_investigation_plan(
    repo,
    *,
    incident: IncidentInput,
    settings: Settings,
) -> InvestigationPlan:
    context = await _graph_context(repo, incident, settings)
    tasks: list[SpecialistTask] = []
    context_ids = context.entity_ids[: settings.agentic_max_context_nodes]

    if incident.node or (incident.namespace and incident.pod):
        _append_task(
            tasks,
            specialist="kubernetes",
            objective="Validate runtime, ownership, scheduling, controller and node signals.",
            rationale=(
                "The incident identifies Kubernetes-scoped runtime resources."
            ),
            max_queries=min(12, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
        )

    _append_task(
        tasks,
        specialist="metrics",
        objective="Measure current and near-incident RED/resource signals.",
        rationale=(
            "Metrics provide independent quantitative evidence for every investigation."
        ),
        max_queries=min(12, settings.agentic_specialist_max_queries),
        required_entity_ids=context_ids,
    )

    if incident.namespace and (incident.pod or incident.service):
        _append_task(
            tasks,
            specialist="logs",
            objective="Search bounded log windows for error signatures and runtime context.",
            rationale=(
                "The incident has enough namespace/resource identity for scoped log queries."
            ),
            max_queries=min(8, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
        )

    if incident.service and settings.trace_analysis_enabled:
        _append_task(
            tasks,
            specialist="traces",
            objective="Reconstruct distributed causal paths from representative traces.",
            rationale=(
                "A service identity is present and trace analysis is enabled."
            ),
            max_queries=min(10, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
        )

    trace_task_id = (
        _task_id("traces")
        if any(task.specialist == "traces" for task in tasks)
        else None
    )
    if (
        incident.service
        and settings.stateful_dependency_analysis_enabled
        and trace_task_id is not None
    ):
        _append_task(
            tasks,
            specialist="database",
            objective=(
                "Validate typed PostgreSQL/Kafka/Redis dependency anomalies "
                "using current telemetry."
            ),
            rationale=(
                "Stateful dependency analysis is enabled and the trace branch "
                "provides typed dependency identity before database checks."
            ),
            max_queries=min(10, settings.agentic_specialist_max_queries),
            depends_on=[trace_task_id],
            required_entity_ids=context_ids,
        )

    if incident.service or incident.pod:
        _append_task(
            tasks,
            specialist="changes",
            objective=(
                "Correlate deployed revision, Git metadata and recent operational changes."
            ),
            rationale=(
                "The incident identifies a workload/service that can be correlated "
                "with deployment history."
            ),
            max_queries=min(6, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
        )

    tasks = tasks[: settings.agentic_max_tasks]
    valid_ids = {task.id for task in tasks}
    normalized_tasks = [
        task.model_copy(update={
            "depends_on": [
                dependency
                for dependency in task.depends_on
                if dependency in valid_ids
            ]
        })
        for task in tasks
    ]

    return InvestigationPlan(
        tenant_id=incident.tenant_id,
        cluster=incident.cluster,
        incident=incident,
        graph_context=context,
        tasks=normalized_tasks,
        max_parallel_tasks=min(
            settings.agentic_max_parallel_tasks,
            max(1, len(normalized_tasks)),
        ),
        max_tasks=settings.agentic_max_tasks,
    )
