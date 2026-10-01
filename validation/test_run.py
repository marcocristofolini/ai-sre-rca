from __future__ import annotations

from validation import run as harness


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


def test_observability_payload_helpers():
    prometheus = {
        "data": {
            "result": [
                {"value": [1720000000, "3.0"]},
            ]
        }
    }
    loki = {
        "data": {
            "result": [
                {"values": [["1", "a"], ["2", "b"]]},
                {"values": [["3", "c"]]},
            ]
        }
    }

    assert harness._prometheus_value(prometheus) == 3.0
    assert harness._loki_entry_count(loki) == 3


def test_stateful_signal_value_reads_exporter_scalar():
    payload = {
        "signals": {
            "connections": {"value": 15.0},
            "max_connections": {"value": "20"},
            "missing": {"value": None},
        }
    }

    assert harness._stateful_signal_value(payload, "connections") == 15.0
    assert harness._stateful_signal_value(payload, "max_connections") == 20.0
    assert harness._stateful_signal_value(payload, "missing") is None
    assert harness._stateful_signal_value(payload, "unknown") is None


def test_validation_settings_switches_full_stack_sources(tmp_path):
    token = tmp_path / "token"
    ca = tmp_path / "ca.crt"
    token.write_text("token", encoding="utf-8")
    ca.write_text("ca", encoding="utf-8")

    enabled = harness.validation_settings(
        database_url="postgresql+asyncpg://user:pass@localhost/db",
        api_server="https://127.0.0.1:6443",
        token_file=token,
        ca_file=ca,
        full_stack_observability=True,
    )
    disabled = harness.validation_settings(
        database_url="postgresql+asyncpg://user:pass@localhost/db",
        api_server="https://127.0.0.1:6443",
        token_file=token,
        ca_file=ca,
        full_stack_observability=False,
    )

    enabled_cluster = enabled.cluster_config[harness.LOGICAL_CLUSTER]
    disabled_cluster = disabled.cluster_config[harness.LOGICAL_CLUSTER]
    assert enabled_cluster["prometheus_url"].endswith(
        f":{harness.PROMETHEUS_PORT}"
    )
    assert enabled_cluster["loki_url"].endswith(f":{harness.LOKI_PORT}")
    assert enabled_cluster["tempo_url"].endswith(f":{harness.TEMPO_PORT}")
    assert enabled.trace_analysis_enabled is True

    assert disabled_cluster["prometheus_url"] == "http://127.0.0.1:9"
    assert disabled_cluster["loki_url"] == "http://127.0.0.1:9"
    assert disabled_cluster["tempo_url"] == "http://127.0.0.1:9"
    assert disabled.trace_analysis_enabled is False


def test_report_writer_includes_full_stack_sections(tmp_path):
    report = {
        "schema_version": harness.REPORT_SCHEMA,
        "generated_at": "2026-10-01T00:00:00+00:00",
        "passed": True,
        "validation": {
            "scenarios": [],
            "observability": {
                "prometheus_failure_requests": 3.0,
                "loki_entries": 3,
                "tempo_trace_count": 3,
                "service_graph_edges": 1,
                "capabilities": {
                    "prometheus": "healthy",
                    "loki": "healthy",
                    "tempo": "healthy",
                },
                "passed": True,
            },
            "distributed_trace": {
                "expected_root_cause": "trace-causal-propagation",
                "classic": {
                    "root_cause": "trace-causal-propagation",
                    "trace_count": 3,
                },
                "agentic": {
                    "root_cause": "trace-causal-propagation",
                },
                "evidence_integrity_ok": True,
                "passed": True,
            },
            "stateful_postgresql": {
                "postgres_exporter": {
                    "connections": 15.0,
                    "max_connections": 20.0,
                    "utilization": 0.75,
                    "critical_threshold": 0.70,
                },
                "classic": {
                    "root_cause": "postgres-connection-exhaustion:dependency:postgresql/orders@orders-db:5432",
                },
                "agentic": {
                    "root_cause": "postgres-connection-exhaustion:dependency:postgresql/orders@orders-db:5432",
                },
                "evidence_integrity_ok": True,
                "passed": True,
            },
            "recovery": {
                "recovered_postgres": {
                    "connections": 1.0,
                    "max_connections": 20.0,
                    "utilization": 0.05,
                },
                "fresh_telemetry": {
                    "prometheus_success_requests": 3.0,
                    "loki_recovery_entries": 3,
                    "tempo_recovery_trace_count": 3,
                },
                "classic": {
                    "root_cause": None,
                    "trace_causal_evidence_count": 0,
                },
                "agentic": {
                    "root_cause": None,
                },
                "evidence_integrity_ok": True,
                "passed": True,
            },
            "inventory": {
                "relationship_types": [],
                "passed": True,
            },
        },
    }

    harness.write_reports(tmp_path, report)
    markdown = (tmp_path / "validation-report.md").read_text(encoding="utf-8")

    assert "## Full-stack observability" in markdown
    assert "Tempo traces: 3" in markdown
    assert "## Distributed trace RCA" in markdown
    assert "trace-causal-propagation" in markdown
    assert "## Stateful PostgreSQL RCA" in markdown
    assert "Exporter utilization: 0.75" in markdown
    assert "postgres-connection-exhaustion:" in markdown
    assert "## Recovery validation" in markdown
    assert "Recovered PostgreSQL utilization: 0.05" in markdown
    assert "Fresh trace causal evidence count: 0" in markdown
