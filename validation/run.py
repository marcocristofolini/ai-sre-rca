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
REPORT_SCHEMA = "ai-sre-rca.validation.v1"


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
        for status in statuses:
            waiting = (status.get("state") or {}).get("waiting") or {}
            reason, exit_code = terminated_reason(pod)
            if waiting.get("reason") == "CrashLoopBackOff" and exit_code:
                return {
                    "pod": pod,
                    "signal": {
                        "waiting_reason": "CrashLoopBackOff",
                        "last_exit_code": exit_code,
                        "last_reason": reason,
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
        str(ROOT / "validation" / "manifests" / "workloads.yaml"),
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
            "prometheus_url": "http://127.0.0.1:9",
            "loki_url": "http://127.0.0.1:9",
            "tempo_url": "http://127.0.0.1:9",
            "argocd_url": "http://127.0.0.1:9",
        }
    }
    return Settings(
        demo_mode=False,
        database_url=database_url,
        query_budget_enabled=False,
        notifications_enabled=False,
        llm_enabled=False,
        trace_analysis_enabled=False,
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
    expected_root: str,
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

    classic_root = classic.root_cause.hypothesis_id
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
    passed = (
        classic.status == "resolved"
        and classic_root == expected_root
        and agentic.status == "resolved"
        and agentic_root == expected_root
        and integrity_ok
        and kubernetes_result is not None
        and kubernetes_result.status == "completed"
    )
    return {
        "name": name,
        "pod": pod_name,
        "node": node_name,
        "signal": signal,
        "expected_root_cause": expected_root,
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
        result.resources_seen
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
                "container-oom",
            ),
            (
                "crashloop",
                "KubePodCrashLooping",
                "application-exit",
            ),
            (
                "failed-scheduling",
                "KubePodFailedScheduling",
                "insufficient-resources",
            ),
        ]
        for name, alert_name, expected_root in definitions:
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
                )
            )
        return {
            "inventory": inventory,
            "scenarios": scenarios,
            "passed": inventory["passed"]
            and all(item["passed"] for item in scenarios),
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
                expected=item["expected_root_cause"],
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
            "CrashLoopBackOff with non-zero exit",
            lambda: wait_for_crashloop(context),
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
