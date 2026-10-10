"""Check the demo in Chromium and API failure states in an isolated fixture server."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'diagnosis-ui'
OUT.mkdir(parents=True, exist_ok=True)
FRONTEND = 'http://127.0.0.1:13000'
FIXTURE_FRONTEND = 'http://127.0.0.1:13002'
with urlopen('http://127.0.0.1:18080/api/v1/incidents/demo', timeout=30) as response:
    baseline = json.load(response)

with urlopen(Request('http://127.0.0.1:18080/api/v1/planner/investigate', data=json.dumps(baseline['incident']).encode(), headers={'Content-Type': 'application/json'}), timeout=60) as response:
    agentic_result = json.load(response)
with urlopen(f"http://127.0.0.1:18080/api/v1/incidents/{agentic_result['incident_id']}", timeout=30) as response:
    agentic_diagnosis = json.load(response)


class FixtureAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.rsplit('/', 1)[-1]
        code = 200
        result = copy.deepcopy(baseline)
        result['incident_id'] = name
        if name == 'ui-agentic-blocked':
            result = copy.deepcopy(agentic_diagnosis)
            result['incident_id'] = name
            root_id = result['root_cause']['hypothesis_id']
            for decision in result['agentic']['decisions']:
                if decision['hypothesis_id'] == root_id:
                    decision['decision'] = 'blocked_by_incomplete_context'
                    decision['reasons'] = ['Required specialist did not complete']
        elif name == 'ui-missing':
            code = 404
        elif name in ('ui-restricted', 'ui-unauthenticated'):
            code = 403 if name == 'ui-restricted' else 401
        elif name == 'ui-authorized' and self.headers.get('Authorization') != 'Bearer ui-test-token':
            code = 403
        elif name == 'ui-inconclusive':
            result['status'] = 'inconclusive'
            result['root_cause']['hypothesis_id'] = None
            result['root_cause']['title'] = 'Evidências insuficientes'
            result['root_cause']['summary'] = 'A coleta não confirmou uma causa.'
            for hypothesis in result['hypotheses']:
                hypothesis['status'] = 'inconclusive'
        elif name == 'ui-llm':
            # Inconsistent upstream input still cannot promote an LLM proposal.
            hypothesis = result['hypotheses'][0]
            hypothesis['origin'] = 'llm'
            hypothesis['status'] = 'confirmed'
            result['root_cause']['hypothesis_id'] = hypothesis['id']
        elif name == 'ui-missing-evidence':
            result['hypotheses'][0]['supporting_evidence'] = ['absent-reference']
            result['hypotheses'][0]['status'] = 'testing'
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result if code == 200 else {'detail': 'fixture error'}).encode())

    def log_message(self, *args):
        pass


fixture_api = ThreadingHTTPServer(('127.0.0.1', 18083), FixtureAPI)
threading.Thread(target=fixture_api.serve_forever, daemon=True).start()
fixture_env = dict(os.environ, API_URL='http://127.0.0.1:18083', HOSTNAME='127.0.0.1', PORT='13002')
errors = []
checks = []
with (OUT / 'fixture-frontend.log').open('w') as log:
    fixture_frontend = subprocess.Popen(['node', 'server.js'], cwd=ROOT / 'frontend/.next/standalone', env=fixture_env, stdout=log, stderr=log)
    try:
        for attempt in range(60):
            try:
                with urlopen(f'{FIXTURE_FRONTEND}/incidents/ui-inconclusive', timeout=5):
                    break
            except OSError:
                if fixture_frontend.poll() is not None:
                    raise RuntimeError('Fixture frontend exited')
                time.sleep(0.5)
        else:
            raise RuntimeError('Fixture frontend did not become ready')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, locale='pt-BR')
            page.on('pageerror', lambda error: errors.append(str(error)))
            for path, name in [('/', 'kubernetes'), ('/trace', 'trace'), ('/stateful/postgresql', 'postgresql'), ('/stateful/kafka', 'kafka'), ('/stateful/redis', 'redis')]:
                page.goto(f'{FRONTEND}{path}', wait_until='networkidle')
                expect(page.get_by_role('heading', name='Informações do alerta', exact=True)).to_be_visible()
                expect(page.get_by_text('Causa confirmada pelo motor', exact=True)).to_be_visible()
                expect(page.locator('#hypotheses .rootHypothesis')).to_have_count(1)
                page.screenshot(path=str(OUT / f'{name}-desktop.png'), full_page=True)
                checks.append(f'{name}: engine diagnosis and desktop render')
            page.goto(f'{FRONTEND}/incidents', wait_until='networkidle')
            expect(page.get_by_role('heading', name='Incidentes', exact=True)).to_be_visible()
            page.get_by_label('Buscar título ou ID', exact=True).fill(agentic_result['incident_id'])
            page.get_by_role('button', name='Filtrar', exact=True).click()
            expect(page.locator('.incidentListCard')).to_have_count(1)
            expect(page.locator('.incidentListCard')).to_contain_text(agentic_result['incident_id'])
            page.goto(f'{FRONTEND}/incidents/new', wait_until='networkidle')
            page.get_by_label('Título', exact=True).fill('Investigação iniciada pela interface')
            page.get_by_label('Cluster', exact=True).fill(baseline['incident']['cluster'])
            page.get_by_label('Alerta', exact=True).fill(baseline['incident']['alert_name'])
            page.get_by_label('Início do incidente (opcional)', exact=True).fill(baseline['incident']['starts_at'])
            for field, label in [('namespace', 'Namespace'), ('pod', 'Pod'), ('service', 'Serviço')]:
                if baseline['incident'].get(field):
                    page.get_by_label(label, exact=True).fill(baseline['incident'][field])
            page.get_by_label('Modo', exact=True).select_option('agentic')
            page.get_by_role('button', name='Iniciar investigação', exact=True).click()
            expect(page.get_by_role('heading', name='Investigação iniciada pela interface', exact=True)).to_be_visible(timeout=60000)
            expect(page.get_by_text('Investigação persistida', exact=True)).to_be_visible()
            expect(page.get_by_role('heading', name='Execução dos especialistas', exact=True)).to_be_visible()
            created_id = page.url.rsplit('/', 1)[-1]
            page.goto(f'{FRONTEND}/incidents?q={created_id}', wait_until='networkidle')
            expect(page.locator('.incidentListCard')).to_have_count(1)
            checks.append('incident list filters and authenticated Server Action form creates a saved agentic diagnosis')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'incident list mobile overflow'
            page.goto(f'{FRONTEND}/incidents/new', wait_until='networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'investigation form mobile overflow'
            page.screenshot(path=str(OUT / 'new-investigation-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(FRONTEND, wait_until='networkidle')
            expect(page.get_by_role('img', name='Entidades e relações observadas nesta investigação')).to_be_visible()
            expected_nodes = len({endpoint for edge in baseline['graph_edges'] for endpoint in (edge['source'], edge['target'])})
            expect(page.locator('.incidentTopologyNode')).to_have_count(min(48, expected_nodes))
            page.get_by_text('Consultar relações em texto', exact=True).click()
            expect(page.locator('.diagnosisRelationsText .entityGraph')).to_be_visible()
            expect(page.locator('.diagnosisRelationsText .graphEdge')).to_have_count(len(baseline['graph_edges']))
            checks.append('SVG topology uses actual graph endpoints; text fallback retains all relations')
            ruled_out = page.locator('.diagnosisHypothesis.ruled_out').first
            expect(ruled_out).not_to_have_attribute('open', '')
            ruled_out.locator('summary').click()
            expect(ruled_out).to_have_attribute('open', '')
            expect(ruled_out.locator('.diagnosisHypothesisBody')).to_be_visible()
            card = page.locator('#evidence .evidenceCard').first
            card.locator('summary').click()
            expect(card.locator('pre').last).to_be_visible()
            expect(card.get_by_text('SHA-256 registrado', exact=True)).to_be_visible()
            checks.append('expand ruled-out hypothesis and evidence provenance')
            page.get_by_role('link', name='Abrir investigação salva').click()
            expect(page.get_by_text('Investigação persistida', exact=True)).to_be_visible()
            expect(page.get_by_role('heading', name=baseline['incident']['title'], exact=True)).to_be_visible()
            checks.append('persisted incident renders same diagnosis without demo fallback')
            page.goto(f"{FRONTEND}{agentic_result['investigation_url']}", wait_until='networkidle')
            expect(page.get_by_role('heading', name='Execução dos especialistas', exact=True)).to_be_visible()
            expect(page.locator('.agenticSpecialist')).to_have_count(len(agentic_diagnosis['agentic']['specialists']))
            expect(page.locator('.agenticDecision')).to_have_count(len(agentic_diagnosis['agentic']['decisions']))
            page.get_by_text('Consultar relações da explicação do avaliador', exact=True).click()
            expect(page.locator('.agenticExplanation')).to_have_attribute('open', '')
            page.screenshot(path=str(OUT / 'agentic-desktop.png'), full_page=True)
            checks.append('actual agentic execution persists and renders specialists, evaluator decisions and explanation')
            page.goto(f'{FIXTURE_FRONTEND}/incidents/ui-agentic-blocked', wait_until='networkidle')
            expect(page.get_by_text('Causa confirmada pelo motor', exact=True)).to_have_count(0)
            expect(page.locator('.rootHypothesis')).to_have_count(0)
            expect(page.get_by_role('heading', name='Bloqueada por contexto incompleto', exact=True)).to_be_visible()
            checks.append('blocked agentic decision never displays confirmed root even with inconsistent upstream status')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.goto(f'{FRONTEND}/stateful/redis', wait_until='networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), 'mobile horizontal overflow'
            page.screenshot(path=str(OUT / 'redis-mobile.png'), full_page=True)
            checks.append('mobile without horizontal overflow')
            for fixture in ('ui-inconclusive', 'ui-llm'):
                page.goto(f'{FIXTURE_FRONTEND}/incidents/{fixture}', wait_until='networkidle')
                expect(page.get_by_text('Diagnóstico inconclusivo', exact=True)).to_be_visible()
                expect(page.get_by_text('Causa confirmada pelo motor', exact=True)).to_have_count(0)
                expect(page.locator('.rootHypothesis')).to_have_count(0)
                checks.append(f'{fixture}: no false confirmed cause')
            page.goto(f'{FIXTURE_FRONTEND}/incidents/ui-missing-evidence', wait_until='networkidle')
            expect(page.get_by_text('Evidência absent-reference não disponível neste resultado.', exact=True)).to_be_visible()
            for fixture in ('ui-restricted', 'ui-unauthenticated'):
                page.goto(f'{FIXTURE_FRONTEND}/incidents/{fixture}', wait_until='networkidle')
                expect(page.get_by_role('heading', name='Acesso à investigação restrito')).to_be_visible()
                expect(page.locator('.diagnosis')).to_have_count(0)
                checks.append(f'{fixture}: no demo data on access failure')
            for headers in ({'Authorization': 'Bearer ui-test-token'}, {'x-auth-request-access-token': 'ui-test-token'}):
                page.set_extra_http_headers(headers)
                page.goto(f'{FIXTURE_FRONTEND}/incidents/ui-authorized', wait_until='networkidle')
                expect(page.get_by_text('Investigação persistida', exact=True)).to_be_visible()
            page.set_extra_http_headers({})
            checks.append('direct and proxy authorization forwarded server-side')
            page.goto(f'{FIXTURE_FRONTEND}/incidents/ui-missing', wait_until='networkidle')
            expect(page.get_by_role('heading', name='Investigação não encontrada')).to_be_visible()
            expect(page.locator('.diagnosis')).to_have_count(0)
            checks.append('missing incident does not fall back to demo')
            browser.close()
        assert not errors, errors
        (OUT / 'report.json').write_text(json.dumps({'passed': True, 'checks': checks, 'browser_errors': errors, 'git_sha': os.environ.get('GITHUB_SHA'), 'screenshot_data': 'actual demo engine, not production telemetry'}, indent=2))
        print(json.dumps({'passed': True, 'checks': checks}, indent=2))
    finally:
        fixture_frontend.terminate()
        try:
            fixture_frontend.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fixture_frontend.kill()
            fixture_frontend.wait()
        fixture_api.shutdown()
