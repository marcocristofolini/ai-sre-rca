import json
from types import SimpleNamespace

import pytest

from app import benchmark


@pytest.mark.asyncio
async def test_both_engines_match_ground_truth_and_repeat(monkeypatch):
    monkeypatch.setenv("AGENTIC_MAX_TASKS", "1")
    monkeypatch.setenv("AGENTIC_KUBERNETES_MAX_QUERIES", "1")
    first = await benchmark.run_benchmark()
    assert first == await benchmark.run_benchmark()
    assert len(first["results"]) == 4
    assert all(row["correct"] for row in first["results"])
    for values in first["metrics"].values():
        assert values["accuracy"] == 1
        assert values["answered_accuracy"] == 1
        assert values["abstention_rate"] == 0.5
        assert values["correct_abstention_rate"] == 1
        assert values["errors"] == 0


def test_metrics_count_wrong_answers_missed_answers_and_errors():
    rows = [
        {"expected_hypothesis": "disk-pressure", "predicted_hypothesis": "wrong", "correct": False, "error": None},
        {"expected_hypothesis": "disk-pressure", "predicted_hypothesis": None, "correct": False, "error": None},
        {"expected_hypothesis": None, "predicted_hypothesis": "wrong", "correct": False, "error": None},
        {"expected_hypothesis": None, "predicted_hypothesis": None, "correct": False, "error": "RuntimeError"},
    ]
    values = benchmark.metrics(rows)
    assert values["accuracy"] == 0
    assert values["answered_accuracy"] == 0
    assert values["abstention_rate"] == 0.25
    assert values["correct_abstention_rate"] == 0
    assert values["false_diagnoses"] == 2
    assert values["errors"] == 1
    assert benchmark.metrics([])["accuracy"] is None
    assert benchmark.metrics([])["abstention_rate"] is None


@pytest.mark.asyncio
async def test_engine_errors_do_not_score_as_correct_abstentions(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(benchmark.InvestigationEngine, "investigate", fail)
    report = await benchmark.run_benchmark()
    values = report["metrics"]["deterministic"]
    assert values["errors"] == 2
    assert values["correct_abstention_rate"] == 0
    assert values["abstention_rate"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["specialist", "integrity", "tampered"])
async def test_agentic_failures_are_errors_not_abstentions(monkeypatch, failure):
    execute = benchmark.execute_investigation_plan

    async def corrupt(*args, **kwargs):
        execution = await execute(*args, **kwargs)
        if failure == "specialist":
            execution.specialist_results[0].status = "failed"
        elif failure == "integrity":
            execution.shared_evidence.evidence_integrity_ok = False
        else:
            execution.shared_evidence.evidence[0].summary = "tampered"
        return execution

    # The answerable fixture ensures there is evidence available to tamper with.
    scenario = benchmark.scenarios()[0]
    monkeypatch.setattr(benchmark, "scenarios", lambda: (scenario,))
    monkeypatch.setattr(benchmark, "execute_investigation_plan", corrupt)
    report = await benchmark.run_benchmark()
    assert report["metrics"]["deterministic"]["accuracy"] == 1
    assert report["metrics"]["agentic"]["errors"] == 1
    assert report["metrics"]["agentic"]["abstention_rate"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing-root", "tampered"])
async def test_invalid_deterministic_results_are_errors(monkeypatch, failure):
    investigate = benchmark.InvestigationEngine.investigate

    async def corrupt(self, *args, **kwargs):
        result = await investigate(self, *args, **kwargs)
        if failure == "missing-root":
            return SimpleNamespace(status="resolved", root_cause=None, evidence=result.evidence)
        result.evidence[0].summary = "tampered"
        return result

    scenario = benchmark.scenarios()[0]
    monkeypatch.setattr(benchmark, "scenarios", lambda: (scenario,))
    monkeypatch.setattr(benchmark.InvestigationEngine, "investigate", corrupt)
    report = await benchmark.run_benchmark()
    assert report["metrics"]["deterministic"]["errors"] == 1
    assert report["metrics"]["deterministic"]["abstention_rate"] == 0


def test_cli_writes_reports_and_returns_failure_status(tmp_path, monkeypatch):
    report = {"benchmark_version": 1, "scenario_suite": "demo-v1", "metrics": {},
              "results": [{"scenario": "unsupported-alert", "engine": "agentic",
                           "expected_hypothesis": None, "predicted_hypothesis": None,
                           "correct": False, "error": "RuntimeError"}]}

    async def run():
        return report

    monkeypatch.setattr(benchmark, "run_benchmark", run)
    monkeypatch.setattr("sys.argv", ["benchmark", "--report-dir", str(tmp_path)])
    assert benchmark.main() == 1
    assert json.loads((tmp_path / "benchmark.json").read_text()) == report
    assert "RuntimeError" in (tmp_path / "benchmark.md").read_text()
