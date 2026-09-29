# AI SRE RCA — v0.8.0 (development)

[![CI](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml) [![CodeQL](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml) [![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca)](https://github.com/marcocristofolini/ai-sre-rca/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Português do Brasil](README_PT-BR.md) · **English**

Evidence-first, read-only SRE incident investigator for Kubernetes, distributed traces and stateful dependencies, designed so that telemetry and typed checks—not an LLM—decide the root cause.

The service receives an incident, resolves Kubernetes ownership and runtime topology, reconstructs distributed-trace causality, promotes typed PostgreSQL/Kafka/Redis dependency nodes into deterministic investigators, gathers evidence from Kubernetes, Prometheus, Loki, Tempo/OpenTelemetry, Argo CD, GitHub and Karpenter, retrieves similar historical incidents from PostgreSQL/pgvector, evaluates explicit hypotheses, and returns a structured RCA.

## v0.8 development — persistent awareness + bounded agentic planning

v0.8 is evolving the project from an incident-local RCA engine into an autonomous, evidence-verified SRE platform. The current development branch now includes:

- **Persistent temporal Awareness Graph** with canonical entity/relationship identity across incidents.
- **First-class ChangeEvent nodes** derived from Argo CD deployment evidence and matching Git revision evidence, with stable identity across repeated observations. Materialization requires application, revision and an authoritative operation timestamp; workload/service/repository links are emitted only when the existing graph resolves them unambiguously.
- **Tenant + cluster isolation** at identity, persistence and query boundaries.
- **Current state + append-only observations** with idempotent ingestion.
- **Topology reconciliation** that classifies observed records as `fresh` or `stale` from `last_seen`, while incomplete incident data never implies deletion.
- **Authoritative inventory tombstones** from complete admin-only snapshots supplied by allow-listed sources, with explicit coverage, newer-observation protection, reactivation semantics and append-only audit/history.
- **Service-centric topology projections** with bounded depth, node and edge budgets.
- **Graph-backed blast radius** using explicit causal direction per relationship type. Unknown, stale or tombstoned relationships are visible as context but never traversed as current impact paths.
- **Historical change timeline semantics.** Change events and their event edges are context-only, reconcile as `historical`, and are never added to the blast-radius causal allow-list.
- **Read-only Awareness Explorer at `/awareness`** with cluster/namespace/service scoping, reconciled fresh/stale/historical/tombstoned state, stored relationships and the bounded ChangeEvent timeline. It reuses the existing viewer authorization and server-side API client without introducing graph-write controls.
- **Bounded Investigation Planner** that emits only allow-listed read-only specialist tasks: Kubernetes, metrics, logs, traces, database and changes.
- **Kubernetes specialist parity with the core engine.** The agentic Kubernetes branch now delegates to the same deterministic stage used by classic RCA, covering eviction/ephemeral-storage, OOMKilled, CrashLoopBackOff, FailedScheduling/Karpenter capacity, HPA, KEDA and NodeNotReady with identical playbook hypotheses and evidence rules.
- **Shared Kubernetes root-priority policy + fail-closed partial branches.** Classic and agentic paths use the same specificity ordering, and hypotheses from a non-completed specialist can never be selected.
- **Typed task dependencies.** Stateful/database investigation is planned only after the trace branch that provides typed dependency identity.
- **Persistent graph context for planning** with hard server-side limits on context size, task count, parallelism and per-specialist query budgets.
- **Experimental parallel specialist execution** behind `AGENTIC_EXECUTION_ENABLED=false` by default. Independent branches run concurrently; dependency branches run only after prerequisites complete.
- **Shared evidence envelope** with evidence-integrity verification. Branch-local hypotheses are advisory-only and the execution response intentionally has no `root_cause` field.
- **Deterministic shared-envelope evaluator** that re-scores only integrity-verified evidence, reapplies trace/stateful causal gates, preserves Kubernetes precedence, fails closed when a required higher-precedence specialist did not complete, and emits a rejected-hypothesis explanation graph.
- **Three distinct agentic surfaces:** `plan` compiles, `execute` collects read-only specialist evidence, and `investigate` runs both plus deterministic evaluation. Only `investigate` can return a root-cause verdict.
- **Optional model-assisted planning**, disabled by default. The LLM may suggest only specialist names and rationale; the server intersects that advice with the existing allow-list, deterministic eligibility gates, mandatory baseline branches and server-owned budgets/dependencies before any task exists.

Current v0.8 APIs include:

```text
GET  /api/v1/awareness/graph
GET  /api/v1/awareness/history/{target_id}
GET  /api/v1/awareness/topology
GET  /api/v1/awareness/services/{service_name}
GET  /api/v1/awareness/blast-radius/{target_id}
GET  /api/v1/awareness/changes
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
```

The planner remains a preview/compilation surface. Parallel specialist execution is an opt-in read-only runtime and recompiles the plan server-side immediately before execution, so caller-authored task graphs are never executed. When `AGENTIC_MODEL_PLANNER_ENABLED=true` and `LLM_ENABLED=true`, an advisory model may prioritize optional branches, but executable tasks are still compiled exclusively from server-owned templates. Kubernetes, metrics and traces remain mandatory whenever they are deterministically eligible; unknown/ineligible model choices are rejected and malformed/model-failure responses preserve the deterministic plan. The `/investigate` path then revalidates the shared envelope, recalculates deterministic hypothesis scores, reapplies trace/stateful causal gates, enforces Kubernetes precedence and produces an explanation graph before a root-cause verdict is allowed. The LLM still cannot create graph facts, attach evidence, invoke arbitrary tools or mutate infrastructure.

Design: [Persistent Awareness Graph](docs/persistent-awareness-graph.md) · [Agentic Investigation](docs/agentic-investigation.md).

## What changed in v0.7

v0.7 adds a **guarded remediation plane** while keeping the RCA investigator read-only:

- separate FastAPI process, Deployment and Kubernetes ServiceAccount for remediation;
- first allow-listed action: typed Deployment scaling through deployments/scale only;
- static/runtime policy gates and Kubernetes server-side dry-run before approval;
- exact OIDC remediator role with four-eyes approval enabled by default;
- plan TTL, explicit reject/execute/rollback transitions and audit events;
- replica/resourceVersion drift detection before execution;
- rollback metadata captured before approval;
- namespace-scoped Helm Roles limited to get/patch on deployments/scale;
- separate REMEDIATION_CLUSTER_CONFIG_JSON credentials, never reused by the investigator.

Full design and API: [docs/remediation.md](docs/remediation.md).

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
    A[Alertmanager / API] --> N[Incident Normalizer]
    N --> EL[Deterministic Eligible Specialists]
    N --> E[Investigation Engine]

    G[(Persistent Awareness Graph)] --> C[Bounded Service Context]
    C --> EL
    EL --> PC[Server-side Plan Compiler]
    EL -. sanitized context .-> ADV[Optional Advisory LLM]
    ADV -. specialist names only .-> PC
    PC --> P[Bounded Investigation Plan]
    P --> T[Allow-listed Specialist Tasks]
    T -->|opt-in| X[Parallel Read-only Specialists]
    X --> SE[Shared Evidence Envelope]
    SE --> V[Deterministic Shared-envelope Evaluator]
    V --> EG[Rejected-hypothesis Explanation Graph]

    E --> K[Kubernetes]
    E --> O[Prometheus / Loki / Tempo]
    E --> CH[Argo CD / GitHub / Karpenter]
    K --> L[Incident-local Graph + Evidence]
    O --> L
    CH --> L

    L --> G
    G --> R[Fresh / Stale / Historical Reconciliation]
    G --> S[Service Projections]
    G --> B[Blast Radius]
    G --> CE[Change Event Timeline]
    R --> AUI[Read-only Awareness Explorer]
    S --> AUI
    CE --> AUI

    L --> D[Deterministic Evaluator]
    V -. same authority rules .-> D
    D --> M[Incident Memory<br/>PostgreSQL + pgvector]
    D --> H[Verified Hypotheses]
    H --> RCA[Evidence-backed RCA]
    RCA --> API[REST API]
    RCA --> UI[Next.js UI]
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
- Persistent Awareness Graph explorer: `http://localhost:3000/awareness?cluster=prod-us-east-1`
- Reconciled topology API: `GET /api/v1/awareness/topology?cluster=<cluster>`
- Service topology API: `GET /api/v1/awareness/services/<service>?cluster=<cluster>&namespace=<ns>`
- Blast-radius API: `GET /api/v1/awareness/blast-radius/<entity-id>?cluster=<cluster>`
- Change timeline API: `GET /api/v1/awareness/changes?cluster=<cluster>&service=<service>&namespace=<ns>`
- Investigation planner preview: `POST /api/v1/planner/plan`
- Experimental parallel specialist execution: `POST /api/v1/planner/execute` (requires `AGENTIC_EXECUTION_ENABLED=true`)
- Full agentic investigation with deterministic verdict + explanation graph: `POST /api/v1/planner/investigate` (same opt-in execution flag)

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
  app/remediation/    separate guarded remediation service
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

After v0.7, the highest-value increments are:

1. Remediation policy bundles per tenant/cluster and signed approval evidence.
2. Post-action verification gates and rollback proposals, still requiring explicit execution.
3. PostgreSQL blocker/blockee graphs and pg_stat_statements correlation.
4. Kafka rebalance/broker timelines and Redis slowlog/hot-key analysis.
5. Remaining Kubernetes depth: ImagePullBackOff, PDB blocking and runtime PVC/volume failures.
6. Trace-to-log derived fields, explicit exemplars and sampling-aware trace evidence.
7. External immutable audit sink and signed incident/remediation export bundles.
