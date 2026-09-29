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
- `awareness_observations`: immutable entity/relationship history plus inventory-snapshot receipts used to enforce replay identity.

Incident observation IDs are deterministic from `incident + target`. Authoritative inventory observations use `source + snapshot + target + action`. Replaying the same evidence is therefore idempotent and cannot inflate observation counts. Older metadata and evidence references remain in `awareness_observations`; the current entity/relationship record reflects the newest valid observation. Entity and relationship records also carry explicit tombstone timestamp, source and snapshot identity when a complete authoritative inventory proves that a previously known fact is no longer current.

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

v0.8 classifies persistent topology entities/relationships as `fresh`, `stale` or `tombstoned`. Fresh/stale comes from `last_seen` and a server-controlled threshold. Immutable temporal facts such as `ChangeEvent` nodes reconcile as `historical` instead.

~~~mermaid
flowchart LR
    INC[Partial Incident Observation] --> CUR[Current Graph State]
    CUR --> AGE[Compare last_seen with threshold]
    AGE -->|inside threshold| F[Fresh]
    AGE -->|older than threshold| S[Stale]
    S --> KEEP[Keep fact, infer no deletion]

    INV[Complete Authoritative Inventory] --> SCOPE[Validate source + declared coverage]
    SCOPE --> PRESENT[Refresh facts present in snapshot]
    SCOPE --> ABSENT[Covered fact absent from snapshot]
    ABSENT -->|last_seen <= snapshot time| T[Tombstoned]
    ABSENT -->|newer graph observation exists| SKIP[Preserve newer fact]
~~~

Absence from an incident is never deletion evidence. A tombstone can only come from `POST /api/v1/awareness/inventory/reconcile`, requires the admin role, a source in `AWARENESS_AUTHORITATIVE_INVENTORY_SOURCES`, `complete=true`, and explicit kind/namespace/relationship coverage. Resources outside that declared coverage are untouched. ChangeEvent nodes and event relationships are excluded from inventory tombstoning.

A newer incident or authoritative inventory observation can reactivate a tombstoned fact. Older snapshots can never overwrite or delete a newer graph observation. The tuple `tenant + cluster + source + snapshot_id` is immutable: replaying identical content is idempotent, while reusing that identity with different content is rejected as a conflict. Tombstoned facts remain queryable in the graph/history for explanation, but current entity resolution and blast-radius traversal exclude them.

## Automatic Kubernetes inventory source

The first automated authoritative source is a separate one-shot Kubernetes inventory worker deployed by Helm as an optional `CronJob`. It is intentionally not embedded in the API process: API horizontal scaling therefore cannot multiply inventory loops, and `concurrencyPolicy: Forbid` prevents overlapping jobs from the same release.

The worker performs only Kubernetes GET/LIST operations using the investigator's read-only ServiceAccount. Supported first-pass kinds are `Service`, `Deployment`, `StatefulSet`, `DaemonSet`, `ReplicaSet`, `Pod` and `Node`. Pagination follows the Kubernetes `metadata.continue` token until completion and fails closed if a page contains malformed items or repeats a continuation token. Any HTTP/list/payload failure aborts the collection before a snapshot reaches reconciliation.

Relationships are emitted only from explicit Kubernetes state:

- `owned_by` from one controller `ownerReference` only when its UID matches the currently observed owner object;
- `scheduled_on` from `Pod.spec.nodeName`, only when the snapshot is cluster-wide and includes Nodes;
- `serves` from a non-empty Service selector matching the Pod-template labels of a Deployment, StatefulSet or DaemonSet, including scale-to-zero workloads.

Namespace-scoped collection deliberately does not assert Pod-to-Node deletion semantics because Node is cluster-scoped. In that mode the worker emits separate authoritative scopes for namespaced resources and Nodes. Cluster-wide collection can safely reconcile cross-scope scheduling edges because both endpoint inventories are observed in the same complete collection.

