# Agentic Investigation — v0.8 foundation

[Português do Brasil](agentic-investigation.pt-BR.md) · **English**

v0.8 introduces a bounded Investigation Planner plus an experimental parallel specialist executor. Planning is available by default; specialist execution is implemented but **disabled by default** and requires `AGENTIC_EXECUTION_ENABLED=true`. Both layers are read-only and neither can confirm root cause.

The design goal is:

> AI decides where to investigate. Typed tools collect facts. Deterministic evidence decides what can be asserted.

## Current planner

The planner consumes:

- the normalized incident;
- server configuration and feature flags;
- a bounded one-hop service projection from the Persistent Awareness Graph when a service identity is available.

It can emit only these specialist task types:

- `kubernetes`
- `metrics`
- `logs`
- `traces`
- `database`
- `changes`

There is no generic tool name, command string, shell field, arbitrary URL or mutation action in the plan schema.
## Planning flow

~~~mermaid
flowchart TD
    I[IncidentInput] --> P[Bounded Investigation Planner]
    G[Persistent Awareness Graph] --> C[Bounded Service Context]
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
    EV -. separate authority boundary .-> DET[Deterministic Evaluator]
~~~

The executor recompiles the plan server-side immediately before execution. Clients cannot submit arbitrary task lists, budgets, dependencies or tool names. The shared evidence envelope intentionally has no `root_cause` field.
## Safety invariants

- Planner output is restricted to a fixed specialist allow-list.
- Every task is marked `read_only=true`.
- The server limits task count, parallelism, graph context size and per-specialist query budget.
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

## API

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
~~~

Both endpoints require the investigator role and existing cluster authorization. Ordinary callers have their tenant overwritten by the authenticated principal. A global principal must still supply a concrete tenant. `/execute` returns 404 unless `AGENTIC_EXECUTION_ENABLED=true`.

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
| `AGENTIC_MAX_TASKS` | 6 |
| `AGENTIC_MAX_PARALLEL_TASKS` | 4 |
| `AGENTIC_MAX_CONTEXT_NODES` | 50 |
| `AGENTIC_SPECIALIST_MAX_QUERIES` | 12 |

These are hard server limits, not hints supplied by the client.

## Parallel execution

The executor runs independent tasks concurrently up to `AGENTIC_MAX_PARALLEL_TASKS`. Dependency tasks run in later waves. Today this means the database/stateful branch cannot execute until the traces branch has completed with typed trace analyses.

Specialist outputs contain evidence, entities, graph edges and typed analysis structures. They are merged into `SharedEvidenceEnvelope`; evidence with a failed integrity hash is excluded from the shared envelope and marks envelope integrity false.

The current execution layer still preserves:

- no shell/SSH/kubectl exec;
- no generic arbitrary HTTP tool;
- deterministic scoped queries only;
- query budgets per branch plus the existing global query budget;
- tenant/cluster isolation;
- evidence hashes and source identity;
- no root-cause output and no remediation action.

## Next phase

The next step is to feed the shared evidence envelope into a dedicated deterministic aggregation/evaluation stage and build the rejected-hypothesis explanation graph. That stage will decide which branch-local hypotheses remain supported, ruled out or inconclusive without giving the planner or specialists root-cause authority.
