"""Project an evaluated execution into the existing incident persistence contract."""
from __future__ import annotations

from app.agentic.models import AgenticEvaluationResult, AgenticExecutionResult
from app.core.integrity import verify_evidence_hash
from app.schemas import (
    AgenticDiagnosis, Hypothesis, HypothesisDecisionSummary, HypothesisStatus,
    InvestigationResult, RootCause, SpecialistExecutionSummary, TimelineEvent,
)


def materialize_agentic_diagnosis(
    execution: AgenticExecutionResult,
    evaluation: AgenticEvaluationResult,
) -> InvestigationResult:
    plan = execution.plan
    envelope = execution.shared_evidence
    if (evaluation.plan_id, evaluation.tenant_id, evaluation.cluster) != (
        plan.id, plan.tenant_id, plan.cluster,
    ) or (envelope.plan_id, envelope.tenant_id, envelope.cluster) != (
        plan.id, plan.tenant_id, plan.cluster,
    ) or (plan.incident.tenant_id, plan.incident.cluster) != (plan.tenant_id, plan.cluster):
        raise ValueError("agentic diagnosis scope mismatch")

    # Only evaluator output can populate the persisted hypotheses and verdict.
    # A specialist may call a candidate confirmed before a global gate blocks it.
    blocked = {"blocked_by_precedence", "blocked_by_incomplete_context", "invalid_evidence"}
    hypotheses = [Hypothesis(
        id=item.id, statement=item.statement, origin="playbook",
        status=HypothesisStatus.INCONCLUSIVE if item.decision in blocked else item.status,
        score=item.score, supporting_evidence=item.supporting_evidence,
        contradicting_evidence=item.contradicting_evidence,
        rationale="; ".join(item.reasons),
    ) for item in evaluation.evaluated_hypotheses]
    root = evaluation.root_cause
    selected = next((item for item in evaluation.evaluated_hypotheses
                     if item.decision == "selected" and root is not None
                     and item.id == root.hypothesis_id
                     and item.status == HypothesisStatus.CONFIRMED), None)
    integrity_ok = envelope.evidence_integrity_ok and all(verify_evidence_hash(item) for item in envelope.evidence)
    resolved = (evaluation.status == "resolved" and selected is not None
                and integrity_ok)
    if not resolved:
        root = RootCause(
            title="Diagnóstico inconclusivo",
            summary="O avaliador não confirmou uma causa com evidências e contexto suficientes.",
            hypothesis_id=None, evidence_strength="weak", supporting_signals=0,
            contradicting_signals=0,
        )
    specialists = execution.specialist_results
    return InvestigationResult(
        # The plan's random stable ID makes repeated saves of this result idempotent.
        incident_id=f"inc-{plan.id}", incident=plan.incident,
        status="resolved" if resolved else "inconclusive",
        created_at=execution.started_at,
        entities=envelope.entities, graph_edges=envelope.graph_edges,
        evidence=envelope.evidence, hypotheses=hypotheses,
        root_cause=root, root_causes=evaluation.root_causes if resolved else [],
        remediation=[], query_budget=execution.query_budget,
        trace_analyses=[item for result in specialists for item in result.trace_analyses],
        stateful_dependencies=[item for result in specialists for item in result.stateful_dependencies],
        timeline=[
            TimelineEvent(timestamp=execution.started_at, source="planner", message="Investigação agentic iniciada"),
            *[TimelineEvent(timestamp=item.finished_at, source=item.specialist,
                            message=f"{item.status}: {item.reason}") for item in specialists],
            TimelineEvent(timestamp=evaluation.generated_at, source="evaluator",
                          message=f"Avaliação determinística: {evaluation.status}"),
        ],
        agentic=AgenticDiagnosis(
            plan_id=plan.id, planner_origin=plan.planner_origin,
            execution_status=execution.status,
            evidence_integrity_ok=integrity_ok,
            started_at=execution.started_at, finished_at=execution.finished_at,
            specialists=[SpecialistExecutionSummary(**item.model_dump()) for item in specialists],
            decisions=[HypothesisDecisionSummary(
                hypothesis_id=item.id, specialist=item.specialist,
                decision=item.decision, reasons=item.reasons,
            ) for item in evaluation.evaluated_hypotheses],
            explanation_graph=evaluation.explanation_graph.model_dump(mode="json"),
        ),
    )
