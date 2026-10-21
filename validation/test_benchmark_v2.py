from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.db.repository import MemoryRepository
from validation import benchmark_v2 as bench
from validation.benchmark_cases import BlindTools, incident


def row(**updates):
    return dict(case='opaque',engine='classic',predicted_hypothesis=None,error=None,
        rejected_status=None,integrity_ok=True,latency_seconds=1,telemetry_query_count=0,
        model_tokens=0,model_cost_usd=0,**updates)


def truth(**updates):
    return {'category':'test','causes':[],'accept':[],'reason':'unsupported'}|updates


@pytest.mark.parametrize('prediction,error,integrity,correct,false_confirmation',[
    (None,None,True,True,False),('foreign',None,True,False,True),
    (None,{'type':'failure'},None,False,False),('oom',None,False,False,True),
])
def test_scoring_cannot_turn_errors_or_invalid_evidence_into_success(prediction,error,integrity,correct,false_confirmation):
    value=row();value.update(predicted_hypothesis=prediction,error=error,integrity_ok=integrity)
    result=bench.score(value,truth())
    assert result['correct'] is correct and result['false_confirmation'] is false_confirmation


def test_multiple_causes_keep_partial_diagnosis_visible():
    value=row();value['predicted_hypothesis']='a'
    result=bench.score(value,truth(causes=['a','b'],accept=['a','b']))
    assert not result['correct'] and result['primary_correct'] and result['partial_diagnosis']
    assert not result['false_confirmation'] and result['unreported_causes']==['b']
    assert not result['correct_abstention']


def test_expected_rejection_is_not_a_causal_abstention():
    value=row();value.update(rejected_status=409,integrity_ok=None)
    result=bench.score(value,truth(rejection=409))
    assert result['correct_rejection'] and not result['correct_abstention']
    value['rejected_status']=503
    assert not bench.score(value,truth(rejection=409))['correct']


def test_nearest_rank_latency_empty_single_and_tail():
    assert bench.percentile([],.5) is None
    assert bench.percentile([2],.95)==2
    assert bench.percentile(list(range(1,21)),.5)==10
    assert bench.percentile(list(range(1,21)),.95)==19


@pytest.mark.asyncio
async def test_snapshot_is_raw_immutable_and_closed_to_unknown_queries():
    collector=bench.SourceSnapshot(BlindTools('b002'))
    first=await collector.get_pod('apps','web-0');first['status']={}
    again=await collector.get_pod('apps','web-0')
    assert again['status'] and collector.network_calls['get_pod']==1
    snapshot=bench.SourceSnapshot(BlindTools('b001'),collector.records)
    assert await snapshot.get_pod('apps','web-0')==again
    with pytest.raises(RuntimeError,match='snapshot miss'):
        await snapshot.get_pod('apps','secret')
    with pytest.raises(AttributeError):
        snapshot.execute_shell
    assert 'expected' not in json.dumps(collector.records)


@pytest.mark.asyncio
async def test_actual_stale_authoritative_identity_gate_rejects():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await bench.gate_stale_inventory(MemoryRepository(),incident('b007'),Settings.model_construct())
    assert exc.value.status_code==409


@pytest.mark.asyncio
async def test_ground_truth_loaded_after_all_predictions(monkeypatch):
    events=[]
    async def compare(case_id,*args,**kwargs):
        events.append(case_id)
        return {'case':case_id,'results':[],'warmup_errors':[]}
    read=Path.read_text
    def guarded(path,*args,**kwargs):
        if path.name=='benchmark_truth_v2.json':
            assert events==list(bench.IDS)
        return read(path,*args,**kwargs)
    monkeypatch.setattr(bench,'compare_case',compare)
    monkeypatch.setattr(bench,'make_report',lambda cases,oracle,**kw: {'benchmark_version':2})
    monkeypatch.setattr(Path,'read_text',guarded)
    report=await bench.run_offline()
    assert report['benchmark_version']==2


