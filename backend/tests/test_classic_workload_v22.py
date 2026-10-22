"""Classic investigations must use live UID-bound controller members, not label guesses."""
import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.core.engine import InvestigationEngine
from app.schemas import IncidentInput
from test_copilot_scope_v21 import scoped_api, proposal, scope
from test_copilot_workload_v22 import objects, collect


class LiveTools:
    def __init__(self, inventory, *, oom=True, second_failure=False, bad_uid=False, bad_root=False):
        self.inventory = inventory
        self.oom = oom
        self.second_failure = second_failure
        self.bad_uid = bad_uid
        self.bad_root = bad_root
        self.seen = []
        self.active = None

    async def get_deployment(self, namespace, name):
        obj = deepcopy(next(x for x in self.inventory.data['Deployment'] if x['metadata']['name'] == name))
        if self.bad_root:
            obj['metadata']['uid'] = 'replacement-controller'
        return obj

    async def get_statefulset(self, namespace, name):
        obj = deepcopy(next(x for x in self.inventory.data['StatefulSet'] if x['metadata']['name'] == name))
        if self.bad_root:
            obj['metadata']['uid'] = 'replacement-controller'
        return obj

    async def get_replicaset(self, namespace, name):
        return deepcopy(next(x for x in self.inventory.data['ReplicaSet'] if x['metadata']['name'] == name))

    async def get_pod(self, namespace, name):
        self.seen.append(name)
        pod = deepcopy(next(x for x in self.inventory.data['Pod'] if x['metadata']['name'] == name))
        pod.setdefault('status', {})['phase'] = 'Running'
        pod.setdefault('spec', {})['nodeName'] = 'fixture-node'
        if name == 'arbitrary-new' and self.oom:
            pod['status']['containerStatuses'] = [
                {'name': 'workload', 'restartCount': 1, 'state': {'terminated': {'reason': 'OOMKilled', 'exitCode': 137}}}
            ]
        if name == 'arbitrary-old' and self.second_failure:
            pod['status']['containerStatuses'] = [
                {'name': 'workload', 'restartCount': 3,
                 'state': {'waiting': {'reason': 'CrashLoopBackOff'}},
                 'lastState': {'terminated': {'reason': 'Error', 'exitCode': 42}}}
            ]
        if self.bad_uid and name == 'arbitrary-new':
            pod['metadata']['uid'] = 'recreated-pod'
        self.active = pod
        return pod

    async def get_node(self, name):
        return {'metadata': {'name': name, 'uid': 'node-uid'},
                'status': {'conditions': [
                    {'type': 'Ready', 'status': 'True'},
                    {'type': 'MemoryPressure', 'status': 'False'},
                ]}}

    async def get_events(self, namespace, name):
        return []

    async def loki_query_range(self, query, start, end, limit):
        return {'data': {'result': []}}


def compiled(kind='Deployment', zero=False):
    app, _ = scoped_api()
    inventory = objects(kind=kind, zero=zero)
    collect(app, inventory)
    with TestClient(app) as client:
        response, _ = proposal(client, scope(kind=kind, name='checkout', namespace='payments'))
        assert response.status_code == 200
        return IncidentInput.model_validate(response.json()['incident']), inventory, app.state.settings


@pytest.mark.parametrize('kind', ['Deployment', 'StatefulSet'])
def test_classic_dispatch_resolves_live_verified_oom(kind):
    incident, inventory, settings = compiled(kind)
    tools = LiveTools(inventory)
    result = asyncio.run(InvestigationEngine(tools, settings=settings).investigate_kubernetes(incident, similar=[]))
    assert result.status == 'resolved'
    assert result.root_cause.hypothesis_id == 'container-oom'
    assert result.incident == incident and result.incident.pod is None
    assert set(tools.seen) == {'arbitrary-old', 'arbitrary-new'}
    assert 'checkout-lookalike' not in tools.seen
    assert any(ev.category == 'verified-workload-membership' for ev in result.evidence)
    assert any(h.id == 'container-oom' and h.supporting_evidence for h in result.hypotheses)


