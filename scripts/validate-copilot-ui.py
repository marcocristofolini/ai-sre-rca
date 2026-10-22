"""Run Incident Copilot browser checks independently from diagnosis smoke tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts' / 'copilot-ui'
OUT.mkdir(parents=True, exist_ok=True)
API_BASE = os.environ.get('VALIDATION_API', 'http://127.0.0.1:18080')
FIXTURE_FRONTEND = 'http://127.0.0.1:13002'
STANDALONE = ROOT / 'frontend' / '.next' / 'standalone'
if not (STANDALONE / '.next' / 'static').is_dir() or not (STANDALONE / 'public').is_dir():
    raise RuntimeError('Standalone frontend is missing .next/static or public; copy both after next build')
with urlopen(f'{API_BASE}/api/v1/incidents/demo', timeout=30) as response:
    baseline = json.load(response)

fixture_requests = []


class FixtureAPI(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.rsplit('/', 1)[-1]
        code = 200
        result = dict(baseline)
        result['incident_id'] = name
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result if code == 200 else {'detail': 'fixture error'}).encode())

    def do_POST(self):
        name = self.path.split('/api/v1/incidents/', 1)[-1].split('/', 1)[0]
        question = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()).get('question', '')
        code = 200
        if name in ('ui-restricted', 'ui-unauthenticated'):
            code = 403 if name == 'ui-restricted' else 401
        elif name == 'ui-authorized' and self.headers.get('Authorization') != 'Bearer ui-test-token':
            code = 403
        if question == 'error':
            code = 503
        if question == 'slow':
            time.sleep(0.8)
        insufficient = question == 'no evidence'
        result = {
            'answer': 'Não há evidência suficiente.' if insufficient else 'Disk exhaustion appears in the stored evidence.' if code == 200 else 'fixture error',
            'insufficient_evidence': insufficient,
            'evidence_ids': ['ev-forged'] if insufficient else [baseline['evidence'][0]['id'], 'ev-forged'] if code == 200 else [],
            'mode': 'deterministic',
        }
        fixture_requests.append({'method': 'POST', 'path': self.path, 'question': question, 'status': code, 'response': result})
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, *args):
        pass


fixture_api = ThreadingHTTPServer(('127.0.0.1', 18083), FixtureAPI)
threading.Thread(target=fixture_api.serve_forever, daemon=True).start()

browser_observations = {'http_failures': [], 'request_failures': [], 'console_errors': [], 'page_errors': []}
expected_http_failures = []
expected_request_failures = []
expected_503 = False


def watch_browser(page):
    page.on('response', lambda response: browser_observations['http_failures'].append({
        'method': response.request.method, 'url': response.url, 'status': response.status,
        'request_body': response.request.post_data, 'response_body': response.text(),
    }) if response.status >= 400 else None)
    page.on('requestfailed', lambda request: browser_observations['request_failures'].append({
        'method': request.method, 'url': request.url, 'failure': request.failure,
    }))
    def on_console(message):
        if message.type != 'error':
            return
        item = {'text': message.text, 'location': message.location}
        item['expected_fixture_503'] = (
            expected_503
            and 'status of 503 (Service Unavailable)' in message.text
            and message.location.get('url') == f'{FIXTURE_FRONTEND}/api/incidents/ui-copilot/copilot'
        )
        browser_observations['console_errors'].append(item)
    page.on('console', on_console)
    page.on('pageerror', lambda error: browser_observations['page_errors'].append(f'{page.url}: {error}'))



fixture_env = dict(os.environ, API_URL='http://127.0.0.1:18083', HOSTNAME='127.0.0.1', PORT='13002')
checks = []
try:
    with (OUT / 'fixture-frontend.log').open('w') as log:
        server = subprocess.Popen(['node', 'server.js'], cwd=STANDALONE, env=fixture_env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                try:
                    with urlopen(f'{FIXTURE_FRONTEND}/incidents/ui-copilot', timeout=5): break
                except OSError:
                    if server.poll() is not None: raise RuntimeError('Fixture frontend exited')
                    time.sleep(.5)
            else: raise RuntimeError('Fixture frontend did not become ready')
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={'width': 390, 'height': 844}, locale='pt-BR')
                watch_browser(page)
                page.goto(f'{FIXTURE_FRONTEND}/incidents/ui-copilot', wait_until='networkidle')
                copilot = page.locator('[data-copilot-hydrated="true"]')
                expect(copilot).to_have_count(1)
                question = page.get_by_role('textbox', name='Pergunta')
                question.fill('slow')
                page.get_by_role('button', name='Perguntar').click()
                expect(page.get_by_role('button', name='Consultando…')).to_be_disabled()
                expect(page.get_by_text('Disk exhaustion appears in the stored evidence.')).to_be_visible()
                allowed = {item['id'] for item in baseline['evidence']}
                citations = page.locator('.copilotAnswer a').all_text_contents()
                assert citations and set(citations) <= allowed, f'untrusted citations: {citations}'
                assert 'ev-forged' not in page.locator('.copilotAnswer').inner_text()
                question.fill('error')
                expected_503 = True
                try:
                    with page.expect_response(lambda r: r.url.endswith('/api/incidents/ui-copilot/copilot') and r.request.method == 'POST' and json.loads(r.request.post_data or '{}').get('question') == 'error') as response_info:
                        page.get_by_role('button', name='Perguntar').click()
                finally:
                    expected_503 = False
                response = response_info.value
                assert response.status == 503 and response.json() == {'answer':'fixture error','insufficient_evidence':False,'evidence_ids':[],'mode':'deterministic'}
                expect(copilot.locator('.copilotError')).to_have_text('Não foi possível consultar o Copilot com esta sessão.')
                expected_http_failures.append(('POST', response.url, 503))
                question.fill('no evidence')
                page.get_by_role('button', name='Perguntar').click()
                expect(page.get_by_text('Evidência insuficiente', exact=False)).to_be_visible()
                assert page.locator('.copilotAnswer a').count() == 0
                question.fill('keyboard check')
                page.get_by_role('button', name='Perguntar').focus()
                page.keyboard.press('Enter')
                expect(page.get_by_text('Disk exhaustion appears in the stored evidence.')).to_be_visible()
                expect(question).to_have_attribute('maxlength', '500')
                denied = page.request.post(f'{FIXTURE_FRONTEND}/api/incidents/ui-restricted/copilot', data={'question':'What happened?'})
                assert denied.status == 403 and 'ev-good' not in denied.text() and 'Disk pressure' not in denied.text()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), 'Copilot mobile horizontal overflow'
                page.screenshot(path=str(OUT / 'copilot-mobile.png'), full_page=True)
                browser.close()
            observed = [(x['method'], x['url'], x['status']) for x in browser_observations['http_failures']]
            assert observed == expected_http_failures, {'observed':browser_observations['http_failures'],'expected':expected_http_failures}
            assert not browser_observations['request_failures'], browser_observations['request_failures']
            assert all(item['expected_fixture_503'] for item in browser_observations['console_errors']), browser_observations['console_errors']
            assert not browser_observations['page_errors'], browser_observations['page_errors']
            assert any(x['question']=='error' and x['status']==503 for x in fixture_requests)
            checks.extend(['hydration and loading','authorized evidence citations and forged-ID filtering','intentional 503 response and user-facing error','insufficient evidence','keyboard submission, access denial, mobile layout'])
            report={'passed':True,'checks':checks,'authorized_evidence_ids':sorted(allowed),'fixture_requests':fixture_requests,'browser_observations':browser_observations}
            (OUT/'copilot-report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'passed':True,'checks':checks},indent=2))
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
finally:
    fixture_api.shutdown()
