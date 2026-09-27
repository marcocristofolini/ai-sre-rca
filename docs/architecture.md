# Architecture — v0.5

[Português do Brasil](architecture.pt-BR.md) · **English**

## System overview

```mermaid
flowchart TD
    A[Alertmanager / REST API] --> N[Incident Normalizer]
    N --> I[Investigation Engine]

    I --> K[Kubernetes Adapter]
    I --> P[Prometheus Adapter]
    I --> L[Loki Adapter]
    I --> O[OpenTelemetry Service Graph]
    I --> T[Tempo / TraceQL<br/>Trace Causality]
    I --> AR[Argo CD Adapter]
    I --> GH[GitHub Adapter]
    I --> KA[Karpenter Adapter]

    K --> G[Awareness Graph]
    P --> G
    L --> G
    O --> G
    T --> G
    AR --> G
    GH --> G
    KA --> G

    G --> SD[Stateful Investigators<br/>PostgreSQL / Kafka / Redis]
    SD --> EV[Evidence Store / Evaluator]
    EV --> H[Deterministic Hypotheses]
    EV --> MEM[Incident Memory<br/>PostgreSQL + pgvector]
    EV --> LLM[Optional LLM<br/>Hypothesis Proposer]

    MEM --> H
    LLM --> H
    H --> RC[Root Cause Selector]
    RC --> RCA[Structured RCA]
    RCA --> API[REST API]
    RCA --> UI[Next.js Investigation UI]
```

## Investigation lifecycle

```mermaid
flowchart TD
    S[Incident received] --> N[Normalize into IncidentInput]
    N --> M[Retrieve similar historical incidents]
    M --> KO[Resolve Kubernetes ownership]
    KO --> SG[Resolve service identity and dependencies]
    SG --> TR[Search representative traces and reconstruct causality]
    TR --> SD[Classify stateful origin and collect dependency-specific metrics]
    SD --> CH[Resolve Argo CD revision and Git metadata]
    CH --> KP[Resolve Karpenter NodeClaim / NodePool]
    KP --> HP[Load deterministic hypotheses]
    HP --> CE[Collect typed read-only evidence]
    CE --> AE[Attach evidence IDs]
    AE --> SC[Score support / contradiction]
    SC --> LP{LLM enabled?}
    LP -->|Yes| LH[Generate additional proposed hypotheses]
    LP -->|No| RC[Select root cause]
    LH --> RC
    RC --> PS[Persist RCA + vector fingerprint]
    PS --> OUT[Return structured RCA]
```

The lifecycle is intentionally evidence-first. Historical incidents and model output can expand context, but neither is allowed to override stronger current telemetry.

## Investigation sequence

```mermaid
sequenceDiagram
    autonumber
    participant AM as Alertmanager/API
    participant ENG as Investigation Engine
    participant K8S as Kubernetes
    participant OBS as Prometheus/Loki/OTel
    participant TEMPO as Tempo/TraceQL
    participant DEP as PostgreSQL/Kafka/Redis
    participant CHG as Argo CD/GitHub
    participant KAR as Karpenter
    participant MEM as pgvector Memory
    participant LLM as Optional LLM
    participant UI as RCA API/UI

    AM->>ENG: Incident payload
    ENG->>MEM: Retrieve similar incidents
    MEM-->>ENG: Similarity hints
    ENG->>K8S: Resolve Pod/RS/Deployment/Node + events
    K8S-->>ENG: Runtime and ownership evidence
    ENG->>OBS: Query metrics/logs/service graph
    OBS-->>ENG: Telemetry evidence
    ENG->>TEMPO: Search error traces + fetch OTLP + RED metrics
    TEMPO-->>ENG: Parent/child chains + origin candidates + RED
    ENG->>DEP: Query typed dependency metrics through Prometheus
    DEP-->>ENG: Current stateful signals + thresholds
    ENG->>CHG: Resolve deployed revision and commit
    CHG-->>ENG: Change evidence
    ENG->>KAR: Resolve NodeClaim/NodePool lifecycle
    KAR-->>ENG: Capacity evidence
    ENG->>ENG: Score deterministic hypotheses
    opt LLM enabled
        ENG->>LLM: Structured context, no credentials/tools
        LLM-->>ENG: Proposed hypotheses + required checks
        ENG->>ENG: Keep status=proposed, score=0 until verified
    end
    ENG->>ENG: Select root cause from verified evidence
    ENG->>MEM: Persist structured RCA + vector
    ENG-->>UI: Evidence-backed RCA
```

