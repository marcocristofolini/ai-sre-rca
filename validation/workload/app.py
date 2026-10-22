from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode

import redis_scenario
from opentelemetry.propagate import extract, inject

SERVICE = os.getenv("OTEL_SERVICE_NAME", "frontend")
POD_UID = os.getenv("POD_UID", "")
DEPENDENCY_URL = os.getenv("DEPENDENCY_URL", "http://copilot-stateful:8080/dependency")
DEPENDENCY_DELAY = float(os.getenv("DEPENDENCY_DELAY_SECONDS", "0"))
POD_NAME = os.getenv("POD_NAME", "rc3-workload")
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100/loki/api/v1/push")
TEMPO_URL = os.getenv("TEMPO_URL", "http://tempo:4318/v1/traces")
PORT = int(os.getenv("PORT", "8080"))

provider = TracerProvider(
    resource=Resource.create(
        {
            "service.name": SERVICE,
            "service.version": "rc3",
            "k8s.pod.uid": POD_UID,
            "k8s.pod.name": POD_NAME,
            "deployment.environment.name": "validation",
            "k8s.namespace.name": "rca-validation",
            ("k8s.statefulset.name" if os.getenv('WORKLOAD_KIND') == 'StatefulSet' else "k8s.deployment.name"): os.getenv('WORKLOAD_NAME', 'rc3-workload'),
        }
    )
)
provider.add_span_processor(
    SimpleSpanProcessor(
        OTLPSpanExporter(
            endpoint=TEMPO_URL,
            timeout=5,
        )
    )
)
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("ai-sre-rc3-validation")

REDIS_SERVICE = "cache-frontend"
redis_provider = TracerProvider(resource=Resource.create({
    "service.name": REDIS_SERVICE,
    "service.version": "rc3",
    "deployment.environment.name": "validation",
    "k8s.namespace.name": "rca-validation",
    "k8s.deployment.name": "rc3-workload",
}))
redis_provider.add_span_processor(SimpleSpanProcessor(
    OTLPSpanExporter(endpoint=TEMPO_URL, timeout=5),
))
redis_tracer = redis_provider.get_tracer("ai-sre-redis-validation")

_lock = threading.Lock()
_requests = 0
_failures = 0
_successes = 0
_recoveries = 0
_redis_failures = 0
_redis_successes = 0
_latency_requests = 0
_latency_failures = 0
_latency_seconds = 0.0


def _post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status >= 300:
            raise RuntimeError(f"POST {url} returned {response.status}")


def emit_log(trace_id: str, *, level: str, message: str, service: str = SERVICE) -> None:
    payload = {
        "streams": [
            {
                "stream": {
                    "namespace": "rca-validation",
                    "pod": POD_NAME,
                    "pod_uid": POD_UID,
                    "app": service,
                    "level": level,
                },
                "values": [
                    [
                        str(time.time_ns()),
                        json.dumps({"message": message, "trace_id": trace_id, "pod_uid": POD_UID}),
                    ]
                ],
            }
        ]
    }
    _post_json(LOKI_URL, payload)


def emit_failure_trace() -> str:
    with tracer.start_as_current_span(
        "GET /checkout",
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": "GET",
            "http.response.status_code": 500,
        },
    ) as root:
        trace_id = f"{root.get_span_context().trace_id:032x}"
        with tracer.start_as_current_span(
            "SELECT orders",
            kind=SpanKind.CLIENT,
            attributes={
                "db.system": "postgresql",
                "db.namespace": "orders",
                "server.address": "orders-db",
                "server.port": 5432,
                "error.type": "connection_capacity",
            },
        ) as child:
            child.set_status(
                Status(StatusCode.ERROR, "postgres connection capacity exhausted")
            )
            time.sleep(0.05)
        root.set_status(Status(StatusCode.ERROR, "downstream database failure"))
    return trace_id


def emit_success_trace() -> str:
    with tracer.start_as_current_span(
        "GET /checkout",
        kind=SpanKind.SERVER,
        attributes={
            "http.request.method": "GET",
            "http.response.status_code": 200,
        },
    ) as root:
        trace_id = f"{root.get_span_context().trace_id:032x}"
        with tracer.start_as_current_span(
            "SELECT orders",
            kind=SpanKind.CLIENT,
            attributes={
                "db.system": "postgresql",
                "db.namespace": "orders",
                "server.address": "orders-db",
                "server.port": 5432,
            },
        ):
            time.sleep(0.02)
    return trace_id


