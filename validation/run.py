from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agentic.evaluator import evaluate_shared_evidence
from app.agentic.executor import execute_investigation_plan
from app.agentic.planner import build_investigation_plan
from app.awareness.kubernetes_inventory import reconcile_kubernetes_inventory
from app.config import Settings
from app.core.engine import InvestigationEngine
from app.core.integrity import verify_evidence_hash
from app.db.repository import SQLRepository
from app.integrations.http_tools import ReadOnlyHTTPTools
from app.schemas import IncidentInput

LOGICAL_CLUSTER = "kind-validation"
NAMESPACE = "rca-validation"
DEFAULT_CLUSTER_NAME = "ai-sre-rca-validation"
DEFAULT_NODE_IMAGE = (
    "kindest/node:v1.37.0@sha256:"
    "a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
)
POSTGRES_CONTAINER = "ai-sre-rca-validation-postgres"
POSTGRES_PORT = 15439
VALIDATION_WORKLOAD_IMAGE = "ai-sre-rca-validation-workload:local"
WORKLOAD_URL = "http://127.0.0.1:18080"
PROMETHEUS_URL = "http://127.0.0.1:19090"
LOKI_URL = "http://127.0.0.1:13100"
TEMPO_URL = "http://127.0.0.1:13200"
REPORT_SCHEMA = "ai-sre-rca.validation.v2"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    process = subprocess.run(
        command,
        text=True,
        input=input_text,
        capture_output=capture,
        check=False,
    )
    if check and process.returncode != 0:
        if process.stdout:
            print(process.stdout, file=sys.stderr)
        if process.stderr:
            print(process.stderr, file=sys.stderr)
        raise RuntimeError(
            f"command failed with exit code {process.returncode}: "
            + " ".join(command)
        )
    return process


def output(command: list[str]) -> str:
    return run(command).stdout.strip()


def require_commands(names: list[str]) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "missing required command(s): " + ", ".join(sorted(missing))
        )


def kubectl(context: str, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["kubectl", "--context", context, *args])


def kubectl_json(context: str, *args: str) -> dict[str, Any]:
    raw = output(["kubectl", "--context", context, *args, "-o", "json"])
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("kubectl JSON response was not an object")
    return value


