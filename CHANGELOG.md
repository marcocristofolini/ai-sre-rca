# Changelog

All notable changes to AI SRE RCA are documented here. The project follows Semantic Versioning.

## [Unreleased]

### Planned
- Kubernetes playbooks for OOMKilled, CrashLoopBackOff, FailedScheduling and NodeNotReady.
- HPA/KEDA saturation and Karpenter provisioning investigations.
- PostgreSQL, Kafka and Redis RCA playbooks.

## [0.2.1] - 2026-09-25

### Added
- Brazilian Portuguese README.
- Bilingual architecture documentation.
- Mermaid diagrams for system architecture, RCA lifecycle, Awareness Graph, incident memory and LLM guardrails.

### Changed
- Published version metadata normalized to `0.2.1`.

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

[Unreleased]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/marcocristofolini/ai-sre-rca/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/marcocristofolini/ai-sre-rca/releases/tag/v0.1.0