@pytest.mark.asyncio
async def test_all_paths_use_same_input_no_model_and_fresh_context(monkeypatch):
    seen=[]
    async def execute(engine,data,tools,settings,repo,preflight=None):
        assert not settings.llm_enabled and not settings.agentic_model_planner_enabled
        assert not repo._orchestration_jobs
        seen.append((engine,data.model_dump(),id(repo)))
        raise RuntimeError('deliberately not diagnosed')
    monkeypatch.setattr(bench,'run_engine',execute)
    outcome=await bench.compare_case('opaque',incident('b001'),BlindTools('b001'),Settings.model_construct())
    assert len(seen)==6 and all(s[1]==seen[0][1] for s in seen)
    assert all(r['error'] and not r['predicted_hypothesis'] for r in outcome['results'])


def test_coverage_mismatch_cannot_hide_unrun_cases():
    with pytest.raises(ValueError,match='coverage mismatch'):
        bench.make_report([],{'cases':{'x':truth()},'suite':'test'},source='synthetic')


@pytest.mark.asyncio
async def test_replay_preserves_observed_source_failure_instead_of_missing_data():
    class Unavailable:
        async def get_pod(self,*args):
            import httpx
            request=httpx.Request('GET','http://source')
            raise httpx.HTTPStatusError('unavailable',request=request,response=httpx.Response(503,request=request))
    import httpx
    collector=bench.SourceSnapshot(Unavailable())
    with pytest.raises(httpx.HTTPStatusError):
        await collector.get_pod('apps','web-0')
    replay=bench.SourceSnapshot(Unavailable(),collector.records)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await replay.get_pod('apps','web-0')
    assert exc.value.response.status_code==503
    assert not replay.replay_failures


@pytest.mark.asyncio
async def test_snapshot_replay_cannot_bypass_query_budget():
    from app.core.query_budget import begin_query_budget,end_query_budget
    collector=bench.SourceSnapshot(BlindTools('b001'))
    await collector.get_pod('apps','web-0')
    replay=bench.SourceSnapshot(BlindTools('b001'),collector.records)
    token=begin_query_budget(Settings.model_construct(query_budget_max_calls=1))
    try:
        await replay.get_pod('apps','web-0')
        with pytest.raises(RuntimeError,match='budget exceeded'):
            await replay.get_pod('apps','web-0')
        assert replay.replay_failures
    finally:
        end_query_budget(token)


@pytest.mark.asyncio
async def test_missing_snapshot_read_cannot_be_swallowed_as_success(monkeypatch):
    async def catches(engine,data,tools,*args):
        try:
            await tools.get_pod('apps','not-recorded')
        except RuntimeError:
            pass
        return object(),None
    monkeypatch.setattr(bench,'run_engine',catches)
    value=await bench.trial('opaque','classic',incident('b001'),bench.SourceSnapshot(BlindTools('b001'),{}),Settings.model_construct(),MemoryRepository())
    assert value['error'] and 'snapshot miss' in value['error']['detail']


@pytest.mark.asyncio
async def test_evidence_tampering_and_foreign_hypothesis_reference_fail():
    result,_=await bench.run_engine('classic',incident('b003'),BlindTools('b003'),Settings.model_construct(),MemoryRepository())
    assert bench.validate_result(result)
    corrupt=deepcopy(result)
    corrupt.hypotheses[0].supporting_evidence.append('other-investigation')
    with pytest.raises(ValueError,match='reference'):
        bench.validate_result(corrupt)
    corrupt=deepcopy(result);corrupt.evidence[0].summary='changed'
    with pytest.raises(ValueError,match='integrity'):
        bench.validate_result(corrupt)


