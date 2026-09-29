# Roadmap — v0.8 in progress

## Completed in v0.2

- [x] Kubernetes pod eviction / ephemeral-storage RCA
- [x] explicit Awareness Graph edges
- [x] OpenTelemetry/Tempo service graph via Prometheus metrics
- [x] Argo CD revision/time correlation
- [x] GitHub commit lookup
- [x] Karpenter NodeClaim/NodePool correlation
- [x] PostgreSQL + pgvector incident memory
- [x] HNSW cosine index
- [x] optional LLM hypothesis proposer with no tool access
- [x] safety tests preventing LLM-proposed hypotheses from becoming root cause

## Completed in v0.3 — broader Kubernetes RCA

- [x] OOMKilled / memory-limit correlation
- [x] CrashLoopBackOff with OOM, probe and non-zero exit differentiation
- [x] FailedScheduling resource / taint / affinity / volume classification
- [x] NodeNotReady / unreachable node investigation
- [x] HPA maxReplicas saturation and controller-condition checks
- [x] KEDA Ready / Active / pause / fallback evidence model
- [x] Karpenter insufficient-capacity provisioning correlation
- [x] Karpenter NodeClaim lifecycle evidence in NodeNotReady investigations
- [x] read-only autoscaling discovery through typed Kubernetes API calls
- [x] regression coverage for all v0.3 deterministic scenarios

## Completed in v0.4 — trace-aware causal propagation

- [x] Tempo/TraceQL read-only adapter
- [x] representative trace IDs around the incident window
- [x] standard OTLP JSON trace parsing
- [x] parent/child span ancestry reconstruction
- [x] deepest failing span selection
- [x] repeated-origin confirmation across independent traces
- [x] upstream/downstream error propagation
- [x] TraceQL RED metrics: rate, error and p95 duration
- [x] service-graph edge RED metrics: requests, failures, error ratio and p95
- [x] virtual database/messaging/external dependency nodes
- [x] trace-to-deployment correlation from OTel resource attributes
- [x] Kubernetes-RCA precedence guardrail
- [x] trace-aware UI and demo route
- [x] v0.4 regression coverage

## Completed in v0.5 — stateful dependency RCA

- [x] typed dependency metadata preserved from distributed traces
- [x] PostgreSQL adapter for connection exhaustion, locks, deadlocks, replication lag and long-running transactions
- [x] Kafka adapter for consumer lag, under-replicated partitions, leaderless/offline partitions and consumer-group membership
- [x] Redis adapter for memory pressure, evictions, blocked clients, client saturation and command latency
- [x] dependency-specific deterministic hypotheses and remediations
- [x] causal promotion from trace dependency origin to dependency-specific RCA
- [x] infrastructure/Kubernetes RCA precedence over stateful dependency anomalies
- [x] bounded stateful target budget and configurable thresholds
- [x] missing dependency metrics remain unknown rather than being inferred healthy
- [x] stateful API payload and UI
- [x] PostgreSQL, Kafka and Redis demo routes
- [x] v0.5 regression coverage

## v0.5.x — dependency depth

- [ ] PostgreSQL blocker/blockee graph
- [ ] PostgreSQL pg_stat_statements / query fingerprint correlation
- [ ] PostgreSQL exporter-version capability discovery
- [ ] Kafka broker/JMX throughput and request-queue context
- [ ] Kafka rebalance and partition leader/ISR timelines
- [ ] Redis slowlog correlation
- [ ] Redis hot-key and keyspace/TTL analysis with cardinality budgets
- [ ] direct dependency-alert promotion without a trace when equivalent causal evidence exists
- [ ] dependency-specific dynamic baselines instead of threshold-only evaluation

## Completed in v0.6 — operational productization

- [x] Slack/PagerDuty delivery
- [x] evidence SHA-256 integrity and audit log
- [x] opt-in NetworkPolicy templates
- [x] OIDC/RBAC for UI/API
- [x] tenant-scoped incident memory, audit and export
- [x] multi-cluster integration routing with cluster claim authorization
- [x] per-incident query budget/cost controls
- [x] incident and audit retention policies
- [x] per-source health/capability reporting
- [x] incident export / sharing format
- [x] Alertmanager machine-token authentication
- [x] operational /ops UI
- [x] external Kubernetes Secret injection for sensitive environment variables

## Completed in v0.6.1 — operational isolation hardening

- [x] tenant-scoped incident lookup at the persistence boundary
- [x] cross-tenant incident identifiers remain not-found rather than disclosing existence
- [x] tenant-scoped retention purge with explicit wildcard platform scope
- [x] fail-closed validation for malformed multi-cluster registry configuration
- [x] regression coverage for isolation and cluster-registry validation

