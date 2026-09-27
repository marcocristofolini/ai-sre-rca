# Changelog

All notable changes to AI SRE RCA are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Planned
- OIDC/RBAC, NetworkPolicy, audit/evidence hashing and multi-cluster operational hardening.
- Deeper PostgreSQL blocker/query analysis, Kafka rebalance/broker context and Redis slowlog/hot-key analysis.
- Remaining Kubernetes depth: ImagePullBackOff, PDB and runtime-volume analysis.

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
- Four v0.5 regression scenarios, bringing the backend suite to 17 deterministic/safety tests.

### Changed
- Trace dependency metadata now preserves a narrow allow-list of database, server and messaging semantic attributes.
- The investigation pipeline now runs infrastructure playbooks, trace causal enrichment and then stateful dependency enrichment.
- A confirmed stateful dependency cause can refine a trace-only root cause into a more specific diagnosis.
- API, backend package, frontend and Helm chart versions are now 0.5.0.
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

[Unreleased]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcocristofolini/ai-sre-rca/releases/tag/v0.1.0
