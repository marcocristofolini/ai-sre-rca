"""Conservative deterministic, independently evidenced multiple-cause reporting.

No model or historical evidence may promote a cause. The primary root is
unchanged and multiple causes are optional additive API/persistence fields.
"""
from __future__ import annotations

from collections import Counter

from app.core.integrity import verify_evidence_hash
from app.schemas import Evidence, Hypothesis, HypothesisStatus, InvestigationResult, RootCause


def node_not_ready(evidence: list[Evidence]) -> tuple[Hypothesis, RootCause] | None:
    """Require an actual integrity-verified Kubernetes Node observation."""
    for ev in evidence:
        if (ev.source != "kubernetes" or
                ev.category not in {"node-conditions", "agentic-node-conditions"} or
                not verify_evidence_hash(ev)):
            continue
        conditions = (ev.facts.get("conditions") if ev.category == "agentic-node-conditions"
                      else ev.facts)
        if not isinstance(conditions, dict):
            continue
        ready = str(conditions.get("Ready") or "")
        if ready not in {"False", "Unknown"}:
            continue
        # The trusted adapter created this Node reference; no inference from
        # Pod logs, label matching, or a historical event is permitted.
        h = Hypothesis(
            id="node-not-ready",
            statement=f"Independently observed Kubernetes Node Ready={ready}.",
            status=HypothesisStatus.CONFIRMED,
            supporting_evidence=[ev.id],
            rationale="Live Kubernetes Node condition directly confirms node unavailability.",
        )
        return h, RootCause(
            title="Node independently NotReady",
            summary=f"Live Kubernetes Node Ready={ready}, independently of the Pod termination.",
            hypothesis_id=h.id, evidence_strength=ev.strength,
            supporting_signals=1, contradicting_signals=0,
        )
    return None


def trace_independent(analyses, hypothesis: Hypothesis, evidence: list[Evidence]) -> bool:
    """Require repeatable origin and two independent integrity-verified traces."""
    if hypothesis.id != "trace-causal-propagation" or hypothesis.status != HypothesisStatus.CONFIRMED:
        return False
    by_id = {e.id: e for e in evidence if verify_evidence_hash(e)}
    traces = {
        by_id[eid].facts.get("trace_id")
        for eid in hypothesis.supporting_evidence
        if eid in by_id and by_id[eid].category == "trace-causal-chain"
    }
    traces.discard(None)
    signatures = Counter(
        (a.candidate_service, a.candidate_operation, a.candidate_instance)
        for a in analyses
        if a.trace_id in traces and a.candidate_service and a.candidate_operation
    )
    return len(traces) >= 2 and any(count >= 2 for count in signatures.values())


def attach_classic_causes(result: InvestigationResult) -> InvestigationResult:
    """Reconcile independently verified stages without replacing the primary."""
    if result.status != "resolved":
        return result
    evidence = {ev.id: ev for ev in result.evidence}
    if len(evidence) != len(result.evidence) or not all(verify_evidence_hash(ev) for ev in evidence.values()):
        return result
    primary = next((h for h in result.hypotheses
                    if h.id == result.root_cause.hypothesis_id
                    and h.status == HypothesisStatus.CONFIRMED
                    and h.supporting_evidence
                    and all(eid in evidence for eid in h.supporting_evidence)), None)
    if primary is None:
        return result
    causes = [result.root_cause]
    if primary.id != "node-not-ready":
        node = node_not_ready(result.evidence)
        if node and not any(h.id == node[0].id for h in result.hypotheses):
            result.hypotheses.append(node[0])
            causes.append(node[1])
    if primary.id != "trace-causal-propagation" and not primary.id.startswith(("postgres-", "kafka-", "redis-")):
        trace = next((h for h in result.hypotheses if h.id == "trace-causal-propagation"), None)
        if trace and trace_independent(result.trace_analyses, trace, result.evidence):
            causes.append(RootCause(
                title=trace.statement,
                summary="Repeated independent traces verify the same downstream origin and causal propagation.",
                hypothesis_id=trace.id, evidence_strength="strong",
                supporting_signals=len(trace.supporting_evidence),
                contradicting_signals=len(trace.contradicting_evidence),
            ))
    result.root_causes = causes
    return result
