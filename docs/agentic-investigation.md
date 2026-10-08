# Agentic Investigation — v0.8.0-rc.2

[Português do Brasil](agentic-investigation.pt-BR.md) · **English**

v0.8.0-rc.1 includes a bounded Investigation Planner plus an experimental parallel specialist executor. Planning is available by default; specialist execution is implemented but **disabled by default** and requires `AGENTIC_EXECUTION_ENABLED=true`. An optional model-assisted planning advisor is also disabled by default and requires both `LLM_ENABLED=true` and `AGENTIC_MODEL_PLANNER_ENABLED=true`. All layers remain read-only and only the deterministic evaluator can confirm root cause.

The design goal is:

> AI decides where to investigate. Typed tools collect facts. Deterministic evidence decides what can be asserted.

## Current planner

The planner consumes:

- the normalized incident;
- server configuration and feature flags;
- a bounded service neighborhood from the Persistent Awareness Graph;
- reconciled current, stale, historical and tombstoned topology state;
- bounded append-only observation history for selected graph targets;
- recent service-scoped ChangeEvents around the incident window;
- Graph Health counts and authoritative inventory-source freshness.

It can emit only these specialist task types:

- `kubernetes`
- `metrics`
- `logs`
- `traces`
- `database`
- `changes`

There is no generic tool name, command string, shell field, arbitrary URL or mutation action in the plan schema.

## Frozen Operational Context Snapshot

The planner freezes the persistent context into the `InvestigationPlan` before execution. The executor passes the same snapshot to every specialist and copies it into the Shared Evidence Envelope, so the investigation cannot silently switch to a different graph view halfway through a run.

~~~mermaid
flowchart LR
    PAG[(Persistent Awareness Graph)] --> TOPO[Bounded topology<br/>fresh / stale / historical / tombstoned]
    PAG --> HIST[Bounded target history]
    PAG --> CH[Recent ChangeEvents]
    GH[Graph Health + inventory freshness] --> SNAP[Frozen Operational Context Snapshot]
    TOPO --> SNAP
    HIST --> SNAP
    CH --> SNAP
    SNAP --> PLAN[InvestigationPlan]
    PLAN --> K[Kubernetes]
    PLAN --> M[Metrics]
    PLAN --> L[Logs]
    PLAN --> T[Traces]
    PLAN --> D[Database]
    PLAN --> C[Changes]
    SNAP --> ENV[Shared Evidence Envelope]
~~~

The snapshot is context, not a root-cause authority. Tombstoned entities remain visible for explanation but are excluded from `required_entity_ids`. Persistent ChangeEvents are emitted as `context_only` evidence when the changes specialist consumes them. Context-only evidence is explicitly removed from hypothesis scoring, so a historical deployment, tombstone or stale graph fact cannot confirm a causal hypothesis by itself.

The snapshot is bounded by `AGENTIC_MAX_CONTEXT_NODES`, `AGENTIC_CONTEXT_DEPTH`, `AGENTIC_CONTEXT_MAX_HISTORY_TARGETS`, `AGENTIC_CONTEXT_HISTORY_PER_TARGET` and `AGENTIC_CONTEXT_CHANGE_LIMIT`.

## Deterministic specialist scoping

A specialist can inherit missing query identity from the frozen graph context only under deterministic rules. Explicit incident identity always wins. A graph-derived Pod or Argo CD application is accepted only when exactly one matching entity is `fresh` and the topology projection is not truncated.

~~~mermaid
flowchart TD
    INC[Explicit incident identity] --> HAS{Target already known?}
    HAS -->|Yes| SCOPE[Typed SpecialistScope]
    HAS -->|No| GRAPH[Fresh graph candidates]
    GRAPH --> COMPLETE{Topology complete within budget?}
    COMPLETE -->|No| BLOCK[Mark ambiguous / do not infer]
    COMPLETE -->|Yes| UNIQUE{Exactly one candidate?}
    UNIQUE -->|No| BLOCK
    UNIQUE -->|Yes| SCOPE
    SCOPE --> LIVE[Live source verification<br/>Kubernetes / Loki / Argo CD]
    LIVE --> EVIDENCE[Typed current evidence]
~~~

The graph-derived value is query narrowing, not evidence. Kubernetes still reads the resolved Pod live, Logs still query Loki for that exact Pod, and Changes still verifies the resolved application against Argo CD. If multiple fresh Pods/applications exist, or the neighborhood was truncated, the resolver records ambiguity and does not guess.

## Optional model-assisted compilation

The model never creates a `SpecialistTask`. The deterministic planner first builds the complete eligible task set from incident identity, feature flags and graph context. The model sees only sanitized context plus those eligible specialist descriptions and may return specialist names with short rationales.

