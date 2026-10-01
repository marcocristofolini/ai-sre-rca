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
    monkeypatch.setattr(harness, "pods_for_label", lambda *_: [pod])

    observed = harness.wait_for_crashloop("kind-test")

    assert observed is not None
    assert observed["signal"]["waiting_reason"] == "CrashLoopBackOff"
    assert observed["signal"]["last_exit_code"] == 42


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