## Causal safety hierarchy

```mermaid
flowchart TB
    D[1. Direct causal signal<br/>e.g. kubelet explicitly reports the resource that caused eviction]
    R[2. Correlated runtime signal<br/>e.g. DiskPressure + filesystem exhaustion]
    C[3. Contributing configuration<br/>e.g. missing ephemeral-storage reservations]
    T[4. Temporal correlation<br/>e.g. deployment near incident time]
    H[5. Historical similarity<br/>e.g. old incident looked similar]
    M[6. Model suggestion<br/>plausible but unverified]

    D --> R --> C --> T --> H --> M
```

A lower layer cannot override a higher layer unless new typed evidence promotes it.

## Awareness Graph

The API returns explicit `graph_edges`; the frontend does not need to infer relationships from a flat entity list.

```mermaid
flowchart LR
    WG[web-gateway] -->|calls| LP[landing-page]
    LP -->|calls| CAT[catalog-api]
    LP -->|calls| REDIS[(redis)]

    POD[Pod] -->|owned_by| RS[ReplicaSet]
    RS -->|owned_by| DEP[Deployment]
    POD -->|scheduled_on| NODE[Node]
    DEP -->|managed_by| ARGO[Argo CD Application]
    ARGO -->|syncs_from| GIT[Git Repository / Revision]
    NODE -->|represented_by| NC[NodeClaim]
    NC -->|owned_by| NP[NodePool]

    DEP -. serves .-> LP
```

## Change correlation

```mermaid
flowchart TD
    P[Pod] --> RS[ReplicaSet]
    RS --> D[Deployment]
    D --> A[Argo CD Application]
    A --> R[Deployed Git Revision]
    R --> C[Git Commit Metadata]
    C --> DT[Deployment Timestamp]
    DT --> CMP{Compare with incident start}
    CMP -->|Inside configured window| S[Supporting evidence]
    CMP -->|Outside window| X[Contradicting / weak evidence]
    S --> V[Still requires runtime evidence]
    X --> V
```

Temporal proximity is intentionally weaker than explicit runtime evidence.

## Service graph source

Tempo metrics-generator, Grafana Alloy or an OTel servicegraph processor can emit Prometheus metrics with `client` and `server` labels. The investigator consumes those metrics from the configured Prometheus-compatible backend.

```mermaid
flowchart LR
    APP[Instrumented Services] --> OTEL[OpenTelemetry]
    OTEL --> TEMPO[Tempo / Alloy Metrics Generator]
    TEMPO --> PROM[(Prometheus-compatible Backend)]
    PROM --> RCA[AI SRE RCA Service Graph Adapter]
    RCA --> GRAPH[Awareness Graph]
```

This keeps aggregate topology on typed PromQL queries. Starting in v0.4, the investigator also has **bounded, read-only** Tempo access for per-trace causality.

## Trace-aware causal propagation — v0.4

```mermaid
flowchart LR
    ALERT[Alert + Service] --> SEARCH[TraceQL Search]
    SEARCH --> IDS[Representative Trace IDs]
    IDS --> OTLP[Tempo v2 OTLP JSON]
    OTLP --> TREE[parentSpanId Tree]
    TREE --> ORIGIN[Deepest Failing Span]
    ORIGIN --> MULTI{Same origin across traces?}
    MULTI -->|Yes| CONF[Confirmed Hypothesis]
    MULTI -->|No| TEST[Testing]
    OTLP --> DEP[Virtual DB / Messaging / External Nodes]
    OTLP --> K8S[Service → Deployment Correlation]
    CONF --> GRAPH[Awareness Graph]
    TEST --> GRAPH
```

A trace-only root cause requires the same downstream origin to repeat across independent representative traces. One trace cannot confirm root cause. A previously confirmed Kubernetes RCA also keeps precedence over trace evidence.

Details: [docs/trace-causality.md](trace-causality.md).

## Stateful dependency RCA — v0.5

```mermaid
flowchart LR
    TRACE[Trace Causal Origin] --> META[Typed Dependency Metadata]
    META --> CLASS{Classify}
    CLASS -->|PostgreSQL| PG[Connections / Locks / Deadlocks / Lag]
    CLASS -->|Kafka| KF[Lag / ISR / Leader / Group Members]
    CLASS -->|Redis| RD[Memory / Evictions / Clients / Latency]

    PG --> SCORE[Evidence Scoring]
    KF --> SCORE
    RD --> SCORE

    SCORE --> GATE{Trace origin +<br/>specific anomaly confirmed?}
    GATE -->|No| CTX[Supporting Context]
    GATE -->|Yes| PRE{Stronger infrastructure RCA exists?}
    PRE -->|Yes| KEEP[Keep Existing RCA]
    PRE -->|No| PROMOTE[Promote Specific Stateful RCA]
```