def emit_redis_trace(*, failing: bool) -> str:
    with redis_tracer.start_as_current_span(
        "GET /cache-checkout", kind=SpanKind.SERVER,
        attributes={"http.request.method": "GET", "http.response.status_code": 500 if failing else 200},
    ) as root:
        trace_id = f"{root.get_span_context().trace_id:032x}"
        with redis_tracer.start_as_current_span(
            "SET validation-cache", kind=SpanKind.CLIENT,
            attributes={"db.system": "redis", "db.namespace": "0", "server.address": "cache", "server.port": 6379},
        ) as child:
            if failing:
                try:
                    redis_scenario.fail_write()
                except redis_scenario.RedisCommandError as exc:
                    if not str(exc).startswith("OOM"):
                        raise
                    child.set_attribute("error.type", "memory_pressure")
                    child.set_status(Status(StatusCode.ERROR, str(exc)))
                    root.set_status(Status(StatusCode.ERROR, "Redis rejected validation write"))
            else:
                redis_scenario.healthy_request()
    return trace_id


def emit_latency_request():
    """Record an actual HTTP dependency call; errors reflect its observed outcome."""
    started = time.monotonic()
    with tracer.start_as_current_span('GET /latency', kind=SpanKind.SERVER) as root:
        trace_id = f'{root.get_span_context().trace_id:032x}'
        failed = False
        with tracer.start_as_current_span('GET /dependency', kind=SpanKind.CLIENT,
            attributes={'peer.service':'copilot-stateful','server.address':'copilot-stateful','server.port':8080}) as child:
            headers = {}; inject(headers)
            try:
                with urllib.request.urlopen(urllib.request.Request(DEPENDENCY_URL, headers=headers), timeout=.25) as response:
                    if response.status != 200: raise RuntimeError('dependency HTTP failure')
            except (OSError, TimeoutError) as error:
                failed = True
                child.record_exception(error)
                child.set_status(Status(StatusCode.ERROR, type(error).__name__))
                root.set_status(Status(StatusCode.ERROR, 'observed downstream timeout'))
        emit_log(trace_id,level='error' if failed else 'info',message='observed downstream timeout' if failed else 'dependency recovered')
    return trace_id, failed, time.monotonic()-started