def wait_until(
    description: str,
    probe: Callable[[], Any | None],
    *,
    timeout_seconds: int = 180,
    interval_seconds: float = 2.0,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = probe()
            if value is not None:
                print(f"observed: {description}", flush=True)
                return value
        except Exception as exc:
            last_error = exc
        time.sleep(interval_seconds)
    suffix = f": {last_error}" if last_error else ""
    raise RuntimeError(f"timed out waiting for {description}{suffix}")


def pods_for_label(context: str, label: str) -> list[dict[str, Any]]:
    payload = kubectl_json(
        context,
        "get",
        "pods",
        "-n",
        NAMESPACE,
        "-l",
        label,
    )
    return [
        item
        for item in payload.get("items", [])
        if isinstance(item, dict)
    ]


def terminated_reason(pod: dict[str, Any]) -> tuple[str | None, int | None]:
    statuses = (pod.get("status") or {}).get("containerStatuses") or []
    for status in statuses:
        if not isinstance(status, dict):
            continue
        for state_name in ("lastState", "state"):
            terminated = (status.get(state_name) or {}).get("terminated")
            if isinstance(terminated, dict):
                reason = terminated.get("reason")
                exit_code = terminated.get("exitCode")
                if reason:
                    return str(reason), (
                        int(exit_code) if exit_code is not None else None
                    )
    return None, None


def wait_for_oom(context: str) -> dict[str, Any] | None:
    for pod in pods_for_label(context, "app=oom-target"):
        reason, exit_code = terminated_reason(pod)
        if reason == "OOMKilled":
            return {
                "pod": pod,
                "signal": {
                    "terminated_reason": reason,
                    "exit_code": exit_code,
                },
            }
    return None


def wait_for_crashloop(context: str) -> dict[str, Any] | None:
    for pod in pods_for_label(context, "app=crashloop-target"):
        statuses = (pod.get("status") or {}).get("containerStatuses") or []
        reason, exit_code = terminated_reason(pod)
        if not exit_code:
            continue

        restart_count = max(
            (
                int(status.get("restartCount") or 0)
                for status in statuses
                if isinstance(status, dict)
            ),
            default=0,
        )
        waiting_reason = next(
            (
                str(((status.get("state") or {}).get("waiting") or {}).get("reason"))
                for status in statuses
                if isinstance(status, dict)
                and ((status.get("state") or {}).get("waiting") or {}).get("reason")
            ),
            None,
        )
        pod_name = str((pod.get("metadata") or {}).get("name") or "")
        events = kubectl_json(
            context,
            "get",
            "events",
            "-n",
            NAMESPACE,
            "--field-selector",
            f"involvedObject.name={pod_name}",
        )
        backoff_event = next(
            (
                event
                for event in events.get("items", [])
                if isinstance(event, dict)
                and str(event.get("reason") or "") == "BackOff"
            ),
            None,
        )
        if restart_count >= 1 and (
            waiting_reason == "CrashLoopBackOff"
            or backoff_event is not None
        ):
            return {
                "pod": pod,
                "signal": {
                    "waiting_reason": waiting_reason,
                    "restart_count": restart_count,
                    "last_exit_code": exit_code,
                    "last_reason": reason,
                    "backoff_event_observed": backoff_event is not None,
                },
            }
    return None


def wait_for_failed_scheduling(context: str) -> dict[str, Any] | None:
    pod = kubectl_json(
        context,
        "get",
        "pod",
        "failed-scheduling-target",
        "-n",
        NAMESPACE,
    )
    events = kubectl_json(
        context,
        "get",
        "events",
        "-n",
        NAMESPACE,
        "--field-selector",
        "involvedObject.name=failed-scheduling-target",
    )
    for event in events.get("items", []):
        if not isinstance(event, dict):
            continue
        reason = str(event.get("reason") or "")
        message = str(event.get("message") or "")
        if reason == "FailedScheduling" and "Insufficient" in message:
            return {
                "pod": pod,
                "signal": {
                    "reason": reason,
                    "message": message,
                },
            }
    return None


def pod_identity(pod: dict[str, Any]) -> tuple[str, str | None]:
    metadata = pod.get("metadata") or {}
    spec = pod.get("spec") or {}
    return str(metadata.get("name") or ""), spec.get("nodeName")


def http_json(
    url: str,
    *,
    expected_statuses: tuple[int, ...] = (200,),
) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = int(response.status)
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read().decode("utf-8")
    if status not in expected_statuses:
        raise RuntimeError(
            f"unexpected HTTP status from {url}: {status}"
        )
    payload = json.loads(body or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP JSON response was not an object: {url}")
    return status, payload


def http_ready(url: str) -> dict[str, Any] | None:
    try:
        status, _ = http_json(
            url,
            expected_statuses=(200,),
        )
    except Exception:
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(request, timeout=3) as response:
                status = int(response.status)
        except Exception:
            return None
    return {"url": url, "status": status}


def build_validation_workload_image(cluster_name: str) -> None:
    run(
        [
            "docker",
            "build",
            "-t",
            VALIDATION_WORKLOAD_IMAGE,
            str(ROOT / "validation" / "workload"),
        ],
        capture=False,
    )
    run(
        [
            "kind",
            "load",
            "docker-image",
            VALIDATION_WORKLOAD_IMAGE,
            "--name",
            cluster_name,
        ],
        capture=False,
    )


def wait_for_deployment(
    context: str,
    deployment: str,
    *,
    timeout: str = "180s",
) -> None:
    kubectl(
        context,
        "rollout",
        "status",
        f"deployment/{deployment}",
        "-n",
        NAMESPACE,
        f"--timeout={timeout}",
    )


def delete_existing_cluster(cluster_name: str) -> None:
    clusters = output(["kind", "get", "clusters"]).splitlines()
    if cluster_name in clusters:
        run(["kind", "delete", "cluster", "--name", cluster_name], capture=False)


def create_cluster(cluster_name: str, node_image: str) -> str:
    delete_existing_cluster(cluster_name)
    run(
        [
            "kind",
            "create",
            "cluster",
            "--name",
            cluster_name,
            "--config",
            str(ROOT / "validation" / "kind-cluster.yaml"),
            "--image",
            node_image,
            "--wait",
            "120s",
        ],
        capture=False,
    )
    context = f"kind-{cluster_name}"
    kubectl(
        context,
        "apply",
        "-f",
        str(ROOT / "validation" / "manifests" / "access.yaml"),
    )
    kubectl(
        context,
        "apply",
        "-f",
        str(ROOT / "validation" / "manifests" / "observability.yaml"),
    )
    build_validation_workload_image(cluster_name)
    kubectl(
        context,
        "apply",
        "-f",
        str(
            ROOT
            / "validation"
            / "manifests"
            / "telemetry-workload.yaml"
        ),
    )
    kubectl(
        context,
        "apply",
        "-f",
        str(ROOT / "validation" / "manifests" / "workloads.yaml"),
    )

    for deployment in ("prometheus", "loki", "tempo", "checkout"):
        wait_for_deployment(context, deployment)

    wait_until(
        "validation workload HTTP readiness",
        lambda: http_ready(f"{WORKLOAD_URL}/health"),
        timeout_seconds=120,
    )
    wait_until(
        "Prometheus readiness",
        lambda: http_ready(f"{PROMETHEUS_URL}/-/ready"),
        timeout_seconds=120,
    )
    wait_until(
        "Loki readiness",
        lambda: http_ready(f"{LOKI_URL}/ready"),
        timeout_seconds=120,
    )
    wait_until(
        "Tempo readiness",
        lambda: http_ready(f"{TEMPO_URL}/ready"),
        timeout_seconds=120,
    )
    return context


def start_postgres(port: int) -> str:
    run(["docker", "rm", "-f", POSTGRES_CONTAINER], check=False)
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            POSTGRES_CONTAINER,
            "-e",
            "POSTGRES_USER=ai_sre",
            "-e",
            "POSTGRES_PASSWORD=ai_sre",
            "-e",
            "POSTGRES_DB=ai_sre",
            "-p",
            f"{port}:5432",
            "pgvector/pgvector:pg17",
        ]
    )
    for _ in range(60):
        probe = run(
            [
                "docker",
                "exec",
                POSTGRES_CONTAINER,
                "pg_isready",
                "-U",
                "ai_sre",
                "-d",
                "ai_sre",
            ],
            check=False,
        )
        if probe.returncode == 0:
            return (
                "postgresql+asyncpg://ai_sre:ai_sre@"
                f"127.0.0.1:{port}/ai_sre"
            )
        time.sleep(1)
    raise RuntimeError("validation PostgreSQL did not become ready")


