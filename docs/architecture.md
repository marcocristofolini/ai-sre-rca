# Architecture — v0.8 development

[Português do Brasil](architecture.pt-BR.md) · **English**

## System overview

```mermaid
flowchart TD
    A[Alertmanager / REST API] --> N[Incident Normalizer]
    N --> EL[Deterministic Eligible Specialists]
    N --> I[Investigation Engine]

    PAG[(Persistent Awareness Graph)] --> CTX[Bounded Service Context]
    CTX --> EL
    EL --> COMP[Server-side Plan Compiler]
    EL -. sanitized eligible context .-> MPL[Optional Advisory LLM]
    MPL -. specialist names + rationale only .-> COMP
    COMP --> PLAN[Bounded Investigation Plan]
    PLAN --> PT[Allow-listed Specialist Tasks<br/>K8s / Metrics / Logs / Traces / DB / Changes]
    PT -->|opt-in| ORCH[Parallel Read-only Specialists]
    ORCH --> SE[Shared Evidence Envelope]
    SE --> AE[Deterministic Agentic Evaluator]
    AE --> XG[Explanation Graph]

    I --> K[Kubernetes Adapter]
    I --> P[Prometheus Adapter]
    I --> L[Loki Adapter]
    I --> T[Tempo / TraceQL]
    I --> AR[Argo CD / GitHub]
    I --> KA[Karpenter]

    K --> LG[Incident-local Graph]
    P --> LG
    L --> LG
    T --> LG
    AR --> LG
    KA --> LG

    LG --> EV[Evidence Evaluator]
    AE -. same deterministic authority rules .-> EV
    LG --> PAG
    PAG --> REC[Fresh / Stale / Historical Reconciliation]
    PAG --> SV[Service Projections]
    PAG --> BR[Blast Radius]
    PAG --> CE[Change Event Timeline]
    REC --> AUI[Next.js Awareness Explorer<br/>Read-only]
    SV --> AUI
    CE --> AUI

    EV --> H[Deterministic Hypotheses]
    EV --> MEM[Incident Memory<br/>PostgreSQL + pgvector]
    EV --> LLM[Optional LLM<br/>Hypothesis Proposer Only]
    MEM --> H
    LLM --> H
    H --> RC[Root Cause Selector]
    RC --> RCA[Structured RCA]
    RCA --> API[REST API]
    RCA --> UI[Next.js Investigation UI]
```



## Model-assisted planning compiler — v0.8

Model assistance is an optional advisory layer, not a new execution authority. The deterministic planner first decides which specialist branches are eligible. Only that sanitized, bounded context is sent to the model. The response is compiled back through server-owned policy before a task graph exists.

```mermaid
flowchart LR
    INC[Incident Identity] --> EL[Deterministic Eligibility]
    GRAPH[Bounded Awareness Context] --> EL
    EL --> DET[Deterministic Plan]
    EL -. sanitized .-> LLM[Optional Advisory LLM]
    LLM -. names + reasons only .-> COMP[Server-side Compiler]
    EL --> COMP
    COMP --> ALLOW[Specialist Allow-list]
    COMP --> BASE[Mandatory Baseline<br/>Kubernetes / Metrics / Traces when eligible]
    COMP --> DEP[Typed Dependencies]
    COMP --> BUD[Server-owned Budgets]
    ALLOW --> PLAN[Compiled InvestigationPlan]
    BASE --> PLAN
    DEP --> PLAN
    BUD --> PLAN
    COMP --> AUDIT[Advisory Trace<br/>requested / accepted / rejected / forced]
```

The model cannot provide a tool name, query, command, budget, dependency or remediation action. Unknown and ineligible names are rejected. Model failure, malformed JSON or advice with no eligible specialist preserves the deterministic plan. The executable plan records whether it was `deterministic` or `model-assisted`.

## Shared Kubernetes playbook stage — v0.8

The classic investigation path and the agentic Kubernetes specialist now call the same deterministic Kubernetes stage. There is no second simplified Kubernetes RCA implementation to drift from the core engine.

```mermaid
flowchart LR
    CLASSIC[Classic Investigation Engine] --> SHARED[Shared Deterministic Kubernetes Stage]
    AGENTIC[Agentic Kubernetes Specialist] --> SHARED

    SHARED --> EVICT[Eviction / Ephemeral Storage]
    SHARED --> OOM[OOMKilled]
    SHARED --> CRASH[CrashLoopBackOff]
    SHARED --> SCHED[FailedScheduling / Karpenter]
    SHARED --> HPA[HPA]
    SHARED --> KEDA[KEDA]
    SHARED --> NODE[NodeNotReady]

    EVICT --> OUT[Canonical Evidence + Playbook Hypotheses]
    OOM --> OUT
    CRASH --> OUT
    SCHED --> OUT
    HPA --> OUT
    KEDA --> OUT
    NODE --> OUT

    OUT --> CORE[Classic Root-cause Selection]
    OUT --> ENV[Agentic Shared Evidence Envelope]
    ENV --> DET[Deterministic Agentic Evaluator]
```

