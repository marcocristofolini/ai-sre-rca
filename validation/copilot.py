"""Live Copilot validation using the existing Kind stack and production adapters."""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import FastAPI

from app.api.copilot_investigations import router as copilot_router
from app.api.incidents import router as incidents_router
from app.api.planner import router as planner_router, _run_orchestration_job
from app.auth import Principal, get_principal
from app.awareness.kubernetes_inventory import reconcile_kubernetes_inventory
from app.core.integrity import verify_evidence_hash
from app.core.trace_analysis import _normalize_spans
from app.core.trace_ids import canonical_trace_id
from app.db.repository import SQLRepository
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.schemas import IncidentInput, InvestigationResult, RootCause
from validation import run as harness


def request_latency():
    request = urllib.request.Request(f'http://127.0.0.1:{harness.WORKLOAD_PORT}/latency')
    started = time.monotonic()
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return {'status': response.status, 'body': json.load(response),
                'duration_seconds': time.monotonic() - started}


async def execute_scope(app, client, source_id, kind, name, label, expected, report):
    """Every phase uses the same proposal/confirmation/job/SSE/citation contract."""
    await reconcile_kubernetes_inventory(app.state.repo, app.state.tools, app.state.settings)
    end = harness.utcnow().replace(microsecond=0)
    body = {'question':f'Investigate {label}', 'scope':{'cluster':harness.LOGICAL_CLUSTER,'kind':kind,'name':name,
        'namespace':harness.NAMESPACE,'starts_at':(end-timedelta(minutes=15)).isoformat(),'ends_at':end.isoformat()}}
    base = f'/api/v1/incidents/{source_id}/copilot'
    proposed = await client.post(base+'/proposals',json=body)
    assert proposed.status_code == 200, proposed.text
    review = proposed.json()
    confirmation = body | {'proposal_id':review['proposal_id'],'confirmed':True}
    response = await client.post(base+'/confirm',json=confirmation)
    assert response.status_code == 202, response.text
    job_id = response.json()['id']; start = time.monotonic()
    await _run_orchestration_job(app,job_id)
    job = await app.state.repo.get_orchestration_job(job_id,'validation')
    assert job['status'] == 'completed', job.get('error_type')
    result = await app.state.repo.get(job['result']['incident_id'],tenant_id='validation')
    stream = await client.get(f'/api/v1/planner/jobs/{job_id}/events')
    assert stream.status_code == 200 and 'completed' in stream.text
    answer = await client.post(f'/api/v1/incidents/{result.incident_id}/copilot',json={'question':'What is the root cause?'})
    assert answer.status_code == 200
    citations = answer.json()['evidence_ids']
    assert set(citations) <= {e.id for e in result.evidence} and all(verify_evidence_hash(e) for e in result.evidence)
    row = {'name':label,'expected':expected,'reported':result.root_cause.hypothesis_id,
        'passed':result.root_cause.hypothesis_id in expected,'job_id':job_id,'seconds':time.monotonic()-start,
        'citations':citations,'scope':review['incident']['workload'],'diagnosis':result.model_dump(mode='json')}
    row['telemetry_provenance'] = await capture_provenance(app.state.tools,result)
    report['scenarios'].append(row)
    return row, body, confirmation


async def capture_provenance(tools, result):
    token = tools.set_cluster(result.incident.cluster)
    proof = {'traces':{}, 'logs':[]}
    try:
        for item in result.evidence:
            if item.category == 'trace-causal-chain':
                trace_id = item.facts['trace_id']
                payload = await tools.tempo_get_trace(trace_id,*result.incident.query_window(15))
                expected_id = canonical_trace_id(trace_id)
                assert expected_id and payload and any(canonical_trace_id(span['trace_id']) == expected_id for span in _normalize_spans(payload)), 'Trace reference did not resolve to its actual ID'
                proof['traces'][trace_id] = payload
            if item.category == 'agentic-log-window':
                payload = await tools.loki_query_range(item.query,*result.incident.query_window(15),100)
                allowed = {(m.name,m.uid) for m in result.incident.workload.members if m.kind=='Pod'}
                assert all((s.get('stream',{}).get('pod'),s.get('stream',{}).get('pod_uid')) in allowed for s in payload.get('data',{}).get('result',[]))
                proof['logs'].append(payload)
    finally:
        tools.reset_cluster(token)
    return proof


