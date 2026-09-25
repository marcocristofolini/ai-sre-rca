from datetime import datetime, timezone

import pytest

from app.core.engine import InvestigationEngine
from app.integrations.demo import DemoTools
from app.schemas import Hypothesis, HypothesisStatus, IncidentInput, SimilarIncident


class FakeMemory:
    async def search_similar(self, incident: IncidentInput, limit: int = 3):
        return [
            SimilarIncident(
                incident_id="inc-old",
                title="Previous eviction",
                root_cause="Ephemeral-storage exhaustion caused kubelet eviction",
                similarity=0.88,
                occurred_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                cluster="prod",
                namespace="exchange",
            )
        ]


class UnsafeLookingLLM:
    async def propose(self, incident, entities, evidence, deterministic):
        return [
            Hypothesis(
                id="llm-database-caused-it",
                statement="A database failure caused everything.",
                origin="llm",
                required_checks=["database connection failures"],
                rationale="LLM-proposed only.",
            )
        ]


@pytest.mark.asyncio
async def test_ephemeral_storage_eviction_is_confirmed():
    engine = InvestigationEngine(DemoTools())
    result = await engine.investigate(IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    ))

    assert result.status == "resolved"
    assert result.root_cause.hypothesis_id == "disk-pressure"
    assert result.root_cause.evidence_strength == "strong"
    by_id = {h.id: h for h in result.hypotheses}
    assert by_id["disk-pressure"].status == HypothesisStatus.CONFIRMED
    assert by_id["memory-pressure"].status == HypothesisStatus.RULED_OUT
    assert by_id["pid-pressure"].status == HypothesisStatus.RULED_OUT
    assert by_id["missing-storage-reservations"].status != HypothesisStatus.RULED_OUT
    assert by_id["recent-change-regression"].status == HypothesisStatus.RULED_OUT
    assert by_id["node-capacity-churn"].status == HypothesisStatus.RULED_OUT


@pytest.mark.asyncio
async def test_awareness_graph_change_correlation_and_karpenter_are_collected():
    engine = InvestigationEngine(DemoTools())
    result = await engine.investigate(IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    ))

    relationships = {(e.source, e.relationship, e.target) for e in result.graph_edges}
    assert ("pod", "owned_by", "replicaset") in relationships
    assert ("replicaset", "owned_by", "deployment") in relationships
    assert ("node", "represented_by", "nodeclaim") in relationships
    assert ("nodeclaim", "owned_by", "nodepool") in relationships
    assert any(edge.relationship == "calls" for edge in result.graph_edges)

    categories = {(e.source, e.category) for e in result.evidence}
    assert ("opentelemetry", "service-graph") in categories
    assert ("argocd", "change-correlation") in categories
    assert ("github", "source-change") in categories
    assert ("karpenter", "nodeclaim") in categories


@pytest.mark.asyncio
async def test_llm_can_only_propose_and_cannot_become_root_cause():
    engine = InvestigationEngine(
        DemoTools(),
        memory=FakeMemory(),
        hypothesis_generator=UnsafeLookingLLM(),
    )
    result = await engine.investigate(IncidentInput(
        title="Pod evicted",
        cluster="prod",
        namespace="exchange",
        pod="landing-page-7d8b6d9b-x9k2p",
        node="ip-10-0-1-42",
        alert_name="KubePodEvicted",
        starts_at=datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc),
    ))

    llm = next(h for h in result.hypotheses if h.origin == "llm")
    assert llm.status == HypothesisStatus.PROPOSED
    assert llm.score == 0
    assert not llm.supporting_evidence
    assert result.root_cause.hypothesis_id == "disk-pressure"
    assert result.similar_incidents[0].incident_id == "inc-old"
