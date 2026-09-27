# AI SRE RCA — v0.4.0

[![CI](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml) [![CodeQL](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml) [![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca)](https://github.com/marcocristofolini/ai-sre-rca/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Português do Brasil](README_PT-BR.md) · **English**

Evidence-first, read-only Kubernetes incident investigator inspired by autonomous RCA systems, but designed so that telemetry and typed checks—not an LLM—decide the root cause.

The service receives an incident, resolves Kubernetes ownership and runtime topology, gathers evidence from Kubernetes, Prometheus, Loki, OpenTelemetry service-graph metrics, Argo CD, GitHub and Karpenter, retrieves similar historical incidents from PostgreSQL/pgvector, evaluates explicit hypotheses, and returns a structured RCA.

## What changed in v0.4

- **Tempo/TraceQL trace discovery** around the incident window using typed, read-only HTTP calls.
- Fetch and parse standard OTLP JSON traces from Tempo v2 without relying on LLM-oriented response formats.
- Deterministic **deepest failing span** selection and parent/child ancestry reconstruction.
- Cross-trace confirmation: the same downstream origin must repeat across representative traces before trace-only RCA can be confirmed.
- **TraceQL RED metrics** (rate, error rate and p95 duration) plus service-graph RED metrics on dependency edges.
- Virtual dependency nodes for database, messaging and external-service calls.
- **Trace → Deployment correlation** from OpenTelemetry resource attributes such as `k8s.deployment.name`, `k8s.namespace.name` and `service.version`.
- New `/trace` UI and `/api/v1/incidents/demo/trace` demo flow.
- Guardrail: trace evidence can enrich an already-confirmed Kubernetes RCA, but cannot silently replace it.

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

The current Kubernetes playbook expects useful labels such as `cluster`, `namespace`, `pod` and optionally `node`.

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

ARGOCD_URL=http://argocd-server.argocd.svc
ARGOCD_TOKEN=...
GITHUB_TOKEN=...
```

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

The demo still reproduces the original ephemeral-storage eviction case, while the regression suite exercises the new v0.3 scenarios.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
GET  /api/v1/incidents/demo/trace
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

v0.4 completes the first trace-aware causal-propagation layer. The next highest-value increments are:

1. PostgreSQL RCA adapter: connection exhaustion, locks, replication lag, slow queries and saturation.
2. Kafka RCA adapter: consumer lag, ISR/leader issues and partition-level failure propagation.
3. Redis RCA adapter: memory pressure, evictions, latency and hot-key context.
4. ImagePullBackOff / ErrImagePull, PDB blocking and deeper PVC/runtime-volume diagnostics.
5. Trace-to-log derived-field correlation and richer operation-level exemplars.
6. GitHub PR metadata plus Kubernetes rollout-history correlation.
7. NetworkPolicy, evidence hashing/audit trail, OIDC/RBAC and per-query budgets.
8. Slack/PagerDuty delivery and a separate human-approved remediation plane.