~~~mermaid
flowchart LR
    CRON[Helm CronJob<br/>concurrencyPolicy: Forbid] --> GET[Kubernetes GET/LIST only]
    GET --> PAGE[Paginate until metadata.continue is empty]
    PAGE --> VALID{Valid objects + token progresses?}
    VALID -->|No| ABORT[Abort run<br/>no reconciliation]
    VALID -->|Yes| COMPLETE{Every configured list complete?}
    COMPLETE -->|No| ABORT
    COMPLETE -->|Yes| BUILD[Build explicit resources + relationships]
    BUILD --> SNAP[Complete authoritative snapshot]
    SNAP --> REC[Existing inventory reconciliation contract]
    REC --> PAG[(Persistent Awareness Graph)]
    REC --> AUDIT[(Audit history)]
~~~

## Graph health

`GET /api/v1/awareness/health?cluster=<cluster>` exposes a factual tenant/cluster-scoped projection of the persisted graph. It reports current, stale, historical and tombstoned counts independently for entities and relationships, the total/latest awareness observation, and freshness for every configured authoritative inventory source.

Inventory-source freshness is based on the latest immutable `inventory_snapshot` receipt. `AWARENESS_INVENTORY_MAX_AGE_MINUTES` defaults to 15 minutes and is intentionally independent from `AWARENESS_STALE_AFTER_MINUTES`, which governs graph-fact freshness. A source is `missing` when no authoritative receipt exists, `current` while the latest receipt remains inside the configured age, and `stale` after that boundary.

The endpoint is viewer-authorized and preserves the existing tenant/cluster isolation boundary. It intentionally does not collapse these facts into a composite graph-health score: consumers receive the observable state and can alert on the dimensions relevant to their environment.

~~~mermaid
flowchart LR
    E[Entities / Relationships] --> C[State Counts]
    O[Append-only Observations] --> L[Latest Observation]
    O --> R[Latest Inventory Receipt by Source]
    C --> H[Graph Health API]
    L --> H
    R --> H
~~~

## Explanation-safe retention

Awareness retention is deliberately separate from incident/audit retention because inventory receipts and graph-history observations have stronger identity and explanation requirements.

`POST /api/v1/awareness/retention/purge?cluster=<cluster>` requires the admin role and defaults to `dry_run=true`. Two independent windows control eligibility:

- `AWARENESS_OBSERVATION_RETENTION_DAYS=90` for redundant entity/relationship observations;
- `AWARENESS_TOMBSTONE_RETENTION_DAYS=30` for tombstoned current-state rows.

Safety rules are stricter than a simple age-based delete:

- every `inventory_snapshot` receipt is preserved, so snapshot replay/idempotency and conflicting-content detection remain intact;
- the latest entity/relationship observation for every target is preserved even when older than the retention window;
- expired tombstoned relationships are removed before expired tombstoned entities;
- a tombstoned entity is removed only when no surviving relationship still references it;
- dry-run performs no deletion and emits no purge audit event;
- an executed purge is audited in the same repository transaction as the deletion.

~~~mermaid
flowchart TD
    AGE[Rows older than retention cutoff] --> TYPE{Record type}
    TYPE -->|inventory_snapshot| KEEP1[Always preserve]
    TYPE -->|entity/relationship observation| LAST{Latest for target?}
    LAST -->|Yes| KEEP2[Preserve explanation minimum]
    LAST -->|No| DEL[Eligible for deletion]

    TREL[Expired tombstoned relationship] --> DELREL[Delete relationship]
    TENT[Expired tombstoned entity] --> REF{Any surviving relationship?}
    REF -->|Yes| BLOCK[Keep entity]
    REF -->|No| DELENT[Delete entity]
~~~

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

Unknown relationship types remain visible in topology responses but are never traversed as impact paths. Relationships classified as `stale` or `tombstoned` are excluded from current blast-radius propagation. A tombstoned entity cannot be used as a current blast-radius source, and tombstoned targets are not promoted as current impacts.

## API

~~~text
GET /api/v1/awareness/graph?cluster=<cluster>
GET /api/v1/awareness/health?cluster=<cluster>
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
GET /api/v1/awareness/topology?cluster=<cluster>
GET /api/v1/awareness/services/<service_name>?cluster=<cluster>&namespace=<ns>&depth=2
GET /api/v1/awareness/blast-radius/<target_id>?cluster=<cluster>&depth=3
POST /api/v1/awareness/inventory/reconcile?cluster=<cluster>
POST /api/v1/awareness/retention/purge?cluster=<cluster>&dry_run=true
~~~