def export_cluster_credentials(
    context: str,
    runtime_dir: Path,
) -> tuple[str, Path, Path]:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    kubeconfig = kubectl_json(context, "config", "view", "--raw", "--minify")
    cluster = (kubeconfig.get("clusters") or [{}])[0].get("cluster") or {}
    server = str(cluster.get("server") or "")
    ca_data = str(cluster.get("certificate-authority-data") or "")
    if not server or not ca_data:
        raise RuntimeError("Kind kubeconfig did not contain server and CA data")

    token = output(
        [
            "kubectl",
            "--context",
            context,
            "create",
            "token",
            "ai-sre-rca-validator",
            "-n",
            NAMESPACE,
            "--duration=1h",
        ]
    )
    token_file = runtime_dir / "kubernetes.token"
    ca_file = runtime_dir / "kubernetes.ca.crt"
    token_file.write_text(token + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    ca_file.write_bytes(base64.b64decode(ca_data))
    return server, token_file, ca_file


def validation_settings(
    *,
    database_url: str,
    api_server: str,
    token_file: Path,
    ca_file: Path,
) -> Settings:
    registry = {
        LOGICAL_CLUSTER: {
            "kubernetes_api_url": api_server,
            "kubernetes_token_file": str(token_file),
            "kubernetes_ca_file": str(ca_file),
            "prometheus_url": PROMETHEUS_URL,
            "loki_url": LOKI_URL,
            "tempo_url": TEMPO_URL,
            "argocd_url": "http://127.0.0.1:9",
        }
    }
    return Settings(
        demo_mode=False,
        database_url=database_url,
        query_budget_enabled=False,
        notifications_enabled=False,
        llm_enabled=False,
        trace_analysis_enabled=True,
        trace_search_limit=8,
        trace_fetch_limit=4,
        stateful_dependency_analysis_enabled=True,
        agentic_execution_enabled=True,
        agentic_model_planner_enabled=False,
        cluster_config_json=json.dumps(registry),
        awareness_inventory_tenant_id="validation",
        awareness_inventory_clusters=LOGICAL_CLUSTER,
        awareness_inventory_namespaces="",
        awareness_inventory_kinds=(
            "Service,Deployment,StatefulSet,DaemonSet,ReplicaSet,Pod,Node"
        ),
    )


async def investigate_scenario(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    name: str,
    alert_name: str,
    pod: dict[str, Any],
    signal: dict[str, Any],
    expected_root: str | None,
    expected_status: str = "resolved",
    allowed_statuses: tuple[str, ...] | None = None,
    allowed_roots: tuple[str | None, ...] | None = None,
    forbidden_roots: tuple[str, ...] = (),
) -> dict[str, Any]:
    pod_name, node_name = pod_identity(pod)
    incident = IncidentInput(
        title=f"Real environment validation: {name}",
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        pod=pod_name,
        node=node_name,
        alert_name=alert_name,
        severity="critical",
        starts_at=utcnow(),
        labels={"validation_scenario": name},
    )

    engine = InvestigationEngine(
        tools=tools,
        memory=repo,
        settings=settings,
    )
    classic = await engine.investigate(incident)
    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=tools,
        settings=settings,
    )
    agentic = evaluate_shared_evidence(execution)

    classic_root = (
        classic.root_cause.hypothesis_id
        if classic.root_cause is not None
        else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None
        else None
    )
    integrity_ok = all(
        verify_evidence_hash(item)
        for item in classic.evidence
    ) and execution.shared_evidence.evidence_integrity_ok
    kubernetes_result = next(
        (
            item
            for item in execution.specialist_results
            if item.specialist == "kubernetes"
        ),
        None,
    )
    accepted_statuses = set(allowed_statuses or (expected_status,))
    accepted_roots = set(
        allowed_roots if allowed_roots is not None else (expected_root,)
    )
    parity_ok = (
        classic.status == agentic.status
        and classic_root == agentic_root
    )
    passed = (
        classic.status in accepted_statuses
        and agentic.status in accepted_statuses
        and classic_root in accepted_roots
        and agentic_root in accepted_roots
        and classic_root not in forbidden_roots
        and agentic_root not in forbidden_roots
        and parity_ok
        and integrity_ok
        and kubernetes_result is not None
        and kubernetes_result.status == "completed"
    )
    return {
        "name": name,
        "pod": pod_name,
        "node": node_name,
        "signal": signal,
        "expected_status": expected_status,
        "expected_root_cause": expected_root,
        "accepted_statuses": sorted(accepted_statuses),
        "accepted_root_causes": sorted(
            (
                "none" if item is None else item
                for item in accepted_roots
            )
        ),
        "forbidden_root_causes": sorted(forbidden_roots),
        "classic_agentic_parity_ok": parity_ok,
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "evidence_count": len(classic.evidence),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": {
                item.specialist: item.status
                for item in execution.specialist_results
            },
            "evidence_count": len(execution.shared_evidence.evidence),
        },
        "evidence_integrity_ok": integrity_ok,
        "passed": passed,
    }


