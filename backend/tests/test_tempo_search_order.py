from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.integrations.http_tools import ReadOnlyHTTPTools


@pytest.mark.asyncio
async def test_bounded_tempo_search_prefers_errors_then_recent_timestamps(monkeypatch):
    original = httpx.AsyncClient
    def handle(request):
        error = 'status = error' in request.url.params['q']
        return httpx.Response(200,json={'traces':[
            {'traceID':'1' if error else '3','startTimeUnixNano':'1'},
            {'traceID':'2' if error else '4','startTimeUnixNano':'20'},
        ]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    tools = ReadOnlyHTTPTools(Settings(tempo_url='http://tempo'))
    end = datetime.now(timezone.utc)
    traces = await tools.tempo_search_traces('authorized-service',end-timedelta(minutes=15),end,8)
    assert [t['traceID'] for t in traces] == [value.zfill(32) for value in ('2','1','4','3')]


def test_trace_ids_preserve_leading_zero_identity_and_reject_invalid_ids():
    from app.core.trace_ids import canonical_trace_id
    assert canonical_trace_id('6f3e557c12d33dd3a1a86a506b90ef0') == '06f3e557c12d33dd3a1a86a506b90ef0'
    assert canonical_trace_id('ABC') == '00000000000000000000000000000abc'
    for invalid in ('', '0','0'*32,'a'*33,'../secrets','not-a-trace-id',None,123):
        assert canonical_trace_id(invalid) is None


@pytest.mark.asyncio
async def test_aliases_of_one_trace_are_not_counted_as_independent_traces(monkeypatch):
    original = httpx.AsyncClient
    def handle(request):
        alias = 'abc' if 'status = error' in request.url.params['q'] else 'abc'.zfill(32)
        return httpx.Response(200,json={'traces':[{'traceID':alias,'startTimeUnixNano':'1'},{'traceID':'../invalid'}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    tools = ReadOnlyHTTPTools(Settings(tempo_url='http://tempo'))
    end = datetime.now(timezone.utc)
    traces = await tools.tempo_search_traces('authorized-service',end-timedelta(minutes=15),end,8)
    assert [t['traceID'] for t in traces] == ['abc'.zfill(32)]


def test_otlp_hex_and_base64_ids_keep_the_same_wire_identity():
    import base64
    from app.core.trace_analysis import _id_to_hex
    trace = '06f3e557c12d33dd3a1a86a506b90ef0'
    assert _id_to_hex(trace) == trace
    assert _id_to_hex(base64.b64encode(bytes.fromhex(trace)).decode()) == trace
    assert _id_to_hex('01'*8) == '01'*8


@pytest.mark.asyncio
async def test_mismatched_fetched_trace_identity_is_not_workload_evidence():
    from app.agentic.specialists import _traces
    from app.agentic.models import SpecialistTask, SpecialistScope
    from app.integrations.demo import DemoTools
    from app.schemas import IncidentInput, WorkloadScope
    class MismatchedTraceTools(DemoTools):
        async def tempo_search_traces(self, *args):
            return [{'traceID':'a'*32}]
        async def tempo_get_trace(self, *args):
            return {'resourceSpans':[{'resource':{'attributes':[{'key':'service.name','value':{'stringValue':'owned-service'}}]},
                'scopeSpans':[{'spans':[{'traceId':'b'*32,'spanId':'01'*8,'name':'incorrect trace',
                    'startTimeUnixNano':'1','endTimeUnixNano':'1000000000','status':{'code':2}}]}]}]}
    incident = IncidentInput(title='Authorized workload',namespace='apps',service='owned-service',workload=WorkloadScope(kind='Deployment',name='owned',uid='uid',members=[]))
    task = SpecialistTask(id='traces-test',specialist='traces',objective='Verify identity',rationale='Reject mismatched references',scope=SpecialistScope(service='owned-service'),max_queries=10)
    result = await _traces(task,incident,MismatchedTraceTools(),Settings())
    assert not result.evidence and not result.hypotheses and not result.trace_analyses
