# RCA Benchmark v2.2 - UID-verified classic controller dispatch

Base: `main` at `b9204b85877d0469d5a7a280508ff49c8d657df7`. Work performed in the isolated `feat/rca-benchmark-v2-2-causal-coverage` worktree. No GitHub Actions, PR, production cluster, paid LLM, remediation, or modification of existing benchmark truth occurred. This is an incremental coverage repair, **not a passing benchmark or a multi-root implementation**.

## Corrected behavior

Previously, the classic path fell through to generic RCA for a verified Deployment/StatefulSet target without a singular `incident.pod`. The trace stage then selected the downstream `trace-causal-propagation` hypothesis in `l002`, omitting the direct Kubernetes OOM evidence.

Classic now traverses the **server-compiled, bounded, UID-bound** Pod membership using the same read-only ownership checks as the agentic Kubernetes specialist. A live controller UID is verified even with zero Pods. Each Pod, ReplicaSet and controller must match the expected live UID/ownership; unrelated Pods selected only by matching labels cannot enter the investigation. The complete list is bounded to 32 members by the existing input schema, with an additional stage read budget. The external query budget and cluster scoping remain in place.

A single confirmed root type is reported using only confirmed Pod evidence. With no verified root, classic abstains. With distinct confirmed Pod roots, the result remains inconclusive instead of arbitrarily choosing one; the conflicting hypotheses and raw supporting evidence remain inspectable, with a conflict marker preventing trace/stateful promotion into a misleading unique root. Existing trace findings can still be recorded as hypotheses without overriding the Kubernetes root. No multi-root API, persistence, benchmark scorer, or remediation contract was changed.

## Actual validation

- **Docker/PostgreSQL:** 306 backend tests passed, 0 skipped, 1 existing Starlette deprecation warning (isolated pgvector/pg17 container).
- **Focused security and workload regression:** 52 passed, 1 previously skipped before all database tests were enabled. Checks include live Deployment/StatefulSet UID, recreated Pod/controller, zero replicas, conflicting roots, bounded reads, trace/stateful promotion and evidence integrity.
- **Benchmark and harness units:** 50 passed. Separate baseline benchmark tests were also included in the 306 backend tests.
- **Synthetic adversarial benchmark (final changes):** the 3 paths each produced 1 complete, 1 partial, 5 correct inconclusives, 1 rejection, 0 incorrect, 0 errors, 0 false confirmations. Strict `passed=false` remains correct for `b002` because the independent Node root is still unreported.
- **Disposable Kind full live run (controller dispatch change, before final defensive follow-up):** `l001` and `l003` abstained correctly on all 3 paths; `l002` reported `container-oom` on **classic, agentic and Copilot**, each with integrity true and no execution/source errors. All 6 historical Copilot regression scenarios passed, including missing-Loki handling. Strict `passed=false`: independent downstream trace causality remains unreported. Captures are stored locally in ignored `validation-artifacts/v22-full/`; this was the existing one-stack Prometheus/Loki/Tempo/PostgreSQL Kind harness, which cleaned up its resources.
- After the live run, added stronger root-UID validation for zero-member workloads and cross-signal conflict gates; reran full PostgreSQL, focused, and synthetic suites. **The exact final patch was not rerun against fresh Kind**, and the benchmark correctly prohibits replay of an expired live-inventory authorization. Do not interpret these as production acceptance results.

## Remaining work

1. Design a reviewed backward-compatible **multi-root contract** across classic, agentic, Copilot, stored diagnosis, citations and the blinded benchmark scoring. Independently prove each cause; keep direct-Kubernetes precedence for the primary, never turn specialist-local confirmation into persisted authority.
2. Add a genuinely independent `node-not-ready` candidate for the synthetic `b002` when supported by verified node conditions; never infer it from Pod OOM or label correlations.
3. Re-run full live Kind, Playwright and PostgreSQL tests after those broader changes, keeping oracle files and prior captures frozen.

The v2.1 comparison and known benchmark failure remain documented in [v2.1 results](rca-benchmark-v21-results.md).
