from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.agentic.models import (
    AgenticExecutionResult,
    InvestigationPlan,
    SharedEvidenceEnvelope,
    SpecialistResult,
    SpecialistTask,
)
from app.agentic.specialists import execute_specialist
from app.config import Settings
from app.core.integrity import verify_evidence_hash
from app.core.query_budget import begin_query_budget, end_query_budget
from app.schemas import Entity, Evidence, GraphEdge


def _skipped_dependency_result(
    task: SpecialistTask,
    *,
    reason: str,
) -> SpecialistResult:
    now = datetime.now(timezone.utc)
    return SpecialistResult(
        task_id=task.id,
        specialist=task.specialist,
        status="skipped",
        query_count=0,
        query_limit=task.max_queries,
        integrity_ok=True,
        reason=reason,
        started_at=now,
        finished_at=now,
    )


def _merge_entities(results: list[SpecialistResult]) -> list[Entity]:
    merged: dict[str, Entity] = {}
    for result in results:
        for entity in result.entities:
            current = merged.get(entity.id)
            if current is None:
                merged[entity.id] = entity.model_copy(deep=True)
                continue
            current.metadata.update(entity.metadata)
            if not current.namespace and entity.namespace:
                current.namespace = entity.namespace
    return list(merged.values())


def _merge_edges(results: list[SpecialistResult]) -> list[GraphEdge]:
    merged: dict[
        tuple[str, str, str, tuple[str, ...]],
        GraphEdge,
    ] = {}
    for result in results:
        for edge in result.graph_edges:
            key = (
                edge.source,
                edge.target,
                edge.relationship,
                tuple(sorted(edge.evidence_ids)),
            )
            if key not in merged:
                merged[key] = edge.model_copy(deep=True)
    return list(merged.values())


def _merge_evidence(
    results: list[SpecialistResult],
) -> tuple[list[Evidence], bool]:
    merged: dict[str, Evidence] = {}
    integrity_ok = True
    for result in results:
        for evidence in result.evidence:
            if not verify_evidence_hash(evidence):
                integrity_ok = False
                continue
            merged.setdefault(
                evidence.id,
                evidence.model_copy(deep=True),
            )
    return list(merged.values()), integrity_ok
def _shared_envelope(
    plan: InvestigationPlan,
    results: list[SpecialistResult],
) -> SharedEvidenceEnvelope:
    evidence, integrity_ok = _merge_evidence(results)
    return SharedEvidenceEnvelope(
        plan_id=plan.id,
        tenant_id=plan.tenant_id,
        cluster=plan.cluster,
        evidence=evidence,
        entities=_merge_entities(results),
        graph_edges=_merge_edges(results),
        evidence_integrity_ok=(
            integrity_ok
            and all(result.integrity_ok for result in results)
        ),
    )


async def execute_investigation_plan(
    plan: InvestigationPlan,
    *,
    tools,
    settings: Settings,
) -> AgenticExecutionResult:
    started_at = datetime.now(timezone.utc)
    budget_token = begin_query_budget(settings)
    cluster_token = None
    results_by_id: dict[str, SpecialistResult] = {}
    pending = {task.id: task for task in plan.tasks}
    semaphore = asyncio.Semaphore(plan.max_parallel_tasks)
    query_snapshot: dict[str, object] = {}

    async def run_task(task: SpecialistTask) -> SpecialistResult:
        async with semaphore:
            return await execute_specialist(
                task,
                incident=plan.incident,
                tools=tools,
                settings=settings,
                prior_results=dict(results_by_id),
            )

    try:
        if hasattr(tools, "set_cluster"):
            cluster_token = tools.set_cluster(plan.cluster)

        while pending:
            ready = [
                task
                for task in pending.values()
                if all(
                    dependency in results_by_id
                    for dependency in task.depends_on
                )
            ]

            if not ready:
                for task in list(pending.values()):
                    results_by_id[task.id] = _skipped_dependency_result(
                        task,
                        reason=(
                            "task dependency graph could not be resolved"
                        ),
                    )
                    pending.pop(task.id, None)
                break

            runnable: list[SpecialistTask] = []
            for task in ready:
                dependency_failures = [
                    dependency
                    for dependency in task.depends_on
                    if results_by_id[dependency].status != "completed"
                ]
                if dependency_failures:
                    results_by_id[task.id] = _skipped_dependency_result(
                        task,
                        reason=(
                            "required specialist dependency did not complete: "
                            + ", ".join(dependency_failures)
                        ),
                    )
                    pending.pop(task.id, None)
                    continue
                runnable.append(task)

            if not runnable:
                continue

            completed = await asyncio.gather(
                *[run_task(task) for task in runnable]
            )
            for task, result in zip(runnable, completed):
                results_by_id[task.id] = result
                pending.pop(task.id, None)
    finally:
        snapshot = end_query_budget(budget_token)
        query_snapshot = snapshot or {}
        if (
            cluster_token is not None
            and hasattr(tools, "reset_cluster")
        ):
            tools.reset_cluster(cluster_token)

    ordered_results = [
        results_by_id[task.id]
        for task in plan.tasks
        if task.id in results_by_id
    ]
    completed_count = sum(
        result.status == "completed"
        for result in ordered_results
    )
    failed_count = sum(
        result.status == "failed"
        for result in ordered_results
    )
    if ordered_results and completed_count == len(ordered_results):
        status = "completed"
    elif failed_count and completed_count == 0:
        status = "failed"
    else:
        status = "partial"

    return AgenticExecutionResult(
        plan=plan,
        status=status,
        specialist_results=ordered_results,
        shared_evidence=_shared_envelope(
            plan,
            ordered_results,
        ),
        query_budget=query_snapshot,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
