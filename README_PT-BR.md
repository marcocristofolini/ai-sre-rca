# AI SRE RCA — v0.2.1

**Português do Brasil** · [English](README.md)

Investigador de incidentes Kubernetes **evidence-first** e **read-only**, inspirado em sistemas autônomos de RCA, mas desenhado para que telemetria e verificações tipadas — e não um LLM — determinem a causa raiz.

O serviço recebe um incidente, resolve ownership e topologia de runtime do Kubernetes, coleta evidências de Kubernetes, Prometheus, Loki, métricas de service graph do OpenTelemetry, Argo CD, GitHub e Karpenter, recupera incidentes históricos semelhantes no PostgreSQL/pgvector, avalia hipóteses explícitas e devolve um RCA estruturado.

## O que mudou na v0.2

- Grafo de dependências entre serviços via OpenTelemetry/Tempo usando `traces_service_graph_request_total`.
- Awareness Graph com relações Pod → ReplicaSet → Deployment → Argo CD → Git e Node → NodeClaim → NodePool.
- Correlação entre deploy/revisão do Argo CD e o horário do incidente.
- Metadados do commit GitHub correspondente à revisão implantada pelo Argo CD.
- Contexto de NodeClaim/NodePool do Karpenter e evidências de ciclo de vida.
- Memória de incidentes com PostgreSQL + pgvector e busca por similaridade de cosseno.
- Índice vetorial HNSW para o histórico de incidentes.
- Propositor opcional de hipóteses via LLM compatível com API OpenAI, com limite rígido de segurança: toda saída do modelo permanece `proposed` e não recebe evidência nem score automaticamente.
- UI com topologia de serviços, correlação de mudanças, memória de incidentes e checks propostos pelo LLM.
- Testes de regressão que garantem que hipóteses geradas pelo modelo não possam se tornar causa raiz diretamente.

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
- RCA de demonstração: `http://localhost:8080/api/v1/incidents/demo`

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

## Playbook determinístico atual: Pod eviction

Ele avalia:

- crash da aplicação
- MemoryPressure
- PIDPressure
- DiskPressure / ephemeral-storage
- ausência de requests/limits de ephemeral-storage
- regressão recente por deploy/código
- churn de capacidade / Karpenter

O demo inclui intencionalmente um deploy antigo no Argo CD e um NodeClaim saudável, para que ambas as correlações tentadoras sejam rejeitadas enquanto a evidência direta de ephemeral-storage é confirmada.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
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

O próximo incremento de maior valor é adicionar mais playbooks determinísticos e evidências mais ricas de traces:

1. CrashLoopBackOff / OOMKilled / FailedScheduling / NodeNotReady.
2. Playbooks de provisionamento e saturação para HPA/KEDA/Karpenter.
3. Exemplars e drill-down por trace ID usando Tempo/TraceQL.
4. Adapters para PostgreSQL, Kafka e Redis.
5. Metadados de PR do GitHub e correlação com rollout history do Kubernetes.
6. NetworkPolicy, hashing/audit trail de evidências e budgets por consulta.
7. Entrega em Slack/PagerDuty e plano separado de remediação com aprovação humana.
