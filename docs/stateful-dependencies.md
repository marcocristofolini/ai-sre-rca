# Stateful dependency RCA — v0.5

[Português do Brasil](stateful-dependencies.pt-BR.md) · **English**

v0.5 extends the trace-aware causal graph into deterministic dependency-specific RCA. A PostgreSQL, Kafka or Redis node detected on a causal trace path can be promoted from a virtual dependency into a typed investigator that validates current runtime evidence through bounded, read-only Prometheus queries.

The design keeps the existing causal hierarchy: direct infrastructure evidence remains strongest, a trace identifies where the failure propagated from, and stateful telemetry explains **why that dependency failed**.

## End-to-end flow

```mermaid
flowchart TD
    I[Incident] --> K8S[Deterministic Infrastructure Playbooks]
    K8S --> TRACE[Tempo / TraceQL Causal Reconstruction]
    TRACE --> DEP[TraceDependency Origin]

    DEP --> META[Dependency Metadata<br/>db.system / db.namespace<br/>messaging.system / destination]
    META --> TYPE{Classify}

    TYPE -->|PostgreSQL| PG[PostgreSQL Investigator]
    TYPE -->|Kafka| KF[Kafka Investigator]
    TYPE -->|Redis / Valkey| RD[Redis Investigator]
    TYPE -->|Unknown| CTX[Keep as Trace Context]

    PG --> PGE[Connections / Locks / Deadlocks<br/>Replication Lag / Long Transactions]
    KF --> KFE[Consumer Lag / Replication<br/>Leader Availability / Group Members]
    RD --> RDE[Memory / Evictions / Blocked Clients<br/>Client Capacity / Command Latency]

    PGE --> SCORE[Evidence Scorer]
    KFE --> SCORE
    RDE --> SCORE

    SCORE --> CONF{Specific hypothesis confirmed?}
    CONF -->|No| SUPPORT[Dependency Context Only]
    CONF -->|Yes| ORIGIN{Dependency is trace origin?}

    ORIGIN -->|No| SUPPORT
    ORIGIN -->|Yes| PRECEDENCE{Existing root cause stronger?}
    PRECEDENCE -->|Kubernetes / infrastructure| KEEP[Keep Existing Root Cause]
    PRECEDENCE -->|Trace-only / inconclusive| PROMOTE[Promote Stateful Root Cause]
```

## Causal hierarchy

```mermaid
flowchart TB
    INFRA[1. Explicit infrastructure causal signal<br/>kubelet / scheduler / controller]
    STATEFUL[2. Stateful anomaly + trace-origin ancestry]
    TRACE_MULTI[3. Repeated trace-origin ancestry]
    TRACE_ONE[4. Single-trace ancestry]
    RED[5. RED / dependency correlation]
    CHANGE[6. Change correlation]
    HISTORY[7. Historical similarity]
    LLM[8. Unverified model proposal]

    INFRA --> STATEFUL --> TRACE_MULTI --> TRACE_ONE --> RED --> CHANGE --> HISTORY --> LLM
```

Stateful evidence can refine a trace-only root cause, but it cannot silently replace a stronger confirmed Kubernetes root cause.

## Dependency discovery

The trace parser preserves a narrow allow-list of semantic attributes:

```text
db.system
db.namespace
db.name
server.address
server.port
peer.service
messaging.system
messaging.destination.name
messaging.destination
messaging.consumer.group.name
messaging.kafka.consumer.group
```

The classifier currently recognizes:

```mermaid
flowchart LR
    ATTR[Trace Dependency Attributes] --> DB{db.system}
    DB -->|postgres / postgresql| PG[postgresql/database]
    DB -->|redis / valkey| RD[redis/cache]

    ATTR --> MSG{messaging.system}
    MSG -->|kafka| KF[kafka/topic]

    ATTR --> OTHER[Unknown / External]
    OTHER --> CONTEXT[No Stateful Promotion]
```

Unknown dependency types remain valid Awareness Graph context and are not guessed into one of the supported adapters.

## PostgreSQL investigator

The PostgreSQL path uses exporter-backed metrics and explicit thresholds.

