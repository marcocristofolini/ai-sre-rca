# ADR-0001: Source-neutral telemetry interface

**Status:** Accepted (2026-11-17)
**Date:** 2026-11-17
**Deciders:** Marco (maintainer)
**Issue:** #73 (prerequisite for #74 Datadog and #75 CloudWatch)

## Context

`InvestigationTools` in `backend/app/integrations/base.py` is one Protocol that mixes three kinds of methods:

1. Kubernetes and platform reads (`get_pod`, `get_events`, `argocd_application`, `karpenter_nodeclaim_for_node`, ...).
2. Telemetry calls named after one product (`prometheus_query`, `prometheus_query_range`, `loki_query_range`, `tempo_search_traces`, `tempo_get_trace`, `tempo_metrics_query`, `service_graph`).
3. Dependency helpers that build PromQL internally (`postgres_dependency_metrics`, `kafka_dependency_metrics`, `redis_dependency_metrics`).

Observed in the code today (all paths from `backend/app`):

- **Callers author native query strings.** `core/engine.py` (filesystem capacity), `core/kubernetes_investigator.py` (memory working set), `core/trace_analysis.py` (TraceQL) and `integrations/http_tools.py` (service graph, dependency metrics) build PromQL, LogQL or TraceQL inline.
- **Callers parse Prometheus/Loki payload shapes.** `_prom_value` and `_prom_range_values` read `data.result[...]`; log counting reads `data.result[].values`.
- **Provenance lives in three `Evidence` fields:** `source` (`"prometheus"`, `"loki"`, `"tempo"`, `"opentelemetry"`), `query` and `raw_reference` (for example `prometheus:/api/v1/query_range`). All three feed `compute_evidence_hash`, so changing any value for an existing path changes `integrity_sha256`.
- **Some logic switches on source names.** `core/copilot_scope.py` requires the sources `{"prometheus", "loki"}`; `agentic/evaluator.py` uses `STATEFUL_SOURCES`; `core/trace_analysis.py` matches `source == "opentelemetry"`.
- **Budgets are enforced per method name** by the specialist wrappers in `agentic/specialists.py` (`self._budget.consume("prometheus_query")`) and counted per source in `core/query_budget.py`.
- **Test doubles subclass or reimplement the current method names** (for example `prometheus_query` overrides in `tests/test_kubernetes_v03.py` and `tests/test_review_hardening_v051.py`).

A Datadog or CloudWatch adapter cannot be added by implementing the same methods: Datadog and CloudWatch use their own query syntax (Datadog metric queries, CloudWatch metric math and Logs Insights). Something has to own "what to ask" separately from "how this backend spells it".

## Decision

Introduce three small capability protocols and a typed request catalogue. Callers ask for a **signal**; each backend translates it to its native query and returns a normalized result that carries its own provenance.

```text
MetricsSource.query(MetricRequest) -> MetricResult     # instant + range
LogsSource.query(LogRequest)       -> LogResult
TracesSource.search / get / rate   -> TraceResult      # existing tempo_* semantics
```

- `MetricRequest` is a frozen, typed catalogue entry (for example `ContainerMemoryWorkingSet(namespace, pod)`, `NodeFilesystemAvailable(node)`, `ServiceRequestRate(service)`), not a free-form string. A free-form `NativeQuery(dialect, text)` escape hatch exists only for the two existing Prometheus call sites that cannot be catalogued yet, and is rejected by non-Prometheus adapters.
- `MetricResult`/`LogResult` hold normalized values (`series: list[Series]`, `entries: list[LogEntry]`) plus a `Provenance` block: `source`, `native_query`, `raw_reference`, `window`, `truncated`.
- `Evidence` is **not** given new fields. Callers copy `provenance.source`, `provenance.native_query` and `provenance.raw_reference` into the existing `source`, `query` and `raw_reference` fields. Evidence IDs, hashing and the integrity checks stay as they are.
- Kubernetes, Argo CD, GitHub and Karpenter methods stay in `InvestigationTools`. Only categories 2 and 3 above move behind the new protocols.
- `InvestigationTools` keeps its current telemetry method names as a thin facade that delegates to the new sources. Existing callers, budget wrappers and test doubles keep working unchanged while migration proceeds.
- Budgets move to the capability level (`metrics.query`, `logs.query`, `traces.search`, ...) **inside** the new sources, with the existing method-name budget keys kept as aliases until the facade is removed, so snapshots and tests that read them do not change in step 2.
- Source selection is per cluster in trusted config (`CLUSTER_CONFIG_JSON`), never from request or LLM input. `capability_report` lists each configured source and its status; `copilot_scope` asks for the capability ("metrics", "logs") rather than the product name.

## Options considered

### Option A: implement the existing method names per backend
| Dimension | Assessment |
|---|---|
| Complexity | Low now, high later |
| Cost | Cheapest first step |
| Scalability | Poor: every playbook still emits PromQL |

**Pros:** no change to callers. **Cons:** a Datadog adapter would have to translate PromQL strings at runtime, which is fragile; provenance would claim a query the backend never ran.

