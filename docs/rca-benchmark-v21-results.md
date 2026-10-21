# RCA Benchmark v2.1 — root cause gap analysis

Base: published `1fb1e2f`. Local Docker/PostgreSQL and the existing disposable Kind telemetry stack; no production cluster, paid model, workflow or release gate was used. The [v2 baseline](rca-benchmark-v2-results.md), its fixtures, independent oracle files and historical reports remain unchanged. “Independent” means runtime-blinded, self-authored ground truth; no external adjudication is claimed.

## Confirmed defects and repairs

### Legacy Argo CD planning (two execution errors)

The baseline legacy capture independently reproduces `RuntimeError: specialist execution failed: [{"specialist": "changes", "error_type": "ConnectError"}]` in agentic and Copilot. Classic abstains. `_specialist_scope()` guessed an Argo CD application from `incident.service` even when no explicit mapping or fresh graph identity existed. The validation registry deliberately points absent Argo CD at `http://127.0.0.1:9`. This is a planner identity defect, not a faulty HTTP adapter or a real Argo CD server outage.

A fresh source probe attempted **GET `http://127.0.0.1:9/api/v1/applications/svc-23555ef527`**: connection refused, with an `httpx.ConnectError` caused by `httpcore.ConnectError`. There is no HTTP status, response body or Argo server log because no server is deployed. The [redacted request/response/exception capture](benchmarks/rca-v21-source-failures.json) preserves this distinction.

The planner now requires an explicit application mapping or unique fresh graph identity. Unmapped changes retain an explicit “Argo CD application identity is unavailable” specialist result; execution can remain partial. No unavailable integration is declared healthy. Tests retain failed execution for explicitly mapped unavailable Argo CD and reproduce the original guessed-identity failure on both operational paths. The executor success fixture now supplies its intended application identity, preserving every completion assertion.

### Tempo metrics HTTP 500

On the original fixture, all three RED queries (`rate`, error `rate`, and duration quantile) against **GET `/api/metrics/query_range`** returned **500** with:

```text
error finding generators in Querier.queryRangeRecent: empty ring
```

Exact URLs, query bounds, response bodies and exception chains are in the source capture. Relevant Tempo logs record the same public request and failed internal `/querier/api/metrics/query_range?...mode=recent` requests. Trace search/fetch and `/ready` can succeed while recent TraceQL metrics fail. A historical-window query returning HTTP 200 with empty series therefore did not establish metrics health.

The disposable fixture lacked a configured metrics generator. It now enables `local-blocks`, a generator WAL (`metrics_generator.storage.path`) and trace storage. The intermediate attempt without the WAL was rejected by the nonempty-series validation; its server log states `metrics-generator is not configured` and `no metrics_generator.storage.path configured, metrics generator will be disabled`. This failed attempt and logs are retained locally.

The repaired stack returned nonempty rate series through the production adapter. The HTTP adapter still raises HTTP 500; no new status allowlist, empty-data substitution or causal evidence is introduced. Fresh live source exceptions now fail collection explicitly, and the benchmark preserves bounded redacted source diagnostics and Tempo logs. Original frozen failures remain in the legacy capture: replay cannot repair historical telemetry or establish current source health.

## Diagnosis gaps retained

The evaluator and persisted/API diagnosis use one `root_cause`. Selection orders verified candidates by causal gates and source precedence. Materialization demotes globally blocked candidates; specialist-local “confirmed” does not authorize a second persisted root.

- **b002, all three paths:** the Pod OOM playbook returns `container-oom`; it collects node conditions but does not generate the separate `node-not-ready` candidate. A single selected root could not report both even if both candidates existed.
- **l002, classic:** server-resolved Deployment input has no single `incident.pod`. Classic Kubernetes dispatch does not expand the controller's verified members and falls through to the generic stage; repeated downstream trace causality becomes the primary root, leaving the actual OOM unreported.
- **l002, agentic/Copilot:** verified workload members produce OOM evidence. The trace candidate meets the repeated-origin gate, but direct Kubernetes OOM evidence blocks downstream promotion in the shared evaluator. Both controlled faults exist in the oracle; removing precedence or counting a specialist candidate as an independently selected root would bypass the current authority contract.

All six remain **partial**, not complete or incorrect primary diagnoses. Multi-root contracts, independently scoped causal proofs and classic controller dispatch remain follow-up work. This repair changes neither evaluator precedence nor expected outcomes.

## Inconclusive classification

