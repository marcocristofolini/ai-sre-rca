# AI SRE RCA

Evidence-first, read-only Kubernetes incident investigator inspired by modern autonomous RCA systems.

The project receives an Alertmanager incident, resolves affected Kubernetes entities, gathers evidence from Kubernetes, Prometheus, Loki and Argo CD, evaluates explicit hypotheses, and returns a structured root-cause analysis with supporting/contradicting evidence, timeline and remediation.

## Design principles

- Read-only by default. No shell, SSH, `kubectl exec`, delete, patch or rollout permissions.
- Evidence before conclusions. Every hypothesis links to evidence IDs.
- Deterministic scoring. The LLM is optional and never owns the confidence score.
- Typed tools. Integrations expose narrowly-scoped investigation operations.
- Reproducible investigations. Evidence includes source, query, timestamps and raw references.
- Demo-first. Run the entire RCA flow locally without a Kubernetes cluster.

## Architecture

```text
Alertmanager
    |
    v
Incident API ---> Entity Resolver
    |                 |
    v                 v
Investigation Engine / Awareness Graph
    |          |          |          |
 Kubernetes Prometheus   Loki      ArgoCD
    \___________ evidence __________/
                    |
                    v
          Hypothesis evaluator
                    |
                    v
          Evidence-backed RCA
                    |
              REST API / UI
```

## Quick start: demo mode

```bash
cp .env.example .env
docker compose up --build
```

Open:

- UI: http://localhost:3000
- API: http://localhost:8080/docs
- Demo incident: http://localhost:8080/api/v1/incidents/demo

Without Docker:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
DEMO_MODE=true uvicorn app.main:app --reload --port 8080
```

Then in another terminal:

```bash
cd frontend
npm install
API_URL=http://localhost:8080 npm run dev
```

## Alertmanager webhook

Point Alertmanager at:

```text
POST /api/v1/webhooks/alertmanager
```

Example:

```yaml
receivers:
  - name: ai-sre-rca
    webhook_configs:
      - url: http://ai-sre-rca-backend.sre.svc.cluster.local:8080/api/v1/webhooks/alertmanager
        send_resolved: true
```

The MVP expects labels such as `namespace`, `pod`, `cluster`, and optionally `node`.

## Real integrations

Set `DEMO_MODE=false` and configure:

```bash
KUBERNETES_API_URL=https://kubernetes.default.svc
KUBERNETES_TOKEN_FILE=/var/run/secrets/kubernetes.io/serviceaccount/token
KUBERNETES_CA_FILE=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
PROMETHEUS_URL=http://prometheus-operated.monitoring.svc:9090
LOKI_URL=http://loki-gateway.logging.svc:3100
ARGOCD_URL=https://argocd-server.argocd.svc
ARGOCD_TOKEN=...
DATABASE_URL=postgresql+asyncpg://ai_sre:password@postgres:5432/ai_sre
```

The Helm chart installs a ServiceAccount + ClusterRole containing only `get`, `list`, and `watch` permissions.

## Supported RCA in the first version

The strongest built-in playbook is Kubernetes pod eviction due to ephemeral-storage exhaustion. It evaluates:

- application crash
- memory pressure
- PID pressure
- disk pressure
- kubelet eviction
- missing ephemeral-storage requests/limits

It also recognizes common OOMKilled and FailedScheduling signals and returns a generic evidence summary for unknown alert types.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
GET  /api/v1/incidents/{id}
POST /api/v1/incidents/investigate
POST /api/v1/webhooks/alertmanager
```

Example manual investigation:

```bash
curl -X POST http://localhost:8080/api/v1/incidents/investigate \
  -H 'content-type: application/json' \
  -d '{
    "title": "Pod evicted",
    "cluster": "prod",
    "namespace": "exchange",
    "pod": "landing-page-7d8b6d9b-x9k2p",
    "node": "ip-10-0-1-42",
    "alert_name": "KubePodEvicted"
  }'
```

## Security model

Do not grant this service write access to Kubernetes or cloud APIs. If remediation is added later, run it as a separate component with explicit human approval and a different identity/RBAC policy.

## Repository layout

```text
backend/                 FastAPI investigation service
  app/core/              hypothesis engine, playbooks, scoring
  app/integrations/      read-only typed tools
  app/db/                persistence models/repository
  tests/                 deterministic RCA tests
frontend/                Next.js investigation UI
helm/ai-sre-rca/         Kubernetes deployment + read-only RBAC
docs/                    architecture and extension notes
```

## Next production steps

1. Add OpenTelemetry service topology from traces.
2. Persist topology relationships and incident history in PostgreSQL/pgvector.
3. Add GitHub/Argo change correlation.
4. Add incident similarity retrieval, but require fresh evidence for every conclusion.
5. Add additional playbooks for CrashLoopBackOff, NodeNotReady, HPA saturation, Kafka lag, PostgreSQL saturation and Redis evictions.
6. Introduce an optional LLM reasoner for proposing additional hypotheses; keep tool execution and scoring deterministic.
