"""Dynamic scope: authoritative targets, time bounds, confirmation and persisted RCA."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/copilot-scope-ui'; OUT.mkdir(parents=True, exist_ok=True)
API = os.environ.get('VALIDATION_API', 'http://127.0.0.1:18084')
FRONTEND = 'http://127.0.0.1:13006'
STANDALONE = ROOT / 'frontend/.next/standalone'
assert (STANDALONE / '.next/static').is_dir() and (STANDALONE / 'public').is_dir()
with urlopen(API + '/api/v1/incidents/demo') as response: source = json.load(response)
primary = source['incident']['cluster']; secondary = 'scope-secondary'


def seed(cluster, uid='scope-uid'):
    payload = {'snapshot_id': uuid4().hex, 'source': 'kubernetes', 'observed_at': datetime.now(timezone.utc).isoformat(), 'complete': True,
        'coverage': {'kinds': ['Pod', 'Service'], 'namespaces': ['scope-apps']},
        'resources': [{'kind': 'Pod', 'name': 'scope-worker', 'namespace': 'scope-apps', 'metadata': {'kubernetes_uid': uid}},
            {'kind': 'Service', 'name': 'scope-api', 'namespace': 'scope-apps', 'metadata': {'kubernetes_uid': uid + '-service', 'telemetry_service': 'frontend'}}]}
    with urlopen(Request(API + '/api/v1/awareness/inventory/reconcile?cluster=' + cluster, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})) as response:
        assert response.status == 200


for cluster in (primary, secondary): seed(cluster)
observed = {'http_errors': [], 'console_errors': [], 'request_failures': [], 'page_errors': [], 'confirmations': []}
expected_http = []; negative = {'url': None, 'status': None}; job_ids = []; checks = []
with (OUT / 'frontend.log').open('w') as log:
    server = subprocess.Popen(['node', 'server.js'], cwd=STANDALONE, env=dict(os.environ, API_URL=API, HOSTNAME='127.0.0.1', PORT='13006'), stdout=log, stderr=log)
    try:
        for _ in range(60):
            try:
                with urlopen(FRONTEND + '/incidents/' + source['incident_id'], timeout=5): break
            except OSError:
                if server.poll() is not None: raise RuntimeError('Frontend exited')
                time.sleep(.5)
        else: raise RuntimeError('Frontend readiness timed out')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 390, 'height': 844}, locale='pt-BR', timezone_id='UTC')
            page.on('response', lambda r: observed['http_errors'].append({'url': r.url, 'status': r.status, 'response': r.text()}) if r.status >= 400 else None)
            page.on('requestfailed', lambda r: observed['request_failures'].append({'url': r.url, 'method': r.method, 'failure': r.failure}))
            page.on('pageerror', lambda err: observed['page_errors'].append(str(err)))
            page.on('request', lambda r: observed['confirmations'].append(r.post_data_json) if r.method == 'POST' and r.url.endswith('/copilot/confirm') else None)
            def console(message):
                if message.type == 'error': observed['console_errors'].append({'text': message.text, 'url': message.location.get('url'), 'expected': message.location.get('url') == negative['url'] and f"status of {negative['status']}" in message.text})
            page.on('console', console)
            page.goto(FRONTEND + '/incidents/' + source['incident_id'], wait_until='networkidle')
            panel = page.locator('[data-copilot-hydrated="true"]'); expect(panel).to_have_count(1)
            question = panel.get_by_role('textbox', name='Pergunta'); question.fill('Investigate this authorized target')
            panel.get_by_label('Selecionar outro alvo ou período').check()
            expect(panel.get_by_label('Cluster autorizado')).to_be_visible()
            panel.get_by_label('Cluster autorizado').select_option(secondary)
            expect(panel.get_by_label('Cluster autorizado')).to_have_value(secondary)
            panel.get_by_label('Tipo de alvo').select_option('Pod')
            panel.get_by_label('Namespace do alvo').fill('scope-apps')
            panel.get_by_label('Nome do alvo').fill('missing-target')
            end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
            start = end - timedelta(minutes=30)
            panel.get_by_label('Início da consulta (hora local)').fill(start.strftime('%Y-%m-%dT%H:%M'))
            panel.get_by_label('Fim da consulta (hora local)').fill((end + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'))
            expect(panel.get_by_role('button', name='Propor investigação')).to_be_disabled()
            panel.get_by_label('Fim da consulta (hora local)').fill(end.strftime('%Y-%m-%dT%H:%M'))
            expect(panel.get_by_role('button', name='Propor investigação')).to_be_enabled()
            negative.update(url=f"{FRONTEND}/api/incidents/{source['incident_id']}/copilot/proposals", status=404)
            try:
                with page.expect_response(lambda r: r.url.endswith('/copilot/proposals')) as missing:
                    panel.get_by_role('button', name='Propor investigação').click()
                assert missing.value.status == 404 and missing.value.json()['detail']['code'] == 'target_unavailable'
                expect(panel.locator('.copilotError')).to_contain_text('No authorized inventory target')
            finally: negative.update(url=None, status=None)
            expected_http.append((missing.value.url, 404))
            assert not observed['confirmations']
            panel.get_by_label('Nome do alvo').fill('scope-worker')
            with page.expect_response(lambda r: r.url.endswith('/copilot/proposals')) as proposed:
                panel.get_by_role('button', name='Propor investigação').click()
            assert proposed.value.status == 200
            compiled = proposed.value.json()['incident']
            assert compiled['cluster'] == secondary and compiled['pod'] == 'scope-worker' and compiled['tenant_id'] == source['incident']['tenant_id']
            assert datetime.fromisoformat(compiled['ends_at'].replace('Z', '+00:00')) == end
            expect(panel.locator('.copilotProposal')).to_contain_text('Fim:')
            assert not observed['confirmations'], 'Proposal executed without explicit confirmation'
            panel.get_by_role('button', name='Cancelar proposta').click()
            expect(panel.locator('.copilotProposal')).to_have_count(0)
            panel.get_by_role('button', name='Propor investigação').click()
            expect(panel.locator('.copilotProposal')).to_be_visible()
            # Recreated object with the same name must invalidate the reviewed UID.
            seed(secondary, uid='replaced-uid')
            negative.update(url=f"{FRONTEND}/api/incidents/{source['incident_id']}/copilot/confirm", status=409)
            try:
                with page.expect_response(lambda r: r.url.endswith('/copilot/confirm')) as stale:
                    panel.get_by_role('button', name='Confirmar investigação').click()
                assert stale.value.status == 409
                expect(panel.locator('.copilotError')).to_contain_text('Não foi possível confirmar')
            finally: negative.update(url=None, status=None)
            expected_http.append((stale.value.url, 409))
            panel.get_by_role('button', name='Propor investigação').click()
            expect(panel.locator('.copilotProposal')).to_be_visible()
            with page.expect_response(lambda r: r.url.endswith('/copilot/confirm')) as confirmed:
                panel.get_by_role('button', name='Confirmar investigação').focus(); page.keyboard.press('Enter')
            assert confirmed.value.status == 202
            job_ids.append(confirmed.value.json()['id'])
            expect(panel.get_by_text('Investigação concluída. As próximas perguntas usam as evidências desta investigação.', exact=True)).to_be_visible(timeout=60000)
            question.fill('What is the root cause?')
            with page.expect_response(lambda r: r.url.endswith('/copilot')) as answer:
                panel.get_by_role('button', name='Perguntar').click()
            assert answer.value.status == 200
            completed_id = answer.value.url.split('/api/incidents/', 1)[1].split('/', 1)[0]
            saved = page.request.get(API + '/api/v1/incidents/' + completed_id).json()
            assert saved['incident']['cluster'] == secondary and saved['incident']['pod'] == 'scope-worker'
            assert saved['incident']['ends_at'] == compiled['ends_at']
            expect(panel.locator('.copilotAnswer')).to_be_visible()
            citations = panel.locator('.copilotAnswer a').all_text_contents()
            assert citations and set(citations) <= {e['id'] for e in saved['evidence']}
            # Select a Service in another configured cluster; no Pod may be guessed.
            panel.get_by_label('Selecionar outro alvo ou período').check()
            expect(panel.get_by_label('Cluster autorizado')).to_be_visible()
            panel.get_by_label('Cluster autorizado').select_option(primary)
            expect(panel.get_by_label('Cluster autorizado')).to_have_value(primary)
            panel.get_by_label('Tipo de alvo').select_option('Service')
            panel.get_by_label('Namespace do alvo').fill('scope-apps'); panel.get_by_label('Nome do alvo').fill('scope-api')
            panel.get_by_label('Início da consulta (hora local)').fill((end-timedelta(minutes=45)).strftime('%Y-%m-%dT%H:%M'))
            panel.get_by_label('Fim da consulta (hora local)').fill(end.strftime('%Y-%m-%dT%H:%M'))
            with page.expect_response(lambda r: r.url.endswith('/copilot/proposals')) as service_proposal:
                panel.get_by_role('button', name='Propor investigação').click()
            assert service_proposal.value.status == 200
            service_scope = service_proposal.value.json()['incident']
            assert service_scope['service'] == 'frontend' and service_scope['pod'] is None
            with page.expect_response(lambda r: r.url.endswith('/copilot/confirm')) as service_job:
                panel.get_by_role('button', name='Confirmar investigação').click()
            assert service_job.value.status == 202; job_ids.append(service_job.value.json()['id'])
            expect(panel.get_by_text('Investigação concluída. As próximas perguntas usam as evidências desta investigação.', exact=True)).to_be_visible(timeout=60000)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Dynamic scope mobile overflow'
            page.screenshot(path=str(OUT / 'dynamic-scope-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            checks.extend(['authorized inventory and configured cluster selection', 'invalid time window blocks proposal', 'unknown target fails without guessing', 'review and cancellation create no job', 'recreated UID invalidates confirmation', 'confirmed Pod scope persists target and exact window', 'completed scoped Q&A cites only authorized evidence', 'explicit Service telemetry identity without guessed Pod', 'mobile and keyboard interaction'])
            browser.close()
        assert [(r['url'],r['status']) for r in observed['http_errors']] == expected_http, observed['http_errors']
        assert all(r['expected'] for r in observed['console_errors']), observed['console_errors']
        assert not observed['page_errors'], observed['page_errors']
        assert all(r['method']=='GET' and r['failure']=='net::ERR_ABORTED' and r['url'] in [f'{FRONTEND}/api/orchestration/{job}/events' for job in job_ids] for r in observed['request_failures']), observed['request_failures']
        (OUT / 'report.json').write_text(json.dumps({'passed':True,'checks':checks,'jobs':job_ids,'observed':observed},indent=2))
        print(json.dumps({'passed':True,'checks':checks},indent=2))
    finally:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill(); server.wait()
