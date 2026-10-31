# Kafka validation: real exporter preflight (passed in disposable Kind)

This is a **first, opt-in part** of the Kafka failure/recovery validation
needed for Tracecroft AI v0.8.0-rc.3. It does **not** certify the RCA engine or
complete the Kafka release gate. The previously tested PostgreSQL and Redis
validation scenarios remain unchanged.

## What is implemented

- A disposable, **single-broker Kafka 4.1.0 KRaft** fixture, a pinned
  `kafka_exporter` v1.10.0, and a separately scalable consumer.
- One fixed topic (`orders-events`) and one fixed group (`checkout-workers`).
  The test seeds a real committed offset before creating backlog.
- Fault: the consumer is kept at zero replicas, then 200 real Kafka records are
  produced. The exporter must report at least 128 messages of lag and **zero
  consumer members**.
- Recovery: the same consumer is scaled to one replica and must drain the
  backlog. At least two observations, seven seconds apart, must show
  **zero lag and an active group member**, with broker, ISR and partition
  leadership healthy.
- Missing, duplicated, NaN or ambiguous metric samples are **unknown**, not
  zero. Errors fail closed rather than being reported as successful recovery.
- A caller must select a `kind-*` context whose nodes have matching Kind
  `kind://docker/<cluster>/<node>` provider IDs. Existing named fixtures, including services, are refused.
  The runner cleans up only the resources it owns, even after partial apply.
  No production Kafka, non-Kind context or existing application is targeted.
- Pinned OCI manifest index digests (Docker Hub API):
  `apache/kafka:4.1.0@sha256:bff074a5d0051dbc0bbbcd25b045bb1fe84833ec0d3c7c965d1797dd289ec88f`
  and `danielqsj/kafka-exporter:v1.10.0@sha256:346a53c2489a3530790d01f7b96632496d0d58926662f4c35bb65f569db2ab42`.

This fixture intentionally uses plaintext Kafka **only inside a disposable
Kind cluster**. It does not introduce Kafka into default Helm values,
production manifests, or the existing release validation workflow.

## Local unit checks

The initial, dependency-free tests cover exporter label matching, missing
metrics, duplicate partitions, non-finite values, recovery invariants, CLI
allow-list, explicit stdin forwarding, Kind identity and partial cleanup:

```bash
python3 -m unittest -v validation.test_kafka_live
python3 -m py_compile validation/kafka_live.py
```

These checks are **not live Kafka evidence**. Run them inside the existing
backend test image when Docker is available.

## Verified real Kafka signal observations — October 31, 2026

The opt-in runner was executed in the disposable two-node `kind-tracecroft-kafka`
cluster, using the pinned Kafka 4.1.0 and exporter 1.10.0 images, with its own
real topic, group and consumer. The initial run **failed** because the broker
readiness and exporter startup probes were too aggressive for the Java process,
and the Python subprocess timeout was shorter than the Kubernetes rollout.
Those issues were fixed and the runner re-executed to completion (**PASS**,
exit code 0).

| Observation | Failure #1 | Failure #2 | Recovery #1 | Recovery #2 |
| --- | ---: | ---: | ---: | ---: |
| Kafka brokers | 1 | 1 | 1 | 1 |
| Consumer group lag (messages) | 200 | 200 | 0 | 0 |
| Consumer group members | 0 | 0 | 1 | 1 |
| Under-replicated partitions | 0 | 0 | 0 | 0 |
| Offline partitions | 0 | 0 | 0 | 0 |

The controlled telemetry report is preserved in
[the Kind JSON record](validation/kafka-exporter-kind-2026-10-31.json).
It contains only the isolated test context, timestamps and exporter measurements.
The snapshots were collected at
`08:22:44Z`, `08:22:51Z`, `08:23:01Z`, and `08:23:08Z`
on October 31, 2026. They confirm that observed consumer lag appeared
during the stopped-consumer fault and was drained after the consumer returned.
This **does not** validate the complete Classic/Agentic Kafka RCA, real Tempo/Loki
correlation or the final release gate. No runtime model tokens or GitHub Actions
were used.

## Running against an isolated Kind cluster

Requires Docker Desktop WSL integration, a working `docker`, `kind`,
`kubectl`, and the existing `ai-sre-rca-test` validation image. Docker Desktop
was restarted for the October 31 live run, and the Kafka signal preflight
passed. The full Kafka RCA validation remains pending.

Create an independently named disposable cluster and the validation
namespace, using the repository's pinned node image:

```bash
kind create cluster --name tracecroft-kafka \
  --config validation/kind-cluster.yaml \
  --image kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
kubectl --context kind-tracecroft-kafka apply -f validation/manifests/access.yaml
sh validation/run-kafka-docker.sh kind-tracecroft-kafka "$HOME/.kube/config"
```

Inspect `validation-artifacts/kafka-exporter-preflight.json` for the two
consecutive failure and recovery snapshots and the explicit `passed` value.
The runner removes its Kafka fixtures; the outer Kind cluster remains owned
by the operator for inspection, and may then be removed explicitly:

```bash
kind delete cluster --name tracecroft-kafka
```

## Still required before checking off the Kafka roadmap item

1. Scrape the Kafka exporter through the real Kind Prometheus service, with
   correct `instance=kafka:9092` identity and verify the Kafka adapter's
   scoped PromQL selects only this topic and group.
2. Generate an **observed** failed and healthy application request, with
   fresh Loki logs and OpenTelemetry/Tempo traces containing the Kafka
   dependency identity; do not fabricate broker errors from test intent.
3. Run Classic and Agentic over those same live observations; require
   the deterministic `kafka-consumer-lag:` root during the fault and
   block stale Kafka/trace causes after consumer recovery.
4. Confirm evidence SHA-256 integrity, complete specialist execution,
   parity, recovery and truthful report fields in the existing v3 validator.
5. Execute the end-to-end disposable Kind gate locally before marking
   `v0.8.0-rc.3` ready. No GitHub Actions runs and no release/tag promotion.

The Kafka exporter metric names and labels are documented by the
[upstream exporter](https://github.com/danielqsj/kafka_exporter).