async def validate_inventory(
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    context: str,
) -> dict[str, Any]:
    first_at = utcnow()
    first = await reconcile_kubernetes_inventory(
        repo,
        tools,
        settings,
        observed_at=first_at,
    )
    graph = await repo.get_awareness_graph(
        "validation",
        LOGICAL_CLUSTER,
        limit=500,
    )
    validation_entities = [
        item
        for item in graph.entities
        if item.namespace == NAMESPACE
    ]
    relationship_types = {
        item.relationship
        for item in graph.relationships
    }
    victim = next(
        (
            item
            for item in graph.entities
            if item.kind == "Deployment"
            and item.namespace == NAMESPACE
            and item.name == "inventory-victim"
        ),
        None,
    )

    kubectl(
        context,
        "delete",
        "deployment,service",
        "inventory-victim",
        "-n",
        NAMESPACE,
        "--wait=true",
    )
    second_at = first_at + timedelta(seconds=2)
    second = await reconcile_kubernetes_inventory(
        repo,
        tools,
        settings,
        observed_at=second_at,
    )
    after = await repo.get_awareness_graph(
        "validation",
        LOGICAL_CLUSTER,
        limit=500,
    )
    tombstoned = next(
        (
            item
            for item in after.entities
            if item.kind == "Deployment"
            and item.namespace == NAMESPACE
            and item.name == "inventory-victim"
        ),
        None,
    )
    tombstones = sum(
        result.entities_tombstoned
        for cluster in second
        for result in cluster.results
    )
    initial_resources = sum(
        result.resources_observed
        for cluster in first
        for result in cluster.results
    )
    passed = (
        len(validation_entities) >= 7
        and "owned_by" in relationship_types
        and "serves" in relationship_types
        and "scheduled_on" in relationship_types
        and victim is not None
        and victim.tombstone_source is None
        and tombstoned is not None
        and tombstoned.tombstone_source == "kubernetes"
        and tombstones >= 1
    )
    return {
        "initial_resources_seen": initial_resources,
        "validation_namespace_entities": len(validation_entities),
        "relationship_types": sorted(relationship_types),
        "victim_seen_before_delete": victim is not None,
        "victim_tombstone_source": (
            tombstoned.tombstone_source if tombstoned else None
        ),
        "entities_tombstoned": tombstones,
        "passed": passed,
    }


async def async_wait_until(
    description: str,
    probe,
    *,
    timeout_seconds: int = 120,
    interval_seconds: float = 2.0,
):
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = await probe()
            if value is not None:
                print(f"observed: {description}", flush=True)
                return value
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(interval_seconds)
    suffix = f": {last_error}" if last_error else ""
    raise RuntimeError(
        f"timed out waiting for {description}{suffix}"
    )


def ready_pod_for_label(
    context: str,
    label: str,
) -> dict[str, Any] | None:
    for pod in pods_for_label(context, label):
        conditions = (pod.get("status") or {}).get("conditions") or []
        ready = any(
            isinstance(item, dict)
            and item.get("type") == "Ready"
            and item.get("status") == "True"
            for item in conditions
        )
        if ready:
            return pod
    return None