v0.5 does not connect directly to PostgreSQL, Kafka or Redis with administrative credentials. Stateful investigators use allow-listed Prometheus queries and preserve the engine's read-only boundary.

Details: [docs/stateful-dependencies.md](stateful-dependencies.md).

## Incident memory

`SQLRepository` stores the full structured RCA payload, a normalized textual fingerprint and a 128-dimensional vector.

```mermaid
flowchart TD
    I[Resolved Incident] --> FP[Build Normalized Fingerprint]
    FP --> EMB[Local Deterministic Embedding]
    EMB --> PG[(PostgreSQL + pgvector)]
    PG --> HNSW[HNSW vector_cosine_ops index]

    NEW[New Incident] --> NF[Build New Fingerprint]
    NF --> NE[Embedding]
    NE --> Q[Cosine Similarity Query]
    HNSW --> Q
    Q --> TOP[Top Similar Historical Incidents]
    TOP --> HINT[Hints / Context Only]
    HINT --> FRESH[Fresh Telemetry Required]
```

The embedding provider is isolated so a production embedding service can replace the default local implementation later.

## LLM boundary

The optional model is a **hypothesis proposer**, not an infrastructure agent.

```mermaid
flowchart TD
    CTX[Sanitized Incident Context<br/>Entities + Evidence Summaries] --> LLM[LLM]
    LLM --> P[Proposed Hypothesis<br/>origin=llm<br/>status=proposed<br/>score=0]
    P --> RC[Required Observable Checks]
    RC --> MAP[Hypothesis Compiler / Allow-list]
    MAP --> TOOLS[Typed Read-only Tools]
    TOOLS --> E[Fresh Evidence]
    E --> DET[Deterministic Evaluator]
    DET -->|Supported| VERIFIED[Verified Deterministic State]
    DET -->|Insufficient| P

    LLM -. no direct access .-> K8S[No Kubernetes Credentials]
    LLM -. no direct access .-> SHELL[No Shell / SSH]
    LLM -. no direct access .-> MUT[No Write / Mutation API]
```

The LLM cannot query Kubernetes directly, execute PromQL/LogQL itself, run shell commands, attach evidence IDs, modify scores, set `confirmed`, or participate directly in primary root-cause selection.

## Security and remediation boundary

```mermaid
flowchart LR
    INV[Investigator ServiceAccount<br/>GET / LIST / WATCH only] --> SRC[Telemetry + Infra Evidence Sources]
    SRC --> RCA[Evidence-backed RCA]
    RCA --> REC[Recommended Remediation]
    REC --> AP{Explicit Human Approval}
    AP -->|Approved| RID[Separate Remediation Identity]
    RID --> POL[Policy / Allow-list Validation]
    POL --> DRY[Dry-run where supported]
    DRY --> ACT[Execute Narrow Action]
    ACT --> AUDIT[Audit Record + Rollback Metadata]
    AP -->|Rejected| STOP[No Action]

    INV -. cannot mutate .-> ACT
```

## Production hardening

- NetworkPolicy allowlists for Kubernetes API, telemetry, Argo and Git provider.
- Workload identity or short-lived credentials where supported.
- Secret redaction before any model call.
- Evidence payload hashing and immutable audit sink.
- Per-source query timeouts, range limits and cardinality budgets.
- Tenant and cluster isolation.
- Admission policy that rejects write verbs for the investigator ServiceAccount.
- Separate identity and explicit human approval for any future remediation executor.


## Operational plane — v0.6

```mermaid
flowchart LR
    U[User / OIDC Proxy] --> UI[Next.js]
    UI --> API[FastAPI]
    API --> AUTH[OIDC + RBAC]
    AUTH --> TENANT[Tenant + Cluster Scope]
    TENANT --> ENG[Investigation Engine]
    ENG --> QB[Query Budget]
    ENG --> HASH[Evidence SHA-256]
    ENG --> AUDIT[Audit Log]
    ENG --> NOTIFY[Slack / PagerDuty]
    ENG --> EXPORT[Integrity-checked Export]
    QB --> SRC[Read-only Sources]
```

v0.6 adds authentication, tenant/cluster isolation, budgets, retention, audit and operational delivery without changing the investigator's read-only boundary. Full details: [operations.md](operations.md).
