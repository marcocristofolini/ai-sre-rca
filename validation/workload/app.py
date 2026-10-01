from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SERVICE_NAME = os.getenv("SERVICE_NAME", "checkout")
NAMESPACE = os.getenv("POD_NAMESPACE", "rca-validation")
POD_NAME = os.getenv("POD_NAME", "checkout-unknown")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "checkout")
OTLP_HTTP_URL = os.getenv(
    "OTLP_HTTP_URL",
    "http://tempo:4318/v1/traces",
)
LOKI_PUSH_URL = os.getenv(
    "LOKI_PUSH_URL",
    "http://loki:3100/loki/api/v1/push",
)
PORT = int(os.getenv("PORT", "8080"))


class TelemetryState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.duration_sum = 0.0
        self.db_active = 20.0
        self.db_idle = 5.0
        self.graph_requests = 0
        self.graph_failures = 0
        self.graph_duration_sum = 0.0

    def record(self, *, failed: bool, duration: float) -> None:
        with self.lock:
            self.requests += 1
            self.errors += int(failed)
            self.duration_sum += duration
            self.graph_requests += 1
            self.graph_failures += int(failed)
            self.graph_duration_sum += duration
            if failed:
                self.db_active = 97.0
                self.db_idle = 0.0
            else:
                self.db_active = 20.0
                self.db_idle = 5.0


STATE = TelemetryState()


def _value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _value(value)}
        for key, value in values.items()
    ]


def _id(raw: bytes) -> str:
    return raw.hex()


def _post_json(url: str, payload: dict[str, Any]) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return int(response.status)


def emit_trace(*, failed: bool) -> str:
    trace_id = secrets.token_bytes(16)
    root_span_id = secrets.token_bytes(8)
    db_span_id = secrets.token_bytes(8)

    started = time.time_ns()
    db_started = started + 2_000_000
    db_duration = 220_000_000 if failed else 10_000_000
    db_finished = db_started + db_duration
    root_finished = started + (250_000_000 if failed else 30_000_000)

    status_code = 2 if failed else 1
    http_status = 500 if failed else 200
    common = {
        "traceId": _id(trace_id),
        "flags": 1,
    }

    spans = [
        {
            **common,
            "spanId": _id(root_span_id),
            "name": "GET /checkout",
            "kind": 2,
            "startTimeUnixNano": str(started),
            "endTimeUnixNano": str(root_finished),
            "attributes": _attributes(
                {
                    "http.request.method": "GET",
                    "http.route": "/checkout",
                    "http.response.status_code": http_status,
                }
            ),
            "status": {"code": status_code},
        },
        {
            **common,
            "spanId": _id(db_span_id),
            "parentSpanId": _id(root_span_id),
            "name": "SELECT orders",
            "kind": 3,
            "startTimeUnixNano": str(db_started),
            "endTimeUnixNano": str(db_finished),
            "attributes": _attributes(
                {
                    "db.system": "postgresql",
                    "db.namespace": "orders",
                    "db.operation.name": "SELECT",
                    "server.address": "orders-db",
                    "server.port": 5432,
                    "error.type": (
                        "connection_pool_exhausted"
                        if failed
                        else ""
                    ),
                }
            ),
            "status": {"code": status_code},
        },
    ]

    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": SERVICE_NAME,
                            "service.version": "0.8.0-rc.2-validation",
                            "deployment.environment.name": "validation",
                            "k8s.namespace.name": NAMESPACE,
                            "k8s.pod.name": POD_NAME,
                            "k8s.deployment.name": DEPLOYMENT_NAME,
                        }
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {
                            "name": "ai-sre-rca.validation",
                            "version": "0.8.0-rc.2",
                        },
                        "spans": spans,
                    }
                ],
            }
        ]
    }
    _post_json(OTLP_HTTP_URL, payload)
    return trace_id.hex()


def push_log(*, failed: bool, trace_id: str) -> None:
    level = "error" if failed else "info"
    message = (
        "checkout failed: PostgreSQL orders connection pool exhausted 97/100"
        if failed
        else "checkout recovered: PostgreSQL orders connection pool healthy 25/100"
    )
    payload = {
        "streams": [
            {
                "stream": {
                    "namespace": NAMESPACE,
                    "pod": POD_NAME,
                    "service": SERVICE_NAME,
                    "level": level,
                },
                "values": [
                    [
                        str(time.time_ns()),
                        json.dumps(
                            {
                                "message": message,
                                "trace_id": trace_id,
                                "service": SERVICE_NAME,
                                "level": level,
                            },
                            separators=(",", ":"),
                        ),
                    ]
                ],
            }
        ]
    }
    _post_json(LOKI_PUSH_URL, payload)