def trigger_checkout(mode: str) -> dict[str, Any]:
    expected = (500,) if mode == "db-error" else (200,)
    status, payload = http_json(
        f"{WORKLOAD_URL}/checkout?mode={mode}",
        expected_statuses=expected,
    )
    telemetry_errors = payload.get("telemetry_errors") or []
    if telemetry_errors:
        raise RuntimeError(
            "validation workload telemetry export failed: "
            + ", ".join(str(item) for item in telemetry_errors)
        )
    if not payload.get("trace_id"):
        raise RuntimeError(
            "validation workload response did not contain a trace_id"
        )
    return {
        "status": status,
        "trace_id": str(payload["trace_id"]),
        "mode": mode,
    }


def prometheus_value(
    payload: dict[str, Any],
) -> float | None:
    rows = payload.get("data", {}).get("result", [])
    if not rows:
        return None
    try:
        return float(rows[0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def run_observability_investigation(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    pod: dict[str, Any],
    starts_at: datetime,
) -> dict[str, Any]:
    pod_name, node_name = pod_identity(pod)
    incident = IncidentInput(
        title=(
            "Full-stack observability validation: checkout "
            "database failure"
        ),
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        pod=pod_name,
        node=node_name,
        service="checkout",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=starts_at,
        labels={
            "validation_scenario": "full-stack-observability",
            "app": "checkout",
        },
    )

    engine = InvestigationEngine(
        tools=tools,
        memory=repo,
        settings=settings,
    )
    classic = await engine.investigate(incident)
    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=tools,
        settings=settings,
    )
    agentic = evaluate_shared_evidence(execution)

    classic_root = (
        classic.root_cause.hypothesis_id
        if classic.root_cause is not None
        else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None
        else None
    )
    specialist_status = {
        item.specialist: item.status
        for item in execution.specialist_results
    }
    required_specialists = {
        "kubernetes",
        "metrics",
        "logs",
        "traces",
        "database",
    }
    required_completed = all(
        specialist_status.get(name) == "completed"
        for name in required_specialists
    )
    root_prefix = "postgres-connection-exhaustion:"
    stateful_origin = any(
        item.kind == "postgresql"
        and item.causal_role == "origin"
        and item.health == "critical"
        and bool(item.hypothesis_id)
        and item.hypothesis_id.startswith(root_prefix)
        for item in classic.stateful_dependencies
    )
    trace_repeat_ok = len(classic.trace_analyses) >= 2
    parity_ok = (
        classic.status == agentic.status
        and classic_root == agentic_root
    )
    integrity_ok = (
        all(verify_evidence_hash(item) for item in classic.evidence)
        and execution.shared_evidence.evidence_integrity_ok
    )
    passed = (
        classic.status == "resolved"
        and agentic.status == "resolved"
        and classic_root is not None
        and classic_root.startswith(root_prefix)
        and agentic_root is not None
        and agentic_root.startswith(root_prefix)
        and parity_ok
        and integrity_ok
        and required_completed
        and stateful_origin
        and trace_repeat_ok
    )
    return {
        "incident_started_at": starts_at.isoformat(),
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "trace_count": len(classic.trace_analyses),
            "stateful_dependencies": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "health": item.health,
                    "causal_role": item.causal_role,
                    "hypothesis_id": item.hypothesis_id,
                }
                for item in classic.stateful_dependencies
            ],
            "evidence_categories": sorted(
                {item.category for item in classic.evidence}
            ),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": specialist_status,
            "evidence_categories": sorted(
                {
                    item.category
                    for item in execution.shared_evidence.evidence
                }
            ),
        },
        "classic_agentic_parity_ok": parity_ok,
        "evidence_integrity_ok": integrity_ok,
        "required_specialists_completed": required_completed,
        "stateful_origin_confirmed": stateful_origin,
        "repeated_trace_origin_confirmed": trace_repeat_ok,
        "passed": passed,
    }


async def run_recovery_investigation(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    pod: dict[str, Any],
    starts_at: datetime,
) -> dict[str, Any]:
    recovery_settings = settings.model_copy(
        update={"trace_window_minutes": 0}
    )
    pod_name, node_name = pod_identity(pod)
    incident = IncidentInput(
        title="Full-stack observability validation: recovered checkout",
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        pod=pod_name,
        node=node_name,
        service="checkout",
        alert_name="HighErrorRateRecoveryCheck",
        severity="warning",
        starts_at=starts_at,
        labels={
            "validation_scenario": "full-stack-recovery",
            "app": "checkout",
        },
    )
    engine = InvestigationEngine(
        tools=tools,
        memory=repo,
        settings=recovery_settings,
    )
    classic = await engine.investigate(incident)
    plan = await build_investigation_plan(
        repo,
        incident=incident,
        settings=recovery_settings,
    )
    execution = await execute_investigation_plan(
        plan,
        tools=tools,
        settings=recovery_settings,
    )
    agentic = evaluate_shared_evidence(execution)

    classic_root = (
        classic.root_cause.hypothesis_id
        if classic.root_cause is not None
        else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None
        else None
    )
    parity_ok = (
        classic.status == agentic.status
        and classic_root == agentic_root
    )
    integrity_ok = (
        all(verify_evidence_hash(item) for item in classic.evidence)
        and execution.shared_evidence.evidence_integrity_ok
    )
    stale_stateful_promoted = any(
        item.hypothesis_id
        and item.hypothesis_id.startswith(
            "postgres-connection-exhaustion:"
        )
        and item.causal_role == "origin"
        and item.health == "critical"
        for item in classic.stateful_dependencies
    )
    passed = (
        classic_root is None
        and agentic_root is None
        and parity_ok
        and integrity_ok
        and not stale_stateful_promoted
    )
    return {
        "incident_started_at": starts_at.isoformat(),
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "trace_count": len(classic.trace_analyses),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": {
                item.specialist: item.status
                for item in execution.specialist_results
            },
        },
        "classic_agentic_parity_ok": parity_ok,
        "evidence_integrity_ok": integrity_ok,
        "stale_stateful_promoted": stale_stateful_promoted,
        "passed": passed,
    }


