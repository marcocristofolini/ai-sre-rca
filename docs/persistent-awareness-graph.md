# Persistent Awareness Graph — v0.8 foundation

[Português do Brasil](persistent-awareness-graph.pt-BR.md) · **English**

v0.8 starts the transition from an incident-local topology into persistent operational knowledge. The graph is populated only from evidence already collected by an investigation. It does not give the LLM a generic graph-write primitive.

## Goals

- stable logical identity across independent incidents;
- strict tenant and cluster isolation;
- current graph state plus append-only temporal observations;
- evidence-linked relationships;
- idempotent ingestion when an incident is persisted again;
- a query surface that future blast-radius, memory and agent-planning layers can consume.

## Canonical identity

Incident-local IDs such as `pod` and `deployment` are deliberately not persisted as global identity. An entity ID is derived from:

`tenant + cluster + kind + namespace + name`

A relationship ID is derived from:

`tenant + cluster + canonical source + relationship + canonical target`
Both IDs use SHA-256-derived stable identifiers. Tenant and cluster are part of the identity boundary, so identical resource names in different scopes cannot collapse into one node.

## Temporal model

The graph stores three record families:

- `awareness_entities`: current logical entity state, first/last seen timestamps and observation count;
- `awareness_relationships`: current relationship state, evidence references from the latest observation, first/last seen and observation count;
- `awareness_observations`: immutable per-incident observations for entity or relationship history.

Observation IDs are deterministic from `incident + target`. Re-saving the same incident is therefore idempotent and cannot inflate observation counts. Older metadata and evidence references remain in `awareness_observations`; the current entity/relationship record reflects the newest observation.

~~~mermaid
flowchart LR
    RCA[Persisted Investigation] --> CAN[Canonicalize local graph]
    CAN --> E[Awareness Entities]
    CAN --> R[Awareness Relationships]
    E --> O[Temporal Observations]
    R --> O
    E --> Q[Persistent Graph Query]
    R --> Q
    O --> H[Entity / Relationship History]
~~~
## Persistence boundary

Graph ingestion happens inside the repository save boundary. With PostgreSQL, the investigation payload and its awareness updates are committed in the same repository transaction. The in-memory repository implements the same contract for tests and demo mode.

## Topology reconciliation

v0.8 now classifies persistent entities and relationships as `fresh` or `stale` from `last_seen` and a server-controlled threshold.

~~~mermaid
flowchart LR
    CUR[Current Graph State] --> AGE[Compare last_seen with threshold]
    AGE -->|inside threshold| F[Fresh]
    AGE -->|older than threshold| S[Stale]
    OBS[Append-only Observations] --> CUR
    S --> KEEP[Keep historical node/edge]
    KEEP --> NO[No inferred deletion]
~~~

The system deliberately does **not** infer deletion or a tombstone from absence in a later incident. Incident-derived observations are partial, not authoritative cluster inventory snapshots. True tombstones require a future full-inventory reconciliation source.

## Service-centric projections

A service projection resolves the exact canonical `Service` entity and walks an undirected topology neighborhood under hard depth/node/edge budgets. If the same service name exists in multiple namespaces, the caller must provide a namespace rather than receiving an arbitrary match.

## Blast radius

Blast radius is not a generic graph traversal. Relationship types have explicit impact direction:

| Relationship | Stored direction | Impact direction |
| --- | --- | --- |
| `calls` / `depends_on` | caller → dependency | dependency → caller |
| `scheduled_on` | workload → node | node → workload |
| `owned_by` | child → owner | owner → child |
| `managed_by` | workload → manager | manager → workload |
| `syncs_from` | app → revision/source | source → app |
| `represented_by` | resource → controller object | controller object → resource |
| `runs_as` | service → workload | workload → service |
| `serves` | workload → service | workload → service |

Unknown relationship types remain visible in topology responses but are never traversed as impact paths. Relationships classified as `stale` are also excluded from current blast-radius propagation and are returned separately as skipped stale relationship IDs.

## API

~~~text
GET /api/v1/awareness/graph?cluster=<cluster>
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
GET /api/v1/awareness/topology?cluster=<cluster>
GET /api/v1/awareness/services/<service_name>?cluster=<cluster>&namespace=<ns>&depth=2
GET /api/v1/awareness/blast-radius/<target_id>?cluster=<cluster>&depth=3
~~~

The tenant comes from the authenticated principal. A platform principal with `tenant_id="*"` must explicitly provide `tenant_id`; ordinary principals cannot request another tenant. Existing cluster authorization is enforced before graph access.

Default server-side bounds are `depth=4`, `nodes=200`, `edges=800`, with a 24-hour stale threshold. They are configurable through Helm/environment settings.

## Safety invariants

- no infrastructure mutation is introduced;
- no LLM can create persistent graph facts directly;
- only investigation entities and relationships enter the graph;
- unknown edge endpoints are skipped rather than invented;
- tenant and cluster are included in canonical identity and every repository query;
- historical observations are preserved even when current metadata changes.

## Next v0.8 phases

Completed in the current development branch:

- freshness reconciliation without unsafe tombstone inference;
- service-centric bounded graph projections;
- causal-direction-aware blast-radius traversal;
- bounded graph context for the Investigation Planner;
- deterministic shared-envelope evaluation and rejected-hypothesis explanation graph.

Still planned:

- authoritative tombstones from full inventory reconciliation;
- change/deployment nodes as first-class temporal events;
- graph health metrics and retention policy;
- UI graph explorer;
- richer graph context for Kubernetes specialist parity and deployment/change event reasoning.
