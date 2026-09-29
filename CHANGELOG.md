# Changelog

All notable changes to AI SRE RCA are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Added
- v0.8 Persistent Awareness Graph foundation with stable canonical entity and relationship identities.
- Tenant- and cluster-scoped current graph state plus append-only temporal observations.
- Idempotent graph ingestion whenever an investigation is persisted.
- Viewer APIs for graph snapshots, target history and reconciled topology.
- First-class `ChangeEvent` graph nodes derived from Argo CD deployment evidence and matching GitHub revision evidence.
- Stable deployment-event identity across repeated incidents, with observation counts instead of duplicate event nodes.
- `historical` reconciliation semantics for immutable event nodes/edges, distinct from fresh/stale topology state.
- Bounded `GET /api/v1/awareness/changes` timeline API with service, namespace and time-window filters.
- Read-only `/awareness` explorer for reconciled entities, stored relationships, service-scoped projections and historical ChangeEvent context.
- Fresh/stale topology reconciliation from last_seen without inferring deletion from partial incident observations.
- Admin-only authoritative inventory reconciliation with explicit kind/namespace/relationship coverage and allow-listed sources.
- First-class entity/relationship tombstones with source/snapshot identity, append-only reconciliation observations, newer-observation protection and reactivation semantics.
- Immutable inventory snapshot identity: identical replays are idempotent, while reuse of the same tenant/cluster/source/snapshot ID with different content fails with conflict.
- Tombstoned facts remain available for topology/history and the Awareness Explorer while being excluded from current entity resolution and blast-radius traversal.
- Audited `POST /api/v1/awareness/inventory/reconcile` endpoint plus in-memory/PostgreSQL repository parity.
- Optional automatic Kubernetes inventory CronJob with complete paginated Service/Deployment/StatefulSet/DaemonSet/ReplicaSet/Pod/Node snapshots.
- Viewer-scoped Awareness Graph Health API with factual current/stale/historical/tombstoned counts, awareness observation volume and per-authoritative-source inventory freshness.
- Independent `AWARENESS_INVENTORY_MAX_AGE_MINUTES` threshold for authoritative snapshot freshness, separate from graph-fact stale semantics.
- Admin-only Awareness Graph retention endpoint with dry-run by default, separate observation/tombstone windows and PostgreSQL/in-memory parity.
- Optional scheduled Awareness retention CronJob with explicit tenant/cluster scope, overlap protection, dedicated database Secret and no mounted Kubernetes ServiceAccount token.
- Explanation-safe retention preserves every authoritative inventory snapshot receipt and at least the latest observation for each entity/relationship target.
- Deterministic Kubernetes inventory relationships from controller ownerReferences, Pod scheduling identity and exact Service selectors.
- Service-centric bounded topology projections with explicit ambiguity handling across namespaces.
- Graph-backed blast radius with an allow-listed relationship impact-direction model.
- Unknown, stale or tombstoned graph relationships remain visible as topology/history context but are excluded from current impact traversal.
- Bounded Investigation Planner with allow-listed read-only specialist task types.
- Planner budgets for maximum tasks, parallelism, graph context nodes and queries per specialist.
- Explicit traces -> database/stateful task dependency so stateful checks require typed dependency identity.
- Planner context retrieval from the Persistent Awareness Graph.
- Frozen agentic Operational Context Snapshot combining bounded reconciled topology, target history, recent ChangeEvents, Graph Health and authoritative inventory-source freshness.
- Operational context is copied into the InvestigationPlan, propagated to specialists and preserved in the Shared Evidence Envelope for one consistent graph view per run.
- Changes specialist can consume persisted ChangeEvents as weak `context_only` context without treating them as fresh causal evidence.
- Planner audit records bounded operational-context counts, truncation state and inventory-source freshness.
- Planner audit events and investigator-role enforcement.
- Experimental opt-in parallel specialist execution in dependency-aware waves.
- Typed read-only specialist execution contracts for Kubernetes, metrics, logs, traces, stateful dependencies and changes.
- Local specialist query budgets layered on top of the existing global query budget.
- Shared evidence envelope with SHA-256 evidence-integrity verification.
- Server-side plan recompilation immediately before execution; caller-authored task graphs are never executed.
- Branch-local hypotheses marked advisory-only.
- `POST /api/v1/planner/execute`, disabled by default through `AGENTIC_EXECUTION_ENABLED=false`.
- Deterministic shared-envelope evaluator that independently revalidates specialist hypotheses.
- Fail-closed evidence-reference and SHA-256 integrity gates before any agentic root-cause selection.
- Reapplied trace-only repetition and stateful origin+telemetry causal gates after parallel execution.
- Direct Kubernetes failure-signal precedence that blocks downstream trace/stateful promotion.
- Rejected-hypothesis explanation graph with evidence, hypothesis, gate and verdict nodes.
- `POST /api/v1/planner/investigate` composing plan, read-only execution and deterministic evaluation.
- Agentic Kubernetes specialist now reuses the same deterministic Kubernetes stage as the classic engine instead of a simplified parallel implementation.
- Agentic Kubernetes coverage now includes eviction/ephemeral-storage, OOMKilled, CrashLoopBackOff, FailedScheduling/Karpenter capacity, HPA, KEDA and NodeNotReady with identical playbook hypothesis IDs and evidence rules.
- Dedicated `AGENTIC_KUBERNETES_MAX_QUERIES` budget, default 30, layered under the existing global query budget.
- Regression coverage comparing classic and agentic Kubernetes outcomes plus fail-closed specialist budget exhaustion.
- Optional model-assisted branch selection compiled through the existing specialist allow-list.
- Model advice is limited to specialist names/rationale; executable task templates, dependencies and budgets remain server-controlled.
- Model-assisted planning preserves the Kubernetes/metrics/traces evidence baseline whenever those branches are deterministically eligible.
- Planner output records requested, accepted, rejected and forced specialist choices for auditability.
- Invalid, unavailable or unknown model choices fall back to the deterministic plan.
- PostgreSQL and in-memory repository parity for entity lookup and bounded neighbor traversal.
- Bilingual Persistent Awareness Graph and Agentic Investigation documentation with Mermaid diagrams.

