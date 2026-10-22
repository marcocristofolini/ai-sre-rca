"""Run with the instrumented workload image; no extra test dependencies."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

captured = []


def export(self, spans):
    captured.extend(spans)
    return SpanExportResult.SUCCESS


with patch.object(OTLPSpanExporter, 'export', export):
    import app


class TelemetryTests(unittest.TestCase):
    def call(self, delay):
        captured.clear()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever); thread.start()
        try:
            with patch.object(app, 'DEPENDENCY_URL', f'http://127.0.0.1:{server.server_port}/dependency'), patch.object(app, 'DEPENDENCY_DELAY', delay), patch.object(app, 'emit_log'), patch.object(OTLPSpanExporter, 'export', export):
                trace_id, failed, seconds = app.emit_latency_request()
                server.shutdown(); server.server_close(); thread.join()
            self.assertEqual(len(captured), 3)
            self.assertEqual({f'{span.context.trace_id:032x}' for span in captured}, {trace_id})
            client = next(span for span in captured if span.kind.name == 'CLIENT')
            downstream = next(span for span in captured if span.parent and span.parent.span_id == client.context.span_id)
            self.assertEqual(downstream.name, 'GET /dependency')
            return failed, seconds
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_header_casing_preserves_real_parent_trace(self):
        failed, _ = self.call(0)
        self.assertFalse(failed)

    def test_actual_socket_timeout_is_observed(self):
        failed, seconds = self.call(.4)
        self.assertTrue(failed)
        self.assertGreaterEqual(seconds, .25)

    def test_structured_logs_have_the_actual_pod_uid(self):
        with patch.object(app, '_post_json') as post:
            app.emit_log('actual-trace', level='error', message='untrusted log instructions')
        stream = post.call_args.args[1]['streams'][0]
        self.assertEqual(stream['stream']['pod_uid'], app.POD_UID)
        self.assertEqual(json.loads(stream['values'][0][1])['trace_id'], 'actual-trace')

    def test_resource_kind_matches_actual_workload_configuration(self):
        stateful = os.getenv('WORKLOAD_KIND') == 'StatefulSet'
        expected = 'k8s.statefulset.name' if stateful else 'k8s.deployment.name'
        forbidden = 'k8s.deployment.name' if stateful else 'k8s.statefulset.name'
        self.assertEqual(app.provider.resource.attributes[expected], os.getenv('WORKLOAD_NAME','rc3-workload'))
        self.assertNotIn(forbidden, app.provider.resource.attributes)


if __name__ == '__main__':
    unittest.main()