def rollout_healthy(context, service):
    pods = harness.pods_for_label(context,'app=rc3-workload')
    harness.kubectl(context,'set','env','deployment/rc3-workload','-n',harness.NAMESPACE,
        'VALIDATION_STARTUP_EXIT-',f'OTEL_SERVICE_NAME={service}','VALIDATION_ALLOW_FAULTS=true')
    harness.kubectl(context,'annotate','deployment/rc3-workload','-n',harness.NAMESPACE,f'ai-sre.io/telemetry-service={service}','--overwrite')
    harness.kubectl(context,'rollout','status','deployment/rc3-workload','-n',harness.NAMESPACE,'--timeout=180s')
    for pod in pods:
        harness.kubectl(context,'wait','--for=delete',f"pod/{pod['metadata']['name']}",'-n',harness.NAMESPACE,'--timeout=90s')
    harness.stop_port_forwards(); harness.start_observability_forwards(context)


async def validate_copilot(context, settings, report_dir):
    report = {'passed': False, 'scenarios': [], 'source_health': [], 'failure': None,
        'planned_scenarios':['stateful-downstream-latency','stateful-degradation','rolling-update-regression','recovery','oomkilled','zero-replicas']}
    started = time.monotonic()
    settings.query_budget_enabled = True
    repo = SQLRepository(settings.database_url)
    await repo.initialize()
    tools = ReadOnlyHTTPTools(settings)
    app = FastAPI()
    app.state.settings, app.state.repo, app.state.tools = settings, repo, tools
    app.state.orchestration_worker_id = 'real-validation-' + uuid4().hex[:8]
    owner = Principal(subject='disposable-validator', tenant_id='validation',
                      roles=frozenset({'investigator'}), clusters=frozenset({harness.LOGICAL_CLUSTER}))
    principal = [owner]
    app.dependency_overrides[get_principal] = lambda: principal[0]
    for router in (incidents_router, copilot_router, planner_router):
        app.include_router(router)
    source_id = 'live-source-' + uuid4().hex[:12]
    source = InvestigationResult(incident_id=source_id, status='inconclusive',
        incident=IncidentInput(title='Live validation source; no diagnosed cause', tenant_id='validation',
            cluster=harness.LOGICAL_CLUSTER, namespace=harness.NAMESPACE), entities=[], evidence=[],
        hypotheses=[], timeline=[], remediation=[], root_cause=RootCause(title='Insufficient evidence',
            summary='No cause has been confirmed', evidence_strength='weak', supporting_signals=0, contradicting_signals=0))
    await repo.save(source)
    try:
        service = 'copilot-frontend-' + uuid4().hex[:8]
        rollout_healthy(context,service)
        harness.kubectl(context, 'rollout', 'status', 'statefulset/copilot-stateful', '-n', harness.NAMESPACE, '--timeout=180s')
        harness.kubectl(context, 'set', 'env', 'statefulset/copilot-stateful', '-n', harness.NAMESPACE, 'DEPENDENCY_DELAY_SECONDS=0.8')
        harness.kubectl(context, 'rollout', 'status', 'statefulset/copilot-stateful', '-n', harness.NAMESPACE, '--timeout=180s')
        harness.stop_port_forwards()
        harness.start_observability_forwards(context)
        calls = [await asyncio.to_thread(request_latency) for _ in range(3)]
        report['controlled_requests'] = calls
        assert all(c['status'] == 504 and c['body']['failed'] and c['body']['duration_seconds'] >= .25 for c in calls), calls
        # Tempo indexing is asynchronous; readiness must prove these actual IDs.
        token = tools.set_cluster(harness.LOGICAL_CLUSTER)
        try:
            expected_trace_ids = {c['body']['trace_id'] for c in calls}
            for _ in range(45):
                traces = await tools.tempo_search_traces(service, harness.utcnow()-timedelta(minutes=15), harness.utcnow(), 8)
                if expected_trace_ids <= {t.get('traceID') for t in traces}:
                    break
                await asyncio.sleep(2)
            else:
                raise AssertionError('Generated trace IDs did not become searchable in Tempo')
        finally:
            tools.reset_cluster(token)
        await reconcile_kubernetes_inventory(repo, tools, settings)
        report['source_health'] = await tools.capability_report(harness.LOGICAL_CLUSTER)
        healthy = {x['source'] for x in report['source_health'] if x['status'] == 'healthy'}
        assert {'kubernetes', 'prometheus', 'loki', 'tempo'} <= healthy, report['source_health']
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url='http://validation', timeout=90) as client:
            base = f'/api/v1/incidents/{source_id}/copilot'
            end = harness.utcnow().replace(microsecond=0)
            selection = {'cluster': harness.LOGICAL_CLUSTER, 'kind': 'Deployment', 'name': 'rc3-workload',
                'namespace': harness.NAMESPACE, 'starts_at': (end-timedelta(minutes=15)).isoformat(), 'ends_at':end.isoformat()}
            body = {'question':'Investigate the observed downstream timeout', 'scope':selection}
            before = await repo.recover_orchestration_jobs()
            proposal = await client.post(base+'/proposals', json=body)
            assert proposal.status_code == 200, proposal.text
            assert await repo.recover_orchestration_jobs() == before, 'Proposal created a job'
            reviewed = proposal.json()
            members = reviewed['incident']['workload']['members']
            live_pods = harness.pods_for_label(context, 'app=rc3-workload')
            assert {(m['name'],m['uid']) for m in members if m['kind']=='Pod'} == {(p['metadata']['name'],p['metadata']['uid']) for p in live_pods}
            assert reviewed['incident']['service'] == service
            confirm = body | {'proposal_id':reviewed['proposal_id'], 'confirmed':True}
            refused = await client.post(base+'/confirm', json=confirm | {'confirmed':False})
            assert refused.status_code == 422
            confirmed = await client.post(base+'/confirm', json=confirm)
            assert confirmed.status_code == 202, confirmed.text
            job_id = confirmed.json()['id']
            retry = await client.post(base+'/confirm', json=confirm)
            assert retry.json()['id'] == job_id
            execution_started = time.monotonic()
            await _run_orchestration_job(app, job_id)
            stored = await repo.get_orchestration_job(job_id, owner.tenant_id)
            report['job'] = {'id':job_id,'status':stored['status'],'execution_seconds':time.monotonic()-execution_started}
            assert stored['status'] == 'completed', stored.get('error_type')
            result = stored['result']
            incident_id = result['incident_id']
            saved = await repo.get(incident_id, tenant_id=owner.tenant_id)
            report['diagnosis'] = saved.model_dump(mode='json')
            report['telemetry_provenance'] = await capture_provenance(tools,saved)
            report['inventory_graph'] = (await repo.get_awareness_graph('validation',harness.LOGICAL_CLUSTER,namespace=harness.NAMESPACE,limit=500)).model_dump(mode='json')
            stream = await client.get(f'/api/v1/planner/jobs/{job_id}/events')
            assert stream.status_code == 200 and all(event in stream.text for event in ('specialist_update','evaluation','completed'))
            assert all(verify_evidence_hash(e) for e in saved.evidence)
            # Prove exact label identities from the real Loki response, not just query strings.
            log_evidence = [e for e in saved.evidence if e.category == 'agentic-log-window']
            assert log_evidence, 'No UID-scoped Loki evidence'
            expected = {(m['name'],m['uid']) for m in members if m['kind']=='Pod'}
            assert all((label.get('pod'), label.get('pod_uid')) in expected for e in log_evidence for label in e.facts['stream_labels'])
            trace_evidence = [e for e in saved.evidence if e.category == 'trace-causal-chain']
            assert len(trace_evidence) >= 2, 'Independent live causal traces missing'
            assert {c['body']['trace_id'] for c in calls} >= {e.facts['trace_id'] for e in trace_evidence}
            assert any(e.source == 'opentelemetry' for e in saved.evidence), 'Live service metrics missing'
            answer = await client.post(f'/api/v1/incidents/{incident_id}/copilot',json={'question':'What is the root cause?'})
            assert answer.status_code == 200
            cited = answer.json()['evidence_ids']
            assert set(cited) <= {e.id for e in saved.evidence}
            assert all(set(h.supporting_evidence) <= {e.id for e in saved.evidence} for h in saved.hypotheses)
            for denied in (replace(owner,tenant_id='unauthorized'),replace(owner,clusters=frozenset({'unauthorized'}))):
                principal[0] = denied
                for path in (f'/api/v1/incidents/{incident_id}',f'/api/v1/planner/jobs/{job_id}/events'):
                    response = await client.get(path)
                    assert response.status_code in (403,404) and not any(e.id in response.text for e in saved.evidence)
            principal[0] = owner
            expected_root = 'trace-causal-propagation'
            reported = saved.root_cause.hypothesis_id
            report['scenarios'].append({'name':'stateful-downstream-latency','expected':expected_root,'reported':reported,
                'passed':reported == expected_root and saved.status == 'resolved','citations':cited,
                'pod_uids':sorted(uid for _,uid in expected), 'live_trace_ids':sorted(e.facts['trace_id'] for e in trace_evidence)})
            # Expand only after the complete real-telemetry slice passes.
            assert report['scenarios'][-1]['passed'], 'Initial live RCA scenario failed'
            stateful, _, _ = await execute_scope(app,client,source_id,'StatefulSet','copilot-stateful','stateful-degradation',['trace-causal-propagation'],report)
            harness.kubectl(context,'set','env','deployment/rc3-workload','-n',harness.NAMESPACE,'VALIDATION_STARTUP_EXIT=42')
            def crashing():
                return next((p for p in harness.pods_for_label(context,'app=rc3-workload') if harness.terminated_reason(p)[1] == 42),None)
            crash = harness.wait_until('new rollout actually exited 42',crashing,timeout_seconds=90)
            report['rollout_failure_pod'] = crash
            rolling, _, _ = await execute_scope(app,client,source_id,'Deployment','rc3-workload','rolling-update-regression',['application-exit'],report)
            stale = await client.post(base+'/confirm',json=confirm)
            report['changed_membership_confirmation'] = {'status':stale.status_code,'passed':stale.status_code == 409}
            assert stale.status_code == 409
            # New healthy revision also verifies old failure telemetry is not reused.
            rollout_healthy(context,'copilot-recovery-'+uuid4().hex[:8])
            harness.kubectl(context,'set','env','statefulset/copilot-stateful','-n',harness.NAMESPACE,'DEPENDENCY_DELAY_SECONDS=0')
            harness.kubectl(context,'rollout','status','statefulset/copilot-stateful','-n',harness.NAMESPACE,'--timeout=180s')
            recovered = [await asyncio.to_thread(request_latency) for _ in range(3)]
            assert all(r['status'] == 200 and not r['body']['failed'] for r in recovered),recovered
            report['recovery_requests'] = recovered
            recovery, _, _ = await execute_scope(app,client,source_id,'Deployment','rc3-workload','recovery',[None],report)
            harness.kubectl(context,'set','resources','deployment/rc3-workload','-n',harness.NAMESPACE,'--limits=memory=128Mi')
            harness.kubectl(context,'rollout','status','deployment/rc3-workload','-n',harness.NAMESPACE,'--timeout=180s')
            harness.stop_port_forwards(); harness.start_observability_forwards(context)
            with urllib.request.urlopen(f'http://127.0.0.1:{harness.WORKLOAD_PORT}/oom',timeout=10) as allocation:
                assert allocation.status == 202
            def oom_state():
                return next((p for p in harness.pods_for_label(context,'app=rc3-workload') if harness.terminated_reason(p)[0] == 'OOMKilled'),None)
            report['oom_pod'] = harness.wait_until('instrumented workload OOMKilled',oom_state,timeout_seconds=90)
            oom, _, _ = await execute_scope(app,client,source_id,'Deployment','rc3-workload','oomkilled',['container-oom'],report)
            harness.kubectl(context,'scale','statefulset/copilot-stateful','-n',harness.NAMESPACE,'--replicas=0')
            harness.kubectl(context,'wait','--for=delete','pod/copilot-stateful-0','-n',harness.NAMESPACE,'--timeout=90s')
            zero, _, _ = await execute_scope(app,client,source_id,'StatefulSet','copilot-stateful','zero-replicas',[None],report)
            assert not any(m['kind']=='Pod' for m in zero['scope']['members'])
            assert next(s for s in zero['diagnosis']['agentic']['specialists'] if s['specialist']=='logs')['query_count'] == 0
            harness.kubectl(context,'annotate','statefulset/copilot-stateful','-n',harness.NAMESPACE,
                'ai-sre.io/telemetry-service='+oom['diagnosis']['incident']['service'],'--overwrite')
            await reconcile_kubernetes_inventory(repo,tools,settings)
            ambiguous = await client.post(base+'/proposals',json=body)
            report['ambiguous_identity'] = {'status':ambiguous.status_code,'response':ambiguous.json(),'passed':ambiguous.status_code == 409}
            assert report['ambiguous_identity']['passed']
            harness.kubectl(context,'annotate','statefulset/copilot-stateful','-n',harness.NAMESPACE,'ai-sre.io/telemetry-service=copilot-stateful','--overwrite')
            await reconcile_kubernetes_inventory(repo,tools,settings)
            # A real telemetry outage must reject a proposal before job creation.
            harness.kubectl(context,'scale','deployment/loki','-n',harness.NAMESPACE,'--replicas=0')
            harness.kubectl(context,'wait','--for=delete','pods','-l','app=loki','-n',harness.NAMESPACE,'--timeout=90s')
            unavailable = await client.post(base+'/proposals',json=body)
            report['missing_telemetry'] = {'status':unavailable.status_code,'response':unavailable.json(),'passed':unavailable.status_code == 503}
            assert report['missing_telemetry']['passed']
            report['passed'] = all(s['passed'] for s in report['scenarios'])
    except Exception as error:
        report['failure'] = {'type':type(error).__name__,'detail':str(error)}
    finally:
        report['duration_seconds'] = time.monotonic()-started
        report['untested_scenarios'] = sorted(set(report['planned_scenarios']) - {s['name'] for s in report['scenarios']})
        report['rca_accuracy'] = {'correct':sum(s['passed'] for s in report['scenarios']), 'tested':len(report['scenarios'])}
        (report_dir/'copilot-live.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
        await repo.close()
    return {'copilot':report, 'passed':report['passed']}