The tenant comes from the authenticated principal. A platform principal with `tenant_id="*"` must explicitly provide `tenant_id`; ordinary principals cannot request another tenant. Existing cluster authorization is enforced before graph access. Inventory reconciliation additionally requires the admin role and an allow-listed authoritative source. Every accepted reconciliation is written to the audit log with its scope and tombstone counts.

Default server-side bounds are `depth=4`, `nodes=200`, `edges=800`, with a 24-hour stale threshold. They are configurable through Helm/environment settings.

## Safety invariants

- no infrastructure mutation is introduced;
- no LLM can create persistent graph facts directly;
- incident evidence can add observations, while deletion state can only be changed by a complete allow-listed authoritative inventory snapshot;
- unknown edge endpoints are skipped rather than invented;
- tenant and cluster are included in canonical identity and every repository query;
- historical observations are preserved even when current metadata changes;
- incident absence never creates tombstones;
- snapshot coverage is explicit and fail-closed;
- older snapshots cannot tombstone newer observations;
- ChangeEvents are historical facts and are never inventory-tombstoned.

## Next v0.8 phases

Completed in the current development branch:

- freshness reconciliation without unsafe tombstone inference;
- service-centric bounded graph projections;
- causal-direction-aware blast-radius traversal;
- bounded graph context for the Investigation Planner;
- deterministic shared-envelope evaluation and rejected-hypothesis explanation graph;
- read-only Awareness Explorer for reconciled topology, service projections and ChangeEvent history;
- authoritative complete-inventory reconciliation with first-class tombstones, audit records and reactivation semantics.

Still planned:

- authoritative change-event enrichment from additional delivery systems beyond Argo CD/GitHub;
- scheduled awareness-retention execution independent from API startup;
- richer graph context for Kubernetes specialist parity and deployment/change event reasoning.


## Read-only Awareness Explorer

The Next.js route `/awareness` consumes only viewer APIs. It can scope by cluster, namespace and service, renders reconciled `fresh` / `stale` / `historical` / `tombstoned` entity state, shows persisted relationships, and places ChangeEvents on a bounded historical timeline. Service filters switch the topology read to the existing bounded service projection endpoint rather than implementing graph traversal in the browser.

The explorer forwards the same authorization headers as the existing UI and exposes an optional tenant override only for principals that already have global tenant scope. It has no mutation controls and no graph-write endpoint.

~~~mermaid
flowchart LR
    UI[Awareness Explorer] --> TOP[GET /awareness/topology]
    UI --> SVC[GET /awareness/services/service]
    UI --> CH[GET /awareness/changes]
    TOP --> PAG[(Persistent Awareness Graph)]
    SVC --> PAG
    CH --> PAG
~~~

## First-class change events

Collected Argo CD deployment evidence is projected into stable `ChangeEvent` graph nodes. The same event is identified by application, revision and event time, so repeated observation across incidents increments history rather than duplicating the event.

Event metadata includes occurrence time, application, revision, repository, target service/workload and supporting evidence IDs. Matching GitHub source evidence adds commit SHA/message/author/link.

Event edges are intentionally contextual:

- `observed_on` → ArgoCDApplication
- `targets` → Deployment
- `targets_service` → Service
- `revision_from` → GitRepository

These relationships are not present in the blast-radius impact-direction allow-list. A recent deployment remains correlation/context until runtime evidence satisfies a deterministic causal hypothesis.

Event materialization is fail-closed. `application`, `revision` and an authoritative Argo CD operation timestamp are required; malformed or missing values remain correlation evidence only. Deployment, Service and GitRepository links are attached only through explicit, unambiguous `managed_by`, `serves` and `syncs_from` relationships already present in the investigation graph. The implementation never picks the first resource in a namespace as identity evidence.

Temporal lookup:

```text
GET /api/v1/awareness/changes
```

Filters: `cluster` (required), `service`, `namespace`, `since`, `until`, `tenant_id` for a global principal, and bounded `limit`.
