# Roadmap after v0.2

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

## v0.3 — broader Kubernetes RCA

- [ ] OOMKilled / memory saturation
- [ ] CrashLoopBackOff
- [ ] FailedScheduling
- [ ] ImagePullBackOff
- [ ] NodeNotReady
- [ ] HPA/KEDA saturation
- [ ] Karpenter insufficient capacity / disruption / consolidation
- [ ] PDB and topology-spread constraints

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
