from __future__ import annotations

from validation import run as harness
from validation.workload import app as workload


def pod_with_termination(
    *,
    reason: str,
    exit_code: int,
    waiting_reason: str | None = None,
):
    state = {}
    if waiting_reason:
        state["waiting"] = {"reason": waiting_reason}
    return {
        "metadata": {"name": "pod-a"},
        "spec": {"nodeName": "node-a"},
        "status": {
            "containerStatuses": [
                {
                    "state": state,
                    "lastState": {
                        "terminated": {
                            "reason": reason,
                            "exitCode": exit_code,
                        }
                    },
                }
            ]
        },
    }


def test_terminated_reason_reads_last_state():
    pod = pod_with_termination(reason="OOMKilled", exit_code=137)
    assert harness.terminated_reason(pod) == ("OOMKilled", 137)


def test_wait_for_oom_requires_real_oom_state(monkeypatch):
    pod = pod_with_termination(reason="OOMKilled", exit_code=137)
    monkeypatch.setattr(harness, "pods_for_label", lambda *_: [pod])

    observed = harness.wait_for_oom("kind-test")

    assert observed is not None
    assert observed["signal"] == {
        "terminated_reason": "OOMKilled",
        "exit_code": 137,
    }


def test_wait_for_crashloop_requires_waiting_and_nonzero_exit(monkeypatch):
    pod = pod_with_termination(
        reason="Error",
        exit_code=42,
        waiting_reason="CrashLoopBackOff",
    )
    pod["status"]["containerStatuses"][0]["restartCount"] = 2
    monkeypatch.setattr(harness, "pods_for_label", lambda *_: [pod])
    monkeypatch.setattr(
        harness,
        "kubectl_json",
        lambda *_: {
            "items": [
                {
                    "reason": "BackOff",
                    "message": "Back-off restarting failed container",
                }
            ]
        },
    )

    observed = harness.wait_for_crashloop("kind-test")

    assert observed is not None
    assert observed["signal"]["waiting_reason"] == "CrashLoopBackOff"
    assert observed["signal"]["restart_count"] == 2
    assert observed["signal"]["last_exit_code"] == 42
    assert observed["signal"]["backoff_event_observed"] is True


def test_failed_scheduling_requires_scheduler_evidence(monkeypatch):
    pod = {
        "metadata": {"name": "failed-scheduling-target"},
        "spec": {},
        "status": {"phase": "Pending"},
    }
    events = {
        "items": [
            {
                "reason": "FailedScheduling",
                "message": "0/2 nodes are available: 2 Insufficient memory.",
            }
        ]
    }

    def fake_json(_context, *args):
        return events if args[1] == "events" else pod

    monkeypatch.setattr(harness, "kubectl_json", fake_json)
    observed = harness.wait_for_failed_scheduling("kind-test")

    assert observed is not None
    assert observed["signal"]["reason"] == "FailedScheduling"
    assert "Insufficient memory" in observed["signal"]["message"]


def test_report_writer_never_writes_runtime_credentials(tmp_path):
    report = {
        "schema_version": harness.REPORT_SCHEMA,
        "generated_at": "2026-10-01T00:00:00+00:00",
        "passed": True,
        "validation": {
            "scenarios": [],
            "inventory": {
                "relationship_types": [],
                "passed": True,
            },
        },
    }

    harness.write_reports(tmp_path, report)

    assert (tmp_path / "validation-report.json").exists()
    assert (tmp_path / "validation-report.md").exists()
    assert not (tmp_path / "runtime").exists()


def test_ready_pod_for_label_requires_ready_condition(monkeypatch):
    not_ready = {
        "metadata": {"name": "checkout-a"},
        "status": {
            "conditions": [
                {"type": "Ready", "status": "False"}
            ]
        },
    }
    ready = {
        "metadata": {"name": "checkout-b"},
        "status": {
            "conditions": [
                {"type": "Ready", "status": "True"}
            ]
        },
    }
    monkeypatch.setattr(
        harness,
        "pods_for_label",
        lambda *_: [not_ready, ready],
    )

    observed = harness.ready_pod_for_label(
        "kind-test",
        "app=checkout",
    )

    assert observed == ready


def test_prometheus_value_reads_first_vector_sample():
    payload = {
        "data": {
            "result": [
                {"value": [1234, "42.5"]}
            ]
        }
    }

    assert harness.prometheus_value(payload) == 42.5
    assert harness.prometheus_value({"data": {"result": []}}) is None


def test_trigger_checkout_fails_closed_on_telemetry_error(monkeypatch):
    monkeypatch.setattr(
        harness,
        "http_json",
        lambda *_args, **_kwargs: (
            500,
            {
                "trace_id": "abc",
                "telemetry_errors": ["tempo:unavailable"],
            },
        ),
    )

    try:
        harness.trigger_checkout("db-error")
    except RuntimeError as exc:
        assert "telemetry export failed" in str(exc)
    else:
        raise AssertionError("telemetry export failure must fail closed")


def test_validation_workload_emits_trace_dependency_contract(monkeypatch):
    captured = {}

    def fake_post(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return 200

    monkeypatch.setattr(workload, "_post_json", fake_post)

    trace_id = workload.emit_trace(failed=True)

    assert len(trace_id) == 32
    assert captured["url"] == workload.OTLP_HTTP_URL
    spans = (
        captured["payload"]["resourceSpans"][0]
        ["scopeSpans"][0]["spans"]
    )
    root, database = spans
    assert root["status"]["code"] == 2
    assert root["kind"] == 2
    assert database["kind"] == 3
    assert database["parentSpanId"] == root["spanId"]
    keys = {item["key"] for item in database["attributes"]}
    assert {
        "db.system",
        "db.namespace",
        "server.address",
        "server.port",
    }.issubset(keys)


def test_validation_workload_exposes_prometheus_contract():
    original = {
        "requests": workload.STATE.requests,
        "errors": workload.STATE.errors,
        "duration_sum": workload.STATE.duration_sum,
        "db_active": workload.STATE.db_active,
        "db_idle": workload.STATE.db_idle,
        "graph_requests": workload.STATE.graph_requests,
        "graph_failures": workload.STATE.graph_failures,
        "graph_duration_sum": workload.STATE.graph_duration_sum,
    }
    try:
        workload.STATE.record(failed=True, duration=0.25)
        metrics = workload.metrics_text()
        assert "validation_requests_total" in metrics
        assert 'instance="orders-db:5432"' in metrics
        assert "traces_service_graph_request_total" in metrics
        assert 'server="postgresql/orders"' in metrics
    finally:
        with workload.STATE.lock:
            for key, value in original.items():
                setattr(workload.STATE, key, value)