The agentic wrapper adds a dedicated read-only query budget but does not alter playbook scoring, evidence categories, ownership resolution or hypothesis IDs. A budget-exhausted Kubernetes specialist fails closed and cannot be treated as clean infrastructure context.
The root-priority policy is shared as well: when multiple Kubernetes hypotheses are confirmed, the agentic evaluator applies the same scenario-specific specificity ordering used by the classic selector. Any candidate from a non-completed specialist is blocked before selection.

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
    RCA --> PLAN[Proposed Remediation Plan]
    PLAN --> POL[Policy / Allow-list Validation]
    POL --> DRY[Kubernetes dryRun=All]
    DRY --> AP{Explicit Human Approval}
    AP -->|Approved| RID[Separate Remediation Identity]
    RID --> DRIFT{Target state unchanged?}
    DRIFT -->|Yes| ACT[Execute Narrow Action]
    DRIFT -->|No| BLOCK[Block and require fresh plan]
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
- Separate remediator identity, policy gates, dry-run and explicit human approval for every mutating action.


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


## Guarded remediation plane — v0.7

v0.7 implements the remediation boundary as a separate runtime. The investigator has no remediation routes and retains its read-only Kubernetes identity. The remediator is independently deployable and currently exposes only a typed Deployment scale operation.

The remediator uses a separate cluster credential registry, namespace-scoped Helm Roles with get/patch on deployments/scale, server-side dry-run, exact remediator-role approval, four-eyes separation, plan expiration, resourceVersion drift detection and explicit rollback.

Full details: [remediation.md](remediation.md).


## Persistent Awareness Graph and topology services — v0.8

The incident-local graph is now canonicalized and persisted so operational topology can accumulate across investigations without letting local IDs leak across scopes.

```mermaid
flowchart LR
    IR[InvestigationResult<br/>entities + graph_edges] --> CAN[Canonicalizer]
    CAN --> CE[(awareness_entities<br/>current state)]
    CAN --> CR[(awareness_relationships<br/>current state)]
    CAN --> AO[(awareness_observations<br/>append-only history)]

    CE --> REC[Fresh / Stale Reconciliation]
    CR --> REC
    AO --> HIST[Target History API]

    REC --> SP[Service-centric Projection]
    REC --> BR[Blast Radius]
    SP --> PC[Planner Context]
    BR --> API8[v0.8 Awareness APIs]
```

Canonical entity identity is derived from `tenant + cluster + kind + namespace + name`. Relationship identity is derived from the scoped canonical endpoints plus relationship type. Re-saving the same incident is idempotent.

### Reconciliation does not invent deletion

```mermaid
flowchart TD
    LS[last_seen] --> CMP{Older than stale threshold?}
    CMP -->|No| F[Fresh]
    CMP -->|Yes| S[Stale]
    S --> KEEP[Keep current record + history]
    KEEP --> WAIT[Wait for authoritative inventory source]
    WAIT -. future .-> T[Tombstone / deletion]
```

Incident observations are partial. A resource missing from a later RCA is therefore not treated as deleted.

### Service projection

```mermaid
flowchart LR
    SVC[Canonical Service] --> N1[Depth 1 Neighbors]
    N1 --> N2[Depth 2 Neighbors]
    N2 --> NX[Up to configured depth]

    B1[Max depth] --> SVC
    B2[Max nodes] --> SVC
    B3[Max edges] --> SVC

    SVC --> OUT[Bounded Service Topology]
    N1 --> OUT
    N2 --> OUT
    NX --> OUT
```

The lookup is exact. If the same service name exists in multiple namespaces, a namespace is required rather than selecting an arbitrary node.

### Causal blast radius

Stored graph direction and impact direction are intentionally separate.

```mermaid
flowchart LR
    FE[frontend] -->|calls| CO[checkout]
    CO -->|calls| PG[(postgres)]
    DEP[checkout Deployment] -->|serves| CO

    PG -. impact: reverse calls .-> CO
    CO -. impact: reverse calls .-> FE
    DEP -. impact: forward serves .-> CO
```

Known relationship types are compiled through an explicit impact-direction allow-list. Unknown relationships are returned by topology APIs but never traversed for blast radius. Stale relationships are historical context only and are skipped during current-impact propagation.

## Bounded Investigation Planner — v0.8

The planner is deterministic and bounded. It uses incident identity, feature flags and a bounded service neighborhood to compile typed specialist branches. Parallel specialist execution is implemented as an opt-in read-only runtime and is disabled by default.

```mermaid
flowchart TD
    INC[IncidentInput] --> PLAN[Deterministic Planner]
    PAG[Persistent Awareness Graph] --> CTX[Bounded 1-hop Context]
    CTX --> PLAN
    CFG[Server Budgets + Feature Flags] --> PLAN

    PLAN --> K[Kubernetes Task]
    PLAN --> M[Metrics Task]
    PLAN --> L[Logs Task]
    PLAN --> T[Traces Task]
    T --> DB[Database / Stateful Task]
    PLAN --> CH[Changes Task]

    K --> IP[InvestigationPlan]
    M --> IP
    L --> IP
    T --> IP
    DB --> IP
    CH --> IP

    IP -->|AGENTIC_EXECUTION_ENABLED=true| ORCH[Parallel Specialist Execution]
    ORCH --> SR[Typed Specialist Results]
    SR --> ENV[Shared Evidence Envelope]
    ENV --> HASH{Integrity verified?}
    HASH -->|No| INC[Inconclusive]
    HASH -->|Yes| SCORE[Re-score Playbook Hypotheses]
    SCORE --> CAUSAL[Trace / Stateful Causal Gates]
    CAUSAL --> PRE[Source Precedence]
    PRE --> EV[Deterministic Verdict]
    EV --> EX[Rejected-hypothesis Explanation Graph]
```

