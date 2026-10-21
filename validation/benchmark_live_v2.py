"""Bounded independent live cases on the existing disposable Kind stack."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import time
import urllib.request
from uuid import uuid4

from app.db.repository import SQLRepository
from app.awareness.kubernetes_inventory import KubernetesInventoryCollector
from app.core.copilot_scope import resolve_scope, ScopeSelection
from app.auth import Principal
from app.schemas import InvestigationResult, RootCause
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.schemas import IncidentInput
from app.core.trace_ids import canonical_trace_id
from validation import run as harness
from validation.benchmark_v2 import compare_case, make_report, write_report
from validation.copilot import request_latency, rollout_healthy, validate_copilot


async def indexed_requests(tools, service, expected_status):
    responses=[await asyncio.to_thread(request_latency) for _ in range(3)]
    if not all(r['status']==expected_status for r in responses):
        raise RuntimeError('controlled HTTP status mismatch: '+json.dumps(responses))
    ids={canonical_trace_id(r['body']['trace_id']) for r in responses}
    token=tools.set_cluster(harness.LOGICAL_CLUSTER)
    try:
        for _ in range(30):
            traces=await tools.tempo_search_traces(service,harness.utcnow()-timedelta(minutes=15),harness.utcnow(),8)
            if ids <= {canonical_trace_id(t.get('traceID')) for t in traces}:
                return responses
            await asyncio.sleep(2)
        raise RuntimeError('independent live trace indexing barrier failed')
    finally:
        tools.reset_cluster(token)


def live_input(context, service):
    pods=[p for p in harness.pods_for_label(context,'app=rc3-workload') if not p['metadata'].get('deletionTimestamp')]
    if len(pods)!=1:
        raise RuntimeError('independent benchmark requires exactly one current caller Pod')
    end=harness.utcnow().replace(microsecond=0)
    return IncidentInput(title='Investigate observed service alert',tenant_id='benchmark',
        cluster=harness.LOGICAL_CLUSTER,namespace=harness.NAMESPACE,pod=pods[0]['metadata']['name'],
        service=service,alert_name='ServiceAlert',starts_at=end-timedelta(minutes=15),ends_at=end)


async def validate_benchmark(context, settings, report_dir, stage='smoke'):
    directory=report_dir/'benchmark-v2'
    directory.mkdir(parents=True,exist_ok=True)
    cases=[]; events=[]; report=None; failure=None; regressions=None
    planned=['l001','l002']+(['l003'] if stage=='full' else [])
    settings=settings.model_copy(update={'query_budget_enabled':True,'agentic_execution_enabled':True,
        'llm_enabled':False,'agentic_model_planner_enabled':False})
    repo=SQLRepository(settings.database_url)
    await repo.initialize();await repo.close()
    tools=ReadOnlyHTTPTools(settings)
    started=time.perf_counter()
    async def evaluate(case_id, service):
        data=live_input(context,service)
        collection=await KubernetesInventoryCollector(tools,page_size=settings.awareness_inventory_page_size).collect_cluster(
            tenant_id='benchmark',cluster=harness.LOGICAL_CLUSTER,kinds=settings.awareness_inventory_kind_list,
            namespaces=[harness.NAMESPACE])
        captured_health=await tools.capability_report(harness.LOGICAL_CLUSTER)
        async def capability_report(cluster):
            assert cluster==harness.LOGICAL_CLUSTER
            return deepcopy(captured_health)
        async def prepare(repo, target, config):
            for snapshot in collection.snapshots:
                await repo.reconcile_authoritative_inventory(snapshot.model_copy(update={'tenant_id':target.tenant_id},deep=True))
            source=InvestigationResult(incident_id='source-context',incident=target,status='inconclusive',
                entities=[],evidence=[],hypotheses=[],timeline=[],remediation=[],
                root_cause=RootCause(title='Pending',summary='Not investigated',evidence_strength='weak',supporting_signals=0,contradicting_signals=0))
            request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=repo,settings=config,
                tools=SimpleNamespace(capability_report=capability_report))))
            principal=Principal(subject='benchmark-actor',tenant_id=target.tenant_id,
                roles=frozenset({'investigator'}),clusters=frozenset({target.cluster}))
            selection=ScopeSelection(cluster=target.cluster,kind='Deployment',name='rc3-workload',
                namespace=target.namespace,starts_at=target.starts_at,ends_at=target.ends_at)
            resolved,_,_=await resolve_scope(source,selection,request,principal,'Investigate observed service alert')
            for name in type(target).model_fields:
                setattr(target,name,getattr(resolved,name))
        captured=await compare_case(case_id,data,tools,settings,preflight=prepare,
            repo_factory=lambda:SQLRepository(settings.database_url))
        captured['inventory']=collection.model_dump(mode='json')
        cases.append(captured)
        # Preserve observations on every completed case, before any oracle load.
        (directory/'blind-captures.json').write_text(json.dumps(cases,indent=2,default=str)+'\n')
        if captured['warmup_errors'] or any(r['error'] for r in captured['results']):
            raise RuntimeError('independent case collection/execution failed: '+case_id)
    try:
        health=await tools.capability_report(harness.LOGICAL_CLUSTER)
        if not {'kubernetes','prometheus','loki','tempo'} <= {r['source'] for r in health if r['status']=='healthy'}:
            raise RuntimeError('required live sources unavailable: '+json.dumps(health))
        # Stage 1: a real healthy rollout is misleading temporal context.
        service='svc-'+uuid4().hex[:10]
        harness.kubectl(context,'set','env','statefulset/copilot-stateful','-n',harness.NAMESPACE,'DEPENDENCY_DELAY_SECONDS=0')
        harness.kubectl(context,'rollout','status','statefulset/copilot-stateful','-n',harness.NAMESPACE,'--timeout=180s')
        rollout_healthy(context,service)
        events.append({'case':'l001','http':await indexed_requests(tools,service,200)})
        await evaluate('l001',service)
        # Stage 2: two independently observed faults coexist in the same scope.
        harness.kubectl(context,'set','env','statefulset/copilot-stateful','-n',harness.NAMESPACE,'DEPENDENCY_DELAY_SECONDS=0.8')
        harness.kubectl(context,'rollout','status','statefulset/copilot-stateful','-n',harness.NAMESPACE,'--timeout=180s')
        harness.kubectl(context,'set','resources','deployment/rc3-workload','-n',harness.NAMESPACE,'--limits=memory=128Mi')
        harness.kubectl(context,'rollout','status','deployment/rc3-workload','-n',harness.NAMESPACE,'--timeout=180s')
        harness.stop_port_forwards();harness.start_observability_forwards(context)
        responses=await indexed_requests(tools,service,504)
        with urllib.request.urlopen(f'http://127.0.0.1:{harness.WORKLOAD_PORT}/oom',timeout=10) as response:
            assert response.status==202
        def oom_state():
            return next((p for p in harness.pods_for_label(context,'app=rc3-workload') if harness.terminated_reason(p)[0]=='OOMKilled'),None)
        oom=harness.wait_until('independent workload memory termination',oom_state,timeout_seconds=90)
        events.append({'case':'l002','http':responses,'kubernetes':oom})
        await evaluate('l002',service)
        if stage=='full':
            # Fresh service identity checks recovery isolation from prior failures.
            service='svc-'+uuid4().hex[:10]
            harness.kubectl(context,'set','env','statefulset/copilot-stateful','-n',harness.NAMESPACE,'DEPENDENCY_DELAY_SECONDS=0')
            harness.kubectl(context,'rollout','status','statefulset/copilot-stateful','-n',harness.NAMESPACE,'--timeout=180s')
            rollout_healthy(context,service)
            events.append({'case':'l003','http':await indexed_requests(tools,service,200)})
            await evaluate('l003',service)
            # Preserve all six original real scenarios, assertions and failures.
            regressions=await validate_copilot(context,settings,report_dir)
    except Exception as exc:
        failure={'type':type(exc).__name__,'detail':harness.redact(str(exc))}
    finally:
        # Truth is inaccessible to engine calls and is loaded only after execution.
        oracle=json.loads(Path(__file__).with_name('benchmark_live_truth_v2.json').read_text())
        selected_oracle={'suite':oracle['suite'],'cases':{k:oracle['cases'][k] for k in planned}}
        if {c['case'] for c in cases}==set(planned):
            report=make_report(cases,selected_oracle,source='live production adapters captured from disposable Kind',
                metadata={'stage':stage,'source_health':locals().get('health',[]),'fault_observations':events,
                    'live_elapsed_seconds':time.perf_counter()-started,
                    'state_isolation':'unique benchmark tenant per warm-up and scored path; PostgreSQL jobs/audit',
                    'regressions':regressions,'failure':failure})
            report['passed']=report['passed'] and failure is None and (stage!='full' or bool(regressions and regressions['passed']))
            write_report(report,directory)
        else:
            report={'passed':False,'failure':failure,'completed_cases':[c['case'] for c in cases],
                'untested_cases':sorted(set(planned)-{c['case'] for c in cases}),
                'fault_observations':events,'source_health':locals().get('health',[])}
            (directory/'benchmark-v2.json').write_text(json.dumps(report,indent=2,default=str)+'\n')
            (directory/'benchmark-v2.md').write_text('# RCA Benchmark v2 — incomplete\n\n'+json.dumps(report,indent=2,default=str)+'\n')
    return {'benchmark_v2':report,'passed':report['passed']}