### Option B: pass native queries with a `dialect` tag
| Dimension | Assessment |
|---|---|
| Complexity | Low |
| Cost | Low |
| Scalability | Poor: playbooks need one query per dialect |

**Pros:** small interface. **Cons:** pushes dialect knowledge into every playbook and evidence builder; no guarantee two backends answer the same question.

### Option C (chosen): typed signal requests, per-backend translation, normalized results
| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Cost | One migration pass over about eight call sites |
| Scalability | Good: a new backend implements one translator per signal |

**Pros:** playbooks stay deterministic and backend-agnostic; each adapter's provenance records the query it really ran; request types are easy to allow-list and budget. **Cons:** the signal catalogue must be curated, and results from different backends may differ slightly in resolution.

## Trade-off analysis

C costs more than A or B once, but is the only option where evidence stays honest across backends. Under A the stored `query` could name a query that never ran; under B correctness depends on each playbook author keeping translations in sync. C keeps the invariant that matters for this product: every claim traces to a recorded, backend-native query and a hashed evidence item.

## Evidence IDs, provenance and query budgets

- **Evidence IDs and hashes:** unchanged shape. For Prometheus, Loki and Tempo the translator must emit byte-identical query strings and `raw_reference` values to today, so existing `integrity_sha256` values and tests do not change. A test asserts this per catalogued signal.
- **Provenance:** every result carries source, native query, raw reference and window. An adapter that cannot supply a native query returns no result instead of an empty `query`.
- **Fail closed:** an unmapped signal, unsupported dialect, truncated result or backend error yields "no evidence" (surfaced in `capability_report`), never a guessed value. Missing telemetry cannot support a confirmed verdict.
- **Budgets:** counted per capability and per source with the existing `QueryBudgetState`; a denied call returns no data and is recorded in `denied`. Adapters add bounded windows and result limits (required by #74 and #75).
- **Read-only:** adapters expose no write methods and use read-only credentials from trusted config.

## Migration plan (step 2 of #73, separate session and PR)

1. Add `integrations/telemetry/` with the three protocols, `MetricRequest` catalogue, `Provenance` and result types. No call-site changes.
2. Implement `PrometheusSource`, `LokiSource` and `TempoSource` by moving the existing code in `http_tools.py` behind them; keep query strings and `raw_reference` identical.
3. Keep the old `InvestigationTools` telemetry methods as a facade delegating to the sources; `DemoTools` and test doubles are untouched.
4. Move call sites one module at a time (`engine.py`, `kubernetes_investigator.py`, `trace_analysis.py`, stateful dependency helpers) to typed requests. The full backend suite must pass unmodified after each module.
5. Add a contract test that every source passes (provenance present, fail-closed on unsupported signal, budget respected). #74 and #75 reuse it.

## Consequences

- **Easier:** adding Datadog and CloudWatch; allow-listing and budgeting telemetry by capability; recording what each backend actually ran.
- **Harder:** adding a new signal requires a catalogue entry and a translator in every supported backend (or an explicit "unsupported" there).
- **To revisit:** whether `service_graph` and the stateful dependency helpers belong in the catalogue or stay Prometheus-only until a second backend needs them; whether the facade can be deleted once no test double depends on the old names.

## Decisions on the open questions

1. **Catalogue scope:** start with the signals the current playbooks already use and add entries only when a playbook needs them. A broad speculative catalogue would force every backend to translate signals nobody asks for.
2. **No `NativeQuery` escape hatch.** A free-form string would let PromQL leak back into callers and make non-Prometheus adapters unverifiable. The remaining PromQL/TraceQL call sites (`service_graph`, the Postgres/Kafka/Redis dependency helpers, `tempo_*`) are catalogued in follow-up PRs before the facade is removed.
3. **Budget keys stay public and stable.** Snapshots and tests read the method-name keys (`prometheus_query`, `loki_query_range`, ...). Capability-level keys are added alongside them; the old keys are removed only in a deliberate, documented breaking change.

## Implementation status (step 2 of #73)

Delivered in the first implementation PR, with the existing test suite passing unmodified:

- `app/integrations/telemetry/`: types, protocols, `PrometheusMetricsSource`, `LokiLogsSource`.
- Signals catalogued: `NodeFilesystemAvailable`, `ContainerMemoryWorkingSet`, `PodLogs`.
- Migrated call sites: `core/engine.py` (filesystem capacity, logs) and `core/kubernetes_investigator.py` (memory working set, logs).
- Query strings and `raw_reference` values are byte-identical to the previous code (golden tests).

Still to do before #74 and #75 can rely on the interface: Tempo source and `TracesSource` protocol, `service_graph`, the stateful dependency helpers, capability-level budget keys, and a shared contract test that every source must pass.

## Action items

1. [ ] Review and accept or amend this ADR.
2. [x] Step 2 of #73, first slice: interface, Prometheus and Loki sources, four migrated call sites.
3. [ ] Remaining slices listed under Implementation status.
4. [ ] Unblock #74 (Datadog) and #75 (CloudWatch).
