import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agentic.models import SpecialistScope, SpecialistTask
from app.agentic.specialists import _kubernetes
from app.schemas import IncidentInput
from test_copilot_scope_v21 import proposal, scope, scoped_api
from test_copilot_workload_v22 import objects, collect


@pytest.mark.parametrize('failure', [None, 'uid', 'owner', 'budget', 'zero', 'readiness', 'liveness'])
@pytest.mark.parametrize('kind', ['Deployment','StatefulSet'])
def test_live_workload_checks_preserve_uids_and_budgets(failure, kind):
    app, _ = scoped_api(); inventory = objects(kind=kind,zero=failure == 'zero'); collect(app, inventory)
    with TestClient(app) as client:
        response, _ = proposal(client, scope(kind=kind,name='checkout',namespace='payments'))
        incident = IncidentInput.model_validate(response.json()['incident'])
    calls = []
    class Tools:
        async def get_deployment(self, namespace, name):
            return next(x for x in inventory.data['Deployment'] if x['metadata']['name'] == name)
        async def get_statefulset(self, namespace, name):
            return next(x for x in inventory.data['StatefulSet'] if x['metadata']['name'] == name)
        async def get_replicaset(self, namespace, name):
            return next(x for x in inventory.data['ReplicaSet'] if x['metadata']['name'] == name)
        async def get_pod(self, namespace, name):
            calls.append(('pod',namespace,name))
            import copy
            pod = copy.deepcopy(next(p for p in inventory.data['Pod'] if p['metadata']['name'] == name))
            pod['status']['phase'] = 'Running'
            pod['spec']['nodeName'] = 'fixture-node'
            if failure in ('readiness','liveness'):
                pod['status']['containerStatuses'] = [{'name':'workload','restartCount':2,
                    'state':{'waiting':{'reason':'CrashLoopBackOff'}},
                    'lastState':{'terminated':{'exitCode':42,'reason':'Error'}}}]
            self.active = pod
            if failure == 'uid': pod['metadata']['uid'] = 'replacement'
            if failure == 'owner': pod['metadata']['ownerReferences'][0]['uid'] = 'foreign-owner'
            return pod
        async def get_events(self, namespace, name):
            calls.append(('events',namespace,name))
            return [{'reason':'Unhealthy','message':f'{failure} probe failed','involvedObject':{'uid':self.active['metadata']['uid']}}] if failure in ('readiness','liveness') else []
        async def get_node(self, name):
            return {'metadata':{'name':name,'uid':'node-uid'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}
        async def loki_query_range(self, query, start, end, limit):
            return {'data':{'result':[{'stream':{'namespace':'payments','pod':self.active['metadata']['name'],'pod_uid':self.active['metadata']['uid']},'values':[['1','process exited 42']]}]}}
    task = SpecialistTask(id='live-kubernetes',specialist='kubernetes',objective='Check real Pod identity',rationale='Required runtime context',
        scope=SpecialistScope(namespace='payments'),max_queries=1 if failure=='budget' else 30)
    if failure in ('uid','owner'):
        with pytest.raises(ValueError): asyncio.run(_kubernetes(task,incident,Tools(),app.state.settings))
    else:
        result = asyncio.run(_kubernetes(task,incident,Tools(),app.state.settings))
        assert result.status == ('budget_exhausted' if failure=='budget' else 'skipped' if failure=='zero' else 'completed')
        if failure in ('readiness','liveness'):
            expected = 'application-exit' if failure == 'readiness' else 'probe-failure'
            assert any(h.id == expected and h.status == 'confirmed' for h in result.hypotheses)
            forbidden = 'probe-failure' if failure == 'readiness' else 'application-exit'
            assert not any(h.id == forbidden and h.status == 'confirmed' for h in result.hypotheses)
        else:
            assert all(h.status != 'confirmed' for h in result.hypotheses)
        assert all(call[2] != 'checkout-lookalike' for call in calls)
        assert result.query_count <= task.max_queries


def test_actual_repeated_nonzero_exit_is_detected_between_backoff_states():
    from app.core.k8s_signals import classify_pod_scenario
    pod = {'status':{'phase':'Running','containerStatuses':[{'restartCount':1,
        'state':{'terminated':{'reason':'Error','exitCode':42}}}]}}
    assert classify_pod_scenario(IncidentInput(title='Manual investigation'),pod,[]) == 'crashloop'
    pod['status']['containerStatuses'][0]['state']['terminated'] = {'reason':'OOMKilled','exitCode':137}
    assert classify_pod_scenario(IncidentInput(title='Manual investigation'),pod,[]) == 'oomkilled'
