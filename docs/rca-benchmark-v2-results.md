# RCA Benchmark v2 — local adversarial results

Base: `914b2d2`; branch `feat/rca-benchmark-v2`. Executed locally on 2026-10-20 using cached Docker images, PostgreSQL/pgvector and the existing disposable Kind/Prometheus/Loki/Tempo stack. [Machine-readable per-case results](benchmarks/rca-v2-local.json); [method and reproduction commands](rca-benchmark.md).

## Results

Eight synthetic cases, three new live cases and one retained real pilot capture produced **36 comparative trials**. The six original real scenarios ran separately and **6/6 matched**, including recovery and zero replicas. These six regression inputs are nonblind.

| Dataset / path | Complete | Partial | Incorrect | Correct abstentions | Correct rejections | Execution errors | False confirmations |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Synthetic / classic | 1 | 1 | 0 | 5 | 1 | 0 | 0 |
| Synthetic / agentic | 1 | 1 | 0 | 5 | 1 | 0 | 0 |
| Synthetic / Copilot | 1 | 1 | 0 | 5 | 1 | 0 | 0 |
| Live / classic | 0 | 1 | 0 | 2 | 0 | 0 | 0 |
| Live / agentic | 0 | 1 | 0 | 2 | 0 | 0 | 0 |
| Live / Copilot | 0 | 1 | 0 | 2 | 0 | 0 | 0 |
| Pilot capture / classic | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| Pilot capture / agentic | 0 | 0 | 0 | 0 | 0 | 1 | 0 |
| Pilot capture / Copilot | 0 | 0 | 0 | 0 | 0 | 1 | 0 |

Totals: **3 complete, 6 partial, 0 incorrect, 22 correct abstentions, 3 correct rejections and 2 execution errors**. No false confirmations among nine emitted diagnoses. All 31 successful non-rejected results passed evidence-hash/reference verification; errors and rejections do not inflate that count. Copilot citations were checked against its authorized saved result.

The complete-cause benchmark is **FAIL**: both multi-cause cases leave a known cause unreported, and the legacy capture contains two execution errors. The deterministic regression tests assert these outcomes; no evaluator, production source adapter, oracle requirement or existing regression assertion was relaxed.

## Live comparison

Server-authorized Deployment scopes used complete UID ownership inventory, explicit service-to-telemetry mapping and Loki Pod UID labels. Each warm-up/scored path used a fresh PostgreSQL tenant. Source reads were frozen after capture for equivalent per-request responses; trusted inventory was cloned into each isolated tenant. No expected answer or prior diagnosis seeded the context.

- A healthy rollout with actual HTTP 200 calls caused all paths to abstain.
- Three actual downstream timeouts and an independently observed `OOMKilled` coexisted. Classic selected `trace-causal-propagation`; agentic and Copilot selected `container-oom`. Each selected cause was true, but each diagnosis was incomplete.
- A recovered revision with a fresh telemetry identity and actual HTTP 200 calls caused all paths to abstain.

| Path | p50 seconds | p95 seconds | Samples | Telemetry budget charges |
| --- | --- | --- | --- | --- |
| Classic | 0.528 | 0.610 | 3 | 31 |
| Agentic | 0.565 | 0.638 | 3 | 44 |
| Copilot | 2.059 | 2.363 | 3 | 44 |

These are nearest-rank **frozen-source execution latencies**, including PostgreSQL/inventory preparation and Copilot job/SSE/Q&A. They exclude source network capture, fault injection and rejected/failed trials. The live run, including the six regressions and controlled transitions, took approximately 405 seconds. This small sample does not measure production performance.

Across all datasets, 219 replayed telemetry budget charges were recorded. Raw capture method counts remain separate in full reports. Model requests, tokens and model cost were **zero**; the comparison used deterministic fallback. Telemetry/infrastructure money costs and energy consumption were not measured.

## Failures retained

The initial pilot used a legacy unscoped Pod/service request, which attempted unavailable Argo CD. The first stage stopped; its JSON captures and Kubernetes failure logs were preserved. The main live comparison then used genuine server-resolved Deployment scope. The pilot's raw observations became a separate permanent fixture: replay still counts agentic/Copilot incomplete execution as errors, so changing scope does not erase this weakness. Replay preserves the original HTTP transport error class.

Optional Tempo metrics queries returned HTTP 500 in the captures. Traces were searchable/fetchable and required source-health checks passed; no failed response was converted to fabricated telemetry. The original 500 response bodies were not recorded. A later direct query returned HTTP 200 with empty series. The intermittent 500 cause remains **unverified** and is retained in per-case source errors and the follow-up probe record.

An intermediate fixture export lost integer nanosecond precision through JavaScript numbers. The checksum rejected it before scoring. The committed fixture was exported with Python integer preservation and matches the original capture digest. Failed reports remain in ignored `validation-artifacts/attempts/`.

## Validation and limits

- Docker/PostgreSQL backend and harness: **339 passed**, one existing Starlette deprecation warning.
- Real telemetry: **3 independent cases × 3 paths**, plus **6/6 original live regressions**. The latter retain their proposal, confirmation, idempotency, SSE, ownership, citation and isolation assertions.
- New tests cover scoring, partial diagnoses, abstention/error separation, percentile calculation, exact replay, transport failures, budgets, stale inventory, evidence tampering, fresh tenants and oracle load order. Offline end-to-end tests reject external HTTP/LLM requests.
- No frontend behavior changed; frontend/build/browser suites were not rerun. READMEs, architecture documents/diagrams, security gates and CI workflows remain unchanged.

Cases are runtime-blinded and self-authored, without external adjudication. Copilot shares the agentic causal evaluator; there are two causal engines and three operational paths. Single-root completeness, legacy optional-source planning and intermittent Tempo metrics failures remain weaknesses. Raw telemetry, inventory, failed attempts and full reports remain local; reproducible fixtures, scoring code and this compact result are committed. Local results do not establish general RCA accuracy or release readiness.