def metrics_text() -> str:
    with STATE.lock:
        requests = STATE.requests
        errors = STATE.errors
        duration_sum = STATE.duration_sum
        db_active = STATE.db_active
        db_idle = STATE.db_idle
        graph_requests = STATE.graph_requests
        graph_failures = STATE.graph_failures
        graph_duration_sum = STATE.graph_duration_sum

    labels = 'client="checkout",server="postgresql/orders",connection_type="database"'
    bucket_100 = 0 if graph_duration_sum > 0.1 else graph_requests
    bucket_500 = graph_requests
    lines = [
        "# HELP validation_requests_total Synthetic checkout requests.",
        "# TYPE validation_requests_total counter",
        f'validation_requests_total{{service="{SERVICE_NAME}"}} {requests}',
        "# HELP validation_errors_total Synthetic checkout failures.",
        "# TYPE validation_errors_total counter",
        f'validation_errors_total{{service="{SERVICE_NAME}"}} {errors}',
        "# HELP validation_request_duration_seconds_sum Total request duration.",
        "# TYPE validation_request_duration_seconds_sum counter",
        f'validation_request_duration_seconds_sum{{service="{SERVICE_NAME}"}} {duration_sum:.6f}',
        "# HELP pg_stat_activity_count PostgreSQL activity compatibility metric.",
        "# TYPE pg_stat_activity_count gauge",
        f'pg_stat_activity_count{{instance="orders-db:5432",datname="orders",state="active"}} {db_active}',
        f'pg_stat_activity_count{{instance="orders-db:5432",datname="orders",state="idle"}} {db_idle}',
        "# HELP pg_settings_max_connections PostgreSQL max_connections compatibility metric.",
        "# TYPE pg_settings_max_connections gauge",
        'pg_settings_max_connections{instance="orders-db:5432"} 100',
        "# HELP pg_locks_count PostgreSQL lock compatibility metric.",
        "# TYPE pg_locks_count gauge",
        'pg_locks_count{instance="orders-db:5432",datname="orders",mode="accesssharelock"} 5',
        'pg_locks_count{instance="orders-db:5432",datname="orders",mode="accessexclusivelock"} 0',
        "# HELP pg_stat_database_deadlocks PostgreSQL deadlock compatibility counter.",
        "# TYPE pg_stat_database_deadlocks counter",
        'pg_stat_database_deadlocks{instance="orders-db:5432",datname="orders"} 0',
        "# HELP pg_stat_replication_pg_wal_lsn_diff PostgreSQL replication lag compatibility metric.",
        "# TYPE pg_stat_replication_pg_wal_lsn_diff gauge",
        'pg_stat_replication_pg_wal_lsn_diff{instance="orders-db:5432"} 0',
        "# HELP pg_stat_activity_max_tx_duration PostgreSQL transaction age compatibility metric.",
        "# TYPE pg_stat_activity_max_tx_duration gauge",
        'pg_stat_activity_max_tx_duration{instance="orders-db:5432",datname="orders"} 2',
        "# HELP traces_service_graph_request_total Service graph request counter.",
        "# TYPE traces_service_graph_request_total counter",
        f"traces_service_graph_request_total{{{labels}}} {graph_requests}",
        "# HELP traces_service_graph_request_failed_total Service graph failed request counter.",
        "# TYPE traces_service_graph_request_failed_total counter",
        f"traces_service_graph_request_failed_total{{{labels}}} {graph_failures}",
        "# HELP traces_service_graph_request_server_seconds Service graph latency histogram.",
        "# TYPE traces_service_graph_request_server_seconds histogram",
        f'traces_service_graph_request_server_seconds_bucket{{{labels},le="0.1"}} {bucket_100}',
        f'traces_service_graph_request_server_seconds_bucket{{{labels},le="0.5"}} {bucket_500}',
        f'traces_service_graph_request_server_seconds_bucket{{{labels},le="1"}} {graph_requests}',
        f'traces_service_graph_request_server_seconds_bucket{{{labels},le="+Inf"}} {graph_requests}',
        f"traces_service_graph_request_server_seconds_sum{{{labels}}} {graph_duration_sum:.6f}",
        f"traces_service_graph_request_server_seconds_count{{{labels}}} {graph_requests}",
        "",
    ]
    return "\n".join(lines)



class Handler(BaseHTTPRequestHandler):
    server_version = "AI-SRE-RCA-Validation/0.8.0-rc.2"

    def log_message(self, format: str, *args: Any) -> None:
        print(
            json.dumps(
                {
                    "message": format % args,
                    "service": SERVICE_NAME,
                    "pod": POD_NAME,
                }
            ),
            flush=True,
        )

    def _write(
        self,
        status: int,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            self._write(
                200,
                b'{"status":"ok"}',
                "application/json",
            )
            return

        if parsed.path == "/metrics":
            body = metrics_text().encode("utf-8")
            self._write(
                200,
                body,
                "text/plain; version=0.0.4; charset=utf-8",
            )
            return

        if parsed.path != "/checkout":
            self._write(
                404,
                b'{"error":"not found"}',
                "application/json",
            )
            return

        query = urllib.parse.parse_qs(parsed.query)
        mode = (query.get("mode") or ["healthy"])[0]
        failed = mode == "db-error"
        duration = 0.25 if failed else 0.03
        STATE.record(failed=failed, duration=duration)

        telemetry_errors: list[str] = []
        trace_id = ""
        try:
            trace_id = emit_trace(failed=failed)
        except Exception as exc:
            telemetry_errors.append(
                f"tempo:{type(exc).__name__}:{exc}"
            )
        try:
            push_log(
                failed=failed,
                trace_id=trace_id or "unavailable",
            )
        except Exception as exc:
            telemetry_errors.append(
                f"loki:{type(exc).__name__}:{exc}"
            )

        response = {
            "service": SERVICE_NAME,
            "mode": mode,
            "trace_id": trace_id or None,
            "telemetry_errors": telemetry_errors,
        }
        status = 500 if failed else 200
        self._write(
            status,
            json.dumps(response).encode("utf-8"),
            "application/json",
        )


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(
        json.dumps(
            {
                "message": "validation workload started",
                "service": SERVICE_NAME,
                "namespace": NAMESPACE,
                "pod": POD_NAME,
                "port": PORT,
                "tempo": OTLP_HTTP_URL,
                "loki": LOKI_PUSH_URL,
            }
        ),
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
