from types import SimpleNamespace
from unittest.mock import Mock
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from validation import run as harness


def test_existing_cluster_cannot_be_replaced(monkeypatch):
    monkeypatch.setattr(harness, 'output', lambda _: 'existing')
    mutation = Mock()
    monkeypatch.setattr(harness, 'run', mutation)
    with pytest.raises(RuntimeError, match='Refusing'):
        harness.create_cluster('existing', 'cached-node')
    mutation.assert_not_called()


def test_existing_postgres_cannot_be_replaced(monkeypatch):
    command = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(harness, 'run', command)
    with pytest.raises(RuntimeError, match='Refusing'):
        harness.start_postgres(15439)
    assert command.call_count == 1
    assert command.call_args.args[0][:3] == ['docker', 'container', 'inspect']


def test_cleanup_only_removes_owned_resources(monkeypatch):
    monkeypatch.setattr(harness, 'OWNED_CLUSTER', None)
    monkeypatch.setattr(harness, 'OWNED_POSTGRES', False)
    command = Mock()
    monkeypatch.setattr(harness, 'run', command)
    harness.cleanup('existing')
    command.assert_not_called()
    monkeypatch.setattr(harness, 'OWNED_CLUSTER', 'ours')
    harness.cleanup('ours')
    assert command.call_args.args[0] == ['kind','delete','cluster','--name','ours']


def test_shared_logs_redact_credentials():
    raw = 'POSTGRES_PASSWORD=secret Bearer jwt-token postgresql+asyncpg://user:db-secret@127.0.0.1/db'
    redacted = harness.redact(raw)
    assert not any(value in redacted for value in ('secret','jwt-token','db-secret'))
    assert redacted.count('[REDACTED]') == 3


def test_report_retains_live_failure_without_claiming_success(tmp_path):
    report = {'passed':False,'validation':{'copilot':{'passed':False,'failure':{'detail':'missing Pod UID telemetry'}}}}
    harness.write_reports(tmp_path, report)
    text = (tmp_path/'validation-report.md').read_text()
    assert '**FAIL**' in text and 'missing Pod UID telemetry' in text


@pytest.mark.parametrize('status', [200, 504, 503])
def test_real_probe_preserves_exact_http_status_and_response(monkeypatch, status):
    from validation.copilot import request_latency
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps({'failed':status != 200,'trace_id':'actual-response'}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    monkeypatch.setattr(harness,'WORKLOAD_PORT',server.server_port)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    try:
        response = request_latency()
        assert response['status'] == status and response['body']['trace_id'] == 'actual-response'
    finally:
        server.shutdown(); server.server_close(); thread.join()
