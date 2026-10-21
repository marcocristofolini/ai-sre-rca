"""Blind comparative evaluation; oracle is read only after all predictions finish."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from app import benchmark as baseline
from app.agentic.persistence import materialize_agentic_diagnosis
from app.api.copilot_investigations import router as copilot_router
from app.api.incidents import router as incidents_router
from app.api.planner import router as planner_router, _run_orchestration_job
from app.auth import Principal, get_principal
from app.awareness.inventory import AuthoritativeInventorySnapshot, InventoryCoverage, InventoryResource
from app.config import Settings
from app.core.copilot_scope import verified_identity
from app.core.query_budget import consume_query, query_budget_snapshot
from app.core.integrity import verify_evidence_hash
from app.db.repository import MemoryRepository
from app.integrations.base import InvestigationTools
from app.schemas import IncidentInput, InvestigationResult, RootCause
from validation.benchmark_cases import IDS, BlindTools, incident
from validation.source_diagnostics import source_failure

ENGINES = ('classic', 'agentic', 'copilot')
READ_METHODS = frozenset(n for n in InvestigationTools.__dict__ if not n.startswith('_'))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()


class SourceSnapshot:
    """Memoize only raw allow-listed reads. Frozen replay rejects unseen reads.

    Collection is a warm-up, not a scored trial. No hypotheses, expected results,
    incident memory, prompts or evaluator output are cached.
    """
    def __init__(self, tools, records=None):
        self.tools = tools
        self.records = deepcopy(records) if records is not None else {}
        self.frozen = records is not None
        self.calls = Counter()
        self.network_calls = Counter()
        self.budget_charges = Counter()
        self.replay_failures = []
        self.lock = asyncio.Lock()

    def set_cluster(self, cluster):
        return self.tools.set_cluster(cluster) if hasattr(self.tools,'set_cluster') else None

    def reset_cluster(self, token):
        if hasattr(self.tools,'reset_cluster'):
            self.tools.reset_cluster(token)

    def __getattr__(self, name):
        if name not in READ_METHODS:
            raise AttributeError(name)
        async def read(*args, **kwargs):
            self.calls[name] += 1
            key = fingerprint([name,args,kwargs])
            async with self.lock:
                if key not in self.records:
                    if self.frozen:
                        self.replay_failures.append('snapshot miss: '+name)
                        raise RuntimeError('snapshot miss: '+name)
                    self.network_calls[name] += 1
                    before = (query_budget_snapshot() or {}).get('per_source',{})
                    observed_error = None
                    try:
                        value = await getattr(self.tools,name)(*args,**kwargs)
                    except Exception as exc:
                        value = None
                        observed_error = source_failure(exc)
                    after = (query_budget_snapshot() or {}).get('per_source',{})
                    charges = {source:count-before.get(source,0) for source,count in after.items() if count>before.get(source,0)}
                    if not charges:
                        source = next((s for s in ('prometheus','loki','tempo') if name.startswith(s)), 'kubernetes')
                        charges = {source:1}
                        if not consume_query(source):
                            self.replay_failures.append('capture budget exceeded')
                            raise RuntimeError('capture budget exceeded')
                    self.budget_charges.update(charges)
                    self.records[key] = {'method':name,'args':deepcopy(args),'kwargs':deepcopy(kwargs),'value':deepcopy(value),'charges':charges,'exception':observed_error}
                else:
                    charges = self.records[key]['charges']
                    for source,cost in charges.items():
                        if not consume_query(source,cost):
                            self.replay_failures.append('replay budget exceeded')
                            raise RuntimeError('replay budget exceeded')
                    self.budget_charges.update(charges)
                observation = self.records[key]
                if observation.get('exception'):
                    error = observation['exception']
                    if error['status'] is not None:
                        request = httpx.Request('GET','http://recorded-source.invalid')
                        raise httpx.HTTPStatusError('recorded source response',request=request,
                            response=httpx.Response(error['status'],request=request))
                    if error['type'] in {'ConnectError','ConnectTimeout','ReadTimeout','ReadError','WriteError','PoolTimeout','RemoteProtocolError'}:
                        raise getattr(httpx,error['type'])('recorded source failure',request=httpx.Request('GET','http://recorded-source.invalid'))
                    raise RuntimeError('recorded source failure: '+error['type'])
                return deepcopy(observation['value'])
        return read


async def gate_stale_inventory(repo, data, settings):
    # Exercise the real identity gate, not a hand-authored benchmark rejection.
    old = datetime.now(timezone.utc)-timedelta(minutes=settings.awareness_inventory_max_age_minutes+2)
    await repo.reconcile_authoritative_inventory(AuthoritativeInventorySnapshot(
        snapshot_id=uuid4().hex,source='kubernetes',tenant_id=data.tenant_id,cluster=data.cluster,
        observed_at=old,complete=True,coverage=InventoryCoverage(kinds=['Pod'],namespaces=['apps']),
        resources=[InventoryResource(kind='Pod',name='web-0',namespace='apps',metadata={'kubernetes_uid':'pod-uid'})]))
    entities = await repo.find_awareness_entities(data.tenant_id,data.cluster,kind='Pod',name='web-0',namespace='apps',limit=2)
    await verified_identity(repo,settings,entities[0])


def validate_result(result):
    evidence = {e.id:e for e in result.evidence}
    if len(evidence) != len(result.evidence) or not all(verify_evidence_hash(e) for e in evidence.values()):
        raise ValueError('evidence integrity failure')
    for h in result.hypotheses:
        if not set(h.supporting_evidence+h.contradicting_evidence) <= evidence.keys():
            raise ValueError('invalid hypothesis evidence reference')
    if result.status == 'resolved':
        root = result.root_cause.hypothesis_id
        match = next((h for h in result.hypotheses if h.id == root),None)
        if not root or not match or match.status != 'confirmed' or not match.supporting_evidence:
            raise ValueError('resolved result has no verified confirmed hypothesis')
    return True


async def run_engine(engine, data, tools, settings, repo, preflight=None):
    if preflight:
        await preflight(repo,data,settings)
    if engine == 'classic':
        return await baseline.InvestigationEngine(tools,settings=settings).investigate(data), None
    if engine == 'agentic':
        plan = await baseline.build_investigation_plan(repo,incident=data,settings=settings)
        execution = await baseline.execute_investigation_plan(plan,tools=tools,settings=settings)
        if any(s.status == 'failed' for s in execution.specialist_results):
            raise RuntimeError('specialist execution failed: '+json.dumps([{'specialist':s.specialist,'error_type':s.error_type} for s in execution.specialist_results if s.status=='failed']))
        return materialize_agentic_diagnosis(execution,baseline.evaluate_shared_evidence(execution)), None
    # Use the existing proposal / confirmation / persistent job / SSE APIs.
    app = FastAPI()
    app.state.repo, app.state.settings, app.state.tools = repo,settings,tools
    app.state.orchestration_worker_id = 'benchmark-worker-'+uuid4().hex
    owner = Principal(subject='benchmark-actor',tenant_id=data.tenant_id,
        roles=frozenset({'investigator'}),clusters=frozenset({data.cluster}))
    app.dependency_overrides[get_principal] = lambda: owner
    for router in (incidents_router,copilot_router,planner_router):
        app.include_router(router)
    source = InvestigationResult(incident_id='source-'+uuid4().hex,incident=data,status='inconclusive',
        entities=[],evidence=[],hypotheses=[],timeline=[],remediation=[],
        root_cause=RootCause(title='Pending investigation',summary='No diagnosis recorded',evidence_strength='weak',supporting_signals=0,contradicting_signals=0))
    await repo.save(source)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://benchmark') as client:
        base = f'/api/v1/incidents/{source.incident_id}/copilot'
        body = {'question':'Investigate the observed service alert'}
        proposed = await client.post(base+'/proposals',json=body)
        proposed.raise_for_status()
        confirmed = await client.post(base+'/confirm',json=body|{'proposal_id':proposed.json()['proposal_id'],'confirmed':True})
        confirmed.raise_for_status()
        job_id = confirmed.json()['id']
        retry = await client.post(base+'/confirm',json=body|{'proposal_id':proposed.json()['proposal_id'],'confirmed':True})
        assert retry.json()['id'] == job_id
        await _run_orchestration_job(app,job_id)
        job = await repo.get_orchestration_job(job_id,data.tenant_id)
        if job['status'] != 'completed':
            raise RuntimeError('job failed: '+str(job.get('error_type')))
        result = await repo.get(job['result']['incident_id'],tenant_id=data.tenant_id)
        stream = await client.get(f'/api/v1/planner/jobs/{job_id}/events')
        assert stream.status_code == 200 and 'event: completed' in stream.text
        qa = await client.post(f'/api/v1/incidents/{result.incident_id}/copilot',json={'question':'What is the root cause?'})
        qa.raise_for_status()
        answer = qa.json()
        assert set(answer['evidence_ids']) <= {e.id for e in result.evidence}
        assert answer['mode'] == 'deterministic'
        assert answer['insufficient_evidence'] == (result.status != 'resolved')
        if result.agentic and any(s.status=='failed' for s in result.agentic.specialists):
            raise RuntimeError('specialist execution failed: '+json.dumps([{'specialist':s.specialist,'error_type':s.error_type} for s in result.agentic.specialists if s.status=='failed']))
        return result,answer


async def trial(case_id, engine, data, tools, settings, repo, preflight=None):
    start = time.perf_counter()
    row = {'case':case_id,'engine':engine,'predicted_hypothesis':None,'error':None,
        'rejected_status':None,'integrity_ok':None,'evidence_ids':[], 'citations':[],
        'input_digest':fingerprint(data.model_dump(mode='json')),
        'model_tokens':0,'model_cost_usd':0,'model_measurement':'LLM disabled; no model requests'}
    try:
        result,answer = await run_engine(engine,data,tools,settings,repo,preflight)
        if getattr(result,'status',None) == 'resolved':
            row['predicted_hypothesis'] = result.root_cause.hypothesis_id
        if tools.replay_failures:
            raise RuntimeError('; '.join(tools.replay_failures))
        row['integrity_ok'] = validate_result(result)
        row['evidence_ids'] = [e.id for e in result.evidence]
        row['citations'] = answer['evidence_ids'] if answer else []
        row['diagnosis'] = result.model_dump(mode='json')
    except HTTPException as exc:
        row['rejected_status'] = exc.status_code
    except Exception as exc:
        row['error'] = {'type':type(exc).__name__,'detail':str(exc)[:500]}
    row['evaluated_input'] = data.model_dump(mode='json')
    row['latency_seconds'] = time.perf_counter()-start
    row['query_calls'] = dict(sorted(tools.calls.items()))
    row['budget_charges'] = dict(tools.budget_charges)
    row['telemetry_query_count'] = sum(v for k,v in tools.budget_charges.items() if k in ('prometheus','loki','tempo'))
    return row


async def compare_case(case_id, data, raw_tools, settings, repo_factory=None, preflight=None, records=None):
    """Fresh context for every warm-up and scored path; oracle unavailable here."""
    settings = settings.model_copy(update={'llm_enabled':False,'agentic_model_planner_enabled':False,'agentic_execution_enabled':True})
    collector = SourceSnapshot(raw_tools)
    async def run(engine, tools):
        repo = repo_factory() if repo_factory else MemoryRepository()
        try:
            isolated = data.model_copy(deep=True)
            if repo_factory:
                # A unique authorized benchmark tenant prevents SQL memory/graph
                # from prior warm-ups or regressions entering this investigation.
                isolated.tenant_id = 'blind-'+uuid4().hex
            result = await trial(case_id,engine,isolated,tools,settings,repo,preflight)
            scope_data=isolated.model_dump(mode='json',exclude={'tenant_id'})
            scope_data['labels'].pop('copilot_target_id',None)
            result['scope_digest'] = fingerprint(scope_data)
            return result
        finally:
            if hasattr(repo,'close'):
                await repo.close()
    collected_at = time.perf_counter()
    warmup = [await run(engine,collector) for engine in ENGINES] if records is None else []
    if records is not None:
        collector.records = deepcopy(records)
    collection_seconds = time.perf_counter()-collected_at
    # Never turn collection errors into successful abstentions or incomplete replay.
    rows = []
    for engine in ENGINES:
        frozen = SourceSnapshot(raw_tools,collector.records)
        rows.append(await run(engine,frozen))
    return {'case':case_id,'input':data.model_dump(mode='json'),'snapshot_digest':fingerprint(collector.records),
        'snapshot':collector.records,'collection_seconds':collection_seconds,
        'source_calls':dict(collector.network_calls),'warmup_errors':[r['error'] for r in warmup if r['error']],
        'results':rows}


def percentile(values, fraction):
    """Nearest rank; explicitly defined for small bounded samples."""
    values = sorted(values)
    return values[max(0,math.ceil(len(values)*fraction)-1)] if values else None


def score(row, truth):
    row = dict(row)
    if row['rejected_status'] is None and row['integrity_ok'] is not True and row['error'] is None:
        row['error'] = {'type':'UnverifiedEvidence','detail':'Evidence verification did not pass'}
    prediction = row['predicted_hypothesis']
    rejection = row['rejected_status']
    failed = row['error'] is not None or (rejection is None and row['integrity_ok'] is not True)
    expected_rejection = truth.get('rejection')
    confirmed = prediction is not None
    correct = not failed and ((rejection == expected_rejection and rejection is not None and not confirmed)
        if expected_rejection else (rejection is None and (prediction in truth['accept'] if truth['accept'] else not confirmed)))
    missing = [cause for cause in truth['causes'] if cause != prediction]
    primary_correct = bool(correct)
    correct = primary_correct and not missing
    return row | {'primary_correct':primary_correct,'partial_diagnosis':primary_correct and bool(missing),
        'category':truth['category'],'expected_causes':truth['causes'],'accepted_hypotheses':truth['accept'],
        'correct':bool(correct),'false_confirmation':bool(confirmed and (failed or expected_rejection or prediction not in truth['accept'])),
        'correct_abstention':bool(correct and not confirmed and not rejection),
        'correct_rejection':bool(correct and rejection),
        'unreported_causes':missing,
        'failure_reason':None if correct else (row['error'] or truth['reason'])}


def make_report(cases, oracle, *, source, metadata=None):
    if len(cases) != len(oracle['cases']) or {c['case'] for c in cases} != set(oracle['cases']):
        raise ValueError('case/oracle coverage mismatch')
    for case in cases:
        scopes={r.get('scope_digest') for r in case['results']}
        if len(scopes)>1:
            raise ValueError('scope mismatch between paths')
        if sorted(r['engine'] for r in case['results']) != sorted(ENGINES):
            raise ValueError('engine coverage mismatch')
    rows=[score(row,oracle['cases'][c['case']]) for c in cases for row in c['results']]
    summary={}
    for engine in ENGINES:
        selected=[r for r in rows if r['engine']==engine]
        latencies=[r['latency_seconds'] for r in selected if r['error'] is None and r['rejected_status'] is None]
        summary[engine]={
            'total':len(selected),'correct_diagnoses':sum(r['correct'] and r['predicted_hypothesis'] is not None for r in selected),
            'partial_diagnoses':sum(r['partial_diagnosis'] for r in selected),
            'incorrect_diagnoses':sum(not r['primary_correct'] and r['predicted_hypothesis'] is not None for r in selected),
            'inconclusive':sum(r['predicted_hypothesis'] is None and r['error'] is None and r['rejected_status'] is None for r in selected),
            'correct_abstentions':sum(r['correct_abstention'] for r in selected),
            'correct_rejections':sum(r['correct_rejection'] for r in selected),
            'false_confirmations':sum(r['false_confirmation'] for r in selected),
            'errors':sum(r['error'] is not None for r in selected),
            'integrity_passed':sum(r['integrity_ok'] is True for r in selected),
            'latency_samples':len(latencies),'p50_seconds':percentile(latencies,.5),
            'p95_seconds':percentile(latencies,.95),
            'telemetry_query_count':sum(r['telemetry_query_count'] for r in selected),
            'model_tokens':sum(r['model_tokens'] for r in selected),'model_cost_usd':0,
            'telemetry_cost_usd':None,'infrastructure_cost_usd':None}
    return {'benchmark_version':2,'suite':oracle['suite'],'source':source,
        'latency_measurement':'Frozen-response investigation wall time; excludes network capture, errors and rejections; nearest-rank percentiles',
        'oracle_digest':fingerprint(oracle),'metadata':metadata or {},'metrics':summary,'results':rows,
        'source_errors':[{"case":c['case'],"method":entry['method'],"error":entry['exception']}
            for c in cases for entry in c.get('snapshot',{}).values() if entry.get('exception')],
        'captures':[{k:v for k,v in c.items() if k != 'results'} for c in cases],
        'passed':all(r['correct'] for r in rows) and not any(c['warmup_errors'] for c in cases),
        'limitations':['Copilot reuses agentic RCA; it is not an independent causal engine.',
            'No model quality comparison: LLM disabled, tokens and cost zero.',
            'Multi-cause recall is exposed per case; partial diagnoses fail complete-cause scoring without counting a true cause as a false confirmation.',
            'Replay latency excludes live source latency; live collection wall time is separate.',
            'CPU/network energy and telemetry/infrastructure cost measurements unavailable.',
            'Cases are blinded to runtime inputs, not independently adjudicated by an external team.']}


def write_report(report, directory):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'benchmark-v2.json').write_text(json.dumps(report,indent=2,sort_keys=True,default=str)+'\n')
    lines=['# RCA Benchmark v2','',f"Suite: `{report['suite']}`; source: {report['source']}",
        '',report['latency_measurement'],'',
        '| Path | Correct | Partial | Incorrect | Inconclusive | Correct abstentions | Rejections | False confirmations | Errors | p50 s | p95 s |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for engine,m in report['metrics'].items():
        lines.append(f"| {engine} | {m['correct_diagnoses']} | {m['partial_diagnoses']} | {m['incorrect_diagnoses']} | {m['inconclusive']} | {m['correct_abstentions']} | {m['correct_rejections']} | {m['false_confirmations']} | {m['errors']} | {m['p50_seconds'] if m['p50_seconds'] is not None else 'n/a'} | {m['p95_seconds'] if m['p95_seconds'] is not None else 'n/a'} |")
    lines+=['','| Case | Category | Path | Prediction | Correct | Integrity | Failure / missing causes |',
        '| --- | --- | --- | --- | --- | --- | --- |']
    for r in report['results']:
        detail=str(r['failure_reason'] or r['unreported_causes']).replace('|','/').replace('\n',' ')
        lines.append(f"| {r['case']} | {r['category']} | {r['engine']} | {r['predicted_hypothesis'] or 'none'} | {r['correct']} | {r['integrity_ok']} | {detail} |")
    lines+=['','## Captured source errors','',json.dumps(report['source_errors'],indent=2),'']
    lines+=['','## Costs','', 'Query counts count replayed adapter calls, not unique backend HTTP requests. Raw capture counts are separate. Model tokens/cost: 0 (LLM disabled). Other unavailable costs are stated explicitly.','', '## Limits','']+['- '+v for v in report['limitations']]
    (directory/'benchmark-v2.md').write_text('\n'.join(lines)+'\n')


async def run_offline():
    settings=Settings.model_construct(agentic_execution_enabled=True)
    cases=[]
    for case_id in IDS:
        cases.append(await compare_case(case_id,incident(case_id),BlindTools(case_id),settings,
            preflight=gate_stale_inventory if case_id=='b007' else None))
    # Scoring oracle is deliberately loaded after all collection and predictions.
    oracle=json.loads(Path(__file__).with_name('benchmark_truth_v2.json').read_text())
    return make_report(cases,oracle,source='controlled synthetic observations; no live telemetry')


async def run_legacy_capture():
    """Retain the pilot-discovered legacy lookup failure as an explicit case.

    Frozen real observations contain no expected answers or prior diagnoses.
    Memory-backed replay measures no network or PostgreSQL performance.
    """
    stored=json.loads(Path(__file__).with_name('fixtures').joinpath('benchmark-v2','legacy-observations.json').read_text())
    if fingerprint(stored['snapshot']) != stored['snapshot_digest']:
        raise ValueError('snapshot digest mismatch')
    settings=Settings.model_construct(agentic_execution_enabled=True,stateful_dependency_analysis_enabled=False)
    capture=await compare_case('l004',IncidentInput.model_validate(stored['input']),BlindTools('b001'),settings,records=stored['snapshot'])
    oracle=json.loads(Path(__file__).with_name('benchmark_legacy_truth_v2.json').read_text())
    return make_report([capture],oracle,source='frozen real pilot observations; memory-backed replay',metadata=stored['provenance'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-capture',action='store_true',help='Retained live-pilot failure, frozen offline replay')
    parser.add_argument('--replay-report',type=Path,help='Replay only raw captures/inputs; never inject scored annotations')
    parser.add_argument('--report-dir',type=Path,default=Path('benchmark-artifacts'))
    args=parser.parse_args()
    async def replay():
        stored=json.loads(args.replay_report.read_text())
        if stored['suite']=='independent-live-v2':
            raise ValueError('Live inventory must be freshly authorized; reproduce using validation/run-docker.sh --benchmark-v2 full')
        settings=Settings.model_construct(agentic_execution_enabled=True)
        cases=[]
        for capture in stored['captures']:
            case_id=capture['case']
            if fingerprint(capture['snapshot']) != capture['snapshot_digest']:
                raise ValueError('snapshot digest mismatch')
            cases.append(await compare_case(case_id,IncidentInput.model_validate(capture['input']),BlindTools(case_id),settings,
                records=capture['snapshot'],preflight=gate_stale_inventory if case_id=='b007' else None))
        oracle=json.loads(Path(__file__).with_name('benchmark_truth_v2.json').read_text())
        return make_report(cases,oracle,source='replayed raw observations; no live queries')
    if args.legacy_capture and args.replay_report:
        parser.error('select legacy capture or replay report')
    report=asyncio.run(run_legacy_capture() if args.legacy_capture else replay() if args.replay_report else run_offline())
    write_report(report,args.report_dir)
    return int(not report['passed'])


if __name__=='__main__':
    raise SystemExit(main())
