from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import math
import os
import re
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
REPORT_SCHEMA = "ai-sre-rca.validation.v3"
PROMETHEUS_PORT = 19090
LOKI_PORT = 13100
TEMPO_PORT = 13200
WORKLOAD_PORT = int(os.getenv("VALIDATION_WORKLOAD_PORT", "18080"))
WORKLOAD_IMAGE = "ai-sre-rc3-workload:local"
PORT_FORWARDS: list[subprocess.Popen[str]] = []
OWNED_CLUSTER: str | None = None
OWNED_POSTGRES = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def redact(text: str) -> str:
    text = re.sub(r'(?i)(PASSWORD=|Bearer\s+)[^\s]+', r'\1[REDACTED]', text)
    return re.sub(r'(postgres(?:ql)?(?:\+asyncpg)?://[^:\s]+:)[^@\s]+@', r'\1[REDACTED]@', text)


def run(
    command: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", redact(" ".join(command)), flush=True)
    process = subprocess.run(
        command,
        text=True,
        input=input_text,
        capture_output=capture,
        check=False,
    )
    if check and process.returncode != 0:
        if process.stdout:
            print(redact(process.stdout), file=sys.stderr)
        if process.stderr:
            print(redact(process.stderr), file=sys.stderr)
        raise RuntimeError(
            f"command failed with exit code {process.returncode}: "
            + redact(" ".join(command))
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


def delete_existing_cluster(cluster_name: str) -> None:
    clusters = output(["kind", "get", "clusters"]).splitlines()
    if cluster_name in clusters:
        run(["kind", "delete", "cluster", "--name", cluster_name], capture=False)


def create_cluster(cluster_name: str, node_image: str) -> str:
    global OWNED_CLUSTER
    if cluster_name in output(["kind", "get", "clusters"]).splitlines():
        raise RuntimeError("Refusing to replace an existing Kind cluster")
    OWNED_CLUSTER = cluster_name
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
        str(ROOT / "validation" / "manifests" / "workloads.yaml"),
    )
    return context


def wait_http(url: str, description: str, timeout_seconds: int = 120) -> None:
    def probe():
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status if response.status < 400 else None
        except Exception:
            return None

    wait_until(description, probe, timeout_seconds=timeout_seconds, interval_seconds=1)


def start_port_forward(
    context: str,
    service: str,
    local_port: int,
    remote_port: int,
) -> None:
    process = subprocess.Popen(
        [
            "kubectl", "--context", context, "-n", NAMESPACE,
            "port-forward", f"service/{service}",
            f"{local_port}:{remote_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    PORT_FORWARDS.append(process)


def stop_port_forwards() -> None:
    while PORT_FORWARDS:
        process = PORT_FORWARDS.pop()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


def deploy_observability(context: str, cluster_name: str) -> None:
    build_command = [
        "docker", "build",
        "-t", WORKLOAD_IMAGE,
        str(ROOT / "validation" / "workload"),
    ]
    for attempt in range(1, 4):
        build = run(build_command, check=False, capture=False)
        if build.returncode == 0:
            break
        if attempt == 3:
            raise RuntimeError(
                "validation workload image build failed after 3 attempts"
            )
        time.sleep(attempt * 2)
    run(
        ["kind", "load", "docker-image", WORKLOAD_IMAGE, "--name", cluster_name],
        capture=False,
    )
    kubectl(
        context,
        "apply",
        "-f",
        str(ROOT / "validation" / "manifests" / "observability.yaml"),
    )
    kubectl(context, "apply", "-f", str(ROOT / "validation" / "manifests" / "redis.yaml"))
    for deployment in (
        "orders-db",
        "postgres-exporter",
        "postgres-connection-loader",
        "cache",
        "redis-exporter",
        "prometheus",
        "loki",
        "tempo",
        "rc3-workload",
    ):
        kubectl(
            context,
            "rollout",
            "status",
            f"deployment/{deployment}",
            "-n",
            NAMESPACE,
            "--timeout=180s",
        )

    start_observability_forwards(context)


def start_observability_forwards(context: str) -> None:
    start_port_forward(context, "prometheus", PROMETHEUS_PORT, 9090)
    start_port_forward(context, "loki", LOKI_PORT, 3100)
    start_port_forward(context, "tempo", TEMPO_PORT, 3200)
    start_port_forward(context, "rc3-workload", WORKLOAD_PORT, 8080)
    wait_http(f"http://127.0.0.1:{PROMETHEUS_PORT}/-/ready", "Prometheus readiness")
    wait_http(f"http://127.0.0.1:{LOKI_PORT}/ready", "Loki readiness")
    wait_http(f"http://127.0.0.1:{TEMPO_PORT}/ready", "Tempo readiness")
    wait_http(f"http://127.0.0.1:{WORKLOAD_PORT}/health", "validation workload readiness")
    time.sleep(3)


def generate_observability_failure(context: str, *, redis: bool = False) -> dict[str, Any]:
    pods = pods_for_label(context, "app=rc3-workload")
    if len(pods) != 1:
        raise RuntimeError(
            f"expected exactly one rc3 workload pod, found {len(pods)}"
        )
    pod_name, node_name = pod_identity(pods[0])
    started_at = utcnow()
    responses: list[str] = []
    for _ in range(3):
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{WORKLOAD_PORT}" + ("/redis/fail" if redis else "/fail"),
                timeout=10,
            )
            raise RuntimeError("validation workload unexpectedly returned success")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            if exc.code != 500:
                raise RuntimeError(
                    f"validation workload returned {exc.code}: {body}"
                )
            responses.append(body.strip())
        time.sleep(0.3)
    time.sleep(5)
    return {
        "started_at": started_at,
        "service": "cache-frontend" if redis else "frontend",
        "requests": len(responses),
        "responses": responses,
        "pod": pod_name,
        "node": node_name,
    }


def generate_observability_recovery(context: str, *, redis: bool = False) -> dict[str, Any]:
    pods = pods_for_label(context, "app=rc3-workload")
    if len(pods) != 1:
        raise RuntimeError(
            f"expected exactly one rc3 workload pod, found {len(pods)}"
        )
    pod_name, node_name = pod_identity(pods[0])
    started_at = utcnow()

    with urllib.request.urlopen(
        f"http://127.0.0.1:{WORKLOAD_PORT}" + ("/redis/recover" if redis else "/recover"),
        timeout=10,
    ) as response:
        if response.status != 200:
            raise RuntimeError(
                f"validation recovery transition returned {response.status}"
            )

    responses: list[str] = []
    for _ in range(3):
        with urllib.request.urlopen(
            f"http://127.0.0.1:{WORKLOAD_PORT}" + ("/redis/ok" if redis else "/ok"),
            timeout=10,
        ) as response:
            body = response.read().decode(errors="replace")
            if response.status != 200:
                raise RuntimeError(
                    f"validation healthy request returned {response.status}: {body}"
                )
            responses.append(body.strip())
        time.sleep(0.3)

    time.sleep(5)
    return {
        "started_at": started_at,
        "service": "cache-frontend" if redis else "frontend",
        "requests": len(responses),
        "responses": responses,
        "pod": pod_name,
        "node": node_name,
    }


def release_postgres_connections(context: str) -> None:
    kubectl(
        context,
        "scale",
        "deployment/postgres-connection-loader",
        "-n",
        NAMESPACE,
        "--replicas=0",
    )
    wait = run(
        [
            "kubectl",
            "--context",
            context,
            "wait",
            "--for=delete",
            "pod",
            "-l",
            "app=postgres-connection-loader",
            "-n",
            NAMESPACE,
            "--timeout=60s",
        ],
        check=False,
    )
    if wait.returncode != 0:
        remaining = pods_for_label(
            context,
            "app=postgres-connection-loader",
        )
        if remaining:
            raise RuntimeError(
                "PostgreSQL connection-loader Pod did not terminate during recovery"
            )


def start_postgres(port: int) -> str:
    global OWNED_POSTGRES
    if run(["docker", "container", "inspect", POSTGRES_CONTAINER], check=False).returncode == 0:
        raise RuntimeError("Refusing to replace an existing PostgreSQL container")
    OWNED_POSTGRES = True
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
    full_stack_observability: bool = True,
) -> Settings:
    registry = {
        LOGICAL_CLUSTER: {
            "kubernetes_api_url": api_server,
            "kubernetes_token_file": str(token_file),
            "kubernetes_ca_file": str(ca_file),
            "prometheus_url": (
                f"http://127.0.0.1:{PROMETHEUS_PORT}"
                if full_stack_observability else "http://127.0.0.1:9"
            ),
            "loki_url": (
                f"http://127.0.0.1:{LOKI_PORT}"
                if full_stack_observability else "http://127.0.0.1:9"
            ),
            "tempo_url": (
                f"http://127.0.0.1:{TEMPO_PORT}"
                if full_stack_observability else "http://127.0.0.1:9"
            ),
            "argocd_url": "http://127.0.0.1:9",
        }
    }
    return Settings(
        demo_mode=False,
        database_url=database_url,
        query_budget_enabled=False,
        notifications_enabled=False,
        llm_enabled=False,
        trace_analysis_enabled=full_stack_observability,
        stateful_dependency_analysis_enabled=False,
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


def _prometheus_value(payload: dict[str, Any]) -> float | None:
    try:
        return float(payload["data"]["result"][0]["value"][1])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _loki_entry_count(payload: dict[str, Any]) -> int:
    return sum(
        len(stream.get("values") or [])
        for stream in payload.get("data", {}).get("result", [])
        if isinstance(stream, dict)
    )


def _stateful_signal_value(payload: dict[str, Any], name: str) -> float | None:
    try:
        value = payload["signals"][name]["value"]
        scalar = float(value) if value is not None else None
        return scalar if scalar is not None and math.isfinite(scalar) else None
    except (KeyError, TypeError, ValueError):
        return None


async def wait_for_postgres_utilization(
    tools: ReadOnlyHTTPTools,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    token = tools.set_cluster(LOGICAL_CLUSTER)
    try:
        while time.monotonic() < deadline:
            metrics = await tools.postgres_dependency_metrics(
                "orders",
                utcnow(),
                1,
                "orders-db:5432",
            )
            connections = _stateful_signal_value(metrics, "connections")
            max_connections = _stateful_signal_value(metrics, "max_connections")
            utilization = (
                connections / max_connections
                if connections is not None
                and max_connections not in {None, 0}
                else None
            )
            last = {
                "connections": connections,
                "max_connections": max_connections,
                "utilization": utilization,
                "metrics": metrics,
            }
            above_ok = minimum is None or (
                utilization is not None and utilization >= minimum
            )
            below_ok = maximum is None or (
                utilization is not None and utilization <= maximum
            )
            if above_ok and below_ok:
                return last
            await asyncio.sleep(2)
    finally:
        tools.reset_cluster(token)

    raise RuntimeError(
        "PostgreSQL exporter utilization did not reach the required state: "
        f"minimum={minimum}, maximum={maximum}, last={last}"
    )


def redis_memory_control(*, recover: bool) -> dict[str, Any]:
    path = "/redis/recover" if recover else "/redis/load"
    with urllib.request.urlopen(f"http://127.0.0.1:{WORKLOAD_PORT}{path}", timeout=45) as response:
        if response.status != 200:
            raise RuntimeError("Redis validation control failed")
        return json.load(response)


async def wait_for_redis_utilization(
    tools: ReadOnlyHTTPTools,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    token = tools.set_cluster(LOGICAL_CLUSTER)
    try:
        while time.monotonic() < deadline:
            metrics = await tools.redis_dependency_metrics("cache:6379", utcnow(), 1)
            used = _stateful_signal_value(metrics, "memory_used_bytes")
            cap = _stateful_signal_value(metrics, "memory_max_bytes")
            if cap in {None, 0}:
                cap = _stateful_signal_value(metrics, "config_maxmemory_bytes")
            utilization = used / cap if used is not None and used >= 0 and cap is not None and cap > 0 else None
            last = {"instance": "cache:6379", "memory_used_bytes": used, "memory_max_bytes": cap,
                    "utilization": utilization, "metrics": metrics}
            if utilization is not None and (minimum is None or utilization >= minimum) and (maximum is None or utilization <= maximum):
                return last
            await asyncio.sleep(2)
    finally:
        tools.reset_cluster(token)
    raise RuntimeError(f"Redis exporter utilization did not reach the required state: minimum={minimum}, maximum={maximum}, last={last}")


async def validate_recovery_observability(
    tools: ReadOnlyHTTPTools,
    signal: dict[str, Any],
) -> dict[str, Any]:
    started_at = signal["started_at"]
    service = signal.get("service", "frontend")
    deadline = time.monotonic() + 45
    last: dict[str, Any] = {}
    token = tools.set_cluster(LOGICAL_CLUSTER)
    try:
        while time.monotonic() < deadline:
            now = utcnow()
            metric_payload, log_payload, traces = await asyncio.gather(
                tools.prometheus_query(
                    f'sum(ai_sre_validation_requests_total{{service="{service}",status="200"}})',
                    now,
                ),
                tools.loki_query_range(
                    f'{{namespace="rca-validation",app="{service}",level="info"}}',
                    started_at,
                    now + timedelta(minutes=1),
                    100,
                ),
                tools.tempo_search_traces(
                    service,
                    started_at,
                    now + timedelta(minutes=1),
                    8,
                ),
            )
            last = {
                "prometheus_success_requests": _prometheus_value(metric_payload),
                "loki_recovery_entries": _loki_entry_count(log_payload),
                "tempo_recovery_trace_count": len(traces),
                "tempo_recovery_trace_ids": [
                    str(item.get("traceID"))
                    for item in traces
                    if item.get("traceID")
                ],
            }
            if (
                (last["prometheus_success_requests"] or 0) >= 3
                and last["loki_recovery_entries"] >= 3
                and last["tempo_recovery_trace_count"] >= 2
            ):
                break
            await asyncio.sleep(2)
    finally:
        tools.reset_cluster(token)

    last["passed"] = (
        (last.get("prometheus_success_requests") or 0) >= 3
        and last.get("loki_recovery_entries", 0) >= 3
        and last.get("tempo_recovery_trace_count", 0) >= 2
    )
    return last


async def validate_observability(
    tools: ReadOnlyHTTPTools,
    signal: dict[str, Any],
) -> dict[str, Any]:
    started_at = signal["started_at"]
    service = signal.get("service", "frontend")
    deadline = time.monotonic() + 45
    last: dict[str, Any] = {}
    token = tools.set_cluster(LOGICAL_CLUSTER)
    try:
        while time.monotonic() < deadline:
            now = utcnow()
            metric_payload, log_payload, traces = await asyncio.gather(
                tools.prometheus_query(
                    f'sum(ai_sre_validation_requests_total{{service="{service}",status="500"}})',
                    now,
                ),
                tools.loki_query_range(
                    f'{{namespace="rca-validation",app="{service}"}}',
                    started_at - timedelta(minutes=1),
                    now + timedelta(minutes=1),
                    100,
                ),
                tools.tempo_search_traces(
                    service,
                    started_at - timedelta(minutes=1),
                    now + timedelta(minutes=1),
                    8,
                ),
            )
            try:
                graph = await tools.service_graph(service, now)
            except Exception:
                graph = []
            last = {
                "prometheus_failure_requests": _prometheus_value(metric_payload),
                "loki_entries": _loki_entry_count(log_payload),
                "tempo_trace_count": len(traces),
                "tempo_trace_ids": [
                    str(item.get("traceID"))
                    for item in traces
                    if item.get("traceID")
                ],
                "service_graph_edges": len(graph),
                "service_graph": graph,
            }
            if (
                (last["prometheus_failure_requests"] or 0) >= 3
                and last["loki_entries"] >= 3
                and last["tempo_trace_count"] >= 2
                and last["service_graph_edges"] >= 1
            ):
                break
            await asyncio.sleep(2)
    finally:
        tools.reset_cluster(token)

    capabilities = await tools.capability_report(LOGICAL_CLUSTER)
    capability_status = {
        item["source"]: item["status"]
        for item in capabilities
        if item["source"] in {"prometheus", "loki", "tempo"}
    }
    last["capabilities"] = capability_status
    last["passed"] = (
        (last.get("prometheus_failure_requests") or 0) >= 3
        and last.get("loki_entries", 0) >= 3
        and last.get("tempo_trace_count", 0) >= 2
        and last.get("service_graph_edges", 0) >= 1
        and capability_status == {
            "prometheus": "healthy",
            "loki": "healthy",
            "tempo": "healthy",
        }
    )
    return last


async def investigate_trace_scenario(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    signal: dict[str, Any],
) -> dict[str, Any]:
    incident = IncidentInput(
        title="Full-stack validation: downstream database failure",
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        service="frontend",
        alert_name="HighErrorRate",
        severity="critical",
        starts_at=signal["started_at"] + timedelta(seconds=1),
        labels={"validation_scenario": "full-stack-trace"},
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
        if classic.root_cause is not None else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None else None
    )
    integrity_ok = all(
        verify_evidence_hash(item)
        for item in classic.evidence
    ) and execution.shared_evidence.evidence_integrity_ok
    specialists = {
        item.specialist: item.status
        for item in execution.specialist_results
    }
    trace_evidence = [
        item for item in classic.evidence
        if item.source == "tempo" and item.category == "trace-causal-chain"
    ]
    passed = (
        classic.status == "resolved"
        and agentic.status == "resolved"
        and classic_root == "trace-causal-propagation"
        and agentic_root == "trace-causal-propagation"
        and len(classic.trace_analyses) >= 2
        and len(trace_evidence) >= 2
        and specialists.get("metrics") == "completed"
        and specialists.get("logs") == "completed"
        and specialists.get("traces") == "completed"
        and any(
            item.source == "loki"
            and item.category == "agentic-log-window"
            and int(item.facts.get("entry_count") or 0) >= 3
            for item in execution.shared_evidence.evidence
        )
        and integrity_ok
    )
    return {
        "name": "full-stack-trace",
        "expected_root_cause": "trace-causal-propagation",
        "classic_agentic_parity_ok": (
            classic.status == agentic.status
            and classic_root == agentic_root
        ),
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "trace_count": len(classic.trace_analyses),
            "trace_evidence_count": len(trace_evidence),
            "evidence_count": len(classic.evidence),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": specialists,
            "evidence_count": len(execution.shared_evidence.evidence),
        },
        "evidence_integrity_ok": integrity_ok,
        "passed": passed,
    }


async def investigate_stateful_scenario(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    signal: dict[str, Any],
    dependency_metrics: dict[str, Any],
    kind: str = "postgresql",
) -> dict[str, Any]:
    specs = {
        "postgresql": ("PostgreSQL connection exhaustion", "PostgreSQLHighConnections", "postgres-connection-exhaustion:", settings.postgres_connection_utilization_critical, "postgres_exporter", ("connections", "max_connections")),
        "redis": ("Redis memory pressure", "RedisHighMemory", "redis-memory-pressure:", settings.redis_memory_utilization_critical, "redis_exporter", ("memory_used_bytes", "memory_max_bytes")),
    }
    title, alert, prefix, threshold, exporter, metric_names = specs[kind]
    expected_dependency = "dependency:redis/0@cache:6379" if kind == "redis" else "dependency:postgresql/orders@orders-db:5432"
    expected_root = prefix + expected_dependency
    incident = IncidentInput(
        title=f"Stateful validation: {title}",
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        service=signal.get("service", "frontend"),
        alert_name=alert,
        severity="critical",
        starts_at=signal["started_at"],
        labels={"validation_scenario": f"stateful-{kind}"},
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
        if classic.root_cause is not None else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None else None
    )
    dependency = next(
        (
            item
            for item in classic.stateful_dependencies
            if item.kind == kind
        ),
        None,
    )
    specialists = {
        item.specialist: item.status
        for item in execution.specialist_results
    }
    integrity_ok = all(
        verify_evidence_hash(item)
        for item in classic.evidence
    ) and execution.shared_evidence.evidence_integrity_ok
    parity_ok = (
        classic.status == agentic.status
        and classic_root == agentic_root
    )
    passed = (
        classic.status == "resolved"
        and agentic.status == "resolved"
        and classic_root == expected_root
        and agentic_root == expected_root
        and parity_ok
        and dependency is not None
        and dependency.dependency_id == expected_dependency
        and dependency.health == "critical"
        and dependency.causal_role == "origin"
        and bool(
            dependency.hypothesis_id
            and dependency.hypothesis_id.startswith(prefix)
        )
        and specialists.get("traces") == "completed"
        and specialists.get("database") == "completed"
        and integrity_ok
        and (dependency_metrics.get("utilization") or 0)
        >= threshold
    )
    return {
        "name": f"stateful-{kind}",
        "expected_root_prefix": prefix,
        "expected_root_cause": expected_root,
        exporter: {
            **{name: dependency_metrics.get(name) for name in metric_names},
            "instance": dependency_metrics.get("instance", "orders-db:5432"),
            "utilization": dependency_metrics.get("utilization"),
            "critical_threshold": threshold,
        },
        "classic_agentic_parity_ok": parity_ok,
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "stateful_dependencies": len(classic.stateful_dependencies),
            "evidence_count": len(classic.evidence),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": specialists,
            "evidence_count": len(execution.shared_evidence.evidence),
        },
        kind: (
            {
                "dependency_id": dependency.dependency_id,
                "health": dependency.health,
                "causal_role": dependency.causal_role,
                "hypothesis_id": dependency.hypothesis_id,
                "signal_count": len(dependency.signals),
            }
            if dependency is not None else None
        ),
        "evidence_integrity_ok": integrity_ok,
        "passed": passed,
    }


def recovery_dependencies_are_safe(items: list[Any]) -> bool:
    return all(
        getattr(item, "health", None) not in {"critical", "degraded"}
        and getattr(item, "hypothesis_id", None) is None
        for item in items
    )


async def investigate_recovery_scenario(
    *,
    repo: SQLRepository,
    tools: ReadOnlyHTTPTools,
    settings: Settings,
    signal: dict[str, Any],
    recovery_observability: dict[str, Any],
    dependency_metrics: dict[str, Any],
    redis: bool = False,
) -> dict[str, Any]:
    incident = IncidentInput(
        title="Recovery validation: healthy checkout after " + ("Redis recovery" if redis else "PostgreSQL recovery"),
        tenant_id="validation",
        cluster=LOGICAL_CLUSTER,
        namespace=NAMESPACE,
        service=signal.get("service", "frontend"),
        alert_name="RecoveryVerification",
        severity="warning",
        starts_at=signal["started_at"],
        labels={"validation_scenario": "stateful-recovery"},
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
        if classic.root_cause is not None else None
    )
    agentic_root = (
        agentic.root_cause.hypothesis_id
        if agentic.root_cause is not None else None
    )
    forbidden_prefixes = ("postgres-", "kafka-", "redis-")

    def root_is_safe(root: str | None) -> bool:
        if root is None:
            return True
        if root == "trace-causal-propagation":
            return False
        return not any(root.startswith(prefix) for prefix in forbidden_prefixes)

    trace_evidence = [
        item
        for item in classic.evidence
        if item.source == "tempo"
        and item.category == "trace-causal-chain"
    ]
    specialists = {
        item.specialist: item.status
        for item in execution.specialist_results
    }
    integrity_ok = all(
        verify_evidence_hash(item)
        for item in classic.evidence
    ) and execution.shared_evidence.evidence_integrity_ok
    parity_ok = (
        classic.status == agentic.status
        and classic_root == agentic_root
    )
    stateful_safe = recovery_dependencies_are_safe(
        classic.stateful_dependencies
    )
    utilization = dependency_metrics.get("utilization")
    passed = (
        signal.get("requests") == 3
        and recovery_observability.get("passed") is True
        and utilization is not None
        and utilization <= 0.30
        and root_is_safe(classic_root)
        and root_is_safe(agentic_root)
        and stateful_safe
        and specialists.get("metrics") == "completed"
        and specialists.get("logs") == "completed"
        and specialists.get("traces") == "completed"
        and specialists.get("database") == "completed"
        and parity_ok
        and integrity_ok
    )
    return {
        "name": "redis-recovery" if redis else "stateful-recovery",
        ("recovered_redis" if redis else "recovered_postgres"): {
            **{name: dependency_metrics.get(name) for name in (
                ("memory_used_bytes", "memory_max_bytes") if redis else ("connections", "max_connections")
            )},
            "utilization": utilization,
        },
        "fresh_telemetry": recovery_observability,
        "classic_agentic_parity_ok": parity_ok,
        "classic": {
            "status": classic.status,
            "root_cause": classic_root,
            "trace_causal_evidence_count": len(trace_evidence),
            "stateful_dependencies": [
                {
                    "kind": item.kind,
                    "health": item.health,
                    "causal_role": item.causal_role,
                    "hypothesis_id": item.hypothesis_id,
                }
                for item in classic.stateful_dependencies
            ],
            "evidence_count": len(classic.evidence),
        },
        "agentic": {
            "status": agentic.status,
            "root_cause": agentic_root,
            "execution_status": execution.status,
            "specialists": specialists,
            "evidence_count": len(execution.shared_evidence.evidence),
        },
        "forbidden_root": "trace-causal-propagation or stateful anomaly root",
        "stateful_anomaly_suppressed": stateful_safe,
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


async def validate_backend(
    *,
    context: str,
    settings: Settings,
    observations: dict[str, dict[str, Any]],
    observability_signal: dict[str, Any],
) -> dict[str, Any]:
    repo = SQLRepository(settings.database_url)
    tools = ReadOnlyHTTPTools(settings)
    await repo.initialize()
    try:
        inventory = await validate_inventory(repo, tools, settings, context)
        observability = await validate_observability(
            tools,
            observability_signal,
        )
        distributed_trace = await investigate_trace_scenario(
            repo=repo,
            tools=tools,
            settings=settings,
            signal=observability_signal,
        )

        stateful_settings = settings.model_copy(
            update={
                "stateful_dependency_analysis_enabled": True,
                "postgres_connection_utilization_critical": 0.70,
            }
        )
        saturated_postgres = await wait_for_postgres_utilization(
            tools,
            minimum=stateful_settings.postgres_connection_utilization_critical,
        )
        stateful_postgresql = await investigate_stateful_scenario(
            repo=repo,
            tools=tools,
            settings=stateful_settings,
            signal=observability_signal,
            dependency_metrics=saturated_postgres,
        )

        release_postgres_connections(context)
        recovered_postgres = await wait_for_postgres_utilization(
            tools,
            maximum=0.30,
        )
        recovery_signal = generate_observability_recovery(context)
        recovery_observability = await validate_recovery_observability(
            tools,
            recovery_signal,
        )
        recovery_settings = stateful_settings.model_copy(
            update={
                "trace_window_minutes": 0,
                "service_graph_window_minutes": 1,
            }
        )
        recovery = await investigate_recovery_scenario(
            repo=repo,
            tools=tools,
            settings=recovery_settings,
            signal=recovery_signal,
            recovery_observability=recovery_observability,
            dependency_metrics=recovered_postgres,
        )

        redis_load = redis_memory_control(recover=False)
        saturated_redis = await wait_for_redis_utilization(
            tools, minimum=stateful_settings.redis_memory_utilization_critical,
        )
        redis_signal = generate_observability_failure(context, redis=True)
        redis_observability = await validate_observability(tools, redis_signal)
        stateful_redis = await investigate_stateful_scenario(
            repo=repo, tools=tools, settings=stateful_settings,
            signal=redis_signal, dependency_metrics=saturated_redis, kind="redis",
        )
        stateful_redis["load"] = redis_load
        stateful_redis["telemetry"] = redis_observability
        stateful_redis["passed"] = stateful_redis["passed"] and redis_observability["passed"]

        redis_memory_control(recover=True)
        recovered_redis = await wait_for_redis_utilization(tools, maximum=0.30)
        redis_recovery_signal = generate_observability_recovery(context, redis=True)
        redis_recovery_telemetry = await validate_recovery_observability(tools, redis_recovery_signal)
        redis_recovery = await investigate_recovery_scenario(
            repo=repo, tools=tools, settings=recovery_settings,
            signal=redis_recovery_signal,
            recovery_observability=redis_recovery_telemetry,
            dependency_metrics=recovered_redis, redis=True,
        )

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
        return {
            "inventory": inventory,
            "observability": observability,
            "distributed_trace": distributed_trace,
            "stateful_postgresql": stateful_postgresql,
            "recovery": recovery,
            "stateful_redis": stateful_redis,
            "redis_recovery": redis_recovery,
            "scenarios": scenarios,
            "passed": (
                inventory["passed"]
                and observability["passed"]
                and distributed_trace["passed"]
                and stateful_postgresql["passed"]
                and recovery["passed"]
                and stateful_redis["passed"]
                and redis_recovery["passed"]
                and all(item["passed"] for item in scenarios)
            ),
        }
    finally:
        await repo.close()


def environment_metadata(context: str) -> dict[str, Any]:
    kubernetes = kubectl_json(context, "version")
    return {
        "git_sha": os.getenv("VALIDATION_COMMIT") or os.getenv("GITHUB_SHA")
        or output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "kind": output(["kind", "version"]),
        "docker_server": output(
            ["docker", "version", "--format", "{{.Server.Version}}"]
        ),
        "kubernetes": kubernetes.get("serverVersion", {}),
        "node_image": os.getenv("KIND_NODE_IMAGE", DEFAULT_NODE_IMAGE),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('validation/run.py','validation/copilot.py','validation/workload/app.py',
                'backend/app/agentic/specialists.py','backend/app/agentic/workload_kubernetes.py',
                'backend/app/core/k8s_signals.py','backend/app/core/kubernetes_investigator.py',
                'backend/app/core/trace_analysis.py','backend/app/core/trace_ids.py','backend/app/integrations/http_tools.py')
        },
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
    if 'benchmark_v2' in validation:
        benchmark = validation['benchmark_v2']
        summary = {key:benchmark.get(key) for key in ('passed','metrics','failure','untested_cases')}
        lines += ['', '## Independent RCA Benchmark v2', '',
            '[Per-case report](benchmark-v2/benchmark-v2.md)', '',
            '```json', json.dumps(summary, indent=2, default=str), '```', '']
    if 'copilot' in validation:
        copilot = validation['copilot']
        summary = {key:copilot.get(key) for key in ('passed','rca_accuracy','untested_scenarios','source_health','failure','missing_telemetry','changed_membership_confirmation','ambiguous_identity')}
        summary['scenarios'] = [{key:item.get(key) for key in ('name','expected','reported','passed','seconds')} for item in copilot.get('scenarios',[])]
        lines += ["", "## Copilot live telemetry", "", "```json", json.dumps(summary, indent=2, default=str), "```", ""]
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
    observability = validation.get("observability") or {}
    distributed = validation.get("distributed_trace") or {}
    lines.extend(
        [
            "",
            "## Full-stack observability",
            "",
            f"- Prometheus failed requests: {observability.get('prometheus_failure_requests')}",
            f"- Loki correlated entries: {observability.get('loki_entries')}",
            f"- Tempo traces: {observability.get('tempo_trace_count')}",
            f"- Service graph edges: {observability.get('service_graph_edges')}",
            f"- Source health: {observability.get('capabilities')}",
            f"- Telemetry result: **{'PASS' if observability.get('passed') else 'FAIL'}**",
            "",
            "## Distributed trace RCA",
            "",
            f"- Expected root cause: {distributed.get('expected_root_cause')}",
            f"- Classic root cause: {(distributed.get('classic') or {}).get('root_cause')}",
            f"- Agentic root cause: {(distributed.get('agentic') or {}).get('root_cause')}",
            f"- Classic trace count: {(distributed.get('classic') or {}).get('trace_count')}",
            f"- Evidence integrity: {'ok' if distributed.get('evidence_integrity_ok') else 'failed'}",
            f"- Result: **{'PASS' if distributed.get('passed') else 'FAIL'}**",
            "",
        ]
    )
    stateful = validation.get("stateful_postgresql") or {}
    stateful_classic = stateful.get("classic") or {}
    stateful_agentic = stateful.get("agentic") or {}
    stateful_exporter = stateful.get("postgres_exporter") or {}
    recovery = validation.get("recovery") or {}
    recovery_classic = recovery.get("classic") or {}
    recovery_agentic = recovery.get("agentic") or {}
    recovery_postgres = recovery.get("recovered_postgres") or {}
    recovery_fresh = recovery.get("fresh_telemetry") or {}
    lines.extend(
        [
            "",
            "## Stateful PostgreSQL RCA",
            "",
            f"- Exporter connections: {stateful_exporter.get('connections')}",
            f"- Exporter max_connections: {stateful_exporter.get('max_connections')}",
            f"- Exporter utilization: {stateful_exporter.get('utilization')}",
            f"- Critical threshold: {stateful_exporter.get('critical_threshold')}",
            f"- Classic root cause: {stateful_classic.get('root_cause')}",
            f"- Agentic root cause: {stateful_agentic.get('root_cause')}",
            f"- Evidence integrity: {'ok' if stateful.get('evidence_integrity_ok') else 'failed'}",
            f"- Result: **{'PASS' if stateful.get('passed') else 'FAIL'}**",
            "",
            "## Recovery validation",
            "",
            f"- Recovered PostgreSQL utilization: {recovery_postgres.get('utilization')}",
            f"- Healthy Prometheus requests: {recovery_fresh.get('prometheus_success_requests')}",
            f"- Recovery Loki entries: {recovery_fresh.get('loki_recovery_entries')}",
            f"- Recovery Tempo traces: {recovery_fresh.get('tempo_recovery_trace_count')}",
            f"- Classic root after recovery: {recovery_classic.get('root_cause')}",
            f"- Agentic root after recovery: {recovery_agentic.get('root_cause')}",
            f"- Fresh trace causal evidence count: {recovery_classic.get('trace_causal_evidence_count')}",
            f"- Evidence integrity: {'ok' if recovery.get('evidence_integrity_ok') else 'failed'}",
            f"- Result: **{'PASS' if recovery.get('passed') else 'FAIL'}**",
            "",
        ]
    )
    redis = validation.get("stateful_redis") or {}
    redis_exporter = redis.get("redis_exporter") or {}
    redis_recovery = validation.get("redis_recovery") or {}
    recovered_redis = redis_recovery.get("recovered_redis") or {}
    redis_fresh = redis_recovery.get("fresh_telemetry") or {}
    lines.extend([
        "", "## Stateful Redis RCA", "",
        f"- Exporter instance: {redis_exporter.get('instance')}",
        f"- Exporter used memory bytes: {redis_exporter.get('memory_used_bytes')}",
        f"- Exporter maxmemory bytes: {redis_exporter.get('memory_max_bytes')}",
        f"- Exporter memory utilization: {redis_exporter.get('utilization')}",
        f"- Classic root cause: {(redis.get('classic') or {}).get('root_cause')}",
        f"- Agentic root cause: {(redis.get('agentic') or {}).get('root_cause')}",
        f"- Classic/agentic parity: {redis.get('classic_agentic_parity_ok')}",
        f"- Evidence integrity: {'ok' if redis.get('evidence_integrity_ok') else 'failed'}",
        f"- Result: **{'PASS' if redis.get('passed') else 'FAIL'}**",
        "", "## Redis recovery validation", "",
        f"- Recovered Redis memory utilization: {recovered_redis.get('utilization')}",
        f"- Fresh Loki entries: {redis_fresh.get('loki_recovery_entries')}",
        f"- Fresh Tempo traces: {redis_fresh.get('tempo_recovery_trace_count')}",
        f"- Classic root after recovery: {(redis_recovery.get('classic') or {}).get('root_cause')}",
        f"- Agentic root after recovery: {(redis_recovery.get('agentic') or {}).get('root_cause')}",
        f"- Stateful anomaly suppressed: {redis_recovery.get('stateful_anomaly_suppressed')}",
        f"- Evidence integrity: {'ok' if redis_recovery.get('evidence_integrity_ok') else 'failed'}",
        f"- Result: **{'PASS' if redis_recovery.get('passed') else 'FAIL'}**",
        "",
    ])
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
    (report_dir / "validation-report.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def cleanup(cluster_name: str) -> None:
    if OWNED_CLUSTER == cluster_name:
        run(["kind", "delete", "cluster", "--name", cluster_name], check=False, capture=False)
    if OWNED_POSTGRES:
        run(["docker", "rm", "-f", POSTGRES_CONTAINER], check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Provision a disposable Kind cluster with Prometheus, Loki and "
            "Tempo and real stateful exporters, inject failure/recovery cycles, "
            "and validate deterministic plus agentic RCA across Kubernetes, "
            "distributed telemetry and PostgreSQL."
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
    parser.add_argument("--benchmark-v2", choices=("smoke", "full"), help="Independent comparative benchmark; full also retains six live regressions")
    parser.add_argument("--copilot", action="store_true", help="Validate Copilot against live workload ownership and telemetry")
    parser.add_argument("--keep", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.benchmark_v2:
        args.copilot = True
    global POSTGRES_CONTAINER
    POSTGRES_CONTAINER = args.cluster_name + "-postgres"
    require_commands(["docker", "kind", "kubectl"] + ([] if os.getenv("VALIDATION_COMMIT") else ["git"]))
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": utcnow().isoformat(),
        "passed": False,
    }
    context = f"kind-{args.cluster_name}"
    try:
        context = create_cluster(args.cluster_name, args.node_image)
        database_url = start_postgres(POSTGRES_PORT)

        observations = {}
        if not args.copilot:
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
        deploy_observability(context, args.cluster_name)
        observability_signal = None if args.copilot else generate_observability_failure(context)

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
        if args.benchmark_v2:
            from validation.benchmark_live_v2 import validate_benchmark
            report["validation"] = asyncio.run(validate_benchmark(context, settings, args.report_dir, args.benchmark_v2))
        elif args.copilot:
            from validation.copilot import validate_copilot
            report["validation"] = asyncio.run(validate_copilot(context, settings, args.report_dir))
        else:
            report["validation"] = asyncio.run(
                validate_backend(
                context=context,
                settings=settings,
                observations=observations,
                observability_signal=observability_signal,
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
        if not report['passed'] and OWNED_CLUSTER == args.cluster_name:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            for label, command in [('pods', ['get','pods','-n',NAMESPACE,'-o','wide']), ('events', ['get','events','-n',NAMESPACE]), ('workload', ['logs','-n',NAMESPACE,'deployment/rc3-workload','--tail=100'])]:
                captured = run(['kubectl','--context',context,*command],check=False)
                (args.report_dir / f'failure-{label}.log').write_text(redact(captured.stdout + captured.stderr))
        write_reports(args.report_dir, report)
        stop_port_forwards()
        if not args.keep:
            cleanup(args.cluster_name)
            for filename in ('kubernetes.token', 'kubernetes.ca.crt'):
                (args.report_dir / 'runtime' / filename).unlink(missing_ok=True)

    print(
        f"real environment validation: {'PASS' if report['passed'] else 'FAIL'}"
    )
    print(f"report: {args.report_dir / 'validation-report.md'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