The 22 baseline inconclusive outcomes are oracle-correct abstentions: b001/b004/b005/b006/b008 × three paths; l001/l003 × three paths; l004 classic. Operationally, **19 are correct abstentions with healthy/recovered, ambiguous or untrusted signals; three b004 paths are unsupported investigations because Pod evidence is missing**. Those three still abstain correctly. No baseline inconclusive outcome has a positive causal oracle, so there are zero positive-oracle coverage misses in this subset. The six partial diagnoses are separate coverage gaps. The repaired legacy agentic/Copilot paths add two abstentions only after removing the unjustified application lookup.

## Reproduction and local results

Run the existing Docker commands in [benchmark documentation](rca-benchmark.md) for synthetic, retained legacy and `--benchmark-v2 full` live runs. The full benchmark intentionally exits nonzero while multi-cause completeness fails. Live collection errors remain failures rather than being relabeled inconclusive. To produce the post-execution comparison, inside the existing backend test container:

```sh
python -m validation.benchmark_gap_v21 \
  --synthetic validation-artifacts/after/synthetic/benchmark-v2.json \
  --legacy validation-artifacts/after/legacy/benchmark-v2.json \
  --live validation-artifacts/after/live/benchmark-v2/benchmark-v2.json \
  --output validation-artifacts/comparison/rca-v21-local.json
```

The comparison reads ground truth only after execution, verifies saved evidence/citations again, rejects coverage or expected-cause changes, and keeps complete/partial/incorrect/inconclusive/rejected/error outcomes separate. Full raw captures, source logs and failed attempts stay in ignored `validation-artifacts/`; compact JSON/Markdown comparisons are committed. Existing READMEs, architecture diagrams, API/SSE contracts, security controls and release workflows are unchanged.

### Actual results (2026-10-21)

[Per-case JSON](benchmarks/rca-v21-local.json) and [Markdown comparison](benchmarks/rca-v21-local.md) cover all 36 unchanged trials.

| Outcome | v2 baseline | v2.1 repaired |
| --- | ---: | ---: |
| Complete diagnoses | 3 | 3 |
| Partial diagnoses | 6 | 6 |
| Incorrect primary diagnoses | 0 | 0 |
| Inconclusive, oracle-correct abstentions | 22 | 24 |
| Correct rejections | 3 | 3 |
| Execution errors | 2 | 0 |

Unsupported causal confirmations: **0/9 emitted diagnoses**, before and after. Integrity verification: **31/31 → 33/33** successful non-rejected trials; available Copilot citations belong to the saved authorized investigation. Fresh live source errors: **0**, versus nine captured Tempo metrics failures in the historical three-case live baseline. Historical failed observations remain explicitly listed separately in the retained-capture report. The strict complete-cause benchmark is still **FAIL** because of six partial diagnoses.

Docker/PostgreSQL/backend/harness: **361 passed**, five subtests passed, zero skips; one existing Starlette deprecation warning. Existing instrumented-workload telemetry unit checks: **4 passed**. Full live comparison: three cases × three paths; original real-telemetry Copilot regressions **6/6 matched** with existing UID ownership, explicit confirmation, persistent jobs, idempotency, SSE, evidence provenance, tenant/cluster isolation and expected missing-Loki HTTP 503 assertions. No frontend code changed; frontend builds/browser suites were not rerun.

| Frozen-source live execution | Baseline p50 / p95 s | Repaired p50 / p95 s | Samples per run |
| --- | --- | --- | ---: |
| Classic | 0.528 / 0.610 | 0.558 / 1.057 | 3 |
| Agentic | 0.565 / 0.638 | 0.560 / 1.023 | 3 |
| Copilot | 2.059 / 2.363 | 2.338 / 3.053 | 3 |

The p95 increase is an observed regression, not a statistically established effect of either fix. These are fresh live captures under the same protocol, with frozen-response execution and PostgreSQL preparation; they exclude telemetry collection and fault injection. The repaired comparative run plus six regressions took **402.95 s** (baseline approximately 405 s). That elapsed time excludes stack setup, source debugging and unsuccessful attempts; it does not establish an overall speedup. Per-case latencies and budget details are in JSON.

Replayed telemetry budget charges remain **219 → 219** with no denied operations in completed investigations. Model requests/tokens and model cost remain **zero**; no paid provider was called. Unique capture reads are separate from replayed budget charges. Infrastructure/telemetry money cost and energy were not measured; the extra metrics generator is part of the same disposable stack, not a second stack.

Remaining work: verified controller dispatch for classic RCA, node-fault candidate coverage, and a reviewed multi-root evidence/independence contract that preserves existing precedence and integrity gates. Broader externally adjudicated cases and repeated latency measurements are still needed. Local results do not establish production accuracy or a passing release gate.
