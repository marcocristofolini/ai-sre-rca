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
- `awareness_relationships`: current relationship state, evidence references, first/last seen and observation count;
- `awareness_observations`: immutable per-incident observations for entity or relationship history.

Observation IDs are deterministic from `incident + target`. Re-saving the same incident is therefore idempotent and cannot inflate observation counts.

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

A later v0.8 phase can add reconciliation and stale-state semantics without changing canonical IDs.

## API

~~~text
GET /api/v1/awareness/graph?cluster=<cluster>
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
~~~

The tenant comes from the authenticated principal. A platform principal with `tenant_id="*"` must explicitly provide `tenant_id`; ordinary principals cannot request another tenant. Existing cluster authorization is enforced before graph access.

## Safety invariants

- no infrastructure mutation is introduced;
- no LLM can create persistent graph facts directly;
- only investigation entities and relationships enter the graph;
- unknown edge endpoints are skipped rather than invented;
- tenant and cluster are included in canonical identity and every repository query;
- historical observations are preserved even when current metadata changes.

## Next v0.8 phases

- topology reconciliation and stale/tombstone semantics;
- service-centric graph projections;
- graph-backed blast-radius traversal;
- change/deployment nodes as first-class temporal events;
- agent-planner context retrieval from bounded graph neighborhoods;
- graph health metrics and retention policy;
- UI graph explorer.
