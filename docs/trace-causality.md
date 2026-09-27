# Trace-aware causal propagation — v0.4

[Português do Brasil](trace-causality.pt-BR.md) · **English**

v0.4 adds a bounded, read-only Tempo investigation path that turns distributed traces into typed evidence. The objective is not to let a model "reason over traces" freely; the engine deterministically reconstructs span ancestry, identifies the deepest failing operation, compares independent representative traces, and only then promotes trace evidence into a root-cause candidate.

## End-to-end flow

```mermaid
flowchart TD
    A[Alertmanager / Manual Incident] --> S[Resolve Service Identity]
    S --> Q1[TraceQL Search<br/>service + status=error]
    Q1 --> Q2[Fallback TraceQL Search<br/>service only]
    Q1 --> IDS[Bounded Representative Trace IDs]
    Q2 --> IDS

    IDS --> FETCH[Tempo v2 Trace Lookup<br/>OTLP JSON]
    FETCH --> PARSE[Normalize ResourceSpans / ScopeSpans]
    PARSE --> TREE[Build parentSpanId Tree]
    TREE --> ERR[Classify Error Spans]
    ERR --> LEAF[Select Deepest Failing Span<br/>with no errored descendant]

    LEAF --> PEER{Client/Producer with Peer?}
    PEER -->|Yes| VN[Create Virtual Dependency Node<br/>DB / Messaging / External]
    PEER -->|No| ORIGIN[Use Instrumented Service as Origin]
    VN --> PATH[Build Root-to-Origin Causal Path]
    ORIGIN --> PATH

    PATH --> GROUP[Group Candidate Origin<br/>service + operation]
    GROUP --> REPEAT{Repeated in >=2<br/>representative traces?}
    REPEAT -->|Yes| CONF[Confirmed trace-causal hypothesis]
    REPEAT -->|No| TEST[Testing only]

    CONF --> RCA{Existing Stronger RCA?}
    RCA -->|No| ROOT[Promote Trace Root Cause]
    RCA -->|Yes| ENRICH[Keep Existing RCA<br/>attach trace evidence]
    TEST --> ENRICH
```

## Investigation sequence

```mermaid
sequenceDiagram
    autonumber
    participant ENG as Investigation Engine
    participant TEMPO as Tempo Query Frontend
    participant PROM as Prometheus / Service Graph Metrics
    participant TRACE as Trace Analyzer
    participant GRAPH as Awareness Graph
    participant RCA as Root Cause Selector

    ENG->>TEMPO: GET /api/search?q={ service && status=error }
    TEMPO-->>ENG: trace IDs
    loop bounded by TRACE_FETCH_LIMIT
        ENG->>TEMPO: GET /api/v2/traces/:traceID
        TEMPO-->>ENG: standard OTLP JSON
        ENG->>TRACE: parse resource spans + span tree
        TRACE->>TRACE: select deepest failing span
        TRACE-->>ENG: causal chain + candidate origin
    end

    ENG->>TEMPO: TraceQL metrics rate / errors / p95
    TEMPO-->>ENG: RED series
    ENG->>PROM: service-graph request/failure/latency metrics
    PROM-->>ENG: edge RED metrics

    ENG->>GRAPH: add trace_calls + runs_as + dependency nodes
    ENG->>RCA: score repeated origin evidence
    RCA-->>ENG: confirmed / testing
```

## Causal origin selection

The deterministic selector prefers an errored span that has no errored descendant. Among multiple candidates it selects the deepest span in the trace tree, then the earliest start time. This approximates the first downstream failure rather than the most visible upstream symptom.

```mermaid
flowchart LR
    F[frontend<br/>HTTP 500] --> C[checkout<br/>ERROR]
    C --> DB[PostgreSQL client span<br/>ERROR]
    DB --> V[(postgresql/orders)]

    DB -. deepest errored leaf .-> O[Origin Candidate]
    O --> P[Propagated failure path]
    P --> F
```

When a failing client or producer span contains peer/database/messaging attributes, the peer becomes a virtual dependency node. The instrumented client span remains on the path as the upstream propagating service.

## Cross-trace confirmation rule

One trace is useful evidence but not enough for a trace-only root cause.

```mermaid
flowchart TD
    T1[Trace A<br/>postgresql/orders : SELECT orders] --> G[Group by candidate service + operation]
    T2[Trace B<br/>postgresql/orders : SELECT orders] --> G
    T3[Trace C<br/>redis : GET cart] --> G

    G --> C1[postgresql/orders + SELECT orders<br/>2 strong signals]
    G --> C2[redis + GET cart<br/>1 signal]

    C1 --> SCORE[Evidence scoring]
    SCORE --> CONF[Confirmed]
    C2 --> TEST[Testing]
```

The existing evidence scorer still applies. Two independent strong trace-chain evidence items reach the confirmation threshold; one strong trace remains testing.

## RED signals

v0.4 gathers two complementary RED views:

1. **TraceQL metrics** for the investigated service:
   - request/span rate
   - error span rate
   - p95 span duration
2. **Service-graph edge metrics**:
   - requests
   - failures
   - error ratio
   - p95 server-side latency

```mermaid
flowchart LR
    TR[Tempo TraceQL Metrics] --> SVC[Service RED]
    SG[Tempo/Alloy Service Graph Metrics] --> EDGE[Edge RED]
    SVC --> E[Evidence]
    EDGE --> E
    E --> G[Awareness Graph]
```

RED data strengthens context and blast-radius understanding. It does not by itself replace parent/child causal ancestry.

## Trace-to-Deployment correlation

The OTLP resource attributes are retained per service. When present, v0.4 correlates:

- `k8s.deployment.name`
- `k8s.namespace.name`
- `k8s.pod.name`
- `k8s.node.name`
- `service.version`
- `deployment.environment.name`

```mermaid
flowchart LR
    SPAN[Trace Resource Attributes] --> SVC[Service]
    SPAN --> DEP[k8s.deployment.name]
    SPAN --> VER[service.version]
    DEP --> EDGE[Service runs_as Deployment]
    VER --> REV{Matches Argo revision?}
    REV -->|Yes| STRONG[Stronger change correlation]
    REV -->|No / unknown| CONTEXT[Runtime context only]
```

## Safety hierarchy

```mermaid
flowchart TB
    K8S[1. Explicit infrastructure causal signal<br/>e.g. kubelet eviction reason]
    MULTI[2. Repeated trace ancestry<br/>same deepest failure across traces]
    SINGLE[3. Single trace ancestry]
    RED[4. RED anomaly / service graph correlation]
    CHANGE[5. Deployment temporal correlation]
    HISTORY[6. Historical similarity]
    LLM[7. LLM proposal]

    K8S --> MULTI --> SINGLE --> RED --> CHANGE --> HISTORY --> LLM
```

A confirmed Kubernetes playbook is never silently replaced by trace evidence. Trace evidence may enrich the Awareness Graph, hypotheses, timeline and remediation context.

## Bounded read-only queries

The trace adapter only issues GET requests:

- `GET /api/search`
- `GET /api/v2/traces/<traceID>`
- `GET /api/metrics/query_range`

Limits are controlled by:

```text
TRACE_ANALYSIS_ENABLED=true
TRACE_WINDOW_MINUTES=15
TRACE_SEARCH_LIMIT=8
TRACE_FETCH_LIMIT=4
TRACE_SLOW_SPAN_MIN_MS=500
TRACE_SLOW_SPAN_ROOT_FRACTION=0.5
```

There is no Tempo write endpoint, shell access or generic arbitrary-command interface in the investigator.