async def validate_observability(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    context: str,
) -> dict[str, Any]:
    pod = wait_until(
        "ready checkout validation Pod",
        lambda: ready_pod_for_label(context, "app=checkout"),
        timeout_seconds=120,
    )
    pod_name, _ = pod_identity(pod)
    cluster_token = tools.set_cluster(LOGICAL_CLUSTER)
    try:
        async def baseline_metric():
            payload = await tools.prometheus_query(
                'validation_requests_total{service="checkout"}',
                utcnow(),
            )
            value = prometheus_value(payload)
            return value if value is not None else None

        baseline = await async_wait_until(
            "initial checkout Prometheus scrape",
            baseline_metric,
        )

        failure_requests = [
            await asyncio.to_thread(trigger_checkout, "db-error")
            for _ in range(3)
        ]

        async def request_metric():
            payload = await tools.prometheus_query(
                'validation_requests_total{service="checkout"}',
                utcnow(),
            )
            value = prometheus_value(payload)
            if value is None or value < baseline + 3:
                return None
            return value

        request_count = await async_wait_until(
            "Prometheus checkout request counter update",
            request_metric,
        )

        async def loki_entries():
            payload = await tools.loki_query_range(
                (
                    f'{{namespace="{NAMESPACE}",'
                    f'pod="{pod_name}"}}'
                ),
                utcnow() - timedelta(minutes=5),
                utcnow() + timedelta(minutes=1),
                100,
            )
            streams = payload.get("data", {}).get("result", [])
            entries = sum(
                len(item.get("values", []))
                for item in streams
                if isinstance(item, dict)
            )
            return {
                "stream_count": len(streams),
                "entry_count": entries,
            } if entries >= 3 else None

        log_probe = await async_wait_until(
            "Loki checkout error logs",
            loki_entries,
        )

        async def tempo_traces():
            traces = await tools.tempo_search_traces(
                "checkout",
                utcnow() - timedelta(minutes=5),
                utcnow() + timedelta(minutes=1),
                8,
            )
            return traces if len(traces) >= 2 else None

        traces = await async_wait_until(
            "Tempo checkout traces",
            tempo_traces,
        )
        fetched_trace = await tools.tempo_get_trace(
            str(traces[0]["traceID"]),
            utcnow() - timedelta(minutes=5),
            utcnow() + timedelta(minutes=1),
        )
        if not fetched_trace:
            raise RuntimeError(
                "Tempo search returned a trace that could not be fetched"
            )

        async def service_graph_edge():
            edges = await tools.service_graph(
                "checkout",
                utcnow(),
            )
            matching = [
                item
                for item in edges
                if item.get("client") == "checkout"
                and item.get("server") == "postgresql/orders"
                and float(item.get("requests") or 0) > 0
                and float(item.get("failures") or 0) > 0
            ]
            return matching[0] if matching else None

        graph_edge = await async_wait_until(
            "Prometheus service-graph RED edge",
            service_graph_edge,
        )

        failure_incident_at = utcnow()
        failure = await run_observability_investigation(
            repo=repo,
            tools=tools,
            settings=settings,
            pod=pod,
            starts_at=failure_incident_at,
        )

        recovery_started_at = utcnow()
        healthy_requests = [
            await asyncio.to_thread(trigger_checkout, "healthy")
            for _ in range(3)
        ]

        async def healthy_connections():
            payload = await tools.prometheus_query(
                (
                    'sum(pg_stat_activity_count'
                    '{instance="orders-db:5432"})'
                ),
                utcnow(),
            )
            value = prometheus_value(payload)
            return value if value is not None and value <= 30 else None

        recovered_connections = await async_wait_until(
            "Prometheus PostgreSQL recovery state",
            healthy_connections,
        )

        async def healthy_traces():
            traces_after_recovery = await tools.tempo_search_traces(
                "checkout",
                recovery_started_at,
                utcnow() + timedelta(minutes=1),
                8,
            )
            if len(traces_after_recovery) < 2:
                return None
            return traces_after_recovery

        recovery_traces = await async_wait_until(
            "Tempo post-recovery traces",
            healthy_traces,
        )
        recovery = await run_recovery_investigation(
            repo=repo,
            tools=tools,
            settings=settings,
            pod=pod,
            starts_at=recovery_started_at,
        )

        direct_passed = (
            request_count >= baseline + 3
            and log_probe["entry_count"] >= 3
            and len(traces) >= 2
            and bool(fetched_trace)
            and float(graph_edge.get("requests") or 0) > 0
            and recovered_connections <= 30
            and len(recovery_traces) >= 2
        )
        return {
            "backends": {
                "prometheus": PROMETHEUS_URL,
                "loki": LOKI_URL,
                "tempo": TEMPO_URL,
            },
            "workload": {
                "pod": pod_name,
                "failure_requests": failure_requests,
                "healthy_requests": healthy_requests,
            },
            "direct_adapter_validation": {
                "baseline_requests": baseline,
                "requests_after_failure": request_count,
                "loki_stream_count": log_probe["stream_count"],
                "loki_entry_count": log_probe["entry_count"],
                "tempo_trace_count": len(traces),
                "tempo_trace_fetch_ok": bool(fetched_trace),
                "service_graph_edge": graph_edge,
                "recovered_postgres_connections": (
                    recovered_connections
                ),
                "recovery_trace_count": len(recovery_traces),
                "passed": direct_passed,
            },
            "failure_investigation": failure,
            "recovery_investigation": recovery,
            "passed": (
                direct_passed
                and failure["passed"]
                and recovery["passed"]
            ),
        }
    finally:
        tools.reset_cluster(cluster_token)


