# Roadmap after v0.3

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

## v0.3.x — remaining Kubernetes depth

- [ ] ImagePullBackOff / ErrImagePull
- [ ] PDB blocking and disruption-budget analysis
- [ ] topology-spread and advanced affinity explanations
- [ ] Karpenter consolidation-specific RCA
- [ ] PVC attach/mount runtime failures beyond scheduler binding
- [ ] per-container memory metric correlation instead of pod aggregate only

## v0.4 — trace-aware causal propagation

- [ ] Tempo/TraceQL adapter
- [ ] representative trace IDs around incident window
- [ ] upstream/downstream error propagation
- [ ] edge RED metrics: rate, error, duration
- [ ] virtual database/messaging nodes
- [ ] trace-to-deployment correlation

## v0.5 — stateful dependencies

- [ ] PostgreSQL adapter: saturation, locks, replication lag, connection exhaustion
- [ ] Kafka adapter: consumer lag, ISR, partition leadership
- [ ] Redis adapter: memory pressure, evictions, latency
- [ ] dependency-specific deterministic hypotheses

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
