# AI SRE RCA — v0.4.0

[![CI](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml) [![CodeQL](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml) [![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca)](https://github.com/marcocristofolini/ai-sre-rca/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Português do Brasil** · [English](README.md)

Investigador de incidentes Kubernetes **evidence-first** e **read-only**, inspirado em sistemas autônomos de RCA, mas desenhado para que telemetria e verificações tipadas — e não um LLM — determinem a causa raiz.

O serviço recebe um incidente, resolve ownership e topologia de runtime do Kubernetes, coleta evidências de Kubernetes, Prometheus, Loki, métricas de service graph do OpenTelemetry, Argo CD, GitHub e Karpenter, recupera incidentes históricos semelhantes no PostgreSQL/pgvector, avalia hipóteses explícitas e devolve um RCA estruturado.

## O que mudou na v0.4

- **Descoberta de traces com Tempo/TraceQL** ao redor da janela do incidente usando chamadas HTTP tipadas e read-only.
- Busca e parsing de traces no formato OTLP JSON padrão retornado pelo Tempo v2, sem depender do formato otimizado para LLM.
- Seleção determinística do **deepest failing span** e reconstrução da ancestralidade pai/filho.
- Confirmação entre traces: a mesma origem downstream precisa se repetir em traces representativos antes de um RCA puramente baseado em trace ser confirmado.
- **RED metrics via TraceQL** (rate, error rate e p95) e RED metrics nas arestas do service graph.
- Nós virtuais para banco de dados, mensageria e serviços externos.
- **Correlação Trace → Deployment** usando atributos OpenTelemetry como `k8s.deployment.name`, `k8s.namespace.name` e `service.version`.
- Nova UI em `/trace` e fluxo de demonstração em `/api/v1/incidents/demo/trace`.
- Guardrail: evidência de trace pode enriquecer um RCA Kubernetes já confirmado, mas não pode sobrescrevê-lo silenciosamente.

## Princípios de design

- **Investigador somente leitura.** Sem shell, SSH, `kubectl exec`, CREATE, PATCH, UPDATE, DELETE, Argo sync ou mutações em cloud.
- **Evidência antes da conclusão.** Toda hipótese determinística referencia IDs das evidências coletadas.
- **Sinal direto vale mais do que coincidência temporal.** Um deploy próximo ao incidente não é automaticamente causal.
- **Incidentes históricos são apenas pistas.** Similaridade nunca reutiliza um RCA antigo sem evidência nova.
- **O LLM propõe; ferramentas determinísticas verificam.** Hipóteses de LLM começam com score `0`, status `proposed` e não participam diretamente da seleção da causa raiz.
- **Integrações tipadas.** Cada sistema externo expõe operações estreitas de GET/query, em vez de uma interface genérica de execução de comandos.

## Arquitetura

```mermaid
flowchart TD
    A[Alertmanager / API] --> B[Normalizador de Incidente]
    B --> C[Investigation Engine<br/>Playbooks Determinísticos]

    C --> K[Kubernetes<br/>Pod / ReplicaSet / Deployment / Node]
    C --> O[Observabilidade<br/>Prometheus / Loki / OpenTelemetry]
    C --> CH[Fontes de Mudança<br/>Argo CD / GitHub]
    C --> KA[Karpenter<br/>NodeClaim / NodePool]

    K --> G[Awareness Graph]
    O --> G
    CH --> G
    KA --> G

    G --> E[Avaliador de Evidências]
    E --> M[Memória de Incidentes<br/>PostgreSQL + pgvector]
    E --> L[LLM Opcional<br/>Somente Propositor de Hipóteses]

    M --> R[RCA Baseado em Evidências]
    L --> R
    E --> R

    R --> API[REST API]
    R --> UI[UI Next.js]
```

Mais diagramas estão disponíveis em [docs/architecture.pt-BR.md](docs/architecture.pt-BR.md).

## Início rápido

```bash
cp .env.example .env
docker compose up --build
```

Acesse:

- UI: `http://localhost:3000`
- Swagger: `http://localhost:8080/docs`
- RCA demo Kubernetes: `http://localhost:8080/api/v1/incidents/demo`
- RCA demo trace-aware: `http://localhost:8080/api/v1/incidents/demo/trace`
- UI trace-aware: `http://localhost:3000/trace`

O stack do Docker Compose usa a imagem `pgvector/pgvector:pg17` e habilita automaticamente a extensão `vector`.

## Executando apenas o backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
DEMO_MODE=true uvicorn app.main:app --reload --port 8080
```

Execute os testes:

```bash
pytest -q
```

## Webhook do Alertmanager

```text
POST /api/v1/webhooks/alertmanager
```

Exemplo:

```yaml
receivers:
  - name: ai-sre-rca
    webhook_configs:
      - url: http://ai-sre-rca-backend.sre.svc.cluster.local:8080/api/v1/webhooks/alertmanager
        send_resolved: true
```

O playbook Kubernetes atual espera labels úteis como `cluster`, `namespace`, `pod` e, opcionalmente, `node`.

## Integrações reais

Configure:

```bash
DEMO_MODE=false
DATABASE_URL=postgresql+asyncpg://ai_sre:password@postgres:5432/ai_sre

KUBERNETES_API_URL=https://kubernetes.default.svc
KUBERNETES_TOKEN_FILE=/var/run/secrets/kubernetes.io/serviceaccount/token
KUBERNETES_CA_FILE=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt

PROMETHEUS_URL=http://prometheus-operated.monitoring.svc:9090
LOKI_URL=http://loki-gateway.logging.svc:3100
TEMPO_URL=http://tempo-query-frontend.tracing.svc:3200
TEMPO_TENANT_ID=
TRACE_ANALYSIS_ENABLED=true
TRACE_WINDOW_MINUTES=15
TRACE_SEARCH_LIMIT=8
TRACE_FETCH_LIMIT=4

ARGOCD_URL=http://argocd-server.argocd.svc
ARGOCD_TOKEN=...
GITHUB_TOKEN=...
```

### Service Graph do OpenTelemetry

O investigador consulta no Prometheus as métricas de service graph geradas por Tempo/Alloy:

```promql
sum(increase(traces_service_graph_request_total{server="landing-page"}[15m])) by (client, server, connection_type) > 0
or
sum(increase(traces_service_graph_request_total{client="landing-page"}[15m])) by (client, server, connection_type) > 0
```

O metrics-generator do Tempo, Grafana Alloy ou o conector `servicegraph` do OTel Collector precisa exportar essas métricas para o backend compatível com Prometheus apontado por `PROMETHEUS_URL`.

### Propagação causal trace-aware

A v0.4 também consulta diretamente a API read-only do Tempo. A engine procura primeiro traces com erro para o serviço afetado, busca um número limitado de traces representativos e faz o parsing da árvore OTLP de spans.

```mermaid
flowchart LR
    A[Alerta / Identidade do Serviço] --> Q[Busca TraceQL<br/>erros primeiro]
    Q --> T[Trace IDs Representativos]
    T --> P[Parser OTLP]
    P --> TREE[Árvore Pai / Filho]
    TREE --> LEAF[Deepest Failing Span]
    LEAF --> PATH[Caminho Causal até a Raiz]
    PATH --> REP{Repete em Vários Traces?}
    REP -->|Sim| RCA[RCA de Trace Confirmado]
    REP -->|Não| TEST[Testing / Evidência de Suporte]

    TREE --> DEP[Nós Virtuais DB / Mensageria / Externo]
    TREE --> K8S[Atributos de Recurso OTel]
    K8S --> D[Correlação com Deployment]
    PATH --> G[Awareness Graph]
```

Uma causa raiz baseada apenas em traces só é confirmada quando traces representativos independentes convergem para o mesmo serviço/operação downstream. Um único trace permanece em `testing`. Se o Kubernetes já produziu um RCA confirmado mais forte, os traces entram como contexto e não substituem a causa raiz existente.

Consultas TraceQL atuais:

```traceql
{ resource.service.name = "frontend" && status = error }
{ resource.service.name = "frontend" } | rate()
{ resource.service.name = "frontend" && status = error } | rate()
{ resource.service.name = "frontend" } | quantile_over_time(span:duration, .95)
```

A consulta por trace usa `GET /api/v2/traces/<traceID>`; o formato OTLP JSON padrão é interpretado programaticamente.

## Correlação de mudanças

Para um Pod gerenciado pelo Argo CD, a engine resolve:

```mermaid
flowchart LR
    P[Pod] --> RS[ReplicaSet]
    RS --> D[Deployment]
    D --> A[Argo CD Application]
    A --> REV[Revisão Git Implantada]
    REV --> C[Commit no GitHub]
    C --> W{Dentro da janela de correlação?}
    W -->|Sim| S[Apenas evidência de suporte]
    W -->|Não| X[Evidência contraditória]
```

`CHANGE_CORRELATION_WINDOW_MINUTES` tem valor padrão `180`.

Um deploy dentro da janela vira **evidência de suporte para uma hipótese de mudança**, e não uma causa automática. Evidências diretas, como uma eviction explícita do kubelet, continuam tendo peso maior.

## Awareness do Karpenter

A engine lista NodeClaims de `karpenter.sh/v1` e associa `status.nodeName` ao Node afetado. Ela registra:

- NodeClaim e NodePool
- estado Ready
- capacity type
- provider ID
- idade do claim
- condições do ciclo de vida
- eventos do Karpenter/Kubernetes
- quantidade de Pods co-localizados no Node

Um NodeClaim saudável, antigo e com `Ready=True` contradiz a hipótese `node-capacity-churn`.

## Memória de incidentes com pgvector

Cada incidente persistido recebe um embedding local normalizado de 128 dimensões baseado em um fingerprint. A implementação padrão não depende de serviços externos e pode ser substituída posteriormente por um modelo dedicado de embeddings.

```mermaid
flowchart LR
    I[Incidente Atual] --> F[Fingerprint Normalizado]
    F --> V[Embedding Local 128-d]
    V --> PG[(PostgreSQL + pgvector)]
    PG --> S[Busca por Similaridade de Cosseno]
    S --> H[Incidentes Históricos Mais Próximos]
    H --> C[Somente Contexto / Pistas]
    C --> E[Evidência Atual Continua Obrigatória]
```

O banco cria um índice HNSW `vector_cosine_ops` para escalar a busca.

## Expansão opcional de hipóteses via LLM

Desabilitada por padrão:

```bash
LLM_ENABLED=true
LLM_BASE_URL=http://ollama.ai.svc:11434/v1
LLM_MODEL=your-model
LLM_API_KEY=
```

Pode ser utilizado qualquer endpoint compatível com `/chat/completions` no formato da API OpenAI.

O modelo recebe contexto estruturado do incidente, entidades e resumos das evidências. Ele **não** recebe credenciais Kubernetes, ferramentas de infraestrutura, shell ou permissões de escrita.

```mermaid
flowchart TD
    CTX[Incidente + Entidades + Resumos de Evidência] --> LLM[LLM Propositor de Hipóteses]
    LLM --> P[Hipótese<br/>origin=llm<br/>status=proposed<br/>score=0]
    P --> HC[Checks Permitidos]
    HC --> T[Ferramentas Tipadas Read-only]
    T --> EV[Evidência Nova]
    EV --> D[Avaliador Determinístico]
    D -->|Validada| H[Estado Determinístico da Hipótese]
    D -->|Não validada| P

    LLM -. sem credenciais .-> N1[Sem acesso direto ao Kubernetes]
    LLM -. sem shell .-> N2[Sem execução de comandos]
    LLM -. sem mutação .-> N3[Sem permissões de escrita]
```

A engine determinística exclui hipóteses não verificadas do LLM da seleção da causa raiz.

## Playbooks determinísticos Kubernetes

| Cenário | Evidências utilizadas | Exemplos de causa raiz |
| --- | --- | --- |
| Pod eviction | reason/events do Pod, pressão do node, tendência de filesystem, política de recursos | DiskPressure / esgotamento de ephemeral-storage |
| OOMKilled | estado atual/anterior do container, limites de memória, working set, MemoryPressure | esgotamento de memória do container, pressão no node |
| CrashLoopBackOff | waiting state, restart count, último exit, eventos de probe, logs | erro da aplicação, OOM, falha de probe |
| FailedScheduling | eventos do scheduler e eventos de provisionamento do Karpenter | recursos insuficientes, taints, affinity, volume binding, capacidade cloud |
| HPA | AbleToScale, ScalingActive, ScalingLimited, réplicas atuais/desejadas/máximas | saturação em maxReplicas, falha de métrica, scaling bloqueado |
| KEDA | Ready/Active do ScaledObject, annotations de pause, eventos de fallback/erro | erro de scaler, pause, fallback, trigger inativo |
| NodeNotReady | condição Ready, taints, eventos do node, pressões, estado do NodeClaim | node inalcançável, kubelet não saudável, pressão, lifecycle disruption |

A engine escolhe o playbook usando o contexto do alerta e o estado real do Kubernetes. Evidência direta da plataforma continua tendo mais peso do que correlações temporais, como um deploy recente.

O demo continua reproduzindo o caso original de eviction por ephemeral-storage, enquanto a suíte de regressão cobre os novos cenários da v0.3.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
GET  /api/v1/incidents/demo/trace
GET  /api/v1/incidents/{id}
POST /api/v1/incidents/investigate
POST /api/v1/webhooks/alertmanager
```

Exemplo:

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

## Limite de segurança

Mantenha remediação em uma identidade de serviço separada. O Role do Helm para o investigador concede apenas:

```text
get
list
watch
```

para os recursos Kubernetes necessários. Não existe permissão de `pods/exec` nem verbo mutável.

```mermaid
flowchart LR
    INV[Identidade do Investigador<br/>get / list / watch] --> OBS[Fontes de Evidência Read-only]
    OBS --> RCA[RCA + Ações Recomendadas]
    RCA --> APPROVAL{Aprovação Humana}
    APPROVAL -->|Aprovado| REM[Identidade Separada de Remediação]
    REM --> ACT[Ação Restrita e Permitida]
    APPROVAL -->|Rejeitado| STOP[Sem Mutação]

    INV -. não pode .-> ACT
```

Em produção, armazene credenciais de Argo/Git/modelo em Kubernetes Secrets ou workload identity — nunca em Helm values commitados no Git.

## Estrutura do repositório

```text
backend/
  app/core/           engine determinística / playbooks / scoring
  app/integrations/   Kubernetes, Prometheus, Loki, Argo, GitHub, Karpenter
  app/llm/            propositor opcional de hipóteses
  app/memory/         embeddings + protocolo de memória
  app/db/             persistência PostgreSQL/pgvector
  tests/              testes de RCA + regressão de segurança
frontend/             UI de investigação em Next.js
helm/ai-sre-rca/      deployment + RBAC read-only
docs/                 arquitetura e roadmap
```

## Próxima evolução

A v0.4 conclui a primeira camada de propagação causal baseada em traces. Os próximos incrementos de maior valor são:

1. Adapter RCA de PostgreSQL: esgotamento de conexões, locks, replication lag, slow queries e saturação.
2. Adapter RCA de Kafka: consumer lag, ISR/liderança e propagação de falhas por partição.
3. Adapter RCA de Redis: pressão de memória, evictions, latência e contexto de hot keys.
4. ImagePullBackOff / ErrImagePull, bloqueios por PDB e diagnósticos mais profundos de PVC/volumes em runtime.
5. Correlação trace → log por derived fields e exemplars mais ricos por operação.
6. Metadados de PR do GitHub e correlação com rollout history do Kubernetes.
7. NetworkPolicy, hashing/audit trail de evidências, OIDC/RBAC e budgets por consulta.
8. Entrega em Slack/PagerDuty e plano separado de remediação com aprovação humana.