### Changed
- CI production-image smoke builds retry bounded transient registry/network failures before failing the job.
- Python 3.14 test tooling now permits pytest-asyncio 1.4+, removing the legacy 0.x compatibility cap.
- The remediation incident-freshness gate is exposed consistently through `.env.example`, Helm values and bilingual remediation documentation.

### Safety
- The planner cannot confirm root cause, attach evidence IDs or request remediation.
- Model-assisted planning is advisory-only and disabled by default.
- The model cannot create SpecialistTask objects, tool names, queries, budgets or dependencies; the server recompiles all executable tasks.
- Unknown or ineligible model specialist names are rejected and cannot expand the deterministic tool surface.
- Model-planner failure or malformed output preserves the deterministic plan.
- Agentic execution is read-only, opt-in and disabled by default.
- Agentic execution responses intentionally expose no `root_cause` field.
- Specialist hypotheses are advisory-only; deterministic evaluation remains the root-cause authority.
- Evidence with failed integrity verification is excluded from the shared envelope.
- A specialist that exhausts its local budget becomes `budget_exhausted`; dependent branches are skipped.
- The execution endpoint recompiles the plan server-side rather than accepting executable plans from callers.
- The evaluator does not trust specialist-local `confirmed` state; it recalculates score and causal eligibility from the integrity-verified envelope.
- Missing support or contradiction references fail closed and prevent root-cause selection.
- Persistent operational records marked `context_only` are removed from causal scoring; a hypothesis that attempts to rely on them fails closed as invalid evidence.
- Tombstoned entities remain available to the frozen operational snapshot for explanation but are excluded from current task-required entity IDs.
- A planned higher-precedence Kubernetes specialist that does not complete blocks downstream trace/stateful promotion, preventing absence-by-failure from being treated as clean infrastructure evidence.
- Kubernetes specialist budget exhaustion is fail-closed; partial infrastructure collection cannot be treated as a clean Kubernetes result.
- `/execute` remains evidence-only; only `/investigate` can emit a deterministic root-cause verdict.
- Blast radius never traverses unknown or stale relationships.
- ChangeEvent relationships are context-only and are deliberately absent from the causal impact-direction allow-list; temporal proximity alone cannot promote a deploy into root cause.
- ChangeEvent materialization fails closed when application/revision identity or the authoritative operation timestamp is missing/malformed.
- Deployment, Service and GitRepository event links are created only from explicit unambiguous graph relationships; namespace/list ordering is never used as identity evidence.
- Incident-derived graph absence is never treated as deletion; only complete snapshots from configured authoritative sources can create tombstones.
- Inventory reconciliation fails closed on incomplete snapshots, out-of-scope resources, non-authoritative relationship types and snapshots older than a newer graph observation.
- Automatic Kubernetes inventory collection aborts before reconciliation if any configured list or page is incomplete, invalid, unavailable or repeats a continuation token.
- Kubernetes `owned_by` inventory edges require a single controller ownerReference whose UID matches the currently observed owner object, preventing name reuse from creating false controller links.
- Namespace-scoped inventory never asserts Pod-to-Node tombstones across a cluster-scoped Node boundary; cluster-wide scheduling edges require a complete cluster-wide pass.
- Graph Health is read-only, tenant/cluster isolated and factual; it introduces no composite reliability score, LLM-derived status or graph mutation path.
- Awareness retention dry-run performs no write; executed purges remove expired relationships before entities, block entity deletion while any relationship survives, preserve snapshot replay identity and write a transactional audit event.
- Immutable ChangeEvent nodes/edges are excluded from inventory tombstoning.
- The investigator remains read-only and the remediator remains a separate runtime/identity.