async def validate_backend(
    *,
    context: str,
    settings: Settings,
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    repo = SQLRepository(settings.database_url)
    tools = ReadOnlyHTTPTools(settings)
    await repo.initialize()
    try:
        inventory = await validate_inventory(repo, tools, settings, context)
        scenarios = []
        definitions = [
            (
                "oomkilled",
                "KubePodOOMKilled",
                "resolved",
                "container-oom",
            ),
            (
                "crashloop",
                "KubePodCrashLooping",
                "resolved",
                "container-crash-loop",
            ),
            (
                "failed-scheduling",
                "KubePodFailedScheduling",
                "resolved",
                "insufficient-resources",
            ),
        ]
        for name, alert_name, expected_status, expected_root in definitions:
            observed = observations[name]
            scenarios.append(
                await investigate_scenario(
                    repo=repo,
                    tools=tools,
                    settings=settings,
                    name=name,
                    alert_name=alert_name,
                    pod=observed["pod"],
                    signal=observed["signal"],
                    expected_root=expected_root,
                    expected_status=expected_status,
                    allowed_statuses=(
                        ("resolved", "inconclusive")
                        if name == "crashloop"
                        else None
                    ),
                    allowed_roots=(
                        (None, "container-crash-loop")
                        if name == "crashloop"
                        else None
                    ),
                    forbidden_roots=(
                        ("application-exit",)
                        if name == "crashloop"
                        else ()
                    ),
                )
            )
        observability = await validate_observability(
            repo=repo,
            tools=tools,
            settings=settings,
            context=context,
        )
        return {
            "inventory": inventory,
            "scenarios": scenarios,
            "observability": observability,
            "passed": (
                inventory["passed"]
                and all(item["passed"] for item in scenarios)
                and observability["passed"]
            ),
        }
    finally:
        await repo.close()


def environment_metadata(context: str) -> dict[str, Any]:
    kubernetes = kubectl_json(context, "version")
    return {
        "git_sha": os.getenv("GITHUB_SHA")
        or output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "kind": output(["kind", "version"]),
        "docker_server": output(
            ["docker", "version", "--format", "{{.Server.Version}}"]
        ),
        "kubernetes": kubernetes.get("serverVersion", {}),
        "node_image": os.getenv("KIND_NODE_IMAGE", DEFAULT_NODE_IMAGE),
    }


def write_reports(report_dir: Path, report: dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "validation-report.json"
    json_path.write_text(
        json.dumps(report, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Real Environment Validation Report",
        "",
        f"- Schema: `{report.get('schema_version')}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Overall: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        "## Kubernetes RCA scenarios",
        "",
        "| Scenario | Expected | Classic | Agentic | Integrity | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    validation = report.get("validation") or {}
    for item in validation.get("scenarios", []):
        lines.append(
            "| {name} | {expected} | {classic} | {agentic} | {integrity} | {result} |".format(
                name=item["name"],
                expected=(
                    " / ".join(item["accepted_root_causes"])
                    if len(item["accepted_root_causes"]) > 1
                    else (
                        item["expected_root_cause"]
                        or f"{item['expected_status']} / no root cause"
                    )
                ),
                classic=item["classic"]["root_cause"],
                agentic=item["agentic"]["root_cause"],
                integrity="ok" if item["evidence_integrity_ok"] else "failed",
                result="PASS" if item["passed"] else "FAIL",
            )
        )
    inventory = validation.get("inventory") or {}
    lines.extend(
        [
            "",
            "## Persistent Awareness Graph",
            "",
            f"- Validation namespace entities: {inventory.get('validation_namespace_entities')}",
            f"- Relationship types: {', '.join(inventory.get('relationship_types', []))}",
            f"- Authoritative tombstones created: {inventory.get('entities_tombstoned')}",
            f"- Tombstone source: {inventory.get('victim_tombstone_source')}",
            f"- Result: **{'PASS' if inventory.get('passed') else 'FAIL'}**",
            "",
        ]
    )
    observability = validation.get("observability") or {}
    direct = observability.get("direct_adapter_validation") or {}
    failure = observability.get("failure_investigation") or {}
    recovery = observability.get("recovery_investigation") or {}
    lines.extend(
        [
            "## Full-stack observability",
            "",
            (
                "- Prometheus request counter after failure traffic: "
                f"{direct.get('requests_after_failure')}"
            ),
            (
                "- Loki entries observed: "
                f"{direct.get('loki_entry_count')}"
            ),
            (
                "- Tempo traces observed: "
                f"{direct.get('tempo_trace_count')}"
            ),
            (
                "- Service-graph edge: "
                f"{(direct.get('service_graph_edge') or {}).get('client')} -> "
                f"{(direct.get('service_graph_edge') or {}).get('server')}"
            ),
            (
                "- Deterministic failure RCA: "
                f"{(failure.get('classic') or {}).get('root_cause')}"
            ),
            (
                "- Agentic failure RCA: "
                f"{(failure.get('agentic') or {}).get('root_cause')}"
            ),
            (
                "- Recovery root cause: "
                f"classic={(recovery.get('classic') or {}).get('root_cause')} / "
                f"agentic={(recovery.get('agentic') or {}).get('root_cause')}"
            ),
            (
                "- Full-stack result: "
                f"**{'PASS' if observability.get('passed') else 'FAIL'}**"
            ),
            "",
        ]
    )
    (report_dir / "validation-report.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def cleanup(cluster_name: str) -> None:
    run(
        ["kind", "delete", "cluster", "--name", cluster_name],
        check=False,
        capture=False,
    )
    run(["docker", "rm", "-f", POSTGRES_CONTAINER], check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Provision a disposable Kind cluster, inject real Kubernetes "
            "failures, and validate deterministic plus agentic RCA."
        )
    )
    parser.add_argument(
        "--cluster-name",
        default=DEFAULT_CLUSTER_NAME,
    )
    parser.add_argument(
        "--node-image",
        default=os.getenv("KIND_NODE_IMAGE", DEFAULT_NODE_IMAGE),
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=ROOT / "validation-artifacts",
    )
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    require_commands(["docker", "kind", "kubectl", "git"])
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": utcnow().isoformat(),
        "passed": False,
    }
    context = f"kind-{args.cluster_name}"
    try:
        context = create_cluster(args.cluster_name, args.node_image)
        database_url = start_postgres(POSTGRES_PORT)

        oom = wait_until(
            "OOMKilled container state",
            lambda: wait_for_oom(context),
        )
        crash = wait_until(
            "container restart backoff with non-zero exit",
            lambda: wait_for_crashloop(context),
            timeout_seconds=90,
        )
        failed = wait_until(
            "FailedScheduling caused by insufficient resources",
            lambda: wait_for_failed_scheduling(context),
        )
        observations = {
            "oomkilled": oom,
            "crashloop": crash,
            "failed-scheduling": failed,
        }

        runtime_dir = args.report_dir / "runtime"
        api_server, token_file, ca_file = export_cluster_credentials(
            context,
            runtime_dir,
        )
        settings = validation_settings(
            database_url=database_url,
            api_server=api_server,
            token_file=token_file,
            ca_file=ca_file,
        )
        report["environment"] = environment_metadata(context)
        report["validation"] = asyncio.run(
            validate_backend(
                context=context,
                settings=settings,
                observations=observations,
            )
        )
        report["passed"] = bool(report["validation"]["passed"])
    except Exception as exc:
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        print(
            f"validation failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    finally:
        report["finished_at"] = utcnow().isoformat()
        write_reports(args.report_dir, report)
        if not args.keep:
            cleanup(args.cluster_name)

    print(
        f"real environment validation: {'PASS' if report['passed'] else 'FAIL'}"
    )
    print(f"report: {args.report_dir / 'validation-report.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
