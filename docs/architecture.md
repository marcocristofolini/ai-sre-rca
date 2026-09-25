# Architecture notes

## Investigation loop

1. Normalize an alert into an `IncidentInput`.
2. Resolve the primary Kubernetes object and its node/owner relationships.
3. Load an incident-family playbook.
4. Collect evidence through narrow read-only tools.
5. Attach evidence IDs to hypotheses.
6. Score evidence deterministically.
7. Select a causal hypothesis only when its evidence threshold is met.
8. Return RCA + timeline + remediation suggestions.

## Why an evidence graph rather than an unconstrained agent

An unconstrained LLM can generate a plausible operational story from incomplete telemetry. This service instead treats telemetry as first-class records and makes the hypothesis state machine explicit. An LLM can later propose additional hypotheses or summarize evidence, but it should not be able to fabricate evidence, assign its own confidence percentage, or execute arbitrary shell commands.

## Tool boundary

Allowed examples:

- Kubernetes GET/LIST/WATCH
- Prometheus instant/range queries
- Loki range queries
- Argo CD GET application/revision
- Git provider read-only commits/deploy metadata

Disallowed in the investigator identity:

- Kubernetes CREATE/UPDATE/PATCH/DELETE
- `pods/exec`
- SSH
- arbitrary shell
- cloud mutations
- Argo sync/rollback

## Production hardening

- NetworkPolicy allowlists to telemetry/control-plane APIs.
- Separate ServiceAccount per cluster.
- Short-lived external credentials via workload identity.
- Immutable audit log for queries and evidence hashes.
- Query budgets, timeouts and cardinality limits.
- Tenant isolation for multi-cluster SaaS.
- Encrypt persisted incident payloads.
- Redact secrets/PII before logs are passed to any model.
- Human approval gateway for a future remediation service.