~~~mermaid
flowchart LR
    I[Incident + Frozen Operational Context] --> EL[Deterministic Eligible Task Set]
    EL --> ADV{Model Planner Enabled?}
    ADV -->|No| DP[Deterministic Plan]
    ADV -->|Yes| LLM[Advisory LLM<br/>specialist names only]
    LLM --> COMP[Server-side Compiler]
    EL --> COMP
    COMP --> AL[Specialist Allow-list]
    COMP --> BASE[Mandatory Baseline<br/>Kubernetes / Metrics / Traces when eligible]
    COMP --> DEP[Typed Dependencies<br/>traces before database]
    COMP --> BUD[Server-owned Budgets]
    AL --> OUT[InvestigationPlan]
    BASE --> OUT
    DEP --> OUT
    BUD --> OUT
    COMP --> TRACE[Advisory Trace<br/>requested / accepted / rejected / forced]
~~~

Unknown or ineligible specialist names are rejected. Model-provided text never replaces task objectives, query limits or dependencies. If the model call fails, returns malformed JSON or proposes no eligible specialist, the full deterministic plan is preserved.

## Planning flow

~~~mermaid
flowchart TD
    I[IncidentInput] --> P[Bounded Investigation Planner]
    G[Persistent Awareness Graph] --> C[Frozen Operational Context]
    C --> P
    S[Server-side Budgets] --> P

    P --> K[Kubernetes Task]
    P --> M[Metrics Task]
    P --> L[Logs Task]
    P --> T[Traces Task]
    T --> D[Database / Stateful Task]
    P --> CH[Changes Task]

    K --> OUT[InvestigationPlan]
    M --> OUT
    L --> OUT
    T --> OUT
    D --> OUT
    CH --> OUT

    OUT -->|opt-in execute| ORCH[Parallel Specialist Orchestrator]
    ORCH --> SE[Specialist Results]
    SE --> EV[Shared Evidence Envelope]
    EV --> HASH{Integrity OK?}
    HASH -->|No| FAIL[Inconclusive / Invalid Evidence]
    HASH -->|Yes| SCORE[Re-score Playbook Hypotheses]
    SCORE --> CAUSAL[Trace / Stateful Causal Gates]
    CAUSAL --> PRE[Source Precedence Gate]
    PRE --> DET[Deterministic Verdict]
    DET --> EX[Rejected-hypothesis Explanation Graph]
~~~

The executor recompiles the plan server-side immediately before execution. Clients cannot submit arbitrary task lists, budgets, dependencies or tool names. The shared evidence envelope intentionally has no `root_cause` field. The separate evaluator can produce a verdict only after integrity, causal and precedence gates pass.
## Safety invariants

- Planner output is restricted to a fixed specialist allow-list.
- Every task is marked `read_only=true`.
- The server limits task count, parallelism, graph context size and per-specialist query budget.
- The Kubernetes specialist delegates to the same deterministic Kubernetes stage used by the core engine; it does not maintain a second simplified RCA implementation.
- Kubernetes root-cause specificity priority is shared with the classic playbook selector, so mechanism hypotheses cannot outrank a more specific confirmed cause only because they have a higher raw score.
- Any hypothesis emitted by a specialist whose final status is not `completed` is blocked by the specialist-context gate; partial evidence from `failed`, `skipped` or `budget_exhausted` branches is never selectable.
- Kubernetes planning is selected only when the incident has Kubernetes runtime identity.
- Logs require scoped namespace plus pod/service identity.
- Traces require a service identity and enabled trace analysis.
- Database/stateful work is planned only if the trace branch exists; the database task explicitly depends on `task-traces`.
- The planner cannot confirm hypotheses or root cause.
- Branch-local hypotheses produced by deterministic specialists are marked advisory-only.
- The executor verifies evidence hashes before evidence enters the shared envelope.
- A specialist that exceeds its local query budget becomes `budget_exhausted`; dependent branches are skipped.
- The global incident query budget remains active in addition to each specialist's logical budget.
- The execution API recompiles the plan server-side; caller-authored plans are never executed.
- The planner/executor cannot request remediation or bypass the separate remediator service.
- The evaluator re-scores hypotheses from the shared envelope instead of trusting specialist-local status.
- Any failed evidence-integrity check or missing support/contradiction reference blocks root-cause selection.
- Trace-only RCA still requires repeated origin across independent representative traces.
- Stateful promotion still requires trace-origin identity plus dependency-specific current telemetry.
- Direct Kubernetes failure signals block downstream trace/stateful promotion until a higher-precedence playbook can be selected.
- If a Kubernetes specialist was planned but failed, was skipped unexpectedly or exhausted its context, downstream trace/stateful promotion is blocked; missing infrastructure evidence is never treated as a clean infrastructure result.

## API

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
~~~

All three endpoints require the investigator role and existing cluster authorization. Ordinary callers have their tenant overwritten by the authenticated principal. A global principal must still supply a concrete tenant. `/execute` and `/investigate` return 404 unless `AGENTIC_EXECUTION_ENABLED=true`.

Example logical result:

~~~json
{
  "planner_origin": "deterministic",
  "tasks": [
    {"specialist": "kubernetes", "read_only": true},
    {"specialist": "metrics", "read_only": true},
    {"specialist": "logs", "read_only": true},
    {"specialist": "traces", "read_only": true},
    {
      "specialist": "database",
      "depends_on": ["task-traces"],
      "read_only": true
    },
    {"specialist": "changes", "read_only": true}
  ]
}
~~~
## Budgets

