# AI SRE RCA — v0.8.0 (em desenvolvimento)

[![CI](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/ci.yml) [![CodeQL](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/codeql.yml) [![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca)](https://github.com/marcocristofolini/ai-sre-rca/releases) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Português do Brasil** · [English](README.md)

Investigador de incidentes SRE **evidence-first** e **read-only** para Kubernetes, traces distribuídos e dependências stateful, desenhado para que telemetria e verificações tipadas — e não um LLM — determinem a causa raiz.

O serviço recebe um incidente, resolve ownership e topologia de runtime do Kubernetes, reconstrói causalidade por traces, promove nós tipados de PostgreSQL/Kafka/Redis para investigadores determinísticos, coleta evidências de Kubernetes, Prometheus, Loki, Tempo/OpenTelemetry, Argo CD, GitHub e Karpenter, recupera incidentes históricos semelhantes no PostgreSQL/pgvector, avalia hipóteses explícitas e devolve um RCA estruturado.

## v0.8 em desenvolvimento — awareness persistente + planejamento agentic bounded

A v0.8 está evoluindo o projeto de um engine de RCA válido apenas durante o incidente para uma plataforma SRE autônoma e verificada por evidências. A branch de desenvolvimento agora inclui:

- **Persistent Awareness Graph temporal** com identidade canônica de entidades e relações entre incidentes.
- **Nós ChangeEvent first-class** derivados das evidências de deploy do Argo CD e da revisão Git correspondente, com identidade estável entre reobservações do mesmo deploy. A materialização exige aplicação, revisão e timestamp autoritativo da operação; vínculos com workload, serviço e repositório só são emitidos quando o grafo existente os resolve sem ambiguidade.
- **Isolamento por tenant + cluster** na identidade, persistência e consultas.
- **Estado atual + observações append-only** com ingestão idempotente.
- **Reconciliação de topologia** que classifica registros observados como `fresh` ou `stale` usando `last_seen`, enquanto dados incompletos de incidentes nunca implicam deleção.
- **Tombstones por inventário autoritativo** gerados somente por snapshots completos, admin-only e vindos de fontes allow-listed, com cobertura explícita, proteção contra snapshots antigos, reativação e histórico/auditoria append-only.
- **Reconciliação automática do inventário Kubernetes** por um CronJob read-only opt-in que pagina listas completas de recursos, rejeita paginação malformada ou sem progresso, verifica ownership de controller pelo UID do Kubernetes, deriva relações determinísticas de selector/scheduling e envia snapshots pelo mesmo contrato protegido de tombstones.
- **Graph Health do Awareness Graph** com contagens factuais por tenant/cluster de entidades e relações current, stale, historical e tombstoned, volume/horário da observação mais recente e freshness de cada fonte autoritativa. Nenhum score composto ou status derivado por LLM é introduzido.
- **Retention segura para explicabilidade** com endpoint admin-only em dry-run por padrão, janelas configuráveis de observações/tombstones, receipts autoritativos preservados indefinidamente e ao menos a observação mais recente mantida para cada entidade/relação.
- **Projeções centradas em serviço** com budgets de profundidade, nós e arestas.
- **Blast radius baseado no grafo** com direção causal explícita por tipo de relação. Relações desconhecidas, stale ou tombstoned continuam visíveis como contexto, mas nunca viram caminhos de impacto atual.
- **Semântica histórica para mudanças.** Change Events e suas arestas são apenas contexto, reconciliam como `historical` e nunca entram na allow-list causal do blast radius.
- **Awareness Explorer read-only em `/awareness`** com escopo por cluster/namespace/serviço, estado reconciliado fresh/stale/historical/tombstoned, relações persistidas e timeline bounded de ChangeEvents. A tela reutiliza a autorização viewer e o client server-side existente sem introduzir controles de escrita no grafo.
- **Investigation Planner bounded** que emite somente tarefas read-only allow-listed: Kubernetes, métricas, logs, traces, database e mudanças.
- **Paridade do specialist Kubernetes com o core engine.** O ramo agentic Kubernetes agora delega para a mesma etapa determinística usada pelo RCA clássico, cobrindo eviction/ephemeral-storage, OOMKilled, CrashLoopBackOff, FailedScheduling/capacidade do Karpenter, HPA, KEDA e NodeNotReady com as mesmas hipóteses e regras de evidência.
- **Política compartilhada de prioridade Kubernetes + fail-closed para ramos parciais.** Os caminhos clássico e agentic usam a mesma ordem de especificidade, e hipóteses de um specialist que não concluiu nunca podem ser selecionadas.
- **Dependências tipadas entre tarefas.** O ramo stateful/database só é planejado depois do ramo de traces que fornece identidade tipada da dependência.
- **Contexto persistente do grafo para o planner**, com limites rígidos no servidor para contexto, quantidade de tarefas, paralelismo e queries por especialista.
- **Execução paralela experimental de especialistas**, protegida por `AGENTIC_EXECUTION_ENABLED=false` por padrão. Ramos independentes rodam em paralelo; ramos dependentes só entram depois que os pré-requisitos concluem.
- **Shared evidence envelope** com verificação da integridade das evidências. Hipóteses branch-local são advisory-only e a resposta de execução propositalmente não possui campo `root_cause`.
- **Avaliador determinístico do shared envelope** que recalcula scores apenas com evidências verificadas, reaplica gates causais de trace/stateful, preserva a precedência do Kubernetes, falha de forma fechada quando um specialist de maior precedência não conclui e gera um explanation graph das hipóteses descartadas.
- **Três superfícies agentic distintas:** `plan` compila, `execute` apenas coleta evidências read-only dos especialistas e `investigate` executa ambos mais a avaliação determinística. Somente `investigate` pode retornar um verdict de causa raiz.
- **Planejamento assistido por modelo opcional**, desabilitado por padrão. O LLM pode sugerir somente nomes de especialistas e justificativas; o servidor intersecta essa sugestão com a allow-list existente, gates determinísticos de elegibilidade, ramos obrigatórios de baseline e budgets/dependências controlados pelo servidor antes de qualquer tarefa existir.

APIs atuais da v0.8:

```text
GET  /api/v1/awareness/graph
GET  /api/v1/awareness/health
GET  /api/v1/awareness/history/{target_id}
GET  /api/v1/awareness/topology
GET  /api/v1/awareness/services/{service_name}
GET  /api/v1/awareness/blast-radius/{target_id}
GET  /api/v1/awareness/changes
POST /api/v1/awareness/retention/purge
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
```

O planner continua sendo a superfície de preview/compilação. A execução paralela de especialistas é um runtime read-only opt-in e recompila o plano no servidor imediatamente antes de executar, portanto grafos de tarefas escritos pelo caller nunca são executados. Quando `AGENTIC_MODEL_PLANNER_ENABLED=true` e `LLM_ENABLED=true`, um modelo advisory pode priorizar ramos opcionais, porém as tarefas executáveis continuam sendo compiladas exclusivamente a partir de templates controlados pelo servidor. Kubernetes, métricas e traces permanecem obrigatórios sempre que forem deterministicamente elegíveis; escolhas desconhecidas ou inelegíveis são rejeitadas e respostas inválidas/falhas do modelo preservam o plano determinístico. O caminho `/investigate` então revalida o shared envelope, recalcula scores determinísticos, reaplica gates causais de trace/stateful, preserva a precedência do Kubernetes e gera um explanation graph antes de permitir um verdict de causa raiz. O LLM continua sem poder criar fatos no grafo, anexar evidências, invocar ferramentas arbitrárias ou mutar infraestrutura.

Design: [Persistent Awareness Graph](docs/persistent-awareness-graph.pt-BR.md) · [Investigação Agentic](docs/agentic-investigation.pt-BR.md).

## O que mudou na v0.7

A v0.7 adiciona um **plano de remediação guardada** sem tirar o investigator do modo read-only:

- outro processo FastAPI, outro Deployment e outro ServiceAccount Kubernetes;
- primeira ação permitida: scale tipado de Deployment usando somente deployments/scale;
- gates estáticos/dinâmicos e dry-run server-side antes da aprovação;
- papel OIDC remediator exato, com regra de quatro olhos habilitada por padrão;
- TTL do plano, rejeição/execução/rollback explícitos e audit log;
- detecção de drift por réplicas e resourceVersion antes da execução;
- metadata de rollback capturada antes da aprovação;
- Roles Helm por namespace limitados a get/patch em deployments/scale;
- credenciais em REMEDIATION_CLUSTER_CONFIG_JSON, nunca reaproveitadas pelo investigator.

Design e API: [docs/remediation.pt-BR.md](docs/remediation.pt-BR.md).

## v0.6.1 hardening de isolamento operacional

A v0.6.1 fecha lacunas de isolamento encontradas na auditoria pós-merge da v0.6.0 sem mudar o modelo read-only do investigador:

- **Leitura de incidentes com escopo de tenant no repositório.** Um caller fora do tenant do incidente recebe o mesmo not-found de um ID inexistente.
- **Purge de retenção com escopo de tenant.** Admins de tenant removem apenas incidentes e eventos de auditoria expirados do próprio tenant; somente um principal explicitamente global com `tenant_id="*"` pode executar retenção em toda a plataforma.
- **Configuração multi-cluster fail-closed.** `CLUSTER_CONFIG_JSON` malformado, registries que não são objetos e entradas de cluster que não são objetos agora falham na validação das settings, em vez de virarem silenciosamente um registry vazio.
- Testes de regressão fixam esses comportamentos de isolamento e configuração.
- Nenhum método de mutação de workload ou credencial de remediação foi adicionado.

## O que mudou na v0.6

- **OIDC + RBAC hierárquico** para API/UI com papéis configuráveis viewer, investigator e admin.
- **Isolamento por tenant e cluster** em leitura de incidentes, memória, auditoria e export.
- **Roteamento multi-cluster** por registry de integrações sem compartilhar estado entre investigações concorrentes.
- **Query budget por incidente** para chamadas totais, chamadas por fonte e tempo máximo; estouro do budget vira evidência explícita.
- **Integridade SHA-256 das evidências** e audit log isolado por tenant.
- **Formato de export de incidente** com verificação de integridade em `/api/v1/incidents/{id}/export`.
- **Entrega em Slack e PagerDuty** após persistência, com resultado da entrega registrado no audit log.
- **Políticas de retenção** para incidentes e auditoria.
- **Health/capabilities por fonte** e nova UI em `/ops`.
- **Templates Helm de NetworkPolicy opt-in** e injeção de Kubernetes Secret externo para credenciais operacionais.
- **Token de máquina para Alertmanager** separado do OIDC interativo.

Detalhes de implantação e segurança: [docs/operations.pt-BR.md](docs/operations.pt-BR.md).

## Patch de hardening v0.5.1

A v0.5.1 reforça a correção do RCA stateful após a revisão da v0.5: rotas inválidas da UI retornam 404, metadata de trace é mesclada em vez de descartada, nomes de métricas/labels do postgres_exporter foram alinhados ao collector atual e PostgreSQL/Redis não executam agregações Prometheus sem identidade de instância suficiente.

## O que mudou na v0.5

- **Promoção de dependências stateful.** Nós de banco/mensageria derivados dos traces agora podem virar alvos determinísticos de investigação para PostgreSQL, Kafka ou Redis.
- **RCA de PostgreSQL** para esgotamento de conexões, contenção por locks, deadlocks, replication lag e transações longas.
- **RCA de Kafka** para consumer lag, partições under-replicated, partições offline/sem líder e ausência de membros no consumer group.
- **RCA de Redis** para pressão de memória, evictions, clientes bloqueados, saturação de conexões/clientes e latência elevada de comandos.
- **Guardrail de promoção causal.** A causa stateful só é promovida quando a dependência é a origem do trace e a telemetria específica da dependência confirma independentemente a anomalia atual.
- **A precedência do Kubernetes permanece.** Uma causa de infraestrutura já confirmada, como DiskPressure reportado pelo kubelet, não é substituída por uma anomalia downstream.
- **Adapters Prometheus tipados e read-only** para métricas comuns de postgres_exporter, kafka_exporter e redis_exporter; métricas ausentes viram `unknown`, nunca sinais saudáveis inventados.
- Novo payload estruturado `stateful_dependencies` com health, papel causal, sinais, thresholds, evidências e hipóteses específicas.
- Nova UI de demonstração em `/stateful/{postgresql|kafka|redis}` e endpoints de demo para cada dependência.
- Budget máximo de dependências e thresholds configuráveis por variáveis de ambiente e Helm.

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
    A[Alertmanager / API] --> N[Normalizador de Incidente]
    N --> EL[Especialistas Deterministicamente Elegíveis]
    N --> E[Investigation Engine]

    G[(Persistent Awareness Graph)] --> C[Contexto Bounded do Serviço]
    C --> EL
    EL --> PC[Compilador de Plano no Servidor]
    EL -. contexto sanitizado .-> ADV[LLM Advisory Opcional]
    ADV -. somente nomes de especialistas .-> PC
    PC --> P[Investigation Plan Bounded]
    P --> T[Tarefas Allow-listed de Especialistas]
    T -->|opt-in| X[Especialistas Paralelos Read-only]
    X --> SE[Shared Evidence Envelope]
    SE --> V[Avaliador Determinístico do Shared Envelope]
    V --> EG[Explanation Graph de Hipóteses Descartadas]

    E --> K[Kubernetes]
    E --> O[Prometheus / Loki / Tempo]
    E --> CH[Argo CD / GitHub / Karpenter]
    K --> L[Grafo Local + Evidências]
    O --> L
    CH --> L

    L --> G
    G --> R[Reconciliação Fresh / Stale / Historical]
    G --> S[Projeções por Serviço]
    G --> B[Blast Radius]
    G --> CE[Timeline de Change Events]
    R --> AUI[Awareness Explorer Read-only]
    S --> AUI
    CE --> AUI

    L --> D[Avaliador Determinístico]
    V -. mesmas regras de autoridade .-> D
    D --> M[Memória de Incidentes<br/>PostgreSQL + pgvector]
    D --> H[Hipóteses Verificadas]
    H --> RCA[RCA Baseado em Evidências]
    RCA --> API[REST API]
    RCA --> UI[UI Next.js]
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
- Demo stateful PostgreSQL: `http://localhost:3000/stateful/postgresql`
- Demo stateful Kafka: `http://localhost:3000/stateful/kafka`
- Demo stateful Redis: `http://localhost:3000/stateful/redis`
- UI de health/RBAC operacional: `http://localhost:3000/ops`
- Explorer do Persistent Awareness Graph: `http://localhost:3000/awareness?cluster=prod-us-east-1`
- API de topologia reconciliada: `GET /api/v1/awareness/topology?cluster=<cluster>`
- API de topologia por serviço: `GET /api/v1/awareness/services/<service>?cluster=<cluster>&namespace=<ns>`
- API de blast radius: `GET /api/v1/awareness/blast-radius/<entity-id>?cluster=<cluster>`
- API de timeline de mudanças: `GET /api/v1/awareness/changes?cluster=<cluster>&service=<service>&namespace=<ns>`
- Preview do Investigation Planner: `POST /api/v1/planner/plan`
- Execução paralela experimental de especialistas: `POST /api/v1/planner/execute` (exige `AGENTIC_EXECUTION_ENABLED=true`)
- Investigação agentic completa com verdict determinístico + explanation graph: `POST /api/v1/planner/investigate` (mesmo opt-in da execução)

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

O playbook Kubernetes atual espera labels úteis como `cluster`, `namespace`, `pod` e, opcionalmente, `node`. Em produção, configure `ALERTMANAGER_WEBHOOK_TOKEN` e envie-o em `X-Webhook-Token`.

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

STATEFUL_DEPENDENCY_ANALYSIS_ENABLED=true
STATEFUL_DEPENDENCY_MAX_TARGETS=4
DEPENDENCY_WINDOW_MINUTES=15

# Exemplos de thresholds; ajuste aos SLOs e à semântica dos exporters em produção.
POSTGRES_CONNECTION_UTILIZATION_CRITICAL=0.90
POSTGRES_LOCKS_CRITICAL=50
POSTGRES_ACCESS_EXCLUSIVE_LOCKS_CRITICAL=1
POSTGRES_DEADLOCKS_DELTA_CRITICAL=1
POSTGRES_REPLICATION_LAG_BYTES_CRITICAL=16777216
POSTGRES_LONG_TX_SECONDS_CRITICAL=60

KAFKA_CONSUMER_LAG_CRITICAL=10000
KAFKA_UNDER_REPLICATED_PARTITIONS_CRITICAL=1
KAFKA_OFFLINE_PARTITIONS_CRITICAL=1
KAFKA_CONSUMER_GROUP_MEMBERS_MIN=1

REDIS_MEMORY_UTILIZATION_CRITICAL=0.90
REDIS_EVICTIONS_DELTA_CRITICAL=1
REDIS_BLOCKED_CLIENTS_CRITICAL=1
REDIS_CLIENT_UTILIZATION_CRITICAL=0.90
REDIS_COMMAND_LATENCY_SECONDS_CRITICAL=0.05

ARGOCD_URL=http://argocd-server.argocd.svc
ARGOCD_TOKEN=...
GITHUB_TOKEN=...

# Controles operacionais
AUTH_ENABLED=false
NOTIFICATIONS_ENABLED=false
INCIDENT_RETENTION_DAYS=30
AUDIT_RETENTION_DAYS=90
QUERY_BUDGET_ENABLED=true
QUERY_BUDGET_MAX_CALLS=80
QUERY_BUDGET_MAX_CALLS_PER_SOURCE=30
QUERY_BUDGET_MAX_WALL_SECONDS=45
CLUSTER_CONFIG_JSON={}
```

Consulte [docs/operations.pt-BR.md](docs/operations.pt-BR.md) para claims OIDC, isolamento por tenant/cluster, Slack/PagerDuty, retenção, export, NetworkPolicy e configuração de Secret externo.

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

### RCA de dependências stateful

A v0.5 transforma um nó de dependência encontrado no trace em um investigador tipado somente quando os metadados permitem classificá-lo com segurança como PostgreSQL, Kafka ou Redis.

```mermaid
flowchart LR
    ALERT[Incidente de Serviço] --> TRACE[Propagação Causal por Trace]
    TRACE --> DEP[Origem TraceDependency]
    DEP --> CLASS{Classificar Dependência}

    CLASS -->|db.system=postgresql| PG[Adapter PostgreSQL]
    CLASS -->|messaging.system=kafka| KF[Adapter Kafka]
    CLASS -->|db.system=redis| RD[Adapter Redis]

    PG --> PM[Conexões / Locks / Deadlocks<br/>Replication Lag / Transação Longa]
    KF --> KM[Consumer Lag / ISR<br/>Disponibilidade de Líder / Membros]
    RD --> RM[Memória / Evictions<br/>Clientes Bloqueados / Capacidade / Latência]

    PM --> SCORE[Scoring Determinístico]
    KM --> SCORE
    RM --> SCORE

    SCORE --> GATE{Dependência é origem do trace<br/>e anomalia foi confirmada?}
    GATE -->|Sim| SPECIFIC[Promover RCA Stateful Específico]
    GATE -->|Não| CONTEXT[Manter como Contexto de Suporte]

    K8S[RCA Kubernetes já Confirmado] --> PRECEDENCE{Existe Causa Mais Forte?}
    SPECIFIC --> PRECEDENCE
    PRECEDENCE -->|Sim| KEEP[Manter Causa Kubernetes]
    PRECEDENCE -->|Não| ROOT[Usar Causa Stateful]
```

Os adapters stateful não se conectam diretamente ao banco, broker ou Redis com credenciais privilegiadas. Eles executam apenas consultas Prometheus permitidas dentro do boundary read-only já existente.

Famílias determinísticas atuais:

| Dependência | Sinais | Exemplos de hipóteses |
| --- | --- | --- |
| PostgreSQL | conexões/max_connections, sessões active/idle-in-transaction, locks, AccessExclusiveLock, deadlocks, replication lag, idade da transação | esgotamento de conexões, contenção por lock, deadlocks, lag da réplica, transação longa |
| Kafka | consumer lag, under-replicated partitions, partições offline/sem líder, membros do consumer group | backlog de consumo, degradação de replicação, indisponibilidade de líder, consumer group vazio |
| Redis | memória/maxmemory, evictions, clientes bloqueados, clients/maxclients, latência de comando | pressão de memória, evictions, clientes bloqueados, saturação de clientes, execução lenta de comandos |

Quando um exporter não expõe um sinal, o check fica em `unknown`. Ele não é transformado em evidência de suporte nem de contradição.

Diagramas completos e detalhes: [docs/stateful-dependencies.pt-BR.md](docs/stateful-dependencies.pt-BR.md).

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

A suíte de regressão cobre os playbooks Kubernetes, causalidade puramente por traces, promoção de PostgreSQL/Kafka/Redis e os guardrails de precedência.

## API

```text
GET  /healthz
GET  /api/v1/incidents/demo
GET  /api/v1/incidents/demo/trace
GET  /api/v1/incidents/demo/stateful/{postgresql|kafka|redis}
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
  app/remediation/    serviço separado de remediação guardada
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

Depois da v0.7, os próximos incrementos de maior valor são:

1. Bundles de política de remediação por tenant/cluster e evidência assinada de aprovação.
2. Verificação pós-ação e propostas de rollback, mantendo execução humana explícita.
3. PostgreSQL com blocker/blockee e correlação por pg_stat_statements.
4. Kafka com timelines de rebalance/broker e Redis com slowlog/hot keys.
5. Kubernetes: ImagePullBackOff, bloqueios por PDB e falhas de PVC/volumes em runtime.
6. Correlação trace → logs por derived fields, exemplars e evidência sensível a sampling.
7. Audit sink externo imutável e bundles assinados de export de incidente/remediação.
