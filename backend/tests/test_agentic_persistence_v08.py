from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.persistence import materialize_agentic_diagnosis
from app.agentic.planner import build_investigation_plan
from app.api.incidents import router as incidents_router
from app.api.planner import router as planner_router
from app.auth import Principal, get_principal
from app.config import Settings
from app.core.integrity import verify_evidence_hash
from app.db.repository import MemoryRepository
from app.integrations.demo import StatefulDemoTools
from app.schemas import IncidentInput, InvestigationResult


def incident():
    return IncidentInput(
        title="Checkout failure", tenant_id="tenant-a", cluster="prod",
        namespace="checkout", service="frontend", alert_name="HighErrorRate",
        starts_at=datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc),
    )


def api():
    app = FastAPI()
    app.state.settings = Settings(agentic_execution_enabled=True, query_budget_enabled=False)
    app.state.repo = MemoryRepository()
    app.state.tools = StatefulDemoTools("postgresql")
    app.include_router(planner_router)
    app.include_router(incidents_router)
    user = SimpleNamespace(principal=Principal(
        subject="investigator-a", tenant_id="tenant-a",
        roles=frozenset({"investigator", "viewer"}), clusters=frozenset({"prod"}),
    ))
    app.dependency_overrides[get_principal] = lambda: user.principal
    return app, user


def test_agentic_api_persists_authorized_diagnosis_with_unchanged_verdict():
    app, user = api()
    with TestClient(app) as client:
        payload = incident().model_dump(mode="json")
        payload['tenant_id'] = 'untrusted-tenant'
        response = client.post('/api/v1/planner/investigate', json=payload)
        assert response.status_code == 200
        result = response.json()
        assert result['execution']['plan']['tenant_id'] == 'tenant-a'
        stored_response = client.get('/api/v1/incidents/' + result['incident_id'])
        assert stored_response.status_code == 200
        stored = stored_response.json()
        assert result['investigation_url'] == '/incidents/' + stored['incident_id']
        assert stored['status'] == result['evaluation']['status'] == 'resolved'
        assert stored['root_cause'] == result['evaluation']['root_cause']
        assert stored['evidence'] == result['execution']['shared_evidence']['evidence']
        assert all(verify_evidence_hash(item) for item in InvestigationResult.model_validate(stored).evidence)
        assert stored['agentic']['explanation_graph'] == result['evaluation']['explanation_graph']
        assert stored['agentic']['specialists']
        assert stored['stateful_dependencies']
        # JSON roundtrip is the same payload contract used by SQLRepository.
        assert InvestigationResult.model_validate_json(stored_response.text).agentic.plan_id == result['execution']['plan']['id']
        events = client.get('/api/v1/incidents/' + result['incident_id'] + '/audit').json()
        assert any(item['action'] == 'planner.investigation_evaluated' for item in events)
        # Agentic planning is read-only: cached graph context isn't copied into topology.
        live_ids = {item['id'] for item in result['execution']['shared_evidence']['entities']}
        assert {item['id'] for item in stored['entities']} == live_ids
        user.principal = Principal(subject='other', tenant_id='tenant-b', roles=frozenset({'viewer'}), clusters=frozenset({'prod'}))
        assert client.get('/api/v1/incidents/' + result['incident_id']).status_code == 404
        user.principal = Principal(subject='other', tenant_id='tenant-a', roles=frozenset({'viewer'}), clusters=frozenset({'other'}))
        assert client.get('/api/v1/incidents/' + result['incident_id']).status_code == 403


def test_disabled_and_unauthorized_agentic_requests_do_not_persist():
    app, user = api()
    with TestClient(app) as client:
        app.state.settings.agentic_execution_enabled = False
        assert client.post('/api/v1/planner/investigate', json=incident().model_dump(mode='json')).status_code == 404
        app.state.settings.agentic_execution_enabled = True
        user.principal = Principal(subject='other', tenant_id='tenant-a', roles=frozenset({'investigator'}), clusters=frozenset({'other'}))
        assert client.post('/api/v1/planner/investigate', json=incident().model_dump(mode='json')).status_code == 403
        assert not app.state.repo._items


@pytest.mark.asyncio
async def test_persistence_keeps_blocked_hypotheses_inconclusive_and_original_decisions():
    settings = Settings(agentic_execution_enabled=True, query_budget_enabled=False)
    plan = await build_investigation_plan(MemoryRepository(), incident=incident().model_copy(update={'pod': 'checkout-abc'}), settings=settings)
    execution = await execute_investigation_plan(plan, tools=StatefulDemoTools('postgresql'), settings=settings)
    evaluation = evaluate_shared_evidence(execution)
    result = materialize_agentic_diagnosis(execution, evaluation)
    blocked = [item for item in evaluation.evaluated_hypotheses if item.decision.startswith('blocked_')]
    assert blocked
    for decision in blocked:
        saved = next(item for item in result.hypotheses if item.id == decision.id)
        assert saved.status.value == 'inconclusive'
        metadata = next(item for item in result.agentic.decisions if item.hypothesis_id == decision.id)
        assert metadata.decision == decision.decision
        assert metadata.reasons == decision.reasons
    assert result.root_cause == evaluation.root_cause
    repo = MemoryRepository()
    await repo.save(result)
    graph = await repo.get_awareness_graph('tenant-a', 'prod')
    await repo.save(result)
    assert (await repo.get_awareness_graph('tenant-a', 'prod')).model_dump(exclude={'generated_at'}) == graph.model_dump(exclude={'generated_at'})


@pytest.mark.asyncio
async def test_tampered_evidence_persists_inconclusive_and_scope_mismatch_rejected():
    settings = Settings(agentic_execution_enabled=True, query_budget_enabled=False)
    plan = await build_investigation_plan(MemoryRepository(), incident=incident(), settings=settings)
    execution = await execute_investigation_plan(plan, tools=StatefulDemoTools('postgresql'), settings=settings)
    execution.shared_evidence.evidence[0].summary = 'tampered after collection'
    evaluation = evaluate_shared_evidence(execution)
    result = materialize_agentic_diagnosis(execution, evaluation)
    assert result.status == 'inconclusive'
    assert result.root_cause.hypothesis_id is None
    assert not result.agentic.evidence_integrity_ok
    assert not any(item.decision == 'selected' for item in result.agentic.decisions)
    evaluation.tenant_id = 'tenant-b'
    with pytest.raises(ValueError, match='scope mismatch'):
        materialize_agentic_diagnosis(execution, evaluation)
