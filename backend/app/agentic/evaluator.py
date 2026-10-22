from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.agentic.models import (
    AgenticEvaluationResult,
    AgenticExecutionResult,
    EvaluatedHypothesis,
    ExplanationEdge,
    ExplanationNode,
    HypothesisExplanationGraph,
)
from app.core.integrity import verify_evidence_hash
from app.core.multicausal import node_not_ready, trace_independent
from app.core.playbooks import kubernetes_hypothesis_priority
from app.core.scoring import score_hypothesis
from app.schemas import Evidence, HypothesisStatus, RootCause


PRECEDENCE = {
    "kubernetes": 0,
    "database": 10,
    "traces": 20,
    "metrics": 30,
    "logs": 30,
    "changes": 40,
}

STATEFUL_SOURCES = {"postgresql", "kafka", "redis"}
DIRECT_KUBERNETES_EVENT_REASONS = {
    "Evicted",
    "FailedScheduling",
    "NodeNotReady",
    "OOMKilling",
}


def _truthy_condition(value: object) -> bool:
    return str(value).lower() == "true"


def _direct_kubernetes_blockers(
    evidence: list[Evidence],
) -> list[str]:
    blockers: list[str] = []
    for item in evidence:
        if item.source != "kubernetes":
            continue

        if item.category in {"agentic-node-conditions", "node-conditions"}:
            conditions = (
                item.facts.get("conditions")
                if item.category == "agentic-node-conditions"
                else item.facts
            ) or {}
            if isinstance(conditions, dict):
                ready = str(conditions.get("Ready") or "")
                if ready in {"False", "Unknown"}:
                    blockers.append(
                        f"node Ready={ready} is a direct infrastructure signal"
                    )
                for name in ("MemoryPressure", "DiskPressure", "PIDPressure"):
                    if _truthy_condition(conditions.get(name)):
                        blockers.append(
                            f"node {name}=True is a direct infrastructure signal"
                        )

        if item.category == "node-ready":
            condition = item.facts.get("condition") or {}
            if isinstance(condition, dict):
                ready = str(condition.get("status") or "")
                if ready in {"False", "Unknown"}:
                    blockers.append(
                        f"node Ready={ready} is a direct infrastructure signal"
                    )

        if item.category == "node-pressure":
            for name in ("MemoryPressure", "DiskPressure", "PIDPressure"):
                if _truthy_condition(item.facts.get(name)):
                    blockers.append(
                        f"node {name}=True is a direct infrastructure signal"
                    )

        if item.category in {"agentic-pod-status", "pod-status"}:
            reason = str(item.facts.get("reason") or "")
            if reason in {"Evicted", "NodeLost"}:
                blockers.append(
                    f"pod reason={reason} has precedence over downstream correlation"
                )

        if item.category == "agentic-events":
            reasons = {
                str(reason)
                for reason in item.facts.get("reasons", [])
                if reason
            }
        elif item.category in {"event", "node-event", "hpa-event"}:
            reason = str(item.facts.get("reason") or "")
            reasons = {reason} if reason else set()
        else:
            reasons = set()

        for reason in sorted(
            reasons.intersection(DIRECT_KUBERNETES_EVENT_REASONS)
        ):
            blockers.append(
                f"Kubernetes event reason={reason} is direct infrastructure evidence"
            )

        if item.category == "container-state":
            terminated = item.facts.get("terminated") or []
            if isinstance(terminated, list) and any(
                str(entry.get("reason") or "").lower() == "oomkilled"
                for entry in terminated
                if isinstance(entry, dict)
            ):
                blockers.append(
                    "container OOMKilled is direct Kubernetes runtime evidence"
                )

    return sorted(set(blockers))


