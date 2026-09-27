# Changelog

All notable changes to AI SRE RCA are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Planned
- Trace-to-log derived-field correlation and explicit exemplars.
- PostgreSQL, Kafka and Redis deterministic dependency adapters.
- Remaining Kubernetes depth: ImagePullBackOff, PDB and runtime-volume analysis.

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

[Unreleased]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcocristofolini/ai-sre-rca/releases/tag/v0.1.0
