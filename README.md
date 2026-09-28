# AI SRE RCA — v0.6.1

[![CI](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml) [![CodeQL](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml) [![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca)](https://github.com/marcocristofolini/ai-sre-rca/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Português do Brasil](README_PT-BR.md) · **English**

Evidence-first, read-only SRE incident investigator for Kubernetes, distributed traces and stateful dependencies, designed so that telemetry and typed checks—not an LLM—decide the root cause.

The service receives an incident, resolves Kubernetes ownership and runtime topology, reconstructs distributed-trace causality, promotes typed PostgreSQL/Kafka/Redis dependency nodes into deterministic investigators, gathers evidence from Kubernetes, Prometheus, Loki, Tempo/OpenTelemetry, Argo CD, GitHub and Karpenter, retrieves similar historical incidents from PostgreSQL/pgvector, evaluates explicit hypotheses, and returns a structured RCA.

## v0.6.1 operational isolation hardening

v0.6.1 closes isolation gaps found in the post-merge audit of v0.6.0 without changing the read-only investigation model:

- **Tenant-scoped incident lookup at the repository boundary.** A caller outside the incident tenant receives the same not-found result as an unknown incident ID.
- **Tenant-scoped retention purge.** Tenant admins can purge only their own expired incidents and audit events; an explicitly global principal with `tenant_id="*"` can perform platform-wide retention.
- **Fail-closed multi-cluster configuration.** Malformed `CLUSTER_CONFIG_JSON`, non-object registries and non-object cluster entries now fail settings validation instead of silently becoming an empty registry.
- Regression coverage locks these isolation and configuration behaviors in place.
- No workload mutation method or remediation credential is introduced.

## What changed in v0.6

- **OIDC + hierarchical RBAC** for API/UI with configurable viewer, investigator and admin roles.
- **Tenant and cluster isolation** across incident reads, incident memory, audit and export.
- **Multi-cluster routing** through a per-cluster integration registry without sharing request state between concurrent investigations.
- **Per-incident query budgets** for total calls, per-source calls and wall-clock time; budget exhaustion becomes explicit evidence.
- **SHA-256 evidence integrity** and tenant-scoped audit events.
- **Incident export format** with integrity verification at `/api/v1/incidents/{id}/export`.
- **Slack and PagerDuty delivery** after persistence, with delivery outcomes written to the audit log.
- **Retention policies** for incidents and audit records.
- **Per-source health/capability reporting** and a new `/ops` UI.
- **Opt-in Helm NetworkPolicy templates** plus external Kubernetes Secret injection for operational credentials.
- **Alertmanager machine token** independent from interactive OIDC.

Full deployment and security details: [docs/operations.md](docs/operations.md).

## v0.5.1 hardening patch

v0.5.1 tightens stateful RCA correctness after the v0.5 review: invalid stateful UI routes return 404, trace metadata is merged instead of dropped, PostgreSQL exporter metric names/lock labels are aligned with the current collector, and PostgreSQL/Redis telemetry refuses unscoped instance-wide aggregation when instance identity is missing.

## What changed in v0.5

- **Stateful dependency promotion.** Trace-derived database/messaging dependency nodes can now become deterministic PostgreSQL, Kafka or Redis investigation targets.
- **PostgreSQL RCA** for connection exhaustion, lock contention, deadlocks, replication lag and long-running transactions.
- **Kafka RCA** for consumer lag, under-replicated partitions, offline/leaderless partitions and missing consumer-group members.
- **Redis RCA** for memory pressure, evictions, blocked clients, client-capacity saturation and elevated command latency.
- **Causal promotion guardrail.** A stateful cause is promoted only when the dependency is the trace origin and dependency-specific current telemetry independently confirms the anomaly.
- **Kubernetes precedence remains intact.** A confirmed infrastructure cause such as kubelet DiskPressure is not replaced by a downstream dependency anomaly.
- **Typed, read-only Prometheus adapters** for common postgres_exporter, kafka_exporter and redis_exporter metrics; missing metrics become `unknown`, never fabricated healthy signals.
- New structured `stateful_dependencies` API payload with health, causal role, signal values, thresholds, evidence and dependency-specific hypotheses.
- New stateful demo UI at `/stateful/{postgresql|kafka|redis}` and API demo endpoints for each dependency type.
- Configurable investigation budget and per-dependency thresholds through environment variables and Helm values.

## Core design rules

- **Read-only investigator.** No shell, SSH, `kubectl exec`, CREATE, PATCH, UPDATE, DELETE, Argo sync or cloud mutations.
- **Evidence before conclusions.** Every deterministic hypothesis points to collected evidence IDs.
- **Direct signal beats temporal coincidence.** A deploy that happened near an incident is not automatically causal.
- **Historical incidents are hints only.** Similarity retrieval never reuses an old RCA without fresh evidence.
- **The LLM proposes; deterministic tools verify.** LLM hypotheses start with score `0`, status `proposed`, and cannot participate in root-cause selection.
- **Typed integrations.** Each external system exposes narrow GET/query methods rather than a generic command execution interface.

## Architecture

```mermaid
flowchart TD
    A[Alertmanager / API] --> B[Incident Normalizer]
    B --> C[Investigation Engine<br/>Deterministic Playbooks]

    C --> K[Kubernetes<br/>Pod / ReplicaSet / Deployment / Node]
    C --> O[Observability<br/>Prometheus / Loki / OpenTelemetry]
    C --> CH[Change Sources<br/>Argo CD / GitHub]
    C --> KA[Karpenter<br/>NodeClaim / NodePool]

    K --> G[Awareness Graph]
    O --> G
    CH --> G
    KA --> G

    G --> E[Evidence Evaluator]
    E --> M[Incident Memory<br/>PostgreSQL + pgvector]
    E --> L[Optional LLM<br/>Hypothesis Proposer Only]

    M --> R[Evidence-backed RCA]
    L --> R
    E --> R

    R --> API[REST API]
    R --> UI[Next.js UI]
```

More diagrams are available in [docs/architecture.md](docs/architecture.md).

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

Open:

- UI: `http://localhost:3000`
- Swagger: `http://localhost:8080/docs`
- Kubernetes demo RCA: `http://localhost:8080/api/v1/incidents/demo`
- Trace-aware demo RCA: `http://localhost:8080/api/v1/incidents/demo/trace`
- Trace-aware UI: `http://localhost:3000/trace`
- Stateful PostgreSQL demo: `http://localhost:3000/stateful/postgresql`
- Stateful Kafka demo: `http://localhost:3000/stateful/kafka`
- Stateful Redis demo: `http://localhost:3000/stateful/redis`
- Operational health/RBAC UI: `http://localhost:3000/ops`

The Docker Compose stack uses the `pgvector/pgvector:pg17` image and automatically enables the `vector` extension.

## Run only the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
DEMO_MODE=true uvicorn app.main:app --reload --port 8080
```

Run tests:

```bash
pytest -q
```

## Alertmanager webhook

```text
POST /api/v1/webhooks/alertmanager
```

Example:

```yaml
receivers:
  - name: ai-sre-rca
    webhook_configs:
      - url: http://ai-sre-rca-backend.sre.svc.cluster.local:8080/api/v1/webhooks/alertmanager
        send_resolved: true
```

The current Kubernetes playbook expects useful labels such as `cluster`, `namespace`, `pod` and optionally `node`. In production, set `ALERTMANAGER_WEBHOOK_TOKEN` and send it in `X-Webhook-Token`.

## Real integrations

Set:

```bash
DEMO_MODE=false
DATABASE_URL=postgresql+asyncpg://ai_sre:password@postgres:5432/ai_sre

KUBERNETES_API_URL=https://kubernetes.default.svc
KUBERNETES_TOKEN_FILE=/var/run/secrets/kubernetes.io/serviceaccount/token
KUBERNETES_CA_FILE=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

PROMETHEUS_URL=http://prometheus-operated.monitoring.svc:9090
LOKI_URL=http://loki-gateway.logging.svc:3100
TEMPO_URL=http://tempo-query-frontend.tracing.svc:3200
TEMPO_TENANT_ID=
TRACE_ANALYSIS_ENABLED=true
TRACE_WINDOW_MINUTES=15
TRACE_SEARCH_LIMIT=8
TRACE_FETCH_LIMIT=4

STATEFUL_DEPENDENCY_ANALYSIS_ENABLED=true
STATEFUL_DEPENDENCY_MAX_TARGETS=4
DEPENDENCY_WINDOW_MINUTES=15

# Example thresholds; tune to your production SLOs and exporter semantics.
POSTGRES_CONNECTION_UTILIZATION_CRITICAL=0.90
POSTGRES_LOCKS_CRITICAL=50
POSTGRES_ACCESS_EXCLUSIVE_LOCKS_CRITICAL=1
POSTGRES_DEADLOCKS_DELTA_CRITICAL=1
POSTGRES_REPLICATION_LAG_BYTES_CRITICAL=16777216
POSTGRES_LONG_TX_SECONDS_CRITICAL=60

KAFKA_CONSUMER_LAG_CRITICAL=10000
KAFKA_UNDER_REPLICATED_PARTITIONS_CRITICAL=1
KAFKA_OFFLINE_PARTITIONS_CRITICAL=1
KAFKA_CONSUMER_GROUP_MEMBERS_MIN=1

REDIS_MEMORY_UTILIZATION_CRITICAL=0.90
REDIS_EVICTIONS_DELTA_CRITICAL=1
REDIS_BLOCKED_CLIENTS_CRITICAL=1
REDIS_CLIENT_UTILIZATION_CRITICAL=0.90
REDIS_COMMAND_LATENCY_SECONDS_CRITICAL=0.05

ARGOCD_URL=http://argocd-server.argocd.svc
ARGOCD_TOKEN=...
GITHUB_TOKEN=...

# Operational controls
AUTH_ENABLED=false
NOTIFICATIONS_ENABLED=false
INCIDENT_RETENTION_DAYS=30
AUDIT_RETENTION_DAYS=90
QUERY_BUDGET_ENABLED=true
QUERY_BUDGET_MAX_CALLS=80
QUERY_BUDGET_MAX_CALLS_PER_SOURCE=30
QUERY_BUDGET_MAX_WALL_SECONDS=45
CLUSTER_CONFIG_JSON={}
```

See [docs/operations.md](docs/operations.md) for OIDC claims, tenant/cluster isolation, Slack/PagerDuty, retention, exports, NetworkPolicy and external Secret configuration.

### OpenTelemetry service graph

The investigator queries Prometheus for Tempo/Alloy service-graph metrics:

```promql
sum(increase(traces_service_graph_request_total{server="landing-page"}[15m])) by (client, server, connection_type) > 0
or
sum(increase(traces_service_graph_request_total{client="landing-page"}[15m])) by (client, server, connection_type) > 0
```

Your Tempo metrics-generator, Grafana Alloy or OTel Collector servicegraph connector must export these metrics to the Prometheus-compatible backend used by `PROMETHEUS_URL`.

### Trace-aware causal propagation

v0.4 also talks directly to Tempo's read-only query API. The engine first searches error traces for the affected service, then fetches a bounded number of representative traces and parses their OTLP span trees.

```mermaid
flowchart LR
    A[Alert / Service Identity] --> Q[TraceQL Search<br/>error traces first]
    Q --> T[Representative Trace IDs]
    T --> P[OTLP Trace Parser]
    P --> TREE[Parent / Child Span Tree]
    TREE --> LEAF[Deepest Failing Span]
    LEAF --> PATH[Causal Path to Root]
    PATH --> REP{Repeated Across Traces?}
    REP -->|Yes| RCA[Confirmed Trace RCA]
    REP -->|No| TEST[Testing / Supporting Evidence]

    TREE --> DEP[Virtual DB / Messaging / External Nodes]
    TREE --> K8S[OTel Resource Attributes]
    K8S --> D[Deployment Correlation]
    PATH --> G[Awareness Graph]
```

The trace-only root cause is confirmed only when independent representative traces converge on the same downstream service/operation. A single trace remains `testing`. If Kubernetes already produced a stronger confirmed RCA, trace evidence is attached as context and does not override it.

The current TraceQL queries include:

```traceql
{ resource.service.name = "frontend" && status = error }
{ resource.service.name = "frontend" } | rate()
{ resource.service.name = "frontend" && status = error } | rate()
{ resource.service.name = "frontend" } | quantile_over_time(span:duration, .95)
```

Tempo v2 trace lookup uses `GET /api/v2/traces/<traceID>`; the standard OTLP JSON response is parsed programmatically.

### Stateful dependency RCA

v0.5 turns a trace dependency node into a typed investigator only when its protocol metadata is specific enough to classify it as PostgreSQL, Kafka or Redis.

```mermaid
flowchart LR
    ALERT[Service Incident] --> TRACE[Trace-aware Causal Propagation]
    TRACE --> DEP[TraceDependency Origin]
    DEP --> CLASS{Classify Dependency}

    CLASS -->|db.system=postgresql| PG[PostgreSQL Adapter]
    CLASS -->|messaging.system=kafka| KF[Kafka Adapter]
    CLASS -->|db.system=redis| RD[Redis Adapter]

    PG --> PM[Connections / Locks / Deadlocks<br/>Replication Lag / Long Tx]
    KF --> KM[Consumer Lag / ISR<br/>Leader Availability / Group Members]
    RD --> RM[Memory / Evictions<br/>Blocked Clients / Client Capacity / Latency]

    PM --> SCORE[Deterministic Evidence Scoring]
    KM --> SCORE
    RM --> SCORE

    SCORE --> GATE{Dependency is trace origin<br/>and anomaly confirmed?}
    GATE -->|Yes| SPECIFIC[Promote Specific Stateful RCA]
    GATE -->|No| CONTEXT[Keep as Supporting Context]

    K8S[Existing Confirmed Kubernetes RCA] --> PRECEDENCE{Stronger Existing Cause?}
    SPECIFIC --> PRECEDENCE
    PRECEDENCE -->|Yes| KEEP[Keep Kubernetes Root Cause]
    PRECEDENCE -->|No| ROOT[Use Stateful Root Cause]
```

The stateful adapters do not connect to databases, brokers or Redis with privileged credentials. They consume allow-listed Prometheus queries through the existing read-only telemetry boundary.

Current deterministic signal families:

| Dependency | Signals | Example hypotheses |
| --- | --- | --- |
| PostgreSQL | connections/max_connections, active/idle-in-transaction sessions, locks, AccessExclusiveLock, deadlocks, replication lag, long transaction age | connection exhaustion, lock contention, deadlocks, replica lag, long transaction |
| Kafka | consumer lag, under-replicated partitions, leaderless/offline partitions, consumer-group members | consumer backlog, replication degradation, leader unavailability, empty consumer group |
| Redis | memory/maxmemory, evictions, blocked clients, clients/maxclients, command latency | memory pressure, eviction storm, blocked clients, client saturation, slow command execution |

If an exporter does not expose one of these signals, that check remains `unknown`. It is not converted into supporting or contradicting evidence.

Full diagrams and signal details: [docs/stateful-dependencies.md](docs/stateful-dependencies.md).

## Change correlation

For a pod managed by Argo CD, the engine resolves:

```mermaid
flowchart LR
    P[Pod] --> RS[ReplicaSet]
    RS --> D[Deployment]
    D --> A[Argo CD Application]
    A --> REV[Deployed Git Revision]
    REV --> C[GitHub Commit]
    C --> W{Inside correlation window?}
    W -->|Yes| S[Supporting evidence only]
    W -->|No| X[Contradicting evidence]
```

`CHANGE_CORRELATION_WINDOW_MINUTES` defaults to `180`.

A deploy inside that window becomes **supporting evidence for a change hypothesis**, not an automatic cause. Direct evidence such as an explicit kubelet eviction remains stronger.

## Karpenter awareness

The engine lists `karpenter.sh/v1` NodeClaims and matches `status.nodeName` to the affected node. It records:

- NodeClaim and NodePool
- Ready state
- capacity type
- provider ID
- claim age
- lifecycle conditions
- Karpenter/Kubernetes events
- number of colocated pods

A healthy, long-lived Ready NodeClaim contradicts the `node-capacity-churn` hypothesis.

## Incident memory with pgvector

Each persisted incident gets a normalized 128-dimensional local fingerprint embedding. The default implementation deliberately has zero external dependency; it can later be swapped for a dedicated embedding model.

```mermaid
flowchart LR
    I[Current Incident] --> F[Normalized Fingerprint]
    F --> V[128-d Local Embedding]
    V --> PG[(PostgreSQL + pgvector)]
    PG --> S[Cosine Similarity Search]
    S --> H[Top Historical Incidents]
    H --> C[Context / Hints Only]
    C --> E[Fresh Evidence Still Required]
```

The database creates an HNSW `vector_cosine_ops` index for scale.

## Optional LLM hypothesis expansion

Disabled by default:

```bash
LLM_ENABLED=true
LLM_BASE_URL=http://ollama.ai.svc:11434/v1
LLM_MODEL=your-model
LLM_API_KEY=
```

Any OpenAI-compatible `/chat/completions` endpoint can be used.

The model receives structured incident context, entities and evidence summaries. It does **not** receive Kubernetes credentials, infrastructure tools, shell access or write permissions.

```mermaid
flowchart TD
    CTX[Incident + Entities + Evidence Summaries] --> LLM[LLM Hypothesis Proposer]
    LLM --> P[Hypothesis<br/>origin=llm<br/>status=proposed<br/>score=0]
    P --> HC[Allow-listed Required Checks]
    HC --> T[Typed Read-only Tools]
    T --> EV[Fresh Evidence]
    EV --> D[Deterministic Evaluator]
    D -->|Validated| H[Deterministic Hypothesis State]
    D -->|Not validated| P

    LLM -. no credentials .-> N1[No Kubernetes direct access]
    LLM -. no shell .-> N2[No command execution]
    LLM -. no mutation .-> N3[No write permissions]
```

The deterministic engine excludes unverified LLM hypotheses from root-cause selection.

## Deterministic Kubernetes playbooks

| Scenario | Evidence used | Example root causes |
| --- | --- | --- |
| Pod eviction | Pod reason/events, node pressure, filesystem trend, resource policy | DiskPressure / ephemeral-storage exhaustion |
| OOMKilled | Current/previous container state, memory limits, working set, Node MemoryPressure | container memory exhaustion, node-wide pressure |
| CrashLoopBackOff | waiting state, restart count, previous exit, probe events, logs | application exit, OOM restart, probe failure |
| FailedScheduling | scheduler events and Karpenter provisioning events | insufficient resources, taints, affinity, volume binding, cloud capacity |
| HPA | AbleToScale, ScalingActive, ScalingLimited, current/desired/max replicas | maxReplicas saturation, metric failure, blocked scaling |
| KEDA | ScaledObject Ready/Active, pause annotations, fallback/warning events | scaler error, pause, fallback, inactive trigger |
| NodeNotReady | Ready condition, taints, node events, pressure conditions, NodeClaim state | unreachable node, kubelet unhealthy, pressure, lifecycle disruption |

The engine selects the playbook from alert context plus live Kubernetes state. Direct platform evidence remains stronger than temporal correlations such as a recent deployment.

The regression suite covers Kubernetes playbooks, trace-only causal propagation, PostgreSQL/Kafka/Redis dependency promotion and precedence safety.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
GET  /api/v1/incidents/demo/trace
GET  /api/v1/incidents/demo/stateful/{postgresql|kafka|redis}
GET  /api/v1/incidents/{id}
POST /api/v1/incidents/investigate
POST /api/v1/webhooks/alertmanager
```

Example:

```bash
curl -X POST http://localhost:8080/api/v1/incidents/investigate \
  -H 'content-type: application/json' \
  -d '{
    "title": "Pod evicted",
    "cluster": "prod",
    "namespace": "exchange",
    "pod": "landing-page-7d8b6d9b-x9k2p",
    "node": "ip-10-0-1-42",
    "alert_name": "KubePodEvicted"
  }'
```

## Security boundary

Keep remediation in a separate service identity. The investigator Helm role grants only:

```text
get
list
watch
```

for the Kubernetes resources it needs. There is no `pods/exec` permission and no mutating verb.

```mermaid
flowchart LR
    INV[Investigator Identity<br/>get / list / watch] --> OBS[Read-only Evidence Sources]
    OBS --> RCA[RCA + Recommended Actions]
    RCA --> APPROVAL{Human Approval}
    APPROVAL -->|Approved| REM[Separate Remediation Identity]
    REM --> ACT[Narrow Allow-listed Action]
    APPROVAL -->|Rejected| STOP[No Mutation]

    INV -. cannot .-> ACT
```

For production, store Argo/Git/model credentials in Kubernetes Secrets or workload identity—not in Helm values committed to Git.

## Repository layout

```text
backend/
  app/core/           deterministic engine/playbooks/scoring
  app/integrations/   Kubernetes, Prometheus, Loki, Argo, GitHub, Karpenter
  app/llm/            optional hypothesis proposer
  app/memory/         embedding + memory protocol
  app/db/             PostgreSQL/pgvector persistence
  tests/              RCA + safety regression tests
frontend/             Next.js investigation UI
helm/ai-sre-rca/      deployment + read-only RBAC
docs/                 architecture and roadmap
```

## Next evolution

v0.5 completes the first deterministic stateful-dependency layer. The next highest-value increments are:

1. Operational productization: OIDC/RBAC for UI/API, NetworkPolicy templates, multi-cluster tenancy, retention and per-query budgets.
2. Slack/PagerDuty delivery with evidence-backed incident summaries.
3. Evidence hashing and immutable audit metadata.
4. PostgreSQL depth: blocker/blockee graphs, pg_stat_statements correlation and database-specific saturation baselines.
5. Kafka depth: rebalance history, broker/JMX throughput and partition-level leader/ISR timelines.
6. Redis depth: slowlog/hot-key and keyspace/TTL analysis with cardinality-safe collection.
7. Remaining Kubernetes depth: ImagePullBackOff, PDB blocking and runtime PVC/volume failures.
8. Trace-to-log derived fields, explicit exemplars and sampling-aware trace evidence.
9. A separate human-approved remediation plane with policy validation and rollback metadata.