Planner safety properties:

- only six allow-listed specialist task kinds exist;
- every task is read-only;
- task count, graph context, parallelism and per-specialist query budget are server bounded;
- database/stateful work depends on the trace task so dependency identity is typed before dependency telemetry is queried;
- the planner cannot confirm hypotheses, attach evidence, invoke mutation or bypass the separate remediation plane;
- the execution endpoint recompiles the plan server-side, so caller-authored task graphs are never executed;
- specialist hypotheses are advisory-only and the shared execution response has no root-cause field;
- local specialist budgets and the existing global query budget both apply;
- evidence hashes are verified before evidence is accepted into the shared envelope;
- the evaluator re-scores playbook hypotheses from integrity-verified evidence instead of trusting specialist-local status;
- missing support or contradiction references fail closed and block root-cause selection;
- trace-only selection still requires repeated origin across independent representative traces;
- stateful selection still requires trace-origin identity plus dependency-specific current telemetry;
- direct Kubernetes failure signals block downstream trace/stateful promotion;
- a planned higher-precedence Kubernetes specialist must complete before downstream trace/stateful candidates can be selected;
- `/execute` still returns no root cause; only `/investigate` can return a deterministic verdict;
- agentic execution is disabled by default.

Full details: [persistent-awareness-graph.md](persistent-awareness-graph.md) and [agentic-investigation.md](agentic-investigation.md).


## Deterministic shared-envelope evaluator — v0.8

The evaluator is a separate authority boundary after specialist execution.

```mermaid
flowchart TD
    ENV[Shared Evidence Envelope] --> I{All evidence hashes valid?}
    I -->|No| FAIL[Fail Closed: Inconclusive]
    I -->|Yes| REF[Validate Hypothesis Evidence References]
    REF --> SCORE[Recalculate Weighted Score]
    SCORE --> TG{Trace-only Candidate?}
    TG -->|Yes| TR[Require Repeated Origin Across 2+ Traces]
    TG -->|No| SG{Stateful Candidate?}
    SG -->|Yes| ST[Require causal_role=origin + Trace Evidence + Dependency Telemetry]
    SG -->|No| PRE[Apply Source Precedence]
    TR --> PRE
    ST --> PRE
    PRE --> K{Direct Kubernetes Failure Signal?}
    K -->|Yes| BLOCK[Block Downstream Promotion]
    K -->|No| PICK[Select Highest-precedence Confirmed Hypothesis]
    PICK --> ROOT[Deterministic Root Cause]
    BLOCK --> INC[Inconclusive Until Higher-precedence Playbook Exists]
    ROOT --> EX[Explanation Graph]
    INC --> EX
```

The explanation graph makes negative reasoning explicit. Evidence nodes connect to hypotheses with `supports` or `contradicts`; gates connect with `blocks`; hypotheses connect to the verdict with `selected` or `considered`.

This stage remains read-only and cannot invoke remediation. It does not trust a specialist's local `confirmed` status. It independently recalculates score and gate eligibility from the integrity-verified shared envelope.


## First-class deployment/change events — v0.8

Deployment changes are persisted as immutable `ChangeEvent` nodes derived only from collected Argo CD `change-correlation` evidence. Matching GitHub `source-change` evidence enriches the same event with commit metadata.

```mermaid
flowchart LR
    AR[Argo CD change-correlation evidence] --> CE[ChangeEvent]
    GH[GitHub source-change evidence] --> CE

    CE -->|observed_on| APP[ArgoCDApplication]
    CE -->|targets| DEP[Deployment]
    CE -->|targets_service| SVC[Service]
    CE -->|revision_from| REPO[GitRepository]

    CE -. temporal context only .-> CTX[Incident / planner context]
    CE -. never auto-causal .-> NO[Not in blast-radius impact allow-list]
```

A ChangeEvent has stable identity from `application + revision + occurred_at`. Reobserving the same deployment from another incident increments its observation count instead of creating a duplicate event.

Change events and their event relationships use `temporal_role=event` and reconcile as `historical`, not `fresh` or `stale`. This distinction is deliberate: topology can become stale, while a deployment that happened in the past remains a valid historical fact.

The bounded temporal API is:

```text
GET /api/v1/awareness/changes?cluster=<cluster>&service=<service>&namespace=<namespace>&since=<iso8601>&until=<iso8601>&limit=100
```

The endpoint remains tenant/cluster scoped and returns at most 200 events. Temporal proximity is context only and never upgrades a deployment/source change into root cause without runtime causal evidence.
