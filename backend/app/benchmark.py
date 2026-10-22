"""Offline RCA benchmark v1. Run with python -m app.benchmark."""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.config import Settings
from app.core.engine import InvestigationEngine
from app.core.integrity import verify_evidence_hash
from app.db.repository import MemoryRepository
from app.integrations.demo import DemoTools
from app.schemas import IncidentInput


@dataclass(frozen=True)
class Scenario:
    id: str
    expected_hypothesis: str | None
    incident: IncidentInput


def scenarios() -> tuple[Scenario, ...]:
    """Fixed inputs and local DemoTools evidence; no live integrations or LLM."""
    at = datetime(2026, 9, 24, 20, 47, tzinfo=timezone.utc)
    return (
        Scenario("storage-eviction", "disk-pressure", IncidentInput(
            title="Pod evicted", cluster="prod", namespace="exchange",
            pod="landing-page-7d8b6d9b-x9k2p", node="ip-10-0-1-42",
            alert_name="KubePodEvicted", starts_at=at,
        )),
        Scenario("unsupported-alert", None, IncidentInput(
            title="Unknown alert without a target", cluster="prod",
            alert_name="UnknownAlert", starts_at=at,
        )),
    )


def metrics(rows: list[dict]) -> dict:
    """Zero denominators are null; expected abstentions never inflate RCA accuracy."""
    answerable = [row for row in rows if row["expected_hypothesis"] is not None]
    unanswerable = [row for row in rows if row["expected_hypothesis"] is None]
    successful = [row for row in rows if row["error"] is None]
    answered = [row for row in successful if row["predicted_hypothesis"] is not None]
    correct = sum(row["correct"] for row in answered)
    abstentions = sum(row["predicted_hypothesis"] is None for row in successful)
    safe = sum(row["correct"] for row in unanswerable)
    return {
        "total": len(rows), "errors": len(rows) - len(successful),
        "correct_diagnoses": correct, "abstentions": abstentions,
        "accuracy": correct / len(answerable) if answerable else None,
        "answered_accuracy": correct / len(answered) if answered else None,
        "abstention_rate": abstentions / len(rows) if rows else None,
        "correct_abstention_rate": safe / len(unanswerable) if unanswerable else None,
        "false_diagnoses": sum(not row["correct"] for row in answered),
    }


async def run_benchmark() -> dict:
    # model_construct uses declared defaults without loading environment or .env.
    # Production settings and security gates are never modified.
    settings = Settings.model_construct(agentic_execution_enabled=True)
    rows = []
    for scenario in scenarios():
        for engine in ("deterministic", "agentic"):
            prediction = None
            error = None
            try:
                tools = DemoTools()
                incident = scenario.incident.model_copy(deep=True)
                if engine == "deterministic":
                    result = await InvestigationEngine(tools, settings=settings).investigate(incident)
                    evidence = result.evidence
                else:
                    plan = await build_investigation_plan(
                        MemoryRepository(), incident=incident, settings=settings,
                    )
                    execution = await execute_investigation_plan(plan, tools=tools, settings=settings)
                    # A failed specialist is an execution error, not an abstention.
                    if any(item.status == "failed" for item in execution.specialist_results):
                        raise RuntimeError("specialist execution failed")
                    if not execution.shared_evidence.evidence_integrity_ok:
                        raise ValueError("shared evidence integrity failed")
                    evidence = execution.shared_evidence.evidence
                    result = evaluate_shared_evidence(execution)
                if not all(verify_evidence_hash(item) for item in evidence):
                    raise ValueError("evidence integrity failed")
                if result.status == "resolved":
                    if result.root_cause is None:
                        raise ValueError("resolved diagnosis has no root cause")
                    prediction = result.root_cause.hypothesis_id
                    if not prediction:
                        raise ValueError("resolved diagnosis has no hypothesis ID")
            except Exception as exc:
                error = type(exc).__name__
            rows.append({
                "scenario": scenario.id, "engine": engine,
                "expected_hypothesis": scenario.expected_hypothesis,
                "predicted_hypothesis": prediction,
                "correct": error is None and prediction == scenario.expected_hypothesis,
                "error": error,
            })
    return {
        "benchmark_version": 1, "scenario_suite": "demo-v1",
        "results": rows,
        "metrics": {engine: metrics([row for row in rows if row["engine"] == engine])
                    for engine in ("deterministic", "agentic")},
    }


def markdown_report(report: dict) -> str:
    lines = ["# RCA Benchmark v1", "", f"Scenario suite: `{report['scenario_suite']}`", "",
             "| Engine | Accuracy | Answered accuracy | Abstention rate | Correct abstention rate | Errors |",
             "| --- | --- | --- | --- | --- | --- |"]
    for engine, values in report["metrics"].items():
        rates = ["n/a" if values[key] is None else f"{values[key]:.1%}" for key in
                 ("accuracy", "answered_accuracy", "abstention_rate", "correct_abstention_rate")]
        lines.append(f"| {engine} | {' | '.join(rates)} | {values['errors']} |")
    lines += ["", "| Scenario | Engine | Expected | Predicted | Correct | Error |",
              "| --- | --- | --- | --- | --- | --- |"]
    for row in report["results"]:
        lines.append(f"| {row['scenario']} | {row['engine']} | "
                     f"{row['expected_hypothesis'] or 'abstain'} | "
                     f"{row['predicted_hypothesis'] or 'abstain'} | {row['correct']} | {row['error'] or ''} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, default=Path("benchmark-artifacts"))
    args = parser.parse_args()
    report = asyncio.run(run_benchmark())
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "benchmark.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.report_dir / "benchmark.md").write_text(markdown_report(report), encoding="utf-8")
    return int(any(not row["correct"] for row in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
