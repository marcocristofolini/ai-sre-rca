"""Opt-in Kafka exporter preflight against an isolated, disposable Kind cluster.

This is NOT the full Tracecroft RCA release gate: it proves real broker
failure/recovery signals but does not invent a trace-based diagnosis. The
existing versioned release gate stays blocked until RCA parity is exercised.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

NAMESPACE = "rca-validation"
TOPIC = "orders-events"
GROUP = "checkout-workers"
PORT = 19308
MANIFEST = Path(__file__).with_name("manifests") / "kafka.yaml"
METRIC = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([^\s]+)(?:\s+.*)?$')
LABEL = re.compile(r'(?:^|,)\s*([A-Za-z_][A-Za-z0-9_]*)="((?:\\.|[^"\\])*)"')


@dataclass(frozen=True)
class KafkaSnapshot:
    brokers: float | None
    lag: float | None
    members: float | None
    under_replicated: float | None
    offline_partitions: float | None
    observed_at: str


def _metric_lines(text: str, name: str, *, topic: str | None = None,
                  group: str | None = None) -> list[tuple[dict[str, str], float]]:
    values: list[tuple[dict[str, str], float]] = []
    for line in text.splitlines():
        match = METRIC.match(line.strip())
        if not match or match.group(1) != name:
            continue
        labels = dict(LABEL.findall(match.group(2) or ""))
        if topic is not None and labels.get("topic") != topic:
            continue
        if group is not None and labels.get("consumergroup") != group:
            continue
        try:
            value = float(match.group(3))
        except ValueError:
            continue
        if math.isfinite(value):
            values.append((labels, value))
    return values


def _unique_gauge(text: str, name: str, *, group: str | None = None) -> float | None:
    rows = _metric_lines(text, name, group=group)
    # Missing or duplicate gauges are unknown, never inferred healthy.
    return rows[0][1] if len(rows) == 1 else None


def parse_exporter(text: str) -> KafkaSnapshot:
    brokers = _unique_gauge(text, "kafka_brokers")
    members = _unique_gauge(text, "kafka_consumergroup_members", group=GROUP)
    lag_rows = _metric_lines(text, "kafka_consumergroup_lag", topic=TOPIC, group=GROUP)
    urp_rows = _metric_lines(text, "kafka_topic_partition_under_replicated_partition", topic=TOPIC)
    leader_rows = _metric_lines(text, "kafka_topic_partition_leader", topic=TOPIC)
    def partitions(rows: list[tuple[dict[str, str], float]]) -> bool:
        return bool(rows) and all(row.get("partition") is not None for row, _ in rows) and (
            len({row["partition"] for row, _ in rows}) == len(rows)
        )
    lag = sum(value for _, value in lag_rows) if partitions(lag_rows) and all(value >= 0 for _,value in lag_rows) else None
    urp = sum(value for _, value in urp_rows) if partitions(urp_rows) else None
    offline = sum(1 for _, value in leader_rows if value < 0) if partitions(leader_rows) else None
    return KafkaSnapshot(brokers, lag, members, urp, offline,
                         datetime.now(timezone.utc).isoformat())


def assert_signal(snapshot: KafkaSnapshot, *, stage: str, minimum_lag: int = 128) -> None:
    if (snapshot.brokers is None or snapshot.brokers < 1 or
            snapshot.lag is None or snapshot.members is None or
            snapshot.under_replicated is None or snapshot.offline_partitions is None):
        raise ValueError(f"{stage}: required exporter series missing/ambiguous")
    if snapshot.under_replicated != 0 or snapshot.offline_partitions != 0:
        raise ValueError(f"{stage}: broker or partition health degraded unexpectedly")
    if stage == "failed":
        if snapshot.lag < minimum_lag or snapshot.members != 0:
            raise ValueError(f"expected stopped consumer and lag >= {minimum_lag}; got {snapshot}")
    elif stage == "recovered":
        if snapshot.lag != 0 or snapshot.members < 1:
            raise ValueError(f"expected drained lag and active consumer; got {snapshot}")
    else:
        raise ValueError("unknown stage")


def kubectl(context: str, *args: str, stdin: str | None = None) -> str:
    cmd = ["kubectl", "--context", context, "-n", NAMESPACE, *args]
    # Kubernetes rollout may legitimately wait for the configured 240s.
    timeout = 270 if args and args[0] == "rollout" else 120
    result = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Do not echo data from a live dependency or leak incidental secrets.
        raise RuntimeError(f"kubectl {args[0]} failed ({result.returncode}): {result.stderr[-250:]}")
    return result.stdout


def _broker(context: str, cli: str, *args: str, stdin: str | None = None) -> str:
    if not re.fullmatch(r"kafka-(?:topics|console-producer|console-consumer)\.sh", cli):
        raise ValueError("Kafka CLI not allow-listed")
    flags = ("-i",) if stdin is not None else ()
    return kubectl(context, "exec", *flags, "deployment/kafka", "-c", "broker", "--",
                   f"/opt/kafka/bin/{cli}", *args, stdin=stdin)


def _read_exporter() -> KafkaSnapshot:
    with urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{PORT}/metrics",
                               headers={"Cache-Control": "no-cache"}), timeout=5,
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"Exporter HTTP {response.status}")
        return parse_exporter(response.read().decode("utf-8"))


def wait_for_stage(stage: str, *, deadline_seconds: int = 120) -> list[KafkaSnapshot]:
    deadline = time.monotonic() + deadline_seconds
    good: list[KafkaSnapshot] = []
    while time.monotonic() < deadline:
        try:
            item = _read_exporter()
            assert_signal(item, stage=stage)
            good.append(item)
            if len(good) == 2:
                return good
            # Exporter metadata refreshes every 5 seconds: require fresh observations.
            time.sleep(7)
        except (ValueError, OSError, urllib.error.URLError):
            good.clear()
            time.sleep(3)
    raise RuntimeError(f"Kafka {stage} condition was not sustained for 2 exporter scrapes")


def run_live(context: str) -> dict:
    if not re.fullmatch(r"kind-[a-z0-9-]{1,48}", context):
        raise ValueError("Kafka preflight restricted to explicit disposable Kind contexts")
    # A context name alone is not proof of a Kind cluster. Match every node's
    # providerID to the cluster and exact Kind node name; Kind v1.37 does not
    # add a kind.x-k8s.io/cluster label to its nodes.
    nodes = json.loads(kubectl(context, "get", "nodes", "-o", "json"))
    observed = nodes.get("items") or []
    name = context.removeprefix("kind-")
    node_pattern = re.compile(
        rf"kind://docker/{re.escape(name)}/{re.escape(name)}-(?:control-plane|worker[0-9]*)"
    )
    if (not observed or
            not any(node.get("metadata", {}).get("name") == f"{name}-control-plane"
                    for node in observed) or
            any(
                not node_pattern.fullmatch(node.get("spec", {}).get("providerID") or "")
                or not (node.get("spec", {}).get("providerID") or "").endswith(
                    "/" + (node.get("metadata", {}).get("name") or "")
                )
                for node in observed
            )):
        raise RuntimeError("Refusing non-Kind or ambiguous cluster target")
    # Refuse to mutate existing fixtures in a shared cluster.
    for resource in ("deployment/kafka", "deployment/kafka-exporter",
                     "deployment/kafka-validation-consumer",
                     "service/kafka", "service/kafka-exporter"):
        found = subprocess.run(["kubectl", "--context", context, "-n", NAMESPACE,
                                "get", resource, "-o", "name"],
                               capture_output=True, text=True, timeout=15)
        if found.returncode == 0:
            raise RuntimeError(f"Existing {resource} refused: use a new Kind cluster")
        if found.returncode != 1 or "NotFound" not in found.stderr:
            raise RuntimeError(f"Cannot prove {resource} absent; refusing to deploy")
    forward: subprocess.Popen | None = None
    owned = False
    try:
        # Even an unsuccessful apply can create a subset of the manifests.
        # Set ownership before writing so finally can remove those resources.
        owned = True
        kubectl(context, "apply", "-f", str(MANIFEST))
        for deployment in ("kafka", "kafka-exporter"):
            kubectl(context, "rollout", "status", f"deployment/{deployment}",
                    "--timeout=240s")
        _broker(context, "kafka-topics.sh", "--bootstrap-server", "localhost:9092",
                "--create", "--if-not-exists", "--topic", TOPIC,
                "--partitions", "1", "--replication-factor", "1")
        # Establish a group offset; otherwise consumer-lag metrics may be absent.
        _broker(context, "kafka-console-producer.sh", "--bootstrap-server",
                "localhost:9092", "--topic", TOPIC, stdin="validation-seed\n")
        seeded = _broker(context, "kafka-console-consumer.sh", "--bootstrap-server",
                         "localhost:9092", "--topic", TOPIC, "--group", GROUP,
                         "--from-beginning", "--max-messages", "1", "--timeout-ms", "30000")
        if "validation-seed" not in seeded:
            raise RuntimeError("Kafka seed was not consumed by the intended group")
        forward = subprocess.Popen(["kubectl", "--context", context, "-n", NAMESPACE,
                                    "port-forward", "service/kafka-exporter", f"{PORT}:9308"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _broker(context, "kafka-console-producer.sh", "--bootstrap-server",
                "localhost:9092", "--topic", TOPIC,
                stdin="".join(f"validation-{n}\n" for n in range(200)))
        failed = wait_for_stage("failed")
        kubectl(context, "scale", "deployment/kafka-validation-consumer", "--replicas=1")
        kubectl(context, "rollout", "status", "deployment/kafka-validation-consumer",
                "--timeout=180s")
        recovered = wait_for_stage("recovered")
        return {"schema": "tracecroft.kafka-exporter-preflight.v1", "context": context,
                "topic": TOPIC, "group": GROUP,
                "failure": [asdict(item) for item in failed],
                "recovery": [asdict(item) for item in recovered], "passed": True,
                "note": "Real exporter telemetry only; full RCA causal parity not evaluated."}
    finally:
        if forward is not None:
            forward.terminate()
            try:
                forward.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forward.kill()
        if owned:
            kubectl(context, "delete", "-f", str(MANIFEST), "--ignore-not-found=true",
                    "--wait=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, help="Explicit disposable kind-* context")
    parser.add_argument("--report", type=Path,
                        default=Path("validation-artifacts/kafka-exporter-preflight.json"))
    args = parser.parse_args()
    result = run_live(args.context)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print("Kafka exporter preflight PASS:", args.report)


if __name__ == "__main__":
    main()