def test_partial_results_fail_complete_cause_score_and_report(tmp_path):
    rows=[]
    for engine in bench.ENGINES:
        item=row();item.update(engine=engine,predicted_hypothesis='a')
        rows.append(item)
    cases=[{'case':'opaque','results':rows,'warmup_errors':[]}]
    oracle={'suite':'test','cases':{'opaque':truth(causes=['a','b'],accept=['a','b'])}}
    report=bench.make_report(cases,oracle,source='synthetic')
    assert report['passed'] is False
    assert all(m['partial_diagnoses']==1 and m['false_confirmations']==0 and m['correct_diagnoses']==0 for m in report['metrics'].values())
    bench.write_report(report,tmp_path)
    assert json.loads((tmp_path/'benchmark-v2.json').read_text())['passed'] is False
    assert 'Partial' in (tmp_path/'benchmark-v2.md').read_text()
    cases[0]['results'].pop()
    with pytest.raises(ValueError,match='engine coverage'):
        bench.make_report(cases,oracle,source='synthetic')


@pytest.mark.asyncio
async def test_complete_blind_suite_never_uses_network_llm_or_oracle_inputs(monkeypatch):
    import httpx
    client=httpx.AsyncClient
    def internal_only(*args,**kwargs):
        assert isinstance(kwargs.get('transport'),httpx.ASGITransport), 'Unexpected external HTTP/LLM request'
        return client(*args,**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',internal_only)
    report=await bench.run_offline()
    assert len(report['results'])==24
    assert sum(r['false_confirmation'] for r in report['results'])==0
    assert all(m['correct_diagnoses']==1 and m['partial_diagnoses']==1 and m['correct_abstentions']==5
               and m['correct_rejections']==1 and m['errors']==0 for m in report['metrics'].values())
    assert not report['passed']  # Multi-cause coverage must remain an exposed limitation.
    for capture in report['captures']:
        serialized=json.dumps(capture['input'])
        assert all(key not in serialized for key in ('expected','accept','ground_truth','b00'))
    assert all(len({r['scope_digest'] for r in report['results'] if r['case']==case_id})==1 for case_id in bench.IDS)


@pytest.mark.asyncio
async def test_real_pilot_failure_remains_reproducible_without_network(monkeypatch):
    import httpx
    client=httpx.AsyncClient
    def internal_only(*args,**kwargs):
        assert isinstance(kwargs.get('transport'),httpx.ASGITransport)
        return client(*args,**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',internal_only)
    report=await bench.run_legacy_capture()
    assert not report['passed']
    assert report['metrics']['classic']['correct_abstentions']==1
    for path in ('agentic','copilot'):
        assert report['metrics'][path]['errors']==1
        assert report['metrics'][path]['correct_abstentions']==0
    assert all(r['predicted_hypothesis'] is None for r in report['results'])
    assert any(e['method']=='argocd_application' for e in report['source_errors'])


@pytest.mark.asyncio
async def test_sql_style_paths_are_isolated_by_unique_tenants(monkeypatch):
    tenants=[]
    async def execute(engine,data,tools,settings,repo,preflight=None):
        assert not hasattr(repo,'prior_trial')
        repo.prior_trial=True
        tenants.append(data.tenant_id)
        raise RuntimeError('deliberate trial marker')
    monkeypatch.setattr(bench,'run_engine',execute)
    report=await bench.compare_case('opaque',incident('b001'),BlindTools('b001'),Settings.model_construct(),repo_factory=MemoryRepository)
    assert len(tenants)==len(set(tenants))==6
    assert len({r['scope_digest'] for r in report['results']})==1


@pytest.mark.asyncio
async def test_replay_preserves_transport_failure_class():
    import httpx
    class Unavailable:
        async def get_pod(self,*args):
            raise httpx.ConnectError('unavailable',request=httpx.Request('GET','http://source'))
    captured=bench.SourceSnapshot(Unavailable())
    with pytest.raises(httpx.ConnectError):
        await captured.get_pod('apps','web-0')
    frozen=bench.SourceSnapshot(Unavailable(),captured.records)
    with pytest.raises(httpx.ConnectError):
        await frozen.get_pod('apps','web-0')
