# Changelog

All notable changes to AI SRE RCA are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Planned
- ImagePullBackOff / ErrImagePull.
- PDB blocking and topology-spread analysis.
- Karpenter consolidation-specific RCA.
- PostgreSQL, Kafka and Redis RCA playbooks.

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

[Unreleased]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcocristofolini/ai-sre-rca/releases/tag/v0.1.0
