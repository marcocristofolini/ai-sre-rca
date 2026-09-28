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

        if item.category == "agentic-node-conditions":
            conditions = item.facts.get("conditions") or {}
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

        if item.category == "agentic-pod-status":
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
            for reason in sorted(
                reasons.intersection(DIRECT_KUBERNETES_EVENT_REASONS)
            ):
                blockers.append(
                    f"Kubernetes event reason={reason} is direct infrastructure evidence"
                )

    return sorted(set(blockers))


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
            item for item in original_support if item in evidence_by_id
        ]
        hypothesis.contradicting_evidence = [
            item for item in original_contradict if item in evidence_by_id
        ]

        reasons: list[str] = []
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

        if (
            precedence_blockers
            and specialist in {"traces", "database"}
            and hypothesis.status == HypothesisStatus.CONFIRMED
        ):
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
            "invalid_evidence",
        }
    ]
    selectable.sort(
        key=lambda item: (
            item.precedence_rank,
            -item.score,
            -len(item.supporting_evidence),
            item.id,
        )
    )

    selected = selectable[0] if selectable else None
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
        evaluated_hypotheses=evaluated,
        explanation_graph=graph,
        generated_at=datetime.now(timezone.utc),
    )
