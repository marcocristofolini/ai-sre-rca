# AI SRE RCA

[![GitHub-first gate](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml)
[![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca?include_prereleases)](https://github.com/marcocristofolini/ai-sre-rca/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Português do Brasil](README_PT-BR.md) · **English**

**AI SRE RCA** is an SRE incident-investigation platform for Kubernetes, distributed observability and stateful dependencies. It collects live evidence, reconstructs causal relationships, keeps persistent operational context and produces a structured RCA without allowing an LLM to invent the root cause.

> **Agentic for deciding where to investigate. Evidence-first for collecting facts. Deterministic for deciding what can be asserted.**

Current status: **v0.8.0-rc.3 in development**.

---

## The problem it solves

During an incident, an engineer usually needs to jump between Kubernetes, metrics, logs, traces, recent changes, stateful dependencies and historical incidents. The hard part is not only finding data. It is separating correlation from causality and refusing conclusions based only on temporal coincidence.

AI SRE RCA automates that investigation while keeping an explicit boundary between **context** and **causal evidence**.

It can answer questions such as:

- did the Pod fail because of OOM, CrashLoop, eviction or a Node issue?
- did the failure originate inside the service or in a downstream dependency?
- do PostgreSQL, Kafka or Redis have current telemetry that confirms the cause?
- is a recent deployment causal or merely close in time?
- what is the likely blast radius in the known topology?
- has a similar incident happened before?
- is the previous cause still valid after the system recovers?
- is there a safe and explicitly allowed remediation action?

---

## Incident history and investigation form

Open `/incidents` to list authorized diagnoses, search by title or ID, and filter by cluster/status. Pagination uses 25 items and excludes full evidence payloads. `resolved` means diagnosis completion, not confirmed recovery.

At `/incidents/new`, investigators provide a title, cluster, alert, severity and known target. Tenant comes from the session; global sessions must provide a concrete tenant. Agentic mode appears only when enabled server-side. Submission uses Server Actions with server-side authentication forwarding, field validation and a pending submission lock. Completion opens the saved diagnosis.

The flow is synchronous and does not yet show live specialist progress. If completion cannot be confirmed, check history before retrying. In demo mode, list and form identify collection through demo adapters.

## Incident diagnosis workspace

The diagnosis workspace uses a scoped dark theme and a directed SVG topology built from the returned graph edges. The graph displays up to 48 connected entities and 96 relationships, announces truncation, and keeps every relationship in an expandable text list. Topology alone does not establish causality.

The interface presents alert context, diagnosis summary, entities and relationships, expandable hypotheses, runtime telemetry, evidence, timeline, final RCA and remediation recommendations. Ruled-out hypotheses start collapsed. Each hypothesis retains its supporting and contradicting evidence links; evidence exposes collected facts, source query, timestamp, reference and recorded hash.

- `/`, `/trace` and `/stateful/{postgresql|kafka|redis}` use demo adapters and visibly identify that data origin.
- `/incidents/{incident_id}` reads a persisted classic investigation through `GET /api/v1/incidents/{incident_id}`, forwarding authentication server-side. The saved-investigation link on demo pages opens the persisted result. Webhook/API investigations can be opened directly using their incident ID.
- A confirmed-cause badge requires a completed result whose root hypothesis is confirmed by a playbook. Inconclusive results and LLM proposals cannot receive that badge. `resolved` means the investigation concluded; it does not establish service recovery.
- Recommendations remain informational in this view. Counts describe observed context rather than confirmed blast radius or downtime. Displaying a recorded hash does not reverify evidence integrity.

CI checks navigation, hypothesis/evidence expansion, inconclusive and restricted-access states, authentication forwarding and mobile layout. Actual Chromium screenshots and the report are uploaded as `diagnosis-ui-<run_id>` by the [GitHub-first gate](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml). Screenshots use the demo engine rather than production telemetry. The agentic workspace includes execution and evaluator decisions. The incident list and form are available; live progress remains pending.


## What the application does

| Capability | What it delivers |
| --- | --- |
| **Kubernetes RCA** | Investigates OOMKilled, CrashLoopBackOff, eviction, FailedScheduling, NodeNotReady, HPA, KEDA, Karpenter and capacity signals. |
| **Trace-aware RCA** | Reconstructs span trees and finds a downstream origin repeated across independent traces. |
| **Stateful dependencies** | Promotes PostgreSQL, Kafka and Redis into deterministic investigators when causal identity and current telemetry agree. |
| **Metrics and logs** | Queries Prometheus and Loki through narrow read-only adapters and server-owned scopes. |
| **Changes** | Correlates Argo CD and GitHub without treating temporal proximity as automatic proof. |
| **Persistent Awareness Graph** | Keeps entities, relationships, history, tombstones, authoritative inventory, changes and operational context across incidents. |
| **Blast radius** | Traverses only allowed, current causal relationships. |
| **Agentic investigation** | Plans Kubernetes, metrics, logs, traces, database and changes specialists with typed budgets and dependencies. |
| **Incident memory** | Retrieves similar incidents from PostgreSQL/pgvector as context, never as a substitute for current evidence. |
| **Audit and integrity** | Provides SHA-256 evidence hashes, tenant/cluster isolation, structured export and audit trail. |
| **Guarded remediation** | Keeps mutation separate from the investigator with allow-lists, dry-run, human approval, drift protection and rollback. |
| **Real validation** | Runs Kind, PostgreSQL/pgvector, Prometheus, Loki, Tempo/OpenTelemetry and controlled scenarios in GitHub Actions. |

---

## How an investigation works

`POST /api/v1/incidents/investigate` uses the classic engine and persists the incident, updating the graph. The experimental `/api/v1/planner/investigate` path requires `AGENTIC_EXECUTION_ENABLED=true`. It returns execution, evaluation, `incident_id` and `investigation_url`, persists the evaluated diagnosis and records incident-linked audit events. Open the returned link to inspect specialists, budgets, decisions and evidence in the same workspace. `/plan` and `/execute` still do not persist a diagnosis.

~~~mermaid
flowchart TD
    I["Incident with tenant and cluster scope"] --> MODE{"API route"}
    MODE -->|"/incidents/investigate"| CORE["Classic deterministic engine"]
    CORE --> SAVE["Persist incident and graph observations"]
    SAVE --> UI["API and investigator UI"]
    MODE -->|"/planner/investigate"| CTX["Freeze operational context"]
    CTX --> PLAN["Compile bounded specialist plan"]
    PLAN --> READ["Read-only specialists"]
    READ -->|"Traces provide dependency identity"| DEP["Database specialist"]
    READ --> ENV["Shared Evidence Envelope"]
    DEP --> ENV
    ENV --> VERIFY{"Integrity and causal gates pass?"}
    VERIFY -->|Yes| RESULT["Deterministic verdict and explanation"]
    VERIFY -->|No| INC["Inconclusive evaluation"]
    RESULT --> PERSIST["Persist evaluated diagnosis"]
    INC --> PERSIST
    PERSIST --> RESP["API, audit and incident UI"]
~~~

The optional LLM may help **prioritize investigation branches**, but it cannot create evidence, choose arbitrary tools, change budgets, write graph facts or confirm a root cause.

---

## Architecture

The platform is divided into four planes so investigation, persistent context, mutation and release validation remain separate.

~~~mermaid
flowchart TD
    IN["REST API or Alertmanager"] --> CORE["Classic investigation"]
    IN --> PLAN["Opt-in agentic investigation"]
    SRC["Typed read-only evidence sources"] --> CORE
    SRC --> PLAN
    subgraph READ["1. Read-only investigation"]
      CORE --> RCA["Classic RCA"]
      PLAN --> ENV["Specialists and shared envelope"]
      ENV --> EVAL["Deterministic evaluator"]
      EVAL --> AR["Persisted and audited agentic diagnosis"]
      AR --> STORE
    end
    subgraph AWARE["2. Persistent awareness"]
      RCA --> STORE["Incident persistence"]
      STORE --> PAG[("Awareness Graph and PostgreSQL/pgvector")]
      INV["Authoritative inventory"] --> PAG
      PAG --> CTX["Frozen operational context"]
      CTX --> PLAN
      PAG --> VIEW["Explorer, blast radius and change timeline"]
    end
    subgraph REM["3. Guarded remediation"]
      STORE --> POLICY["Typed proposal, policy, dry-run and approval"]
      POLICY --> ACT["Separate identity: Deployment scaling"]
    end
    subgraph VAL["4. Release validation"]
      GH["GitHub-first gate"] --> KIND["Disposable Kind and telemetry"]
      KIND --> HAR["Failure, recovery and topology checks"]
      HAR --> REP["JSON and Markdown evidence"]
    end
~~~

Detailed architecture: [docs/architecture.md](docs/architecture.md).

---

## Application running

The screenshots below show **v0.8.0-rc.1 running locally with `DEMO_MODE=true`**, using demo adapters. They are rendered application screens. Real-source validation is recorded separately in the Kind gate reports.

### Kubernetes investigation

![Kubernetes RCA showing an evidence-backed root cause](docs/images/investigator-k8s.png)

### Distributed trace causality

![Trace-aware RCA showing downstream failure propagation](docs/images/trace-aware-rca.png)

### Stateful PostgreSQL dependency

![PostgreSQL RCA showing connection-exhaustion evidence](docs/images/stateful-postgresql.png)

### Persistent Awareness Graph

![Persistent Awareness Graph explorer](docs/images/awareness-explorer.png)

### Integration health

![Operations UI showing source capabilities](docs/images/operations.png)

---

## Main components

### Investigation Engine

The deterministic core normalizes the incident, collects typed evidence, applies playbooks and computes support/contradiction. A hypothesis can become root cause only when its evidence references are valid and the required causal gates pass.

### Investigation Planner

Compiles a bounded plan with these allowed specialists:

- Kubernetes
- metrics
- logs
- traces
- database
- changes

The server recompiles the plan before execution. Clients never execute an arbitrary task graph.

### Trace-aware RCA

Searches representative traces in Tempo, rebuilds parent/child span relationships and identifies the deepest failing span. One trace is not enough to confirm causality. Promotion requires repeated origin across independent traces.

### Stateful Dependency RCA

Dependencies inferred from traces can be investigated with current telemetry:

- **PostgreSQL:** connection utilization, locks, deadlocks, replication lag and long transactions;
- **Kafka:** consumer lag, under-replicated partitions, offline partitions and consumer-group members;
- **Redis:** memory pressure, evictions, blocked clients, client saturation and command latency.

A stateful cause is promoted only when the dependency is causally relevant **and** current telemetry independently confirms the anomaly.

### Persistent Awareness Graph

Keeps operational topology across incidents with stable tenant/cluster identity, append-only history and authoritative reconciliation.

It includes:

- current, stale, historical and tombstoned state;
- Kubernetes entities and relationships;
- ChangeEvents;
- inventory reconciliation;
- graph health;
- retention;
- blast radius;
- change timeline;
- Awareness Explorer;
- Operational Context Snapshot used by the planner.

More detail: [docs/persistent-awareness-graph.md](docs/persistent-awareness-graph.md).

### Guarded remediation

The investigator remains read-only. Mutation lives in a separate service and identity.

The flow is:

~~~text
confirmed RCA
→ typed proposal
→ policy gates
→ Kubernetes dry-run
→ human approval
→ drift check
→ allow-listed mutation
→ audit
→ explicit rollback
~~~

The first supported action is <code>scale_deployment</code>. There is no shell, <code>kubectl exec</code>, generic patch or arbitrary mutation primitive.

More detail: [docs/remediation.md](docs/remediation.md).

---

## Integrations

| System | Purpose |
| --- | --- |
| Kubernetes | runtime state, ownership, events, scheduling, inventory |
| Prometheus | service, Kubernetes and dependency metrics |
| Loki | logs bounded by namespace, Pod or service |
| Tempo / OpenTelemetry | traces, causal paths and service graph |
| Argo CD | deployment state and applied revision |
| GitHub | commit/revision metadata |
| Karpenter | NodeClaim, NodePool and capacity |
| PostgreSQL + pgvector | incidents, awareness graph, audit and vector memory |
| Alertmanager | automatic incident ingestion |
| Slack / PagerDuty | optional notification after persistence |

---

## Safety model

The platform is designed to **fail closed**.

- the investigator uses only GET/LIST/WATCH in Kubernetes;
- there is no <code>pods/exec</code>;
- there is no investigator shell;
- evidence receives SHA-256 integrity hashes;
- integrity failure blocks root-cause selection;
- historical context cannot count as causal proof;
- a recent change is not automatically blamed;
- similar incidents are hints only;
- incomplete branches, exhausted budgets or unavailable sources do not fabricate healthy state;
- the LLM does not confirm RCA;
- remediation uses separate credentials and explicit approval;
- tenant and cluster are part of identity and access boundaries.

---

## Runtime modes

| Mode | Purpose |
| --- | --- |
| <code>DEMO_MODE=true</code> | Explore the UI and investigation flows without external systems. |
| Docker Compose | Run frontend, backend and PostgreSQL/pgvector locally. |
| Test environment | Deploy GitHub-validated images into a Kubernetes test cluster. |
| Helm / production | Connect real sources, OIDC, RBAC, Secrets, NetworkPolicy and multi-cluster routing. |

---

## Quick start

~~~bash
cp .env.example .env
docker compose up --build
~~~

Open:

~~~text
UI:       http://localhost:3000
Swagger:  http://localhost:8080/docs
Health:   http://localhost:8080/healthz
~~~

Demo surfaces:

~~~text
/                         Kubernetes RCA
/trace                    trace-aware RCA
/stateful/postgresql      PostgreSQL
/stateful/kafka           Kafka
/stateful/redis           Redis
/awareness                Persistent Awareness Graph
/ops                      source health
~~~

Compose starts in demo mode and defines the backend environment directly in `docker-compose.yml`. Editing `.env` alone does not activate real integrations; configure Compose or use the Helm overrides described in the runbook.

Backend only:

~~~bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
DEMO_MODE=true uvicorn app.main:app --reload --port 8080
~~~

Tests, from the repository root:

~~~bash
(cd backend && pytest -q)
~~~

---

## Test environment

The repository has a GitHub-first chain that builds and deploys validated images without depending on a developer workstation.

After the gate passes on `main`, `Publish test images` publishes images to GHCR. Deployment is started manually in **Actions → Deploy test environment** and requires cluster credentials configured in the GitHub Environment `test`. The workflow waits for rollouts and runs backend/frontend smoke checks.

Published images:

~~~text
ghcr.io/marcocristofolini/ai-sre-rca-backend:edge
ghcr.io/marcocristofolini/ai-sre-rca-backend:sha-<12-character-sha>
ghcr.io/marcocristofolini/ai-sre-rca-frontend:edge
ghcr.io/marcocristofolini/ai-sre-rca-frontend:sha-<12-character-sha>
~~~

Runbook: [docs/test-environment.md](docs/test-environment.md).

---

## Main APIs

### Incidents

~~~text
GET  /healthz
GET  /api/v1/incidents?q=&cluster=&status=&limit=25&offset=0
GET  /api/v1/incidents/{id}
POST /api/v1/incidents/investigate
POST /api/v1/webhooks/alertmanager
~~~

### Persistent Awareness Graph

~~~text
GET  /api/v1/awareness/graph
GET  /api/v1/awareness/health
GET  /api/v1/awareness/history/{target_id}
GET  /api/v1/awareness/topology
GET  /api/v1/awareness/services/{service_name}
GET  /api/v1/awareness/blast-radius/{target_id}
GET  /api/v1/awareness/changes
POST /api/v1/awareness/inventory/reconcile
POST /api/v1/awareness/retention/purge
~~~

### Agentic investigation

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
~~~

`/plan` only compiles the plan. `/execute` collects evidence and returns no root cause. `/investigate` also runs deterministic evaluation. The last two return 404 while `AGENTIC_EXECUTION_ENABLED=false`, the default. Model-assisted planning additionally requires `AGENTIC_MODEL_PLANNER_ENABLED=true` and `LLM_ENABLED=true`.

The full OpenAPI interface is available at <code>/docs</code>.

---

## Real-environment validation

The release gate uses a disposable Kind cluster and executes real or controlled scenarios.

It currently validates:

- OOMKilled;
- restart-backoff / CrashLoop safety;
- FailedScheduling;
- authoritative Kubernetes inventory;
- tombstones;
- Persistent Awareness Graph;
- Prometheus;
- Loki;
- Tempo/OpenTelemetry;
- service graph;
- trace causality;
- stateful PostgreSQL and Redis failure/recovery;
- classic/agentic parity;
- SHA-256 evidence integrity.

Reports are emitted as JSON and Markdown and tied to the validated Git SHA.

Full contract: [validation/README.md](validation/README.md).

---

## GitHub-first delivery

GitHub is the merge and release authority.

~~~text
branch
→ Pull Request
→ GitHub-first gate
   → CI
   → CodeQL
   → Real Environment Validation
→ gate
→ merge to main
→ Publish test images
→ Deploy test environment (manual)
~~~

Release promotion validates the SHA, <code>VERSION</code> and real-environment evidence before creating the tag.

Details: [docs/github-first.md](docs/github-first.md).

---

## Current status

**v0.8.0-rc.3** focuses on real stateful telemetry and recovery validation.

Current work includes:

- PostgreSQL 17 + postgres_exporter in the disposable environment;
- controlled connection pressure;
- deterministic PostgreSQL root-cause promotion;
- dependency recovery;
- fresh post-recovery traces;
- proof that stale causes stop being promoted;
- Redis with 16 MiB maxmemory and redis_exporter scoped to `cache:6379`;
- actual OOM write rejection, `redis-memory-pressure` RCA and recovery with successful fresh operations.

Next rc.3 targets:

- Kafka + kafka_exporter;
- Kafka failure/recovery semantics;
- release-candidate closure.

Full roadmap: [docs/roadmap.md](docs/roadmap.md).

---

## Repository layout

~~~text
backend/
  app/core/             engine, causality, playbooks and scoring
  app/agentic/          planner, specialists and evaluator
  app/integrations/     read-only adapters
  app/remediation/      separate remediation service
  app/db/               PostgreSQL/pgvector
  tests/                regressions and safety invariants

frontend/               Next.js UI
helm/ai-sre-rca/        chart, RBAC and deployment
validation/             real-environment release gate
docs/                   architecture, operations and runbooks
~~~

---

## Documentation

- [Architecture](docs/architecture.md)
- [Agentic investigation](docs/agentic-investigation.md)
- [Persistent Awareness Graph](docs/persistent-awareness-graph.md)
- [Guarded remediation](docs/remediation.md)
- [Operations and security](docs/operations.md)
- [Test environment](docs/test-environment.md)
- [GitHub-first delivery](docs/github-first.md)
- [Roadmap](docs/roadmap.md)
- [Validation harness](validation/README.md)
- [Changelog](CHANGELOG.md)

---

## Core principle

**Context guides. Evidence proves. The deterministic evaluator decides.**

That principle applies to Kubernetes, traces, stateful dependencies, changes, incident memory and agentic investigation.

