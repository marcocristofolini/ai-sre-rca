from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.agentic.models import SpecialistResult, SpecialistTask
from app.config import Settings
from app.core.integrity import verify_evidence_hash
from app.core.query_safety import quoted_string
from app.core.stateful_dependency import enrich_with_stateful_dependencies
from app.core.trace_analysis import enrich_with_trace_causality
from app.schemas import (
    Entity,
    Evidence,
    GraphEdge,
    Hypothesis,
    IncidentInput,
    InvestigationResult,
    RootCause,
    StatefulDependencyAnalysis,
    TraceAnalysis,
)


class SpecialistBudgetExceeded(RuntimeError):
    pass


@dataclass
class SpecialistBudget:
    limit: int
    used: int = 0
    exhausted: bool = False

    def consume(self, operation: str) -> None:
        if self.used >= self.limit:
            self.exhausted = True
            raise SpecialistBudgetExceeded(
                f"specialist query budget exhausted before {operation}"
            )
        self.used += 1
class TraceBudgetedTools:
    """Explicit narrow surface required by trace enrichment."""

    def __init__(self, tools, budget: SpecialistBudget):
        self._tools = tools
        self._budget = budget

    async def tempo_search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int = 8,
    ):
        self._budget.consume("tempo_search_traces")
        return await self._tools.tempo_search_traces(
            service, start, end, limit
        )

    async def tempo_get_trace(
        self,
        trace_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ):
        self._budget.consume("tempo_get_trace")
        return await self._tools.tempo_get_trace(
            trace_id, start, end
        )

    async def tempo_metrics_query(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ):
        self._budget.consume("tempo_metrics_query")
        return await self._tools.tempo_metrics_query(
            query, start, end, step
        )

    async def service_graph(self, service: str, at: datetime):
        self._budget.consume("service_graph")
        return await self._tools.service_graph(service, at)
class StatefulBudgetedTools:
    """Explicit narrow surface required by stateful dependency enrichment."""

    def __init__(self, tools, budget: SpecialistBudget):
        self._tools = tools
        self._budget = budget

    async def postgres_dependency_metrics(
        self,
        database: str,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ):
        self._budget.consume("postgres_dependency_metrics")
        return await self._tools.postgres_dependency_metrics(
            database,
            at,
            window_minutes,
            instance_hint,
        )

    async def kafka_dependency_metrics(
        self,
        topic: str | None,
        consumer_group: str | None,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ):
        self._budget.consume("kafka_dependency_metrics")
        return await self._tools.kafka_dependency_metrics(
            topic,
            consumer_group,
            at,
            window_minutes,
            instance_hint,
        )

    async def redis_dependency_metrics(
        self,
        instance_hint: str | None,
        at: datetime,
        window_minutes: int = 15,
    ):
        self._budget.consume("redis_dependency_metrics")
        return await self._tools.redis_dependency_metrics(
            instance_hint,
            at,
            window_minutes,
        )
def _scaffold(
    incident: IncidentInput,
    *,
    evidence: list[Evidence] | None = None,
    entities: list[Entity] | None = None,
    graph_edges: list[GraphEdge] | None = None,
    hypotheses: list[Hypothesis] | None = None,
    trace_analyses: list[TraceAnalysis] | None = None,
) -> InvestigationResult:
    return InvestigationResult(
        incident_id=f"agentic-{uuid4().hex[:12]}",
        status="inconclusive",
        incident=incident,
        entities=[item.model_copy(deep=True) for item in (entities or [])],
        graph_edges=[
            item.model_copy(deep=True) for item in (graph_edges or [])
        ],
        evidence=[
            item.model_copy(deep=True) for item in (evidence or [])
        ],
        hypotheses=[
            item.model_copy(deep=True) for item in (hypotheses or [])
        ],
        timeline=[],
        root_cause=RootCause(
            title="Unresolved specialist branch",
            summary=(
                "Specialist output is evidence context only; root cause "
                "requires the deterministic evaluator."
            ),
            evidence_strength="weak",
            supporting_signals=0,
            contradicting_signals=0,
        ),
        remediation=[],
        trace_analyses=[
            item.model_copy(deep=True)
            for item in (trace_analyses or [])
        ],
    )


def _status_for(
    *,
    budget: SpecialistBudget,
    evidence: list[Evidence],
    structured_count: int = 0,
) -> tuple[str, str]:
    if budget.exhausted:
        return "budget_exhausted", "specialist query budget exhausted"
    if evidence or structured_count:
        return "completed", ""
    return "skipped", "no scoped evidence was available"


