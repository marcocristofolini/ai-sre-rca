from __future__ import annotations

from app.agentic.models import (
    InvestigationPlan,
    ModelPlanningAdvice,
    PlannerAdvisoryTrace,
    PlannerContextChange,
    PlannerContextEntity,
    PlannerContextObservation,
    PlannerContextRelationship,
    PlannerGraphContext,
    PlannerGraphHealthContext,
    PlannerInventorySourceContext,
    PlannerStateCounts,
    SpecialistKind,
    SpecialistScope,
    SpecialistTask,
)
from app.awareness.change_events import query_change_events
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
    scope: SpecialistScope | None = None,
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
        scope=(
            scope.model_copy(deep=True)
            if scope is not None
            else SpecialistScope()
        ),
    ))


def _state_counts(value) -> PlannerStateCounts:
    return PlannerStateCounts(
        total=value.total,
        current=value.current,
        stale=value.stale,
        historical=value.historical,
        tombstoned=value.tombstoned,
    )


async def _graph_context(
    repo,
    incident: IncidentInput,
    settings: Settings,
) -> PlannerGraphContext:
    health = await repo.get_awareness_health(
        tenant_id=incident.tenant_id,
        cluster=incident.cluster,
        stale_after_minutes=settings.awareness_stale_after_minutes,
        inventory_max_age_minutes=settings.awareness_inventory_max_age_minutes,
        authoritative_sources=(
            settings.awareness_authoritative_inventory_source_set
        ),
    )
    base = PlannerGraphContext(
        inventory_sources=[
            PlannerInventorySourceContext(
                source=item.source,
                freshness=item.freshness,
                snapshot_id=item.snapshot_id,
                observed_at=item.observed_at,
                age_seconds=item.age_seconds,
            )
            for item in health.inventory_sources
        ],
        graph_health=PlannerGraphHealthContext(
            entities=_state_counts(health.entities),
            relationships=_state_counts(health.relationships),
            observations_total=health.observations_total,
            latest_observation_at=health.latest_observation_at,
        ),
    )

    recent_changes: list[PlannerContextChange] = []
    if incident.service:
        changes = await query_change_events(
            repo,
            tenant_id=incident.tenant_id,
            cluster=incident.cluster,
            service_name=incident.service,
            namespace=incident.namespace,
            since=incident.query_window(settings.change_correlation_window_minutes)[0],
            until=incident.query_window(settings.change_correlation_window_minutes)[1],
            limit=settings.agentic_context_change_limit,
        )
        recent_changes = [
            PlannerContextChange(
                entity_id=item.entity_id,
                application=item.application,
                revision=item.revision,
                service_name=item.service_name,
                workload_kind=item.workload_kind,
                workload_name=item.workload_name,
                occurred_at=item.occurred_at,
            )
            for item in changes
        ]
        base.recent_changes = recent_changes

    if not incident.service:
        return base

    try:
        projection = await build_service_topology(
            repo,
            tenant_id=incident.tenant_id,
            cluster=incident.cluster,
            service_name=incident.service,
            namespace=incident.namespace,
            requested_depth=settings.agentic_context_depth,
            max_depth=min(
                settings.agentic_context_depth,
                settings.awareness_max_traversal_depth,
            ),
            max_nodes=min(
                settings.agentic_max_context_nodes,
                settings.awareness_max_traversal_nodes,
            ),
            max_edges=settings.awareness_max_traversal_edges,
            stale_after_minutes=settings.awareness_stale_after_minutes,
            include_tombstoned=True,
        )
    except (AwarenessEntityNotFoundError, AmbiguousServiceError):
        return base

    entities = [
        PlannerContextEntity(
            id=item.id,
            kind=item.kind,
            name=item.name,
            namespace=item.namespace,
            freshness=item.freshness,
            depth=item.depth,
            last_seen=item.last_seen,
            tombstoned_at=item.tombstoned_at,
        )
        for item in projection.entities
    ]
    relationships = [
        PlannerContextRelationship(
            id=item.id,
            source_id=item.source_id,
            target_id=item.target_id,
            relationship=item.relationship,
            freshness=item.freshness,
            last_seen=item.last_seen,
            tombstoned_at=item.tombstoned_at,
        )
        for item in projection.relationships
    ]

    target_candidates: list[tuple[str, str]] = [
        ("entity", projection.root.id),
        *[
            ("entity", item.id)
            for item in projection.entities
            if item.id != projection.root.id
        ],
        *[
            ("relationship", item.id)
            for item in projection.relationships
        ],
    ]
    max_targets = settings.agentic_context_max_history_targets
    selected_targets = target_candidates[:max_targets]
    observations: list[PlannerContextObservation] = []
    history_truncated = len(target_candidates) > len(selected_targets)

    for target_type, target_id in selected_targets:
        history = await repo.get_awareness_history(
            target_id=target_id,
            tenant_id=incident.tenant_id,
            cluster=incident.cluster,
            limit=settings.agentic_context_history_per_target,
        )
        if len(history) >= settings.agentic_context_history_per_target:
            history_truncated = True
        observations.extend(
            PlannerContextObservation(
                id=item.id,
                target_type=target_type,
                target_id=item.target_id,
                incident_id=item.incident_id,
                observed_at=item.observed_at,
                action=(
                    str(item.payload.get("action"))
                    if item.payload.get("action") is not None
                    else None
                ),
                origin=(
                    str(item.payload.get("origin"))
                    if item.payload.get("origin") is not None
                    else None
                ),
            )
            for item in history
            if item.target_type in {"entity", "relationship"}
        )

    return base.model_copy(update={
        "root_entity_id": projection.root.id,
        # Preserve the original planner contract: required entity/relationship
        # IDs are current-or-historical context, never tombstoned facts.
        "entity_ids": [
            item.id
            for item in projection.entities
            if item.freshness != "tombstoned"
        ],
        "relationship_ids": [
            item.id
            for item in projection.relationships
            if item.freshness != "tombstoned"
        ],
        "entities": entities,
        "relationships": relationships,
        "observations": observations,
        "recent_changes": recent_changes,
        "truncated": projection.truncated,
        "history_truncated": history_truncated,
    })