## v0.6.x — operational depth

- [ ] OIDC discovery URL support in addition to explicit JWKS URL
- [ ] external immutable audit sink / SIEM delivery
- [ ] signed export bundles using KMS/workload identity
- [ ] per-tenant notification routing
- [ ] per-cluster Prometheus label mapping for exporter identity aliases
- [ ] scheduled retention job independent from process restarts
- [ ] capability history and alerting

## Completed in v0.7 — guarded remediation plane

Separate service and identity:

- [x] persisted proposed action plan
- [x] static and runtime policy validation
- [x] Kubernetes server-side dry-run
- [x] explicit human approval and rejection
- [x] exact remediator OIDC role
- [x] four-eyes requester/approver separation
- [x] plan TTL / expiration
- [x] narrow per-action credentials
- [x] first action restricted to deployments/scale
- [x] resourceVersion and replica drift protection
- [x] rollback plan and audit record
- [x] tenant/cluster-scoped plan access
- [x] separate Helm Deployment and ServiceAccount
- [x] namespace-scoped get/patch Role for deployments/scale
- [x] investigator remains independently deployable and read-only
- [x] v0.7 regression coverage

## v0.7.x — remediation depth

- [ ] policy bundles per tenant/cluster
- [ ] signed approval evidence
- [ ] post-action health verification
- [ ] rollback proposal generation from failed verification
- [ ] additional typed actions only when they can preserve the same narrow-credential model
- [ ] external immutable remediation audit sink

## v0.8 — persistent awareness and agentic foundation

Persistent Awareness Graph:

- [x] stable canonical entity identity across incidents
- [x] stable canonical relationship identity
- [x] tenant- and cluster-scoped graph persistence
- [x] current entity/relationship state with first_seen and last_seen
- [x] append-only per-incident temporal observations
- [x] idempotent graph ingestion on incident re-save
- [x] evidence references preserved on persistent relationships
- [x] bounded graph and history query API
- [x] PostgreSQL and in-memory repository parity
- [x] regression tests for identity, temporal history and isolation
- [x] freshness reconciliation from last_seen with no unsafe tombstone inference
- [x] service-centric bounded neighborhood projections
- [x] graph-backed blast-radius traversal with explicit impact direction
- [x] unknown and stale relationship types/instances excluded from current impact traversal
- [ ] authoritative tombstones from full inventory reconciliation
- [x] first-class deployment/change event nodes
- [x] stable deployment-event identity across repeated incidents
- [x] historical event semantics distinct from fresh/stale topology
- [x] bounded service/time-window change-event API
- [x] change-event relationships excluded from causal blast-radius traversal
- [ ] persistent graph explorer UI

Agentic investigation foundation:

- [x] Investigation Planner with bounded task/context/query budgets
- [x] typed specialist task contracts
- [x] allow-listed Kubernetes, metrics, logs, traces, database and change task types
- [x] database/stateful branch depends on typed trace identity
- [x] planner context retrieval from bounded Awareness Graph neighborhoods
- [x] deterministic hypothesis verification remains the root-cause authority
- [x] opt-in parallel specialist execution in dependency-aware waves
- [x] per-specialist read-only execution contracts and result envelopes
- [x] local specialist query budgets plus the existing global query budget
- [x] shared evidence envelope with SHA-256 integrity verification
- [x] server-side plan recompilation before execution; caller-authored plans are never executed
- [x] branch-local hypotheses are advisory-only and execution has no root-cause field
- [x] deterministic shared-envelope aggregation / evaluation stage
- [x] rejected-hypothesis explanation graph
- [x] fail-closed integrity gate for missing/tampered hypothesis evidence references
- [x] deterministic trace/stateful causal gates reapplied after specialist execution
- [x] Kubernetes direct-signal precedence gate blocks downstream promotion
- [x] higher-precedence specialist completeness gate prevents absence-by-failure promotion
- [x] composed `/planner/investigate` endpoint with deterministic verdict
- [x] Kubernetes specialist parity with the full infrastructure playbook engine
- [x] shared Kubernetes stage reused by classic and agentic investigation paths
- [x] eviction, OOMKilled, CrashLoopBackOff, FailedScheduling/Karpenter, HPA, KEDA and NodeNotReady parity regressions
- [x] dedicated Kubernetes specialist budget layered under the global query budget
- [x] optional model-assisted planning compiled through the specialist allow-list
- [x] model advice cannot alter task templates, query budgets or dependencies
- [x] Kubernetes/metrics/traces baseline preserved when deterministically eligible
- [x] invalid/unknown model specialist names fail back to the deterministic plan