def test_classic_no_members_and_conflicting_roots_abstain():
    incident, inventory, settings = compiled(zero=True)
    result = asyncio.run(InvestigationEngine(LiveTools(inventory), settings=settings).investigate_kubernetes(incident, similar=[]))
    assert result.status == 'inconclusive' and result.root_cause.hypothesis_id is None

    incident, inventory, settings = compiled()
    tools = LiveTools(inventory, second_failure=True)
    result = asyncio.run(InvestigationEngine(tools, settings=settings).investigate_kubernetes(incident, similar=[]))
    assert result.status == 'inconclusive' and result.root_cause.hypothesis_id is None
    assert any(ev.category == 'verified-workload-root-conflict' for ev in result.evidence)
    from app.core.integrity import verify_evidence_hash
    evidence_ids = {ev.id for ev in result.evidence}
    assert all(verify_evidence_hash(ev) for ev in result.evidence)
    assert all(set(h.supporting_evidence) <= evidence_ids for h in result.hypotheses)
    assert any(h.id.endswith('@arbitrary-new') and h.status == 'confirmed' for h in result.hypotheses)
    assert any(h.id.endswith('@arbitrary-old') and h.status == 'confirmed' for h in result.hypotheses)


def test_classic_recreated_uid_rejected_before_promotion():
    incident, inventory, settings = compiled()
    tools = LiveTools(inventory, bad_uid=True)
    with pytest.raises(ValueError, match='UID changed'):
        asyncio.run(InvestigationEngine(tools, settings=settings).investigate_kubernetes(incident, similar=[]))
    assert 'checkout-lookalike' not in tools.seen


@pytest.mark.parametrize('kind', ['Deployment', 'StatefulSet'])
@pytest.mark.parametrize('zero', [False, True])
def test_classic_live_controller_uid_required_even_for_zero_replicas(kind, zero):
    incident, inventory, settings = compiled(kind, zero)
    tools = LiveTools(inventory, bad_root=True)
    with pytest.raises(ValueError, match='UID changed'):
        asyncio.run(InvestigationEngine(tools, settings=settings).investigate_kubernetes(incident, similar=[]))
    assert tools.seen == []


def test_classic_controller_reads_are_bounded():
    incident, inventory, settings = compiled()
    settings = settings.model_copy(update={'query_budget_max_calls': 1})
    tools = LiveTools(inventory)
    with pytest.raises(RuntimeError, match='budget exhausted'):
        asyncio.run(InvestigationEngine(tools, settings=settings).investigate_kubernetes(incident, similar=[]))
    assert tools.seen == []


@pytest.mark.asyncio
async def test_controller_conflict_blocks_trace_and_stateful_promotion():
    from types import SimpleNamespace
    from app.core.trace_analysis import enrich_with_trace_causality
    from app.core.stateful_dependency import _can_promote
    from app.schemas import Evidence
    from test_trace_causality_v04 import TraceTools, START

    incident = IncidentInput(title='Conflicting Pod RCA', cluster='prod',
                             service='frontend', starts_at=START)
    engine = InvestigationEngine(TraceTools(trace_count=2))
    result = engine._generic_result('conflict-test', incident, [])
    result.evidence.append(Evidence(
        source='kubernetes', category='verified-workload-root-conflict',
        summary='Two separately verified Pod roots; no unique controller root.',
        facts={'hypothesis_ids': ['container-oom', 'probe-failure']},
    ))
    result = await enrich_with_trace_causality(result, tools=engine.tools, settings=engine.settings)
    assert len(result.trace_analyses) == 2
    assert any(h.id == 'trace-causal-propagation' and h.status == 'confirmed'
               for h in result.hypotheses)
    assert result.status == 'inconclusive'
    assert result.root_cause.hypothesis_id is None
    assert not _can_promote(result, SimpleNamespace(
        causal_role='origin', hypothesis_id='postgres-connection-exhaustion:orders',
        metadata={},
    ))