```mermaid
flowchart TD
    PG[PostgreSQL Trace Origin] --> CONN[Connections / max_connections]
    PG --> LOCKS[Locks / AccessExclusiveLock]
    PG --> DEAD[Deadlocks in Incident Window]
    PG --> LAG[Replication Lag Bytes]
    PG --> TX[Longest Transaction Age]

    CONN --> H1[Connection Exhaustion]
    LOCKS --> H2[Lock Contention]
    DEAD --> H3[Deadlock Failures]
    LAG --> H4[Replication Lag]
    TX --> H5[Long Transaction]

    H1 --> SCORE[Deterministic Scoring]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
    H5 --> SCORE
```

Signals currently queried:

| Signal | Purpose |
| --- | --- |
| sessions / max_connections | detect connection-capacity exhaustion |
| active connections | runtime context |
| idle-in-transaction | pool/transaction leak context |
| total locks | broad lock pressure |
| AccessExclusiveLock | disruptive schema/DDL-style lock pressure |
| deadlock delta | direct deadlock evidence |
| WAL lag bytes | replication-health evidence |
| long transaction age | transaction lifetime / blocking context |

An unavailable optional metric stays `unknown`; it does not count as healthy evidence.

## Kafka investigator

```mermaid
flowchart TD
    K[Kafka Trace Origin] --> LAG[Consumer Lag]
    K --> URP[Under-replicated Partitions]
    K --> OFF[Offline / Leaderless Partitions]
    K --> MEM[Consumer-group Members]

    LAG --> H1[Consumer Backlog]
    URP --> H2[Replication Degradation]
    OFF --> H3[Leader Unavailability]
    MEM --> H4[No Active Consumers]

    H1 --> SCORE[Deterministic Scoring]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
```

The topic and consumer group are derived from trace messaging attributes when available. A missing consumer group does not cause the engine to invent one.

## Redis investigator

```mermaid
flowchart TD
    R[Redis Trace Origin] --> MEM[used_memory / maxmemory]
    R --> EVICT[Evicted Keys Delta]
    R --> BLOCK[Blocked Clients]
    R --> CLIENTS[connected_clients / maxclients]
    R --> LAT[Command Duration / Command Rate]

    MEM --> H1[Memory Pressure]
    EVICT --> H1
    BLOCK --> H2[Blocked Clients]
    CLIENTS --> H3[Client Saturation]
    LAT --> H4[Command Latency]

    H1 --> SCORE[Deterministic Scoring]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
```

Memory pressure and eviction evidence are intentionally evaluated together because eviction without memory context may be expected under some cache policies.

## Evidence promotion

A dependency-specific hypothesis must pass two different tests before it can become the root cause:

```mermaid
flowchart LR
    T[Trace Evidence<br/>dependency is causal origin] --> GATE1{Origin?}
    M[Stateful Metrics<br/>threshold crossed] --> GATE2{Specific anomaly confirmed?}

    GATE1 --> BOTH{Both conditions true?}
    GATE2 --> BOTH

    BOTH -->|No| CONTEXT[Supporting Context]
    BOTH -->|Yes| PRE{Existing stronger RCA?}
    PRE -->|Yes| KEEP[Keep Existing RCA]
    PRE -->|No| PROMOTE[Promote Specific Stateful RCA]
```

This prevents a globally unhealthy database or broker from being blamed for an unrelated incident merely because it appears somewhere in the service graph.

## Query and target budgets

Stateful analysis is bounded by:

```text
STATEFUL_DEPENDENCY_ANALYSIS_ENABLED=true
STATEFUL_DEPENDENCY_MAX_TARGETS=4
DEPENDENCY_WINDOW_MINUTES=15
```

Only a small set of typed Prometheus queries is executed per supported target. There is no generic PromQL planner exposed to an LLM.

## Thresholds

Default thresholds are starting points, not universal production truth. They should be tuned to the deployment, exporter semantics and SLOs.

```text
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
```

## Read-only boundary

```mermaid
flowchart LR
    RCA[AI SRE RCA] --> PROM[Prometheus GET /api/v1/query]
    PROM --> PG[postgres_exporter Metrics]
    PROM --> KF[kafka_exporter Metrics]
    PROM --> RD[redis_exporter Metrics]

    RCA -. no SQL session .-> PGDB[(PostgreSQL)]
    RCA -. no broker admin API .-> KFB[(Kafka)]
    RCA -. no Redis commands .-> RDB[(Redis)]
```

The investigator does not open SQL sessions, alter brokers, execute Redis commands or mutate dependency state. Remediation remains recommendation-only and requires a separate future approval plane.