def _specialist_context_blockers(
    execution: AgenticExecutionResult,
    specialist: str,
) -> list[str]:
    blockers: list[str] = []
    planned = {task.specialist for task in execution.plan.tasks}
    results = {
        result.specialist: result
        for result in execution.specialist_results
    }

    source_result = results.get(specialist)
    if source_result is None or source_result.status != "completed":
        source_status = (
            source_result.status if source_result is not None else "missing"
        )
        blockers.append(
            f"source specialist status={source_status}; partial or missing "
            "specialist evidence cannot authorize root-cause promotion"
        )

    if specialist in {"traces", "database"} and "kubernetes" in planned:
        kubernetes = results.get("kubernetes")
        if kubernetes is None or kubernetes.status != "completed":
            blockers.append(
                "Kubernetes specialist was planned but did not complete; "
                "downstream promotion cannot assume infrastructure evidence is absent"
            )

    if specialist == "database":
        traces = results.get("traces")
        if traces is None or traces.status != "completed":
            blockers.append(
                "stateful promotion requires a completed trace specialist"
            )

    denied = execution.query_budget.get("denied")
    if specialist in {"traces", "database"} and isinstance(denied, list) and denied:
        blockers.append(
            "global query budget denied one or more operations; "
            "downstream evaluation is incomplete"
        )

    return blockers


def _trace_gate(
    support: list[Evidence],
) -> tuple[bool, str]:
    trace_ids = {
        str(item.facts.get("trace_id"))
        for item in support
        if item.category == "trace-causal-chain"
        and item.facts.get("trace_id")
    }
    if len(trace_ids) < 2:
        return (
            False,
            "trace-only causality requires the same origin across at least two representative traces",
        )
    return True, f"trace causality is repeated across {len(trace_ids)} traces"