### Planned
- Remediation policy bundles, post-action verification and signed approval evidence.
- Deeper PostgreSQL blocker/query analysis, Kafka rebalance/broker context and Redis slowlog/hot-key analysis.

## [0.7.0] - 2026-09-28

### Added
- Separate FastAPI remediator service with its own Kubernetes ServiceAccount and API surface.
- Typed remediation plans persisted alongside incident/audit state.
- First allow-listed action: Deployment scaling through the Kubernetes deployments/scale subresource.
- Static policy checks for incident state, evidence strength, action/namespace allow-lists, investigated Deployment identity, replica bounds and remediation cluster registry.
- Runtime policy checks for no-op changes and bounded replica delta.
- Kubernetes server-side dry-run before approval.
- Exact OIDC remediator role and four-eyes requester/approver separation by default.
- Explicit approve, reject, execute and rollback API transitions.
- Plan TTL with automatic expiration.
- resourceVersion and replica drift protection before execution and rollback.
- Atomic compare-and-save state claims for approval, execution and rollback concurrency.
- A second Kubernetes server-side dry-run immediately before execution/rollback mutation.
- Rollback metadata captured before approval and persisted execution/rollback results.
- Separate REMEDIATION_CLUSTER_CONFIG_JSON credential registry.
- Helm remediator Deployment/Service, separate ServiceAccount and namespace-scoped scale Roles.
- Bilingual guarded-remediation documentation.

### Changed
- API, backend package, frontend and Helm chart versions are now 0.7.0.
- CI now validates the investigator and remediator as separate runtime surfaces and production-container entrypoints.

### Safety
- The investigator remains read-only and does not expose remediation routes.
- The remediator has no generic Kubernetes patch, shell, kubectl, pods/exec, create/delete, Argo sync or cloud mutation primitive.
- Helm grants only get/patch on deployments/scale and only in explicitly listed namespaces.
- Remediation is disabled by default; the default target namespace list and causal hypothesis allow-list are empty.
- An enabled remediator refuses startup without authentication, shared persistent storage, complete OIDC configuration, or a distinct remediator role.
- Helm rejects reuse of the investigator ServiceAccount identity by the remediator.
- Scale is denied unless the RCA root is a confirmed deterministic playbook hypothesis whose exact ID is explicitly allow-listed.
- An RCA recommendation never executes automatically. Dry-run, explicit approval and explicit execution are separate steps.