def metrics() -> str:
    with _lock:
        requests = _requests
        failures = _failures
        successes = _successes
        recoveries = _recoveries
        redis_failures = _redis_failures
        redis_successes = _redis_successes
    lines = [
        "# HELP ai_sre_validation_requests_total Requests generated by the rc3 validation workload.",
        "# TYPE ai_sre_validation_requests_total counter",
        f'ai_sre_validation_requests_total{{service="{SERVICE}",status="all"}} {requests}',
        f'ai_sre_validation_requests_total{{service="{SERVICE}",status="500"}} {failures}',
        f'ai_sre_validation_requests_total{{service="{SERVICE}",status="200"}} {successes}',
        "# HELP ai_sre_validation_recoveries_total Recovery transitions generated by the rc3 validation workload.",
        "# TYPE ai_sre_validation_recoveries_total counter",
        f'ai_sre_validation_recoveries_total{{service="{SERVICE}"}} {recoveries}',
        "# HELP traces_service_graph_request_total Synthetic service graph request counter for adapter validation.",
        "# TYPE traces_service_graph_request_total counter",
        f'traces_service_graph_request_total{{client="frontend",server="postgresql/orders",connection_type="database"}} {requests}',
        "# HELP traces_service_graph_request_failed_total Synthetic service graph failure counter for adapter validation.",
        "# TYPE traces_service_graph_request_failed_total counter",
        f'traces_service_graph_request_failed_total{{client="frontend",server="postgresql/orders",connection_type="database"}} {failures}',
        "# HELP traces_service_graph_request_server_seconds Request latency histogram.",
        "# TYPE traces_service_graph_request_server_seconds histogram",
        'traces_service_graph_request_server_seconds_bucket{client="frontend",server="postgresql/orders",connection_type="database",le="0.1"} 0',
        f'traces_service_graph_request_server_seconds_bucket{{client="frontend",server="postgresql/orders",connection_type="database",le="0.5"}} {successes}',
        f'traces_service_graph_request_server_seconds_bucket{{client="frontend",server="postgresql/orders",connection_type="database",le="1"}} {requests}',
        f'traces_service_graph_request_server_seconds_bucket{{client="frontend",server="postgresql/orders",connection_type="database",le="+Inf"}} {requests}',
        f'traces_service_graph_request_server_seconds_sum{{client="frontend",server="postgresql/orders",connection_type="database"}} {failures * 0.7 + successes * 0.08}',
        f'traces_service_graph_request_server_seconds_count{{client="frontend",server="postgresql/orders",connection_type="database"}} {requests}',
    ]
    lines.extend([
        f'ai_sre_validation_requests_total{{service="{REDIS_SERVICE}",status="500"}} {redis_failures}',
        f'ai_sre_validation_requests_total{{service="{REDIS_SERVICE}",status="200"}} {redis_successes}',
        f'traces_service_graph_request_total{{client="{REDIS_SERVICE}",server="redis/0",connection_type="database"}} {redis_failures + redis_successes}',
        f'traces_service_graph_request_failed_total{{client="{REDIS_SERVICE}",server="redis/0",connection_type="database"}} {redis_failures}',
    ])
    lines += [f'ai_sre_validation_latency_requests_total{{service="{SERVICE}",status="504"}} {_latency_failures}',
        f'ai_sre_validation_latency_requests_total{{service="{SERVICE}",status="200"}} {_latency_requests-_latency_failures}',
        f'traces_service_graph_request_total{{client="{SERVICE}",server="copilot-stateful",connection_type="http"}} {_latency_requests}',
        f'traces_service_graph_request_failed_total{{client="{SERVICE}",server="copilot-stateful",connection_type="http"}} {_latency_failures}']
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        global _requests, _failures, _successes, _recoveries
        global _redis_failures, _redis_successes, _latency_requests, _latency_failures, _latency_seconds
        if self.path == "/health":
            self._send(200, b"ok\n")
            return
        if self.path == '/dependency':
            with tracer.start_as_current_span('GET /dependency', context=extract({key.lower():value for key,value in self.headers.items()}), kind=SpanKind.SERVER) as span:
                time.sleep(DEPENDENCY_DELAY)
                trace_id = f'{span.get_span_context().trace_id:032x}'
                emit_log(trace_id,level='info',message=f'dependency duration_seconds={DEPENDENCY_DELAY}')
                try: self._send(200,b'ok')
                except (BrokenPipeError, ConnectionResetError): pass
            return
        if self.path == '/oom' and os.getenv('VALIDATION_ALLOW_FAULTS') == 'true':
            with tracer.start_as_current_span('controlled memory allocation') as span:
                emit_log(f'{span.get_span_context().trace_id:032x}',level='info',message='controlled allocation requested bytes=268435456')
            self._send(202,b'allocation requested')
            allocation = bytearray(256 * 1024 * 1024)
            time.sleep(1)
            del allocation
            return
        if self.path == '/latency':
            trace_id, failed, elapsed = emit_latency_request()
            with _lock:
                _latency_requests += 1; _latency_failures += int(failed); _latency_seconds += elapsed
            self._send(504 if failed else 200,json.dumps({'trace_id':trace_id,'failed':failed,'duration_seconds':elapsed}).encode())
            return
        if self.path == "/metrics":
            self._send(200, metrics().encode(), "text/plain; version=0.0.4")
            return
        if self.path in {"/redis/load", "/redis/recover"}:
            try:
                action = redis_scenario.load_memory if self.path == "/redis/load" else redis_scenario.recover_memory
                state = action()
            except Exception as exc:
                self._send(503, f"Redis control failed: {exc}\n".encode())
                return
            self._send(200, json.dumps(state).encode(), "application/json")
            return
        if self.path in {"/redis/fail", "/redis/ok"}:
            failing = self.path == "/redis/fail"
            try:
                trace_id = emit_redis_trace(failing=failing)
                emit_log(trace_id, level="error" if failing else "info", service=REDIS_SERVICE,
                         message="cache checkout failed dependency=cache error=memory_pressure" if failing else "cache checkout recovered dependency=cache status=healthy")
            except Exception as exc:
                self._send(503, f"Redis validation or telemetry failed: {exc}\n".encode())
                return
            with _lock:
                if failing:
                    _redis_failures += 1
                else:
                    _redis_successes += 1
            self._send(500 if failing else 200, f"cache checkout trace_id={trace_id}\n".encode())
            return
        if self.path == "/fail":
            with _lock:
                _requests += 1
                _failures += 1
            try:
                trace_id = emit_failure_trace()
                emit_log(
                    trace_id,
                    level="error",
                    message=(
                        "checkout failed dependency=orders-db "
                        "error=connection_capacity"
                    ),
                )
            except Exception as exc:
                self._send(503, f"telemetry emission failed: {exc}\n".encode())
                return
            self._send(500, f"dependency failure trace_id={trace_id}\n".encode())
            return
        if self.path == "/recover":
            with _lock:
                _recoveries += 1
            self._send(200, b"recovery transition accepted\n")
            return
        if self.path == "/ok":
            with _lock:
                _requests += 1
                _successes += 1
            try:
                trace_id = emit_success_trace()
                emit_log(
                    trace_id,
                    level="info",
                    message="checkout recovered dependency=orders-db status=healthy",
                )
            except Exception as exc:
                self._send(503, f"telemetry emission failed: {exc}\n".encode())
                return
            self._send(200, f"checkout healthy trace_id={trace_id}\n".encode())
            return
        self._send(404, b"not found\n")


if __name__ == "__main__":
    if os.getenv('VALIDATION_STARTUP_EXIT') == '42':
        with tracer.start_as_current_span('controlled startup exit') as span:
            span.set_status(Status(StatusCode.ERROR, 'process will exit with code 42'))
            emit_log(f'{span.get_span_context().trace_id:032x}',level='error',message='deliberate validation crash exit code 42')
        sys.exit(42)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
