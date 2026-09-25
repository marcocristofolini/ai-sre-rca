# Roadmap

## Phase 1 — Kubernetes RCA

- Pod Evicted / DiskPressure
- OOMKilled
- CrashLoopBackOff
- ImagePullBackOff
- FailedScheduling
- NodeNotReady
- HPA saturation
- Karpenter provisioning failures

## Phase 2 — Change correlation

- Argo CD revision history
- Git commits and pull requests
- Kubernetes rollout history
- feature-flag changes
- cloud audit events

## Phase 3 — Service dependency awareness

- OpenTelemetry service graph
- traces around the alert window
- upstream/downstream error propagation
- PostgreSQL, Kafka and Redis entity adapters

## Phase 4 — Incident memory

- PostgreSQL + pgvector embeddings
- similar incident retrieval
- duplicate incident clustering
- recurring-pattern reports
- never reuse an old root cause without fresh telemetry verification

## Phase 5 — Optional model reasoning

Allow a model to propose additional hypotheses from the structured context. The model does not receive arbitrary shell access, does not invent evidence, and does not determine the evidence score. Every proposed check must map to an allow-listed typed tool.

## Phase 6 — Remediation plane

A separate service identity can offer guarded remediation workflows. It must require explicit approval, include dry-run/plan output, enforce per-action policy, and remain isolated from the read-only investigator identity.