def _result(
    task: SpecialistTask,
    budget: SpecialistBudget,
    *,
    started_at: datetime,
    evidence: list[Evidence] | None = None,
    entities: list[Entity] | None = None,
    graph_edges: list[GraphEdge] | None = None,
    trace_analyses: list[TraceAnalysis] | None = None,
    stateful_dependencies: list[StatefulDependencyAnalysis] | None = None,
    hypotheses: list[Hypothesis] | None = None,
    reason: str | None = None,
) -> SpecialistResult:
    evidence = list(evidence or [])
    trace_analyses = list(trace_analyses or [])
    stateful_dependencies = list(stateful_dependencies or [])
    status, default_reason = _status_for(
        budget=budget,
        evidence=evidence,
        structured_count=len(trace_analyses) + len(stateful_dependencies),
    )
    integrity_ok = all(verify_evidence_hash(item) for item in evidence)
    return SpecialistResult(
        task_id=task.id,
        specialist=task.specialist,
        status=status,
        evidence=evidence,
        entities=list(entities or []),
        graph_edges=list(graph_edges or []),
        trace_analyses=trace_analyses,
        stateful_dependencies=stateful_dependencies,
        hypotheses=list(hypotheses or []),
        query_count=budget.used,
        query_limit=budget.limit,
        integrity_ok=integrity_ok,
        reason=reason if reason is not None else default_reason,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
    )