def _fresh_context_entities(
    context: PlannerGraphContext,
    kind: str,
    *,
    namespace: str | None = None,
) -> list[PlannerContextEntity]:
    return [
        item
        for item in context.entities
        if item.kind.casefold() == kind.casefold()
        and item.freshness == "fresh"
        and (namespace is None or item.namespace == namespace)
    ]


def _specialist_scope(
    incident: IncidentInput,
    context: PlannerGraphContext,
) -> SpecialistScope:
    resolved: list[str] = []
    ambiguous: list[str] = []

    pod = incident.pod
    if pod is None and incident.namespace and not incident.labels.get("copilot_target_kind"):
        pod_candidates = _fresh_context_entities(
            context,
            "Pod",
            namespace=incident.namespace,
        )
        if context.truncated and context.root_entity_id is not None:
            ambiguous.append("pod")
        elif len(pod_candidates) == 1:
            pod = pod_candidates[0].name
            resolved.append("pod")
        elif len(pod_candidates) > 1:
            ambiguous.append("pod")

    explicit_application = (
        incident.labels.get("argocd_app")
        or incident.labels.get("app.kubernetes.io/instance")
    )
    application = explicit_application
    if application is None and not incident.labels.get("copilot_target_kind"):
        application_candidates = _fresh_context_entities(
            context,
            "ArgoCDApplication",
        )
        if context.truncated and context.root_entity_id is not None:
            ambiguous.append("application")
        elif len(application_candidates) == 1:
            application = application_candidates[0].name
            resolved.append("application")
        elif len(application_candidates) > 1:
            ambiguous.append("application")
        else:
            # Preserve legacy service-name lookup only when the complete
            # bounded topology has no Argo CD application identity.
            application = incident.service

    return SpecialistScope(
        namespace=incident.namespace,
        service=incident.service,
        pod=pod,
        node=incident.node,
        application=application,
        graph_resolved_fields=resolved,
        graph_ambiguous_fields=ambiguous,
    )


def _eligible_tasks(
    *,
    incident: IncidentInput,
    context: PlannerGraphContext,
    settings: Settings,
) -> list[SpecialistTask]:
    tasks: list[SpecialistTask] = []
    context_ids = context.entity_ids[: settings.agentic_max_context_nodes]
    scope = _specialist_scope(incident, context)

    if incident.workload is not None or scope.node or (scope.namespace and scope.pod):
        kubernetes_reason = (
            "The incident identifies Kubernetes-scoped runtime resources."
        )
        if incident.workload is not None:
            kubernetes_reason = "Use UID-verified inventory membership as context only; no Pod is guessed."
        if "pod" in scope.graph_resolved_fields:
            kubernetes_reason = (
                "A single fresh Pod was resolved from the bounded persistent "
                "topology and will be verified against live Kubernetes state."
            )
        _append_task(
            tasks,
            specialist="kubernetes",
            objective=(
                "Validate runtime, ownership, scheduling, controller and node signals."
            ),
            rationale=kubernetes_reason,
            max_queries=settings.agentic_kubernetes_max_queries,
            required_entity_ids=context_ids,
            scope=scope,
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
        scope=scope,
    )

    if scope.namespace and (scope.pod or scope.service):
        log_reason = (
            "The incident has explicit namespace and Pod identity for scoped log queries."
        )
        if "pod" in scope.graph_resolved_fields:
            log_reason = (
                "A single fresh Pod was resolved from the bounded persistent "
                "topology and will be used only to narrow the live Loki query."
            )
        elif incident.workload is not None:
            log_reason = "Query only verified workload Pod names and UIDs; zero replicas produce no log queries."
        elif scope.pod is None and scope.service:
            log_reason = (
                "No unambiguous Pod identity is available, so logs remain "
                "bounded to the explicit namespace and service label."
            )
        _append_task(
            tasks,
            specialist="logs",
            objective=(
                "Search bounded log windows for error signatures and runtime context."
            ),
            rationale=log_reason,
            max_queries=min(8, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
            scope=scope,
        )

    if scope.service and settings.trace_analysis_enabled:
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
            scope=scope,
        )

    trace_task_id = (
        _task_id("traces")
        if any(task.specialist == "traces" for task in tasks)
        else None
    )
    if (
        scope.service
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
            scope=scope,
        )

    if scope.service or scope.pod or scope.application:
        change_reason = (
            "The incident identifies a workload/service that can be correlated "
            "with deployment history."
        )
        if "application" in scope.graph_resolved_fields:
            change_reason = (
                "A single fresh Argo CD application was resolved from persistent "
                "topology and will be verified against the live Argo CD API."
            )
        elif "application" in scope.graph_ambiguous_fields:
            change_reason = (
                "Persistent topology contains multiple fresh Argo CD application "
                "candidates, so live application lookup remains fail-closed while "
                "bounded persisted ChangeEvents remain available as context."
            )
        _append_task(
            tasks,
            specialist="changes",
            objective=(
                "Correlate deployed revision, Git metadata and recent operational changes."
            ),
            rationale=change_reason,
            max_queries=min(6, settings.agentic_specialist_max_queries),
            required_entity_ids=context_ids,
            scope=scope,
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
