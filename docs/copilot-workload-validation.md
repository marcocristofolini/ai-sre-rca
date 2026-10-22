# Copilot v2.2: workload scope validation

Base: `4a7be9d`; isolated branch `feat/copilot-v2-2-workload-intelligence`.

Deployment → ReplicaSet → Pod and StatefulSet → Pod membership comes from complete Kubernetes list collection and UID-verified `owned_by` edges in the Persistent Awareness Graph. Selected controllers, members and ownership proofs must have fresh observations from the same authoritative snapshot. Membership changes invalidate confirmation and queued execution. Resolution rejects ambiguous ownership, incomplete coverage, duplicate UIDs and more than 32 members (including ReplicaSets).

Metrics/traces require the explicit `ai-sre.io/telemetry-service` inventory annotation and reject conflicting controller mappings. Workload logs require namespace, Pod name **and `pod_uid`** labels matching collected UIDs; missing UID labels produce no accepted log evidence. Unrelated metric edges, traces without the selected service and out-of-scope log streams are rejected. Zero replicas require explicit `spec.replicas: 0`; no Pod log query runs. Terminating Pods remain visible in the reviewed scope.

Kubernetes ownership is current inventory context, never historical state or causal proof. Membership evidence is marked `context_only`; deterministic RCA gates remain authoritative. This slice reuses existing service telemetry specialists and does not run per-Pod live Kubernetes diagnostics. No database migration is required; optional typed scope persists in existing JSON payloads. Legacy request fingerprints exclude the absent new field.

## Local validation

- Docker/PostgreSQL: **274 passed, 0 skipped**, including concurrent confirmations, restart persistence, planner entry-point tampering, scope isolation, UID/name reuse, prompt injection and evidence boundaries. One existing Starlette TestClient deprecation warning.
- Next.js Docker production build: passed, including TypeScript; standalone `.next/static` and `public` assets verified by browser runners.
- Independent Docker Playwright entry points: diagnosis **19**, evidence Q&A **5**, interactive Copilot **9**, dynamic scope **11** checks; **44 total**. Final reports remain in untracked `artifacts/`.
- Negative tests retain exact expected HTTP errors, including 503, and reject unexpected console errors, request failures and page exceptions. Initial startup refusal was resolved by waiting for API readiness. Browser validation exposed and fixed unrelated, unverified topology incorrectly blocking a valid workload proposal.
- LLM calls disabled/mocked. No GitHub Actions, merge, deployment or workflow changes.

Next validation: instrumented disposable Kubernetes workloads with real Loki UID labels, Prometheus and Tempo; local fixtures do not establish production telemetry readiness.
