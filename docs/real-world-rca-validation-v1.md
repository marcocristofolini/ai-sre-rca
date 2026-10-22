# Real-World RCA Validation v1 — local results

Base: `f6992ca`; isolated branch `feat/real-world-rca-validation-v1`. The original working tree and workflows were preserved.

## Controlled live cases

One disposable two-node Kind cluster reused the existing Prometheus, Loki, Tempo, workload and PostgreSQL infrastructure. Production read-only adapters collected actual Kubernetes state, Prometheus counters, structured Loki logs and SDK-generated OTLP traces. No LLM provider was enabled.

| Investigation / condition | Expected and reported result |
| --- | --- |
| Deployment caller: actual downstream HTTP timeout | `trace-causal-propagation` |
| StatefulSet owner: injected service degradation | `trace-causal-propagation` |
| New rollout repeatedly exits 42 | `application-exit` |
| Instrumented container exceeds its memory limit | `container-oom` |
| Recovered revision and successful HTTP calls | No confirmed cause |
| StatefulSet scaled to zero | No confirmed cause; no Pod log query |

**6/6 scoped cases matched**, including both negative cases. This is a controlled sample, not a production accuracy benchmark. Rollout validation proves the observed process-exit mechanism; it does not assert Git revision causality without Git/Argo evidence.

Changed membership rejected old confirmation with 409; conflicting controller telemetry mappings returned 409; a real Loki outage returned 503 before job creation. Tenant/cluster denial checks disclosed no evidence IDs. UID/ownerReference chains, exact Loki Pod UID labels, idempotency, persistent jobs, SSE, deterministic fallback and authorized citations were verified. Raw referenced traces/logs, inventory provenance, timestamps, failure details and ten source fingerprints are retained in local JSON reports.

## Defects found and corrected

- Inventory-only Kubernetes context left the specialist skipped and correctly blocked downstream promotion. Bounded live Pod/controller/ReplicaSet UID checks now reuse the deterministic Kubernetes engine; the causal gate remains intact.
- HTTP header casing broke cross-process parent propagation; readiness and asynchronous indexing also required explicit barriers.
- Actual repeated exits between backoff states were missed; readiness failures were incorrectly treated as restart-causing probes.
- Tempo returned unordered results and omitted leading zeros from some IDs. Bounded error-first/recency selection, canonical IDs, alias deduplication and fetched-payload identity checks preserve independent trace evidence.
- Real evidence summaries overflowed the mobile grid. Tracks and text now wrap without hiding content or weakening assertions.

## Executed validation

- Docker/PostgreSQL backend and harness: **325 passed, zero skipped**, plus five subtests; one existing Starlette deprecation warning.
- Workload-image tests: **8 passed** across Deployment and StatefulSet configurations, using actual local HTTP calls and mocked OTLP export.
- Independent browser regressions: **44 passed**; separate production-adapter/live-Kind browser flow: **6 passed**. Hydration, explicit keyboard confirmation, actual SSE, real OOM RCA, citations and mobile bounds passed without unexpected browser failures.
- Next.js Docker production build: passed, including TypeScript and standalone assets.

Run with [the Docker entry point](../validation/README.md). Local artifacts are intentionally excluded from commits: `validation-artifacts/validation-report.{json,md}`, `copilot-live.json`, referenced raw telemetry, failed-attempt reports, browser screenshots and `test-results/`. Runtime credentials are private and removed during cleanup. GitHub Actions, production deployment and merges were not performed.