The current server-side defaults are:

| Setting | Default |
| --- | ---: |
| `AGENTIC_EXECUTION_ENABLED` | false |
| `AGENTIC_MODEL_PLANNER_ENABLED` | false |
| `AGENTIC_MODEL_PLANNER_TIMEOUT_SECONDS` | 10 |
| `AGENTIC_MODEL_PLANNER_MAX_SPECIALISTS` | 6 |
| `AGENTIC_MAX_TASKS` | 6 |
| `AGENTIC_MAX_PARALLEL_TASKS` | 4 |
| `AGENTIC_MAX_CONTEXT_NODES` | 50 |
| `AGENTIC_CONTEXT_DEPTH` | 3 |
| `AGENTIC_CONTEXT_MAX_HISTORY_TARGETS` | 12 |
| `AGENTIC_CONTEXT_HISTORY_PER_TARGET` | 2 |
| `AGENTIC_CONTEXT_CHANGE_LIMIT` | 20 |
| `AGENTIC_SPECIALIST_MAX_QUERIES` | 12 |
| `AGENTIC_KUBERNETES_MAX_QUERIES` | 30 |

These are hard server limits, not hints supplied by the client.

## Parallel execution

The executor runs independent tasks concurrently up to `AGENTIC_MAX_PARALLEL_TASKS`. Dependency tasks run in later waves. Today this means the database/stateful branch cannot execute until the traces branch has completed with typed trace analyses.

The Kubernetes branch uses a dedicated read-only budget because the full deterministic playbook stage can legitimately query Pod/Node state, events, ReplicaSet/Deployment ownership, HPA/KEDA, Prometheus, Loki, Argo CD, GitHub and Karpenter context. The default is 30 Kubernetes specialist queries, still bounded by the global incident query budget.

Specialist outputs contain evidence, entities, graph edges and typed analysis structures. They are merged into `SharedEvidenceEnvelope`; evidence with a failed integrity hash is excluded from the shared envelope and marks envelope integrity false.

The current execution layer still preserves:

- no shell/SSH/kubectl exec;
- no generic arbitrary HTTP tool;
- deterministic scoped queries only;
- query budgets per branch plus the existing global query budget;
- tenant/cluster isolation;
- evidence hashes and source identity;
- no root-cause output and no remediation action.

## Deterministic shared-envelope evaluation

`POST /api/v1/planner/investigate` composes the current three stages:

1. build the bounded server-side plan;
2. execute allow-listed read-only specialists in dependency-aware waves;
3. evaluate the shared envelope deterministically.

The evaluator does **not** trust specialist-local `confirmed` state. It filters to integrity-verified evidence, removes invalid references, recalculates the existing weighted score and then applies causal gates:

- trace-only hypotheses need the same origin across at least two representative trace IDs;
- stateful hypotheses need a typed stateful analysis with `causal_role=origin`, fresh trace-origin evidence and dependency-specific telemetry;
- direct Kubernetes signals such as eviction, failed scheduling, node NotReady or active node pressure block downstream promotion;
- stateful hypotheses outrank their less-specific trace-only parent when both are confirmed.

The result includes a deterministic `root_cause` only when those gates pass. It also returns an explanation graph with hypothesis, evidence, gate and verdict nodes connected by `supports`, `contradicts`, `blocks`, `selected` and `considered` edges.

## v0.8.0-rc.1 status and next validation

The release candidate already includes first-class deployment/change events, the read-only Persistent Awareness Graph explorer, authoritative inventory reconciliation with tombstones, the frozen Operational Context Snapshot, model-assisted compilation behind deterministic server gates, deterministic specialist scoping, parallel read-only specialist execution behind an opt-in flag, and shared-envelope evaluation.

The v0.8.0-rc.1 real-environment gate validates Kubernetes specialist parity and persistent graph reconciliation against a real disposable cluster. The next validation target, v0.8.0-rc.2, should run the planner/executor against real Prometheus, Loki and Tempo/OpenTelemetry data from an instrumented workload. The authority model remains unchanged: model/planner output selects investigation work, while typed live evidence and deterministic gates decide the verdict.


## Saved diagnosis and UI

After evaluation, `/planner/investigate` saves a diagnosis projection, including inconclusive outcomes, and returns `incident_id` and `investigation_url`. `GET /api/v1/incidents/{id}` and `/incidents/{id}` reopen it under the same tenant/cluster permissions as classic incidents. The feature flag controls new executions; saved diagnoses remain available to authorized viewers.

The projection retains evidence/hashes, trace/stateful analyses, evaluated hypotheses, timeline, budgets and versioned `agentic` metadata. Metadata contains specialist status/limits, decisions/reasons and the explanation graph. It is not a complete copy of the plan or frozen operational context. Evaluator blocks display as inconclusive; only the `selected` hypothesis can be highlighted as root. Investigation completion does not prove service recovery.

The path is synchronous, with no queue or live progress. `/plan` and `/execute` remain without diagnosis persistence. Notifications and remediation generation are not enabled by this flow.
