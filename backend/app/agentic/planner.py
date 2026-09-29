from __future__ import annotations

from datetime import timedelta

from app.agentic.models import (
    InvestigationPlan,
    ModelPlanningAdvice,
    PlannerAdvisoryTrace,
    PlannerChangeContext,
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
from app.llm.planning import PlanningAdvisor
from app.schemas import IncidentInput


SPECIALIST_ALLOWLIST: tuple[SpecialistKind, ...] = (
    "kubernetes",
    "metrics",
    "logs",
    "traces",
    "database",
    "changes",
)

# These branches form the evidence baseline whenever they are deterministically
# eligible. Model advice may prioritize/prune optional branches, but it cannot
# suppress infrastructure, quantitative metrics or distributed causality.
MODEL_ASSISTED_BASELINE: tuple[SpecialistKind, ...] = (
    "kubernetes",
    "metrics",
    "traces",
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

    window = timedelta(
        minutes=settings.change_correlation_window_minutes
    )
    recent_events = await repo.list_awareness_change_events(
        incident.tenant_id,
        incident.cluster,
        namespace=incident.namespace,
        subject_entity_id=projection.root.id,
        since=incident.starts_at - window,
        until=incident.starts_at + window,
        limit=min(20, settings.agentic_max_context_nodes),
    )

    return PlannerGraphContext(
        root_entity_id=projection.root.id,
        entity_ids=[item.id for item in projection.entities],
        relationship_ids=[
            item.id for item in projection.relationships
        ],
        recent_changes=[
            PlannerChangeContext(
                id=item.id,
                event_type=item.event_type,
                source=item.source,
                occurred_at=item.occurred_at,
                revision=item.revision,
            )
            for item in recent_events
        ],
        truncated=(
            projection.truncated
            or len(recent_events) >= min(
                20,
                settings.agentic_max_context_nodes,
            )
        ),
    )


def _eligible_tasks(
    *,
    incident: IncidentInput,
    context: PlannerGraphContext,
    settings: Settings,
) -> list[SpecialistTask]:
    tasks: list[SpecialistTask] = []
    context_ids = context.entity_ids[: settings.agentic_max_context_nodes]

    if incident.node or (incident.namespace and incident.pod):
        _append_task(
            tasks,
            specialist="kubernetes",
            objective=(
                "Validate runtime, ownership, scheduling, controller and node signals."
            ),
            rationale=(
                "The incident identifies Kubernetes-scoped runtime resources."
            ),
            max_queries=settings.agentic_kubernetes_max_queries,
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
            objective=(
                "Search bounded log windows for error signatures and runtime context."
            ),
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
            objective=(
                "Reconstruct distributed causal paths from representative traces."
            ),
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

    return tasks


def _normalize_dependencies(
    tasks: list[SpecialistTask],
) -> list[SpecialistTask]:
    valid_ids = {task.id for task in tasks}
    return [
        task.model_copy(update={
            "depends_on": [
                dependency
                for dependency in task.depends_on
                if dependency in valid_ids
            ]
        })
        for task in tasks
    ]


def _compile_model_advice(
    eligible_tasks: list[SpecialistTask],
    advice: ModelPlanningAdvice,
    *,
    settings: Settings,
) -> tuple[list[SpecialistTask], PlannerAdvisoryTrace, bool]:
    by_kind = {task.specialist: task for task in eligible_tasks}
    all_requested = [
        str(item).strip().lower()
        for item in advice.requested_specialists
        if str(item).strip()
    ]
    requested = all_requested[
        : settings.agentic_model_planner_max_specialists
    ]
    overflow_requested = all_requested[
        settings.agentic_model_planner_max_specialists :
    ]

    forced: list[SpecialistKind] = [
        kind
        for kind in MODEL_ASSISTED_BASELINE
        if kind in by_kind
    ]
    accepted: list[SpecialistKind] = []
    rejected: list[str] = list(overflow_requested)
    seen: set[str] = set()

    if len(forced) > settings.agentic_max_tasks:
        return (
            _normalize_dependencies(
                eligible_tasks[: settings.agentic_max_tasks]
            ),
            PlannerAdvisoryTrace(
                requested_specialists=requested,
                accepted_specialists=[],
                rejected_specialists=sorted(set(rejected)),
                forced_specialists=[],
                rationale=advice.rationale,
                fallback_reason=(
                    "server task budget is smaller than the mandatory "
                    "model-assisted baseline; deterministic plan preserved"
                ),
            ),
            False,
        )

    for raw in requested:
        if raw in seen:
            continue
        seen.add(raw)
        if raw not in SPECIALIST_ALLOWLIST:
            rejected.append(raw)
            continue
        kind = raw
        if kind not in by_kind:
            rejected.append(raw)
            continue
        accepted.append(kind)

    if not accepted:
        return (
            _normalize_dependencies(
                eligible_tasks[: settings.agentic_max_tasks]
            ),
            PlannerAdvisoryTrace(
                requested_specialists=requested,
                accepted_specialists=[],
                rejected_specialists=sorted(set(rejected)),
                forced_specialists=[
                    kind
                    for kind in forced
                    if kind in by_kind
                ],
                rationale=advice.rationale,
                fallback_reason=(
                    "model advice contained no eligible specialist; "
                    "deterministic plan preserved"
                ),
            ),
            False,
        )

    # Database execution has a typed dependency on traces. The model cannot
    # remove or rewrite that dependency.
    if "database" in accepted and "traces" in by_kind and "traces" not in forced:
        forced.append("traces")

    selected_kinds: list[SpecialistKind] = []
    for kind in [*forced, *accepted]:
        if kind in by_kind and kind not in selected_kinds:
            selected_kinds.append(kind)

    selected_kinds = selected_kinds[: settings.agentic_max_tasks]
    selected_set = set(selected_kinds)

    # A task that was otherwise valid but did not fit the server-side task
    # budget is reported as rejected rather than silently disappearing.
    for kind in accepted:
        if kind not in selected_set:
            rejected.append(kind)

    selected_tasks = [
        by_kind[kind].model_copy(deep=True)
        for kind in selected_kinds
    ]
    selected_tasks = _normalize_dependencies(selected_tasks)

    # Fail closed if a dependency would be lost by a pathological task budget.
    selected_ids = {task.id for task in selected_tasks}
    filtered: list[SpecialistTask] = []
    for task in selected_tasks:
        if all(dependency in selected_ids for dependency in task.depends_on):
            filtered.append(task)
        else:
            rejected.append(task.specialist)

    filtered_kinds = {task.specialist for task in filtered}
    return (
        filtered,
        PlannerAdvisoryTrace(
            requested_specialists=requested,
            accepted_specialists=[
                kind
                for kind in accepted
                if kind in filtered_kinds
            ],
            rejected_specialists=sorted(set(rejected)),
            forced_specialists=[
                kind
                for kind in forced
                if kind in filtered_kinds
            ],
            rationale=advice.rationale,
        ),
        True,
    )


async def build_investigation_plan(
    repo,
    *,
    incident: IncidentInput,
    settings: Settings,
    advisor: PlanningAdvisor | None = None,
) -> InvestigationPlan:
    context = await _graph_context(repo, incident, settings)
    eligible = _eligible_tasks(
        incident=incident,
        context=context,
        settings=settings,
    )
    deterministic_tasks = _normalize_dependencies(
        eligible[: settings.agentic_max_tasks]
    )

    tasks = deterministic_tasks
    planner_origin: str = "deterministic"
    advisory_trace: PlannerAdvisoryTrace | None = None

    if settings.agentic_model_planner_enabled and advisor is not None:
        try:
            advice = await advisor.advise(
                incident,
                context,
                eligible,
            )
        except Exception as exc:
            advice = None
            advisory_trace = PlannerAdvisoryTrace(
                fallback_reason=(
                    "model planning advisor failed; deterministic plan preserved "
                    f"({type(exc).__name__})"
                )
            )

        if advice is not None:
            compiled, trace, used_model = _compile_model_advice(
                eligible,
                advice,
                settings=settings,
            )
            tasks = compiled
            advisory_trace = trace
            if used_model:
                planner_origin = "model-assisted"

    return InvestigationPlan(
        tenant_id=incident.tenant_id,
        cluster=incident.cluster,
        incident=incident,
        planner_origin=planner_origin,
        advisory_trace=advisory_trace,
        graph_context=context,
        tasks=tasks,
        max_parallel_tasks=min(
            settings.agentic_max_parallel_tasks,
            max(1, len(tasks)),
        ),
        max_tasks=settings.agentic_max_tasks,
    )
