"""Live Kind browser runner: no seeded telemetry or synthetic Kubernetes responses."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'validation-artifacts/live-browser'; OUT.mkdir(parents=True, exist_ok=True)
API = os.environ.get('VALIDATION_API','http://127.0.0.1:18096')
FRONTEND = os.environ.get('VALIDATION_FRONTEND','http://127.0.0.1:13012')
source = json.loads((ROOT/'validation-artifacts/copilot-live.json').read_text())['diagnosis']['incident_id']
observed = {'http':[], 'console':[], 'page':[], 'failed':[], 'confirmations':[], 'streams':[]}
with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width':390,'height':844},timezone_id='UTC')
    page.on('response',lambda r:observed['http'].append({'url':r.url,'status':r.status,'body':r.text()}) if r.status>=400 else None)
    page.on('response',lambda r:observed['streams'].append(r.url) if r.status==200 and r.url.endswith('/events') and 'text/event-stream' in r.headers.get('content-type','') else None)
    page.on('console',lambda m:observed['console'].append(m.text) if m.type=='error' else None)
    page.on('pageerror',lambda error:observed['page'].append(str(error)))
    page.on('requestfailed',lambda r:observed['failed'].append({'url':r.url,'method':r.method,'failure':r.failure}))
    page.on('request',lambda r:observed['confirmations'].append(r.post_data_json) if r.method=='POST' and r.url.endswith('/copilot/confirm') else None)
    page.goto(FRONTEND+'/incidents/'+source,wait_until='networkidle')
    panel = page.locator('[data-copilot-hydrated="true"]'); expect(panel).to_have_count(1)
    question = panel.get_by_role('textbox',name='Pergunta'); question.fill('Investigate the real Kubernetes workload')
    panel.get_by_label('Selecionar outro alvo ou período').check()
    expect(panel.get_by_label('Cluster autorizado')).to_be_visible()
    panel.get_by_label('Tipo de alvo').select_option('Deployment')
    panel.get_by_label('Namespace do alvo').fill('rca-validation'); panel.get_by_label('Nome do alvo').fill('rc3-workload')
    end = datetime.now(timezone.utc).replace(second=0,microsecond=0)
    panel.get_by_label('Início da consulta (hora local)').fill((end-timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M'))
    panel.get_by_label('Fim da consulta (hora local)').fill(end.strftime('%Y-%m-%dT%H:%M'))
    with page.expect_response(lambda r:r.url.endswith('/copilot/proposals')) as proposed:
        panel.get_by_role('button',name='Propor investigação').click()
    assert proposed.value.status == 200,proposed.value.text()
    scope = proposed.value.json()['incident']['workload']
    assert scope['kind']=='Deployment' and scope['members']
    expect(panel.locator('.copilotProposal')).to_contain_text('Deployment/rc3-workload')
    assert not observed['confirmations']
    with page.expect_response(lambda r:r.url.endswith('/copilot/confirm')) as confirmed:
        panel.get_by_role('button',name='Confirmar investigação').focus(); page.keyboard.press('Enter')
    assert confirmed.value.status == 202,confirmed.value.text()
    job = confirmed.value.json()['id']
    expect(panel.get_by_text('Investigação concluída. As próximas perguntas usam as evidências desta investigação.',exact=True)).to_be_visible(timeout=90000)
    question.fill('What is the root cause?')
    with page.expect_response(lambda r:r.url.endswith('/copilot')) as answered:
        panel.get_by_role('button',name='Perguntar').click()
    assert answered.value.status == 200 and answered.value.json()['mode']=='deterministic'
    incident_id = answered.value.url.split('/api/incidents/',1)[1].split('/',1)[0]
    response = page.request.get(API+'/api/v1/incidents/'+incident_id)
    assert response.status == 200
    saved = response.json()
    assert saved['incident']['workload'] == scope
    assert saved['root_cause']['hypothesis_id']=='container-oom'
    assert f'{FRONTEND}/api/orchestration/{job}/events' in observed['streams']
    assert panel.locator('.orchestrationAgents article').count() >= 3
    expect(panel.locator('.copilotAnswer')).to_be_visible()
    citations = panel.locator('.copilotAnswer a').all_text_contents()
    assert citations and set(citations) <= {e['id'] for e in saved['evidence']}
    assert not observed['http'] and not observed['console'] and not observed['page'],observed
    assert all(r['method']=='GET' and r['failure']=='net::ERR_ABORTED' and r['url']==f'{FRONTEND}/api/orchestration/{job}/events' for r in observed['failed']),observed
    page.screenshot(path=str(OUT/'live-mobile.png'),full_page=True)
    overflow = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e=>e instanceof HTMLElement && e.getBoundingClientRect().right > innerWidth+1).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,text:e.textContent.slice(0,160)}))""")
    (OUT/'overflow.json').write_text(json.dumps(overflow,indent=2)+'\n')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),overflow
    report = {'passed':True,'checks':['React hydration','live UID inventory review','explicit keyboard confirmation','persistent job and SSE','real OOM RCA and verified citations','mobile accessibility'],
        'job':job,'incident_id':incident_id,'scope':scope,'citations':citations,'observed':observed}
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':True,'checks':len(report['checks'])}))
    browser.close()