def _stateful_gate(
    hypothesis_id: str,
    support: list[Evidence],
    execution: AgenticExecutionResult,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    analyses = [
        analysis
        for result in execution.specialist_results
        for analysis in result.stateful_dependencies
        if analysis.hypothesis_id == hypothesis_id
    ]
    if not analyses:
        return False, ["no typed stateful analysis references this hypothesis"]

    if not any(item.causal_role == "origin" for item in analyses):
        reasons.append(
            "stateful promotion requires the dependency to be the trace causal origin"
        )

    trace_support = [
        item for item in support if item.category == "trace-causal-chain"
    ]
    if not trace_support:
        reasons.append(
            "stateful promotion requires fresh trace-origin evidence"
        )

    dependency_support = [
        item for item in support if item.source in STATEFUL_SOURCES
    ]
    if not dependency_support:
        reasons.append(
            "stateful promotion requires dependency-specific current telemetry"
        )

    if reasons:
        return False, reasons

    return (
        True,
        [
            "typed trace-origin evidence and dependency-specific telemetry both passed"
        ],
    )


def _decision_for_status(status: HypothesisStatus) -> str:
    if status == HypothesisStatus.CONFIRMED:
        return "supported"
    if status == HypothesisStatus.RULED_OUT:
        return "ruled_out"
    if status == HypothesisStatus.TESTING:
        return "testing"
    return "inconclusive"


def _evidence_strength(
    supporting_ids: list[str],
    evidence_by_id: dict[str, Evidence],
) -> str:
    strengths = {
        evidence_by_id[item].strength
        for item in supporting_ids
        if item in evidence_by_id
    }
    if "strong" in strengths:
        return "strong"
    if "medium" in strengths:
        return "medium"
    return "weak"


def _build_explanation_graph(
    evaluated: list[EvaluatedHypothesis],
    evidence_by_id: dict[str, Evidence],
    *,
    selected_id: str | None,
    precedence_blockers: list[str],
    integrity_blocked: bool,
    status: str,
) -> HypothesisExplanationGraph:
    nodes: dict[str, ExplanationNode] = {}
    edges: list[ExplanationEdge] = []

    verdict_id = "verdict"
    nodes[verdict_id] = ExplanationNode(
        id=verdict_id,
        kind="verdict",
        label=(
            f"selected {selected_id}" if selected_id else "inconclusive"
        ),
        status=status,
    )

    if precedence_blockers:
        gate_id = "gate:kubernetes-precedence"
        nodes[gate_id] = ExplanationNode(
            id=gate_id,
            kind="gate",
            label="direct Kubernetes evidence precedence",
            status="active",
            metadata={"reasons": precedence_blockers},
        )
    else:
        gate_id = None

    if integrity_blocked:
        integrity_gate_id = "gate:evidence-integrity"
        nodes[integrity_gate_id] = ExplanationNode(
            id=integrity_gate_id,
            kind="gate",
            label="shared evidence integrity",
            status="failed",
        )
    else:
        integrity_gate_id = None

    context_reasons = sorted({
        reason
        for item in evaluated
        if item.decision == "blocked_by_incomplete_context"
        for reason in item.reasons
    })
    if context_reasons:
        context_gate_id = "gate:specialist-context"
        nodes[context_gate_id] = ExplanationNode(
            id=context_gate_id,
            kind="gate",
            label="required specialist context completeness",
            status="failed",
            metadata={"reasons": context_reasons},
        )
    else:
        context_gate_id = None

    for item in evaluated:
        hypothesis_node_id = f"hypothesis:{item.id}"
        nodes[hypothesis_node_id] = ExplanationNode(
            id=hypothesis_node_id,
            kind="hypothesis",
            label=item.statement,
            status=item.decision,
            metadata={
                "specialist": item.specialist,
                "score": item.score,
                "hypothesis_status": item.status.value,
                "precedence_rank": item.precedence_rank,
                "reasons": item.reasons,
            },
        )

        for evidence_id in item.supporting_evidence:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            evidence_node_id = f"evidence:{evidence_id}"
            nodes.setdefault(
                evidence_node_id,
                ExplanationNode(
                    id=evidence_node_id,
                    kind="evidence",
                    label=evidence.summary,
                    status=evidence.strength,
                    metadata={
                        "source": evidence.source,
                        "category": evidence.category,
                    },
                ),
            )
            edges.append(
                ExplanationEdge(
                    source=evidence_node_id,
                    target=hypothesis_node_id,
                    relationship="supports",
                )
            )

        for evidence_id in item.contradicting_evidence:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            evidence_node_id = f"evidence:{evidence_id}"
            nodes.setdefault(
                evidence_node_id,
                ExplanationNode(
                    id=evidence_node_id,
                    kind="evidence",
                    label=evidence.summary,
                    status=evidence.strength,
                    metadata={
                        "source": evidence.source,
                        "category": evidence.category,
                    },
                ),
            )
            edges.append(
                ExplanationEdge(
                    source=evidence_node_id,
                    target=hypothesis_node_id,
                    relationship="contradicts",
                )
            )

        if item.decision == "blocked_by_precedence" and gate_id:
            edges.append(
                ExplanationEdge(
                    source=gate_id,
                    target=hypothesis_node_id,
                    relationship="blocks",
                )
            )

        if item.decision == "invalid_evidence" and integrity_gate_id:
            edges.append(
                ExplanationEdge(
                    source=integrity_gate_id,
                    target=hypothesis_node_id,
                    relationship="blocks",
                )
            )

        if (
            item.decision == "blocked_by_incomplete_context"
            and context_gate_id
        ):
            edges.append(
                ExplanationEdge(
                    source=context_gate_id,
                    target=hypothesis_node_id,
                    relationship="blocks",
                )
            )

        edges.append(
            ExplanationEdge(
                source=hypothesis_node_id,
                target=verdict_id,
                relationship=(
                    "selected" if item.id == selected_id else "considered"
                ),
            )
        )

    return HypothesisExplanationGraph(
        nodes=list(nodes.values()),
        edges=edges,
    )


def evaluate_shared_evidence(
    execution: AgenticExecutionResult,
) -> AgenticEvaluationResult:
    verified_evidence = [
        item
        for item in execution.shared_evidence.evidence
        if verify_evidence_hash(item)
    ]
    integrity_blocked = (
        not execution.shared_evidence.evidence_integrity_ok
        or len(verified_evidence)
        != len(execution.shared_evidence.evidence)
    )
    evidence_by_id = {
        item.id: item for item in verified_evidence
    }
    context_only_ids = {
        item.id
        for item in verified_evidence
        if item.facts.get("context_only") is True
    }
    precedence_blockers = _direct_kubernetes_blockers(
        verified_evidence
    )

    candidate_sources: dict[str, list[tuple[str, object]]] = defaultdict(list)
    for result in execution.specialist_results:
        if not result.hypotheses_advisory_only:
            continue
        for hypothesis in result.hypotheses:
            if hypothesis.origin != "playbook":
                continue
            candidate_sources[hypothesis.id].append(
                (result.specialist, hypothesis)
            )

    evaluated: list[EvaluatedHypothesis] = []
    for hypothesis_id, candidates in sorted(candidate_sources.items()):
        specialist, original = sorted(
            candidates,
            key=lambda item: PRECEDENCE.get(item[0], 100),
        )[0]
        hypothesis = original.model_copy(deep=True)

        original_support = list(hypothesis.supporting_evidence)
        original_contradict = list(hypothesis.contradicting_evidence)
        hypothesis.supporting_evidence = [
            item
            for item in original_support
            if item in evidence_by_id and item not in context_only_ids
        ]
        hypothesis.contradicting_evidence = [
            item
            for item in original_contradict
            if item in evidence_by_id and item not in context_only_ids
        ]

        reasons: list[str] = []
        context_only_references = sorted(
            (
                set(original_support)
                | set(original_contradict)
            ).intersection(context_only_ids)
        )
        if context_only_references:
            reasons.append(
                "persistent operational context is context-only and cannot "
                "score or confirm a causal hypothesis"
            )
        missing_support = sorted(
            set(original_support) - set(hypothesis.supporting_evidence)
        )
        missing_contradict = sorted(
            set(original_contradict)
            - set(hypothesis.contradicting_evidence)
        )
        missing_references = bool(missing_support or missing_contradict)
        if missing_references:
            reasons.append(
                "evidence references missing from the integrity-verified shared envelope"
            )

        score_hypothesis(hypothesis, evidence_by_id)
        decision = _decision_for_status(hypothesis.status)

        if missing_references or integrity_blocked:
            hypothesis.status = HypothesisStatus.INCONCLUSIVE
            decision = "invalid_evidence"
            if integrity_blocked:
                reasons.append(
                    "shared evidence envelope integrity verification failed"
                )

        support = [
            evidence_by_id[item]
            for item in hypothesis.supporting_evidence
            if item in evidence_by_id
        ]

        # Integrity is the highest-precedence gate. Once evidence is invalid,
        # no later causal or source-precedence rule may reclassify the
        # hypothesis into a selectable state.
        if decision != "invalid_evidence":
            if hypothesis.id == "trace-causal-propagation":
                gate_ok, gate_reason = _trace_gate(support)
                reasons.append(gate_reason)
                if not gate_ok:
                    hypothesis.status = HypothesisStatus.TESTING
                    decision = "testing"

            if specialist == "database":
                gate_ok, gate_reasons = _stateful_gate(
                    hypothesis.id,
                    support,
                    execution,
                )
                reasons.extend(gate_reasons)
                if not gate_ok:
                    hypothesis.status = HypothesisStatus.INCONCLUSIVE
                    decision = "inconclusive"

            context_blockers = _specialist_context_blockers(
                execution,
                specialist,
            )
            if (
                context_blockers
                and hypothesis.status == HypothesisStatus.CONFIRMED
            ):
                decision = "blocked_by_incomplete_context"
                reasons.extend(context_blockers)
            elif (
                precedence_blockers
                and specialist in {"traces", "database"}
                and hypothesis.status == HypothesisStatus.CONFIRMED
            ):
                # Precedence still determines the primary. A separately
                # repeatable trace origin may be reported as a second cause,
                # but only after verifying trace IDs and origin ancestry.
                analyses = [a for stage in execution.specialist_results
                            if stage.specialist == "traces" and stage.status == "completed"
                            for a in stage.trace_analyses]
                if (specialist == "traces"
                        and trace_independent(analyses, hypothesis, verified_evidence)
                        and not _specialist_context_blockers(execution, specialist)):
                    decision = "supported"
                    reasons.append("Independent repeated trace origin is secondary to direct Kubernetes evidence")
                else:
                    decision = "blocked_by_precedence"
                    reasons.extend(precedence_blockers)

        evaluated.append(
            EvaluatedHypothesis(
                id=hypothesis.id,
                specialist=specialist,
                statement=hypothesis.statement,
                status=hypothesis.status,
                decision=decision,
                score=hypothesis.score,
                supporting_evidence=hypothesis.supporting_evidence,
                contradicting_evidence=hypothesis.contradicting_evidence,
                reasons=reasons,
                precedence_rank=PRECEDENCE.get(specialist, 100),
            )
        )

    selectable = [
        item
        for item in evaluated
        if item.status == HypothesisStatus.CONFIRMED
        and item.decision not in {
            "blocked_by_precedence",
            "blocked_by_incomplete_context",
            "invalid_evidence",
        }
    ]
    selectable.sort(
        key=lambda item: (
            item.precedence_rank,
            (
                kubernetes_hypothesis_priority(item.id)
                if item.specialist == "kubernetes"
                else 0
            ),
            -item.score,
            -len(item.supporting_evidence),
            item.id,
        )
    )

    selected = selectable[0] if selectable else None

    # Independent Node observations are not inferred from a Pod crash.
    # Keep the established primary ranking unchanged.
    kubernetes_complete = any(
        r.specialist == "kubernetes" and r.status == "completed"
        for r in execution.specialist_results
    )
    observed_node = (
        node_not_ready(verified_evidence)
        if not integrity_blocked and kubernetes_complete and not execution.query_budget.get("denied")
        else None
    )
    if (selected is not None and observed_node is not None
            and not any(h.id == "node-not-ready" for h in evaluated)):
        node_h, _ = observed_node
        evaluated.append(EvaluatedHypothesis(
            id=node_h.id, specialist="kubernetes", statement=node_h.statement,
            status=HypothesisStatus.CONFIRMED, decision="supported", score=1.0,
            supporting_evidence=node_h.supporting_evidence,
            contradicting_evidence=[], reasons=[node_h.rationale], precedence_rank=0,
        ))

    root_causes: list[RootCause] = []
    if selected is not None:
        for item in evaluated:
            if item.id == selected.id:
                item.decision = "selected"

        root_cause = RootCause(
            title=selected.statement,
            summary=(
                "Deterministic shared-envelope evaluation revalidated the "
                "candidate against integrity-verified evidence, causal gates "
                "and source precedence before selection."
            ),
            hypothesis_id=selected.id,
            evidence_strength=_evidence_strength(
                selected.supporting_evidence,
                evidence_by_id,
            ),
            supporting_signals=len(selected.supporting_evidence),
            contradicting_signals=len(selected.contradicting_evidence),
        )
        status = "resolved"
        root_causes = [root_cause]
        if observed_node is not None and selected.id != "node-not-ready":
            root_causes.append(observed_node[1])
        for item in evaluated:
            if (item.id != selected.id and item.id == "trace-causal-propagation"
                    and item.decision == "supported"
                    and item.status == HypothesisStatus.CONFIRMED):
                root_causes.append(RootCause(
                    title=item.statement,
                    summary="Repeated independent trace origins passed the deterministic evaluator's source and context gates.",
                    hypothesis_id=item.id,
                    evidence_strength=_evidence_strength(item.supporting_evidence, evidence_by_id),
                    supporting_signals=len(item.supporting_evidence),
                    contradicting_signals=len(item.contradicting_evidence),
                ))
    else:
        root_cause = None
        status = "inconclusive"

    graph = _build_explanation_graph(
        evaluated,
        evidence_by_id,
        selected_id=selected.id if selected else None,
        precedence_blockers=precedence_blockers,
        integrity_blocked=integrity_blocked,
        status=status,
    )

    return AgenticEvaluationResult(
        plan_id=execution.plan.id,
        tenant_id=execution.plan.tenant_id,
        cluster=execution.plan.cluster,
        status=status,
        root_cause=root_cause,
        root_causes=root_causes,
        evaluated_hypotheses=evaluated,
        explanation_graph=graph,
        generated_at=datetime.now(timezone.utc),
    )