## [0.6.1] - 2026-09-27

### Fixed
- Incident reads are tenant-scoped at the repository query boundary, so cross-tenant incident IDs remain indistinguishable from unknown IDs.
- Manual retention purge is tenant-scoped for ordinary tenant admins; only a principal with `tenant_id="*"` receives platform-wide purge scope.
- Invalid `CLUSTER_CONFIG_JSON` now fails settings validation instead of silently disabling the configured cluster registry.
- Regression tests cover tenant-scoped reads, tenant-scoped retention and fail-closed cluster-registry parsing.

### Changed
- API, backend package, frontend and Helm chart versions are now 0.6.1.

### Safety
- The patch strengthens operational isolation without adding infrastructure mutation methods, workload write credentials or remediation privileges.
- Existing evidence-first, query-budget, cluster-authorization and read-only integration guardrails remain unchanged.

## [0.6.0] - 2026-09-27

### Added
- Optional OIDC JWT validation with issuer/audience/signature verification through JWKS.
- Hierarchical viewer/investigator/admin RBAC and bearer-token forwarding from the Next.js UI.
- Tenant-scoped incident memory, incident reads, audit events and exports.
- Cluster claims plus per-cluster Kubernetes/Prometheus/Loki/Tempo/Argo CD routing through `CLUSTER_CONFIG_JSON`.
- Per-incident total/per-source/wall-time query budgets with explicit budget evidence.
- SHA-256 integrity hash on every evidence object.
- Tenant-scoped audit log for investigation, view, export, notification and retention operations.
- `ai-sre-rca.export.v1` incident sharing/export payload with integrity verification.
- Slack incoming-webhook and PagerDuty Events API v2 delivery.
- Incident and audit retention policies plus admin purge endpoint.
- Per-source capability probes for Kubernetes, Prometheus, Loki, Tempo, Argo CD and GitHub.
- New operational UI at `/ops`.
- Alertmanager machine-token authentication.
- Opt-in Helm NetworkPolicy templates.
- External Kubernetes Secret injection through `backend.existingSecret`.
- Bilingual operational architecture documentation.

### Changed
- Investigation results now include a query-budget snapshot.
- Similar-incident search is tenant scoped.
- The live HTTP integration uses context-local cluster routing so concurrent incidents can target different clusters safely.
- Demo endpoints are available only when `DEMO_MODE=true`.
- API, backend package, frontend and Helm chart versions are now 0.6.0.

### Safety
- OIDC tenant and cluster claims are server-enforced; caller-supplied tenant IDs are overwritten.
- Query-budget exhaustion returns bounded empty telemetry instead of widening a query or bypassing limits.
- Notification delivery occurs only after RCA persistence and cannot erase the investigation when delivery fails.
- NetworkPolicy is opt-in to avoid breaking unknown cluster topologies; sensitive values are intended to come from an external Secret.
- v0.6 does not add workload-remediation credentials or infrastructure write methods.

## [0.5.1] - 2026-09-27

