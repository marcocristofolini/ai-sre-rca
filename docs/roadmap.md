# Roadmap after v0.4

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

## v0.4.x — remaining tracing depth

- [ ] trace-to-log derived-field correlation
- [ ] explicit exemplar ingestion from Prometheus/Mimir
- [ ] operation-level anomaly baselines
- [ ] sampling-ratio-aware evidence weighting
- [ ] trace correlation across multi-cluster service identities

## v0.5 — stateful dependencies

- [ ] PostgreSQL adapter: saturation, locks, replication lag, connection exhaustion
- [ ] Kafka adapter: consumer lag, ISR, partition leadership
- [ ] Redis adapter: memory pressure, evictions, latency
- [ ] dependency-specific deterministic hypotheses
- [ ] causal promotion from trace dependency node to stateful adapter evidence

## v0.6 — operational productization

- [ ] Slack/PagerDuty delivery
- [ ] evidence hash/audit log
- [ ] NetworkPolicy templates
- [ ] OIDC/RBAC for UI/API
- [ ] multi-cluster tenancy
- [ ] query budget/cost controls
- [ ] retention policies

## v0.7 — guarded remediation plane

Separate service and identity:

- [ ] proposed action plan
- [ ] policy validation
- [ ] dry-run where supported
- [ ] explicit human approval
- [ ] narrow per-action credentials
- [ ] rollback plan and audit record

The read-only investigator should remain usable independently of remediation.
