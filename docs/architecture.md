# Architecture — v0.2

## Investigation lifecycle

1. Normalize alert into `IncidentInput`.
2. Retrieve similar historical incidents as hints.
3. Resolve Kubernetes ownership: Pod → ReplicaSet → Deployment and Pod → Node.
4. Resolve service identity and trace-derived service dependencies.
5. Resolve Argo CD application, deployed revision and Git commit metadata.
6. Resolve Karpenter NodeClaim/NodePool for the affected node.
7. Load deterministic hypotheses for the incident family.
8. Collect typed read-only evidence.
9. Attach evidence IDs to deterministic hypotheses.
10. Score support/contradiction deterministically.
11. Optionally ask an LLM for additional hypotheses; mark them `proposed` only.
12. Choose root cause only from deterministic hypotheses that satisfy evidence thresholds.
13. Persist result and vector fingerprint for future incident-memory retrieval.

## Causal safety hierarchy

The engine intentionally distinguishes these concepts:

```text
Direct causal signal
  Kubernetes says resource X caused eviction
           >
Correlated runtime signal
  DiskPressure + filesystem collapse
           >
Contributing configuration
  missing ephemeral-storage reservations
           >
Temporal correlation
  deployment happened near incident
           >
Historical similarity
  old incident looked similar
           >
Model suggestion
  plausible unverified hypothesis
```

A lower layer cannot override a higher layer without new typed evidence.

## Awareness Graph

The API returns explicit `graph_edges` rather than requiring the frontend to infer relationships from a flat entity list.

Example:

```text
web-gateway --calls--> landing-page --calls--> catalog-api
                              |
                              +--calls--> redis

Pod --owned_by--> ReplicaSet --owned_by--> Deployment
 |                                        |
 +--scheduled_on--> Node                  +--managed_by--> ArgoCD --syncs_from--> Git
                      |
                      +--represented_by--> NodeClaim --owned_by--> NodePool
```

## Service graph source

Tempo metrics-generator / Grafana Alloy / OTel servicegraph processor emits Prometheus metrics with `client` and `server` labels. The investigator reads these metrics; it does not need arbitrary Tempo shell/API access for the topology MVP.

## Incident memory

`SQLRepository` stores:

- full structured RCA payload
- normalized textual fingerprint
- 128-dimensional vector

The default embedding is local and deterministic. This keeps the demo self-contained and makes incident memory available without sending incident data to an external model.

The embedding provider is deliberately isolated so a production embedding service can replace it later.

## LLM boundary

The optional model is **not an agent with tools**. It is a hypothesis proposer.

It cannot:

- query Kubernetes directly
- run PromQL/LogQL
- execute shell
- attach evidence IDs
- change hypothesis score
- set `confirmed`
- participate in primary root-cause selection

A future `HypothesisCompiler` can map LLM-proposed `required_checks` to allow-listed typed tools, but only deterministic evidence evaluation should transition those candidates.

## Production hardening

- NetworkPolicy allowlists for Kubernetes API, telemetry, Argo and Git provider.
- workload identity/short-lived credentials where supported.
- secret redaction before any model call.
- evidence payload hashing and immutable audit sink.
- per-source query timeouts, range limits and cardinality budgets.
- tenant/cluster isolation.
- admission policy that rejects write verbs for investigator ServiceAccount.
- separate identity and human approval for any future remediation executor.