### Fixed
- Unsupported stateful UI routes now return Next.js 404 instead of silently rendering the PostgreSQL demo.
- Existing trace dependency entities merge later OpenTelemetry metadata instead of discarding database, messaging and server identity attributes.
- PostgreSQL lock RCA now matches the lowercase `accessexclusivelock` label emitted by the current postgres_exporter locks collector.
- PostgreSQL long-transaction RCA now uses database- and instance-scoped `pg_stat_activity_max_tx_duration`, which directly represents transaction age in seconds.
- PostgreSQL dependency telemetry requires a scoped instance identity before querying Prometheus, preventing cross-instance aggregation for similarly named databases.
- Redis dependency telemetry requires a scoped instance identity and continues escaping traced addresses before building PromQL regex matchers.
- PostgreSQL stateful analysis now exposes the instance hint used to scope telemetry.
- Trace dependency identity now includes the observed `server.address`, preventing same-named databases, topics or caches on different instances from collapsing into one graph node.
- Cross-trace causal confirmation now includes dependency instance identity, so identical operations on different backends do not count as repeated confirmation of one cause.
- Kafka Prometheus queries now require and apply instance scope, preventing same-topic/group aggregation across Kafka clusters.
- PostgreSQL connection utilization now compares instance-wide sessions with instance-wide `max_connections`, while retaining database-specific session counts as context.
- Kafka offline-partition detection now uses a boolean comparison so a healthy topic reports zero instead of becoming `unknown` when no partition is leaderless.
- Prometheus regex literals now escape RE2 metacharacters without over-escaping ordinary hostname characters such as hyphens.

### Safety
- Missing PostgreSQL or Redis instance identity yields `unknown` signals rather than global Prometheus queries.
- Review-regression tests cover exporter metric names, lock-mode casing, regex escaping, non-finite values and dependency metadata merging.

## [0.5.0] - 2026-09-27

### Added
- Stateful dependency RCA stage executed after trace-aware causal propagation.
- Typed PostgreSQL, Kafka and Redis dependency discovery from OpenTelemetry span metadata.
- Read-only Prometheus adapters for dependency-specific telemetry.
- PostgreSQL hypotheses for connection exhaustion, lock contention, deadlocks, replication lag and long-running transactions.
- Kafka hypotheses for consumer lag, under-replicated partitions, leaderless/offline partitions and empty consumer groups.
- Redis hypotheses for memory pressure/evictions, blocked clients, client saturation and elevated command latency.
- Structured `stateful_dependencies` response payload with health, causal role, evidence, thresholds and signals.
- Configurable stateful target budget and per-signal thresholds.
- Dependency-specific remediation recommendations.
- Stateful demo endpoints for PostgreSQL, Kafka and Redis.
- New stateful UI routes and dependency signal cards.
- Bilingual stateful-dependency architecture documentation with Mermaid diagrams.
- Four v0.5 stateful regression scenarios plus a clean-runtime application import regression, bringing the backend suite to 18 deterministic/safety/runtime tests.

### Changed
- Trace dependency metadata now preserves a narrow allow-list of database, server and messaging semantic attributes.
- The investigation pipeline now runs infrastructure playbooks, trace causal enrichment and then stateful dependency enrichment.
- A confirmed stateful dependency cause can refine a trace-only root cause into a more specific diagnosis.
- API, backend package, frontend and Helm chart versions are now 0.5.0.
- SQLAlchemy is installed with its `asyncio` extra so clean runtime installations include the required greenlet dependency.
- Helm and environment examples expose stateful RCA budgets and thresholds.

### Safety
- Stateful adapters use bounded Prometheus GET queries only; they do not open SQL sessions, use Kafka admin APIs or execute Redis commands.
- A stateful root cause is promoted only when the dependency is the trace causal origin and dependency-specific current telemetry confirms an anomaly.
- A confirmed Kubernetes/infrastructure root cause retains precedence over downstream stateful anomalies.
- Missing exporter metrics remain unknown and are not treated as healthy or causal evidence.
- The LLM still cannot create PromQL, invoke dependency adapters, attach evidence, change scores or confirm hypotheses.

## [0.4.0] - 2026-09-27