async def _kubernetes(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    evidence: list[Evidence] = []
    try:
        if incident.namespace and incident.pod:
            budget.consume("get_pod")
            pod = await tools.get_pod(incident.namespace, incident.pod)
            if pod:
                status = pod.get("status", {})
                evidence.append(Evidence(
                    source="kubernetes",
                    category="agentic-pod-status",
                    summary=(
                        f"Pod {incident.namespace}/{incident.pod} "
                        f"phase={status.get('phase', 'unknown')} "
                        f"reason={status.get('reason', 'unknown')}."
                    ),
                    facts={
                        "namespace": incident.namespace,
                        "pod": incident.pod,
                        "phase": status.get("phase"),
                        "reason": status.get("reason"),
                        "node": pod.get("spec", {}).get("nodeName"),
                    },
                    raw_reference=(
                        f"k8s://pod/{incident.namespace}/{incident.pod}"
                    ),
                    strength="strong",
                ))

            budget.consume("get_events")
            events = await tools.get_events(
                incident.namespace,
                incident.pod,
            )
            if events:
                evidence.append(Evidence(
                    source="kubernetes",
                    category="agentic-events",
                    summary=(
                        f"Kubernetes returned {len(events)} events for "
                        f"{incident.namespace}/{incident.pod}."
                    ),
                    facts={
                        "event_count": len(events),
                        "reasons": [
                            str(item.get("reason") or "")
                            for item in events[:20]
                        ],
                        "types": [
                            str(item.get("type") or "")
                            for item in events[:20]
                        ],
                    },
                    raw_reference=(
                        f"k8s://events/{incident.namespace}/{incident.pod}"
                    ),
                    strength="medium",
                ))

        if incident.node:
            budget.consume("get_node")
            node = await tools.get_node(incident.node)
            if node:
                conditions = {
                    str(item.get("type")): item.get("status")
                    for item in node.get("status", {}).get("conditions", [])
                    if item.get("type")
                }
                evidence.append(Evidence(
                    source="kubernetes",
                    category="agentic-node-conditions",
                    summary=(
                        f"Node {incident.node} conditions were collected "
                        "through the read-only Kubernetes API."
                    ),
                    facts={
                        "node": incident.node,
                        "conditions": conditions,
                    },
                    raw_reference=f"k8s://node/{incident.node}",
                    strength="strong",
                ))
    except SpecialistBudgetExceeded:
        pass

    return _result(
        task,
        budget,
        started_at=started,
        evidence=evidence,
    )
async def _metrics(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    evidence: list[Evidence] = []
    if not incident.service:
        return _result(
            task,
            budget,
            started_at=started,
            reason="service identity is required for scoped service metrics",
        )

    try:
        budget.consume("service_graph")
        edges = await tools.service_graph(
            incident.service,
            incident.starts_at,
        )
        if edges:
            evidence.append(Evidence(
                source="opentelemetry",
                category="agentic-service-graph-red",
                summary=(
                    f"Service graph returned {len(edges)} RED edges "
                    f"around {incident.service}."
                ),
                facts={
                    "service": incident.service,
                    "edge_count": len(edges),
                    "edges": [
                        {
                            "client": item.get("client"),
                            "server": item.get("server"),
                            "requests": item.get("requests"),
                            "failures": item.get("failures"),
                            "error_ratio": item.get("error_ratio"),
                            "p95_seconds": item.get("p95_seconds"),
                            "connection_type": item.get("connection_type"),
                        }
                        for item in edges[:50]
                    ],
                },
                raw_reference="prometheus:traces_service_graph_*",
                strength="medium",
            ))
    except SpecialistBudgetExceeded:
        pass

    return _result(
        task,
        budget,
        started_at=started,
        evidence=evidence,
    )
async def _logs(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    if not incident.namespace or not incident.pod:
        return _result(
            task,
            budget,
            started_at=started,
            reason=(
                "namespace and pod identity are required for bounded log execution"
            ),
        )

    evidence: list[Evidence] = []
    query = (
        f'{{namespace="{quoted_string(incident.namespace)}",'
        f' pod="{quoted_string(incident.pod)}"}}'
    ).replace(", ", ",")
    try:
        budget.consume("loki_query_range")
        payload = await tools.loki_query_range(
            query,
            incident.starts_at - timedelta(minutes=15),
            incident.starts_at + timedelta(minutes=5),
            100,
        )
        streams = payload.get("data", {}).get("result", [])
        entry_count = sum(
            len(item.get("values", []))
            for item in streams
        )
        if streams:
            evidence.append(Evidence(
                source="loki",
                category="agentic-log-window",
                summary=(
                    f"Loki returned {entry_count} bounded log entries "
                    f"across {len(streams)} stream(s) for the incident pod."
                ),
                facts={
                    "namespace": incident.namespace,
                    "pod": incident.pod,
                    "stream_count": len(streams),
                    "entry_count": entry_count,
                    "stream_labels": [
                        item.get("stream", {})
                        for item in streams[:20]
                    ],
                },
                query=query,
                raw_reference="loki:/loki/api/v1/query_range",
                strength="medium",
            ))
    except SpecialistBudgetExceeded:
        pass

    return _result(
        task,
        budget,
        started_at=started,
        evidence=evidence,
    )
async def _traces(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
    settings: Settings,
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    if not incident.service:
        return _result(
            task,
            budget,
            started_at=started,
            reason="service identity is required for trace execution",
        )

    scaffold = _scaffold(
        incident,
        entities=[
            Entity(
                id=f"service:{incident.service}",
                kind="Service",
                name=incident.service,
                namespace=incident.namespace,
            )
        ],
    )
    proxy = TraceBudgetedTools(tools, budget)
    try:
        await enrich_with_trace_causality(
            scaffold,
            tools=proxy,
            settings=settings,
        )
    except SpecialistBudgetExceeded:
        budget.exhausted = True

    return _result(
        task,
        budget,
        started_at=started,
        evidence=scaffold.evidence,
        entities=scaffold.entities,
        graph_edges=scaffold.graph_edges,
        trace_analyses=scaffold.trace_analyses,
        hypotheses=scaffold.hypotheses,
    )
async def _database(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
    settings: Settings,
    prior_results: dict[str, SpecialistResult],
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    trace_result = prior_results.get("task-traces")
    if (
        trace_result is None
        or trace_result.status != "completed"
        or not trace_result.trace_analyses
    ):
        return _result(
            task,
            budget,
            started_at=started,
            reason=(
                "trace dependency identity is required before stateful execution"
            ),
        )

    scaffold = _scaffold(
        incident,
        evidence=trace_result.evidence,
        entities=trace_result.entities,
        graph_edges=trace_result.graph_edges,
        hypotheses=trace_result.hypotheses,
        trace_analyses=trace_result.trace_analyses,
    )
    initial_evidence_ids = {item.id for item in scaffold.evidence}
    initial_hypothesis_ids = {item.id for item in scaffold.hypotheses}
    proxy = StatefulBudgetedTools(tools, budget)
    try:
        await enrich_with_stateful_dependencies(
            scaffold,
            tools=proxy,
            settings=settings,
        )
    except SpecialistBudgetExceeded:
        budget.exhausted = True

    new_evidence = [
        item
        for item in scaffold.evidence
        if item.id not in initial_evidence_ids
    ]
    new_hypotheses = [
        item
        for item in scaffold.hypotheses
        if item.id not in initial_hypothesis_ids
    ]
    return _result(
        task,
        budget,
        started_at=started,
        evidence=new_evidence,
        entities=scaffold.entities,
        graph_edges=scaffold.graph_edges,
        stateful_dependencies=scaffold.stateful_dependencies,
        hypotheses=new_hypotheses,
    )
async def _changes(
    task: SpecialistTask,
    incident: IncidentInput,
    tools,
) -> SpecialistResult:
    started = datetime.now(timezone.utc)
    budget = SpecialistBudget(task.max_queries)
    app_name = (
        incident.labels.get("argocd_app")
        or incident.labels.get("app.kubernetes.io/instance")
        or incident.service
    )
    if not app_name:
        return _result(
            task,
            budget,
            started_at=started,
            reason="Argo CD application identity is unavailable",
        )

    evidence: list[Evidence] = []
    try:
        budget.consume("argocd_application")
        app = await tools.argocd_application(app_name)
        if not app:
            return _result(
                task,
                budget,
                started_at=started,
                reason=f"Argo CD application not found: {app_name}",
            )

        status = app.get("status", {})
        revision = str(status.get("sync", {}).get("revision") or "")
        source = app.get("spec", {}).get("source") or {}
        if not source:
            sources = app.get("spec", {}).get("sources", [])
            source = sources[0] if sources else {}

        evidence.append(Evidence(
            source="argocd",
            category="agentic-change-state",
            summary=(
                f"Argo CD application {app_name} sync="
                f"{status.get('sync', {}).get('status', 'unknown')} "
                f"health={status.get('health', {}).get('status', 'unknown')}."
            ),
            facts={
                "application": app_name,
                "sync_status": status.get("sync", {}).get("status"),
                "health_status": status.get("health", {}).get("status"),
                "revision": revision or None,
                "finished_at": status.get(
                    "operationState", {}
                ).get("finishedAt"),
            },
            raw_reference=f"argocd://application/{app_name}",
            strength="medium",
        ))

        repo_url = str(source.get("repoURL") or "")
        if repo_url and revision:
            budget.consume("github_commit")
            commit = await tools.github_commit(repo_url, revision)
            if commit:
                evidence.append(Evidence(
                    source="github",
                    category="agentic-revision",
                    summary=(
                        f"Git revision {revision[:12]} associated with "
                        f"Argo CD application {app_name} was resolved."
                    ),
                    facts={
                        "application": app_name,
                        "revision": revision,
                        "commit_message": commit.get(
                            "commit", {}
                        ).get("message"),
                        "author_date": commit.get(
                            "commit", {}
                        ).get("author", {}).get("date"),
                    },
                    raw_reference=commit.get("html_url"),
                    strength="medium",
                ))
    except SpecialistBudgetExceeded:
        pass

    return _result(
        task,
        budget,
        started_at=started,
        evidence=evidence,
    )
async def execute_specialist(
    task: SpecialistTask,
    *,
    incident: IncidentInput,
    tools,
    settings: Settings,
    prior_results: dict[str, SpecialistResult],
) -> SpecialistResult:
    try:
        if task.specialist == "kubernetes":
            return await _kubernetes(task, incident, tools)
        if task.specialist == "metrics":
            return await _metrics(task, incident, tools)
        if task.specialist == "logs":
            return await _logs(task, incident, tools)
        if task.specialist == "traces":
            return await _traces(task, incident, tools, settings)
        if task.specialist == "database":
            return await _database(
                task,
                incident,
                tools,
                settings,
                prior_results,
            )
        if task.specialist == "changes":
            return await _changes(task, incident, tools)
    except Exception as exc:
        now = datetime.now(timezone.utc)
        return SpecialistResult(
            task_id=task.id,
            specialist=task.specialist,
            status="failed",
            query_count=0,
            query_limit=task.max_queries,
            integrity_ok=True,
            reason="specialist execution failed",
            error_type=type(exc).__name__,
            started_at=now,
            finished_at=now,
        )

    now = datetime.now(timezone.utc)
    return SpecialistResult(
        task_id=task.id,
        specialist=task.specialist,
        status="failed",
        query_count=0,
        query_limit=task.max_queries,
        integrity_ok=True,
        reason="unsupported specialist task",
        error_type="UnsupportedSpecialist",
        started_at=now,
        finished_at=now,
    )