### Added
- Read-only Tempo integration for TraceQL search, Tempo v2 trace retrieval and TraceQL metrics.
- Standard OTLP JSON trace parser.
- Deterministic deepest-failing-span selection.
- Parent/child causal ancestry reconstruction.
- Cross-trace confirmation requiring repeated downstream origin before trace-only RCA can be confirmed.
- TraceQL RED evidence for request rate, error rate and p95 duration.
- Service-graph edge RED metadata: requests, failures, error ratio and p95 latency.
- Virtual database, messaging and external dependency nodes in the Awareness Graph.
- Trace-to-Deployment correlation using OpenTelemetry resource attributes including Kubernetes workload identity and service version.
- New `trace_analyses` API payload with causal chains.
- New trace-aware UI at `/trace`.
- New trace-aware demo endpoint at `/api/v1/incidents/demo/trace`.
- Tempo and trace-analysis configuration in `.env.example` and Helm values.
- Regression coverage for repeated-trace confirmation, single-trace non-confirmation and Kubernetes-RCA precedence.

### Changed
- API, backend package, frontend and Helm chart versions are now 0.4.0.
- Alertmanager service identity can be resolved from common service labels.
- Awareness Graph service edges can carry RED metadata.
- Investigation results are enriched with trace causality after deterministic infrastructure playbooks.

### Safety
- Tempo access is read-only and bounded by configurable search/fetch limits.
- A single trace remains testing evidence and cannot confirm a trace-only RCA.
- Trace evidence cannot override an already-confirmed Kubernetes root cause.
- The LLM still cannot query Tempo directly, attach evidence, set scores or confirm hypotheses.

## [0.3.0] - 2026-09-25

### Added
- Deterministic OOMKilled investigation with container memory-limit and node MemoryPressure separation.
- CrashLoopBackOff investigation that distinguishes OOM restarts, probe failures and non-zero application exits.
- FailedScheduling classification for insufficient resources, taints/tolerations, affinity/selectors, volume binding and Karpenter capacity failures.
- NodeNotReady investigation for unreachable nodes, kubelet health, node resource pressure and Karpenter lifecycle context.
- HPA diagnostics for maxReplicas saturation, metrics unavailability and blocked scaling.
- KEDA ScaledObject diagnostics for scaler errors, paused autoscaling, fallback mode and inactive triggers.
- Typed read-only HPA and KEDA discovery methods through the Kubernetes API.
- Read-only KEDA RBAC permissions in the Helm chart.
- Seven new v0.3 regression scenarios.

### Changed
- Incident dispatch now selects a deterministic playbook from Kubernetes state and alert context.
- API, backend package, UI and Helm chart versions are now 0.3.0.
- Roadmap now separates completed v0.3 capabilities from remaining Kubernetes depth work.

### Safety
- New playbooks preserve the existing evidence-first scoring model.
- LLM-generated hypotheses remain proposed, score 0, and excluded from deterministic root-cause selection.
- No mutating Kubernetes, KEDA, Karpenter, Argo CD or cloud operation was introduced.

## [0.2.1] - 2026-09-25

### Added
- Brazilian Portuguese README.
- Bilingual architecture documentation.
- Mermaid diagrams for system architecture, RCA lifecycle, Awareness Graph, incident memory and LLM guardrails.

### Changed
- Published version metadata normalized to 0.2.1.

## [0.2.0] - 2026-09-24

### Added
- OpenTelemetry/Tempo service graph correlation.
- Awareness Graph relations across Kubernetes, Argo CD, GitHub and Karpenter.
- PostgreSQL + pgvector incident memory with HNSW indexing.
- Change correlation for Argo CD revisions and GitHub commits.
- Karpenter NodeClaim/NodePool context.
- Optional OpenAI-compatible LLM hypothesis proposer.
- Guardrails preventing LLM-only hypotheses from becoming root cause.

## [0.1.0] - 2026-09-24

### Added
- Initial evidence-first Kubernetes RCA MVP.
- FastAPI API and Alertmanager webhook.
- Kubernetes, Prometheus, Loki and Argo CD read-only integrations.
- Deterministic pod-eviction / ephemeral-storage playbook.
- Next.js investigation UI.
- Docker Compose and Helm deployment assets.

[Unreleased]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.5.1...v0.6.0
[0.5.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcocristofolini/ai-sre-rca/releases/tag/v0.1.0

[0.5.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.5.0...v0.5.1
