# Arquitetura: v0.8.0-rc.3 em desenvolvimento

**Português do Brasil** · [English](architecture.md)

## Visão geral do sistema

A plataforma possui quatro planos. A investigação clássica persiste os incidentes e atualiza o grafo. O caminho agentic usa o contexto do grafo, coleta evidências e persiste o diagnóstico avaliado para API/UI com auditoria. A remediação roda com identidade própria, e a validação de release executa cenários de falha e recuperação em um cluster descartável.

```mermaid
flowchart TD
    IN["REST API ou Alertmanager"] --> CORE["Investigação clássica"]
    IN --> PLAN["Investigação agentic opcional"]
    SRC["Fontes tipadas de evidência read-only"] --> CORE
    SRC --> PLAN
    subgraph READ["1. Investigação read-only"]
      CORE --> RCA["RCA clássico"]
      PLAN --> ENV["Especialistas e shared envelope"]
      ENV --> EVAL["Avaliador determinístico"]
      EVAL --> AR["Diagnóstico agentic avaliado"]
      AR --> STORE
    end
    subgraph AWARE["2. Awareness persistente"]
      RCA --> STORE["Persistência do incidente"]
      STORE --> DIAG["Histórico e diagnóstico por incidente"]
      STORE --> PAG[("Awareness Graph e PostgreSQL/pgvector")]
      INV["Inventário autoritativo"] --> PAG
      PAG --> CTX["Contexto operacional congelado"]
      CTX --> PLAN
      PAG --> VIEW["Explorer, blast radius e mudanças"]
    end
    subgraph REM["3. Remediação guardada"]
      STORE --> POLICY["Proposta tipada, política, dry-run e aprovação"]
      POLICY --> ACT["Identidade separada: escala de Deployment"]
    end
    subgraph VAL["4. Validação da release"]
      GH["GitHub-first gate"] --> KIND["Kind descartável e telemetria"]
      KIND --> HAR["Checks de falha, recuperação e topologia"]
      HAR --> REP["Evidência JSON e Markdown"]
    end
```

`AGENTIC_EXECUTION_ENABLED=false` é o padrão. `/api/v1/planner/plan` compila o plano; `/execute` coleta evidências sem causa raiz; `/investigate` também executa a avaliação determinística. O diagnóstico agentic avaliado é persistido e apresentado na tela do incidente. O planejamento assistido por modelo exige `AGENTIC_MODEL_PLANNER_ENABLED=true` e `LLM_ENABLED=true`.

A fronteira principal de correção não mudou: evidência live tipada e gates determinísticos decidem o que pode ser afirmado. O planner e o LLM opcional podem restringir ou priorizar a investigação, mas não podem fabricar fatos do grafo, evidências, scores ou um verdict de causa raiz.


## Superfícies da UI em execução

Estas capturas foram feitas com a v0.8.0-rc.1 executando localmente em `DEMO_MODE=true`. São telas reais renderizadas pela aplicação usando os adapters de demonstração.

| Superfície | Captura |
| --- | --- |
| RCA Kubernetes determinístico | ![RCA Kubernetes](images/investigator-k8s.png) |
| Causalidade baseada em traces | ![RCA trace-aware](images/trace-aware-rca.png) |
| RCA stateful de PostgreSQL | ![RCA stateful PostgreSQL](images/stateful-postgresql.png) |
| Persistent Awareness Graph | ![Awareness Explorer](images/awareness-explorer.png) |
| Capabilities operacionais | ![Operations](images/operations.png) |


## Reconciliação automática do inventário Kubernetes — v0.8

O caminho automático de inventário roda em um CronJob Kubernetes separado, e não em um loop dentro do Deployment da API. Ele usa o ServiceAccount do investigador, com somente GET/LIST/WATCH, e grava exclusivamente pelo contrato autoritativo já existente no repositório do grafo.

```mermaid
flowchart LR
    CRON[CronJob<br/>Sem sobreposição] --> LIST[LIST Kubernetes paginado]
    LIST --> OK{Todas as listas configuradas completas?}
    OK -->|Não| FAIL[Sair com erro<br/>Nenhum snapshot aplicado]
    OK -->|Sim| UID[Validar UID do controller ownerReference]
    UID --> REL[Derivar relações explícitas]
    REL --> SNAP[Snapshot autoritativo]
    SNAP --> LOCK[Reconciliação PostgreSQL serializada]
    LOCK --> GRAPH[(Persistent Awareness Graph)]
    LOCK --> AUDIT[(Eventos de auditoria)]
```

O collector nunca infere ownership por convenção de nomes. Uma aresta `owned_by` só é emitida quando existe um único `ownerReference` controlador e seu UID corresponde ao objeto owner observado no snapshot. Scheduling usa `Pod.spec.nodeName`. O roteamento de Service usa selectors exatos contra os labels do Pod template do workload, preservando a topologia mesmo com o workload escalado para zero. Execuções limitadas por namespace separam o inventário de Nodes dos recursos namespaced para que um escopo parcial não afirme deleções Pod-to-Node como autoritativas.

## Graph Health do Awareness Graph — v0.8

Graph Health é uma projeção factual e read-only do estado persistido. Ela não atribui um score sintético de confiabilidade e não usa LLM para inferir se o grafo está saudável.

```mermaid
flowchart LR
    PAG[(Persistent Awareness Graph)] --> STATE[Agregar current / stale / historical / tombstoned]
    OBS[(Awareness Observations)] --> INV[Último receipt de inventário por source]
    STATE --> HEALTH[GET /api/v1/awareness/health]
    INV --> HEALTH
    HEALTH --> READ[Leitura viewer por tenant/cluster]
```

`AWARENESS_STALE_AFTER_MINUTES` controla quando fatos ativos do grafo ficam stale. `AWARENESS_INVENTORY_MAX_AGE_MINUTES` controla separadamente quando o snapshot autoritativo mais recente de cada source configurada passa de `current` para `stale`; uma source sem receipt aparece como `missing`.

## Retention do Awareness Graph — v0.8

A retention é explícita, admin-only e roda em dry-run por padrão. Nesta fase ela nunca remove receipts autoritativos de snapshot, pois esses receipts protegem a identidade imutável do snapshot e a detecção de conflito em replays. Observações históricas de entidades/relações só ficam elegíveis depois da janela configurada e quando não são a observação mais recente daquele alvo.

```mermaid
flowchart LR
    CRON[CronJob opcional de retention<br/>sem token de ServiceAccount] --> OLD
    ADMIN[Requisição admin] --> PREVIEW{dry_run?}
    PREVIEW -->|Sim| COUNT[Somente contar elegíveis]
    PREVIEW -->|Não| OLD[Observações antigas redundantes]
    OLD --> KEEP[Preservar última observação por alvo]
    OLD --> RECEIPT[Preservar todos os receipts de inventário]
    KEEP --> PURGE[Remover histórico elegível]
    RECEIPT --> PURGE
    PURGE --> REL[Remover relações tombstoned expiradas]
    REL --> ENT[Remover entidades tombstoned expiradas<br/>somente sem relação sobrevivente]
    ENT --> AUDIT[Auditoria transacional do purge]
```

`AWARENESS_OBSERVATION_RETENTION_DAYS` tem padrão 90 e `AWARENESS_TOMBSTONE_RETENTION_DAYS` tem padrão 30. O endpoint usa a mesma fronteira de autorização por tenant/cluster das APIs do grafo. O scheduler opcional chama o mesmo contrato do repositório diretamente com escopo explícito de tenant/cluster, Secret dedicado de banco e `automountServiceAccountToken: false`.

## Operational Context Snapshot agentic — v0.8

O planner agentic não recebe mais somente IDs de entidades/relações vindos de uma projeção de um salto. Antes de compilar as tarefas, ele congela um snapshot operacional bounded que combina topologia reconciliada, histórico recente do grafo, ChangeEvents e Graph Health.

```mermaid
flowchart LR
    PAG[(Persistent Awareness Graph)] --> T[Topologia<br/>fresh / stale / historical / tombstoned]
    PAG --> H[Observações bounded]
    PAG --> C[ChangeEvents recentes]
    GH[Graph Health] --> S[Operational Context Congelado]
    T --> S
    H --> S
    C --> S
    S --> PLAN[InvestigationPlan]
    PLAN --> SPEC[Especialistas read-only]
    S --> ENV[Shared Evidence Envelope]
    SPEC --> ENV
    ENV --> DET[Avaliador Determinístico]
```

Fatos tombstoned permanecem visíveis para explicação, mas ficam fora de `required_entity_ids` das tarefas. ChangeEvents persistidos consumidos durante a execução são marcados como `context_only`. O evaluator remove explicitamente referências context-only do scoring causal, portanto o contexto persistente pode orientar a investigação e preservar continuidade sem virar autoridade de causa raiz.

O contexto possui limites rígidos para quantidade de nós, profundidade do grafo, alvos de histórico, observações por alvo e número de mudanças recentes. O LLM opcional do planner recebe somente uma projeção rasa e sanitizada desse snapshot; continua sem poder criar tarefas, ferramentas, queries, dependências ou verdicts.

## Scoping determinístico dos especialistas — v0.8

O contexto congelado pode restringir leituras live sem virar evidência. O scope dos especialistas é tipado e controlado pelo servidor. Campos explícitos do incidente têm precedência; Pod ou aplicação Argo CD ausentes só podem ser preenchidos a partir de um único candidato fresh em uma projeção completa e não truncada.

```mermaid
flowchart LR
    INC[Identidade do Incidente] --> RES[Resolver Determinístico de Scope]
    OCS[Operational Context Congelado] --> RES
    RES -->|Pod fresh único| K[Scope Kubernetes + Loki]
    RES -->|Aplicação Argo fresh única| A[Scope Argo CD]
    RES -->|ambíguo ou truncado| F[Fail closed<br/>sem alvo inferido]
    K --> LIVE1[Verificação live Kubernetes / Loki]
    A --> LIVE2[Verificação live Argo CD]
    LIVE1 --> EV[Evidência atual tipada]
    LIVE2 --> EV
```

A profundidade padrão do contexto operacional passa a 3 para que o grafo bounded do serviço alcance a cadeia Service → Deployment → ReplicaSet → Pod. Um contexto maior nunca autoriza adivinhação: se o budget de nós/arestas truncar a projeção, a resolução de Pod/aplicação derivada do grafo é desabilitada.

## Compilador de planejamento assistido por modelo — v0.8

A assistência do modelo é uma camada advisory opcional, não uma nova autoridade de execução. Primeiro, o planner determinístico decide quais ramos especialistas são elegíveis. Somente esse contexto sanitizado e bounded é enviado ao modelo. A resposta volta para um compilador controlado pelo servidor antes de existir qualquer grafo de tarefas executável.

```mermaid
flowchart LR
    INC[Identidade do Incidente] --> EL[Elegibilidade Determinística]
    GRAPH[Contexto Bounded do Awareness Graph] --> EL
    EL --> DET[Plano Determinístico]
    EL -. sanitizado .-> LLM[LLM Advisory Opcional]
    LLM -. somente nomes + razões .-> COMP[Compilador no Servidor]
    EL --> COMP
    COMP --> ALLOW[Allow-list de Especialistas]
    COMP --> BASE[Baseline Obrigatório<br/>Kubernetes / Métricas / Traces quando elegíveis]
    COMP --> DEP[Dependências Tipadas]
    COMP --> BUD[Budgets Controlados pelo Servidor]
    ALLOW --> PLAN[InvestigationPlan Compilado]
    BASE --> PLAN
    DEP --> PLAN
    BUD --> PLAN
    COMP --> AUDIT[Advisory Trace<br/>requested / accepted / rejected / forced]
```

O modelo não pode fornecer nome de ferramenta, query, comando, budget, dependência ou ação de remediação. Nomes desconhecidos ou inelegíveis são rejeitados. Falha do modelo, JSON inválido ou conselho sem especialista elegível preserva o plano determinístico. O plano executável registra se sua origem foi `deterministic` ou `model-assisted`.

## Etapa Kubernetes compartilhada — v0.8

O caminho clássico de investigação e o specialist Kubernetes agentic agora chamam a mesma etapa determinística Kubernetes. Não existe uma segunda implementação simplificada de RCA Kubernetes que possa divergir do core engine.

```mermaid
flowchart LR
    CLASSIC[Investigation Engine Clássico] --> SHARED[Etapa Kubernetes Determinística Compartilhada]
    AGENTIC[Specialist Kubernetes Agentic] --> SHARED

    SHARED --> EVICT[Eviction / Ephemeral Storage]
    SHARED --> OOM[OOMKilled]
    SHARED --> CRASH[CrashLoopBackOff]
    SHARED --> SCHED[FailedScheduling / Karpenter]
    SHARED --> HPA[HPA]
    SHARED --> KEDA[KEDA]
    SHARED --> NODE[NodeNotReady]

    EVICT --> OUT[Evidências Canônicas + Hipóteses Playbook]
    OOM --> OUT
    CRASH --> OUT
    SCHED --> OUT
    HPA --> OUT
    KEDA --> OUT
    NODE --> OUT

    OUT --> CORE[Seleção Clássica de Causa Raiz]
    OUT --> ENV[Shared Evidence Envelope Agentic]
    ENV --> DET[Avaliador Agentic Determinístico]
```

O wrapper agentic adiciona um budget read-only próprio, mas não altera scoring dos playbooks, categorias de evidência, resolução de ownership ou IDs de hipóteses. Um specialist Kubernetes com budget esgotado falha fechado e não pode ser interpretado como contexto de infraestrutura saudável.
A política de prioridade de causa raiz também é compartilhada: quando múltiplas hipóteses Kubernetes estão confirmadas, o evaluator agentic aplica a mesma ordem de especificidade por cenário usada pelo seletor clássico. Qualquer candidate vindo de um specialist que não concluiu é bloqueado antes da seleção.

## Ciclo de investigação

```mermaid
flowchart TD
    S[Incidente recebido] --> N[Normalizar para IncidentInput]
    N --> M[Recuperar incidentes históricos semelhantes]
    M --> KO[Resolver ownership no Kubernetes]
    KO --> SG[Resolver identidade do serviço e dependências]
    SG --> TR[Buscar traces representativos e reconstruir causalidade]
    TR --> SD[Classificar origem stateful e coletar métricas específicas]
    SD --> CH[Resolver revisão no Argo CD e metadados Git]
    CH --> KP[Resolver NodeClaim / NodePool no Karpenter]
    KP --> HP[Carregar hipóteses determinísticas]
    HP --> CE[Coletar evidências tipadas read-only]
    CE --> AE[Associar IDs das evidências]
    AE --> SC[Calcular suporte / contradição]
    SC --> LP{LLM habilitado?}
    LP -->|Sim| LH[Gerar hipóteses adicionais como proposed]
    LP -->|Não| RC[Selecionar causa raiz]
    LH --> RC
    RC --> PS[Persistir RCA + fingerprint vetorial]
    PS --> OUT[Retornar RCA estruturado]
```

O ciclo é propositalmente **evidence-first**. Incidentes históricos e saída do modelo podem ampliar o contexto, mas não podem substituir telemetria atual mais forte.

## Sequência de investigação

```mermaid
sequenceDiagram
    autonumber
    participant AM as Alertmanager/API
    participant ENG as Investigation Engine
    participant K8S as Kubernetes
    participant OBS as Prometheus/Loki/OTel
    participant TEMPO as Tempo/TraceQL
    participant DEP as PostgreSQL/Kafka/Redis
    participant CHG as Argo CD/GitHub
    participant KAR as Karpenter
    participant MEM as Memória pgvector
    participant LLM as LLM Opcional
    participant UI as RCA API/UI

    AM->>ENG: Payload do incidente
    ENG->>MEM: Buscar incidentes semelhantes
    MEM-->>ENG: Pistas de similaridade
    ENG->>K8S: Resolver Pod/RS/Deployment/Node + eventos
    K8S-->>ENG: Evidências de runtime e ownership
    ENG->>OBS: Consultar métricas/logs/service graph
    OBS-->>ENG: Evidências de telemetria
    ENG->>TEMPO: Buscar traces de erro + consultar OTLP e RED metrics
    TEMPO-->>ENG: Cadeias pai/filho + candidatos de origem + RED
    ENG->>DEP: Consultar métricas tipadas da dependência via Prometheus
    DEP-->>ENG: Sinais stateful atuais + thresholds
    ENG->>CHG: Resolver revisão implantada e commit
    CHG-->>ENG: Evidências de mudança
    ENG->>KAR: Resolver ciclo de vida de NodeClaim/NodePool
    KAR-->>ENG: Evidências de capacidade
    ENG->>ENG: Pontuar hipóteses determinísticas
    opt LLM habilitado
        ENG->>LLM: Contexto estruturado, sem credenciais/ferramentas
        LLM-->>ENG: Hipóteses propostas + checks necessários
        ENG->>ENG: Manter status=proposed e score=0 até validação
    end
    ENG->>ENG: Selecionar causa raiz a partir de evidências verificadas
    ENG->>MEM: Persistir RCA estruturado + vetor
    ENG-->>UI: RCA baseado em evidências
```

## Hierarquia de segurança causal

```mermaid
flowchart TB
    D[1. Sinal causal direto<br/>ex.: kubelet informa explicitamente o recurso que causou a eviction]
    R[2. Sinal correlacionado de runtime<br/>ex.: DiskPressure + filesystem esgotado]
    C[3. Configuração contribuinte<br/>ex.: ausência de reservas de ephemeral-storage]
    T[4. Correlação temporal<br/>ex.: deploy próximo ao incidente]
    H[5. Similaridade histórica<br/>ex.: incidente antigo era parecido]
    M[6. Sugestão do modelo<br/>hipótese plausível, porém não verificada]

    D --> R --> C --> T --> H --> M
```

Uma camada inferior não pode sobrepor uma superior sem nova evidência tipada que a promova.

## Awareness Graph

A API retorna `graph_edges` explícitas; o frontend não precisa inferir relacionamentos a partir de uma lista plana de entidades.

```mermaid
flowchart LR
    WG[web-gateway] -->|calls| LP[landing-page]
    LP -->|calls| CAT[catalog-api]
    LP -->|calls| REDIS[(redis)]

    POD[Pod] -->|owned_by| RS[ReplicaSet]
    RS -->|owned_by| DEP[Deployment]
    POD -->|scheduled_on| NODE[Node]
    DEP -->|managed_by| ARGO[Argo CD Application]
    ARGO -->|syncs_from| GIT[Git Repository / Revision]
    NODE -->|represented_by| NC[NodeClaim]
    NC -->|owned_by| NP[NodePool]

    DEP -. serves .-> LP
```

## Correlação de mudanças

```mermaid
flowchart TD
    P[Pod] --> RS[ReplicaSet]
    RS --> D[Deployment]
    D --> A[Argo CD Application]
    A --> R[Revisão Git Implantada]
    R --> C[Metadados do Commit Git]
    C --> DT[Timestamp do Deploy]
    DT --> CMP{Comparar com início do incidente}
    CMP -->|Dentro da janela configurada| S[Evidência de suporte]
    CMP -->|Fora da janela| X[Evidência contraditória / fraca]
    S --> V[Ainda exige evidência de runtime]
    X --> V
```

Proximidade temporal é propositalmente mais fraca do que evidência explícita de runtime.

## Origem do Service Graph

O metrics-generator do Tempo, Grafana Alloy ou um processor `servicegraph` do OTel pode emitir métricas Prometheus com labels `client` e `server`. O investigador consome essas métricas do backend Prometheus compatível configurado.

```mermaid
flowchart LR
    APP[Serviços Instrumentados] --> OTEL[OpenTelemetry]
    OTEL --> TEMPO[Tempo / Alloy Metrics Generator]
    TEMPO --> PROM[(Backend Prometheus-compatible)]
    PROM --> RCA[Adapter Service Graph do AI SRE RCA]
    RCA --> GRAPH[Awareness Graph]
```

Isso mantém a topologia agregada baseada em consultas PromQL tipadas. A partir da v0.4, o investigador também possui acesso **read-only e limitado** ao Tempo para causalidade por trace.

## Propagação causal por traces — v0.4

```mermaid
flowchart LR
    ALERT[Alerta + Serviço] --> SEARCH[TraceQL Search]
    SEARCH --> IDS[Trace IDs Representativos]
    IDS --> OTLP[Tempo v2 OTLP JSON]
    OTLP --> TREE[Árvore parentSpanId]
    TREE --> ORIGIN[Deepest Failing Span]
    ORIGIN --> MULTI{Mesma origem em vários traces?}
    MULTI -->|Sim| CONF[Hipótese Confirmada]
    MULTI -->|Não| TEST[Testing]
    OTLP --> DEP[Nós virtuais DB / Mensageria / Externo]
    OTLP --> K8S[Correlação Service → Deployment]
    CONF --> GRAPH[Awareness Graph]
    TEST --> GRAPH
```

A confirmação puramente trace-aware exige repetição da mesma origem downstream em traces independentes. Um único trace não confirma causa raiz. Um RCA Kubernetes já confirmado também mantém precedência sobre evidência de trace.

Detalhes: [docs/trace-causality.pt-BR.md](trace-causality.pt-BR.md).

## RCA de dependências stateful — v0.5

```mermaid
flowchart LR
    TRACE[Origem Causal no Trace] --> META[Metadados Tipados]
    META --> CLASS{Classificar}
    CLASS -->|PostgreSQL| PG[Conexões / Locks / Deadlocks / Lag]
    CLASS -->|Kafka| KF[Lag / ISR / Líder / Group Members]
    CLASS -->|Redis| RD[Memória / Evictions / Clientes / Latência]

    PG --> SCORE[Scoring de Evidências]
    KF --> SCORE
    RD --> SCORE

    SCORE --> GATE{Origem do trace +<br/>anomalia específica confirmada?}
    GATE -->|Não| CTX[Contexto de Suporte]
    GATE -->|Sim| PRE{Já existe RCA de infraestrutura mais forte?}
    PRE -->|Sim| KEEP[Manter RCA Existente]
    PRE -->|Não| PROMOTE[Promover RCA Stateful Específico]
```

A v0.5 não acessa PostgreSQL, Kafka ou Redis diretamente com credenciais administrativas. Os investigadores stateful usam consultas Prometheus allow-listed e mantêm a mesma fronteira read-only da engine.

Detalhes: [docs/stateful-dependencies.pt-BR.md](stateful-dependencies.pt-BR.md).

## Memória de incidentes

`SQLRepository` armazena o payload completo do RCA estruturado, um fingerprint textual normalizado e um vetor de 128 dimensões.

```mermaid
flowchart TD
    I[Incidente Resolvido] --> FP[Gerar Fingerprint Normalizado]
    FP --> EMB[Embedding Local Determinístico]
    EMB --> PG[(PostgreSQL + pgvector)]
    PG --> HNSW[Índice HNSW vector_cosine_ops]

    NEW[Novo Incidente] --> NF[Gerar Novo Fingerprint]
    NF --> NE[Embedding]
    NE --> Q[Consulta por Similaridade de Cosseno]
    HNSW --> Q
    Q --> TOP[Incidentes Históricos Mais Próximos]
    TOP --> HINT[Apenas Pistas / Contexto]
    HINT --> FRESH[Telemetria Atual Continua Obrigatória]
```

O provider de embeddings é isolado para que uma solução de produção possa substituir a implementação local padrão posteriormente.

## Boundary do LLM

O modelo opcional é um **propositor de hipóteses**, não um agente de infraestrutura.

```mermaid
flowchart TD
    CTX[Contexto Sanitizado<br/>Entidades + Resumos de Evidência] --> LLM[LLM]
    LLM --> P[Hipótese Proposta<br/>origin=llm<br/>status=proposed<br/>score=0]
    P --> RC[Checks Observáveis Necessários]
    RC --> MAP[Hypothesis Compiler / Allow-list]
    MAP --> TOOLS[Ferramentas Tipadas Read-only]
    TOOLS --> E[Evidência Nova]
    E --> DET[Avaliador Determinístico]
    DET -->|Suportada| VERIFIED[Estado Determinístico Verificado]
    DET -->|Insuficiente| P

    LLM -. sem acesso direto .-> K8S[Sem Credenciais Kubernetes]
    LLM -. sem acesso direto .-> SHELL[Sem Shell / SSH]
    LLM -. sem acesso direto .-> MUT[Sem API de Escrita / Mutação]
```

O LLM não pode consultar Kubernetes diretamente, executar PromQL/LogQL por conta própria, usar shell, anexar IDs de evidência, alterar scores, definir `confirmed` nem participar diretamente da seleção primária da causa raiz.

## Limite entre investigação e remediação

```mermaid
flowchart LR
    INV[ServiceAccount do Investigador<br/>somente GET / LIST / WATCH] --> SRC[Fontes de Evidência de Infra + Telemetria]
    SRC --> RCA[RCA Baseado em Evidências]
    RCA --> PLAN[Plano de Remediação Proposto]
    PLAN --> POL[Validação de Política / Allow-list]
    POL --> DRY[Kubernetes dryRun=All]
    DRY --> AP{Aprovação Humana Explícita}
    AP -->|Aprovado| RID[Identidade Separada de Remediação]
    RID --> DRIFT{Estado do alvo permaneceu igual?}
    DRIFT -->|Sim| ACT[Executar Ação Restrita]
    DRIFT -->|Não| BLOCK[Bloquear e exigir novo plano]
    ACT --> AUDIT[Registro de Auditoria + Metadados de Rollback]
    AP -->|Rejeitado| STOP[Sem Ação]

    INV -. não pode mutar .-> ACT
```

## Hardening para produção

- Allow-lists de NetworkPolicy para Kubernetes API, telemetria, Argo e provedor Git.
- Workload identity ou credenciais de curta duração quando suportado.
- Redação de secrets antes de qualquer chamada ao modelo.
- Hash dos payloads de evidência e audit sink imutável.
- Timeouts, limites de janela e budgets de cardinalidade por fonte.
- Isolamento por tenant e cluster.
- Admission policy rejeitando verbos de escrita para o ServiceAccount do investigador.
- Identidade separada do remediator, gates de política, dry-run e aprovação humana explícita para toda ação mutável.


## Plano operacional — v0.6

```mermaid
flowchart LR
    U[Usuário / Proxy OIDC] --> UI[Next.js]
    UI --> API[FastAPI]
    API --> AUTH[OIDC + RBAC]
    AUTH --> TENANT[Tenant + Cluster Scope]
    TENANT --> ENG[Investigation Engine]
    ENG --> QB[Query Budget]
    ENG --> HASH[Evidence SHA-256]
    ENG --> AUDIT[Audit Log]
    ENG --> NOTIFY[Slack / PagerDuty]
    ENG --> EXPORT[Export com Integridade]
    QB --> SRC[Fontes Read-only]
```

A v0.6 adiciona autenticação, isolamento de tenant/cluster, budgets, retenção, auditoria e entrega operacional sem alterar o boundary read-only do investigador. Detalhes completos: [operations.pt-BR.md](operations.pt-BR.md).


## Plano de remediação guardada — v0.7

A v0.7 implementa o boundary de remediação como outro runtime. O investigator não expõe rotas de remediação e continua usando identidade Kubernetes read-only. O remediator pode ser implantado separadamente e atualmente expõe apenas scale tipado de Deployment.

O remediator usa registry separado de credenciais, Roles Helm por namespace com get/patch somente em deployments/scale, dry-run server-side, aprovação com papel remediator exato, regra de quatro olhos, expiração de plano, detecção de drift por resourceVersion e rollback explícito.

Detalhes: [remediation.pt-BR.md](remediation.pt-BR.md).


## Persistent Awareness Graph e serviços de topologia — v0.8

O grafo local do incidente agora é canonicalizado e persistido, permitindo que a topologia operacional se acumule entre investigações sem deixar IDs locais vazarem entre escopos.

```mermaid
flowchart LR
    IR[InvestigationResult<br/>entities + graph_edges] --> CAN[Canonicalizador]
    CAN --> CE[(awareness_entities<br/>estado atual)]
    CAN --> CR[(awareness_relationships<br/>estado atual)]
    CAN --> AO[(awareness_observations<br/>histórico append-only)]

    CE --> REC[Reconciliação Fresh / Stale]
    CR --> REC
    AO --> HIST[API de Histórico do Alvo]

    REC --> SP[Projeção Centrada em Serviço]
    REC --> BR[Blast Radius]
    SP --> PC[Contexto do Planner]
    BR --> API8[APIs Awareness v0.8]
```

A identidade canônica da entidade deriva de `tenant + cluster + kind + namespace + name`. A identidade da relação deriva dos endpoints canônicos no mesmo escopo mais o tipo da relação. Persistir novamente o mesmo incidente é idempotente.

### Reconciliação autoritativa sem inferir deleção

```mermaid
flowchart TD
    INC[Observação de Incidente] --> LS[last_seen]
    LS --> CMP{Mais antigo que o threshold de stale?}
    CMP -->|Não| F[Fresh]
    CMP -->|Sim| S[Stale]
    S --> KEEP[Preservar registro atual + histórico]

    API[Reconciliação Admin de Inventário] --> SRC{Fonte allow-listed?}
    SRC -->|Não| DENY[Rejeitar]
    SRC -->|Sim| COMPLETE{Snapshot completo + cobertura explícita?}
    COMPLETE -->|Não| DENY
    COMPLETE -->|Sim| DIFF[Comparar inventário coberto]
    DIFF -->|Presente| REFRESH[Atualizar / Reativar]
    DIFF -->|Ausente e sem observação mais nova| T[Tombstone]
    DIFF -->|Existe fato mais novo| PRESERVE[Preservar fato mais novo]
```

Observações de incidentes continuam parciais e nunca removem estado do grafo. Tombstones só são produzidos por snapshot completo, autorizado para admin e vindo de fonte autoritativa allow-listed. O escopo é explícito por kind, namespace e tipo de relação. Tombstones permanecem no histórico/topologia, aparecem no Awareness Explorer e ficam fora da resolução corrente de entidades e da travessia de blast radius.

### Projeção de serviço

```mermaid
flowchart LR
    SVC[Service Canônico] --> N1[Vizinhos Depth 1]
    N1 --> N2[Vizinhos Depth 2]
    N2 --> NX[Até a profundidade configurada]

    B1[Max depth] --> SVC
    B2[Max nodes] --> SVC
    B3[Max edges] --> SVC

    SVC --> OUT[Topologia Bounded do Serviço]
    N1 --> OUT
    N2 --> OUT
    NX --> OUT
```

A busca é exata. Se o mesmo nome de serviço existir em namespaces diferentes, o namespace passa a ser obrigatório em vez de escolher um nó arbitrariamente.

### Blast radius causal

A direção armazenada no grafo e a direção de impacto são propositalmente separadas.

```mermaid
flowchart LR
    FE[frontend] -->|calls| CO[checkout]
    CO -->|calls| PG[(postgres)]
    DEP[Deployment checkout] -->|serves| CO

    PG -. impacto: calls reverso .-> CO
    CO -. impacto: calls reverso .-> FE
    DEP -. impacto: serves forward .-> CO
```

Tipos de relação conhecidos passam por uma allow-list explícita de direção de impacto. Relações desconhecidas aparecem nas APIs de topologia, mas nunca são percorridas no blast radius. Relações stale são apenas contexto histórico e são ignoradas na propagação de impacto atual.

## Investigation Planner Bounded — v0.8

O planner é determinístico e bounded. Ele usa identidade do incidente, feature flags e um Operational Context Snapshot congelado com topologia, histórico, ChangeEvents e Graph Health bounded para compilar ramos tipados de especialistas. A execução paralela está implementada como runtime read-only opt-in e fica desabilitada por padrão.

```mermaid
flowchart TD
    INC[IncidentInput] --> PLAN[Planner Determinístico]
    PAG[Persistent Awareness Graph] --> CTX[Operational Context Congelado]
    CTX --> PLAN
    CFG[Budgets do Servidor + Feature Flags] --> PLAN

    PLAN --> K[Tarefa Kubernetes]
    PLAN --> M[Tarefa Métricas]
    PLAN --> L[Tarefa Logs]
    PLAN --> T[Tarefa Traces]
    T --> DB[Tarefa Database / Stateful]
    PLAN --> CH[Tarefa Mudanças]

    K --> IP[InvestigationPlan]
    M --> IP
    L --> IP
    T --> IP
    DB --> IP
    CH --> IP

    IP -->|AGENTIC_EXECUTION_ENABLED=true| ORCH[Execução Paralela de Especialistas]
    ORCH --> SR[Resultados Tipados dos Especialistas]
    SR --> ENV[Shared Evidence Envelope]
    ENV --> HASH{Integridade verificada?}
    HASH -->|Não| INC[Inconclusivo]
    HASH -->|Sim| SCORE[Recalcular Hipóteses Playbook]
    SCORE --> CAUSAL[Gates Causais Trace / Stateful]
    CAUSAL --> PRE[Precedência por Fonte]
    PRE --> EV[Verdict Determinístico]
    EV --> EX[Explanation Graph de Hipóteses Descartadas]
```

Propriedades de segurança do planner:

- existem somente seis tipos allow-listed de tarefas especialistas;
- toda tarefa é read-only;
- quantidade de tarefas, profundidade/nós de topologia, alvos de histórico, observações por alvo, mudanças recentes, paralelismo e budget de queries por especialista têm limites no servidor;
- o scope dos especialistas é controlado pelo servidor e identidades derivadas do grafo exigem um único candidato fresh em projeção não truncada;
- scope ambíguo falha fechado em vez de escolher pela ordem da lista;
- fatos tombstoned permanecem visíveis no contexto operacional, porém ficam fora de `required_entity_ids` atuais;
- registros persistentes context-only são rejeitados do scoring causal de hipóteses;
- database/stateful depende da tarefa de traces para que a identidade da dependência seja tipada antes de consultar telemetria específica;
- o planner não confirma hipóteses, não anexa evidências, não executa mutações e não contorna o plano de remediação separado;
- o endpoint de execução recompila o plano no servidor, então grafos de tarefas escritos pelo caller nunca são executados;
- hipóteses dos especialistas são advisory-only e a resposta compartilhada não possui campo de causa raiz;
- budgets locais por especialista e o query budget global já existente continuam ativos;
- hashes das evidências são verificados antes de entrar no shared envelope;
- o evaluator recalcula hipóteses playbook usando apenas evidências verificadas, em vez de confiar no status local dos specialists;
- referências de suporte ou contradição ausentes falham de forma fechada e bloqueiam seleção de causa raiz;
- seleção puramente trace-aware continua exigindo origem repetida em traces independentes;
- seleção stateful continua exigindo identidade de origem no trace mais telemetria atual específica da dependência;
- sinais Kubernetes diretos bloqueiam promoção downstream de trace/stateful;
- um specialist Kubernetes de maior precedência que foi planejado precisa concluir antes de qualquer candidato downstream de trace/stateful ser selecionado;
- `/execute` continua sem causa raiz; somente `/investigate` pode retornar verdict determinístico;
- a execução agentic fica desabilitada por padrão.

Detalhes: [persistent-awareness-graph.pt-BR.md](persistent-awareness-graph.pt-BR.md) e [agentic-investigation.pt-BR.md](agentic-investigation.pt-BR.md).


## Avaliador determinístico do shared envelope — v0.8

O evaluator é uma fronteira de autoridade separada depois da execução dos especialistas.

```mermaid
flowchart TD
    ENV[Shared Evidence Envelope] --> I{Todos os hashes são válidos?}
    I -->|Não| FAIL[Fail Closed: Inconclusivo]
    I -->|Sim| REF[Validar Referências de Evidência]
    REF --> SCORE[Recalcular Score Ponderado]
    SCORE --> TG{Candidato Trace-only?}
    TG -->|Sim| TR[Exigir Origem Repetida em 2+ Traces]
    TG -->|Não| SG{Candidato Stateful?}
    SG -->|Sim| ST[Exigir causal_role=origin + Evidência de Trace + Telemetria da Dependência]
    SG -->|Não| PRE[Aplicar Precedência por Fonte]
    TR --> PRE
    ST --> PRE
    PRE --> K{Existe Sinal Kubernetes Direto?}
    K -->|Sim| BLOCK[Bloquear Promoção Downstream]
    K -->|Não| PICK[Selecionar Hipótese Confirmada de Maior Precedência]
    PICK --> ROOT[Causa Raiz Determinística]
    BLOCK --> INC[Inconclusivo até existir Playbook de Maior Precedência]
    ROOT --> EX[Explanation Graph]
    INC --> EX
```

O explanation graph torna explícito o raciocínio negativo. Nós de evidência se conectam às hipóteses com `supports` ou `contradicts`; gates usam `blocks`; hipóteses se conectam ao verdict com `selected` ou `considered`.

Essa etapa permanece read-only e não pode invocar remediação. Ela não confia no `confirmed` local de um specialist. Score e elegibilidade causal são recalculados independentemente a partir do shared envelope com integridade verificada.


## Eventos de deployment/change first-class — v0.8

Mudanças de deployment são persistidas como nós imutáveis `ChangeEvent`, derivados somente das evidências `change-correlation` coletadas do Argo CD. Evidência correspondente `source-change` do GitHub enriquece o mesmo evento com metadata do commit.

```mermaid
flowchart LR
    AR[Evidência change-correlation do Argo CD] --> CE[ChangeEvent]
    GH[Evidência source-change do GitHub] --> CE

    CE -->|observed_on| APP[ArgoCDApplication]
    CE -->|targets| DEP[Deployment]
    CE -->|targets_service| SVC[Service]
    CE -->|revision_from| REPO[GitRepository]

    CE -. somente contexto temporal .-> CTX[Contexto do incidente / planner]
    CE -. nunca causal automaticamente .-> NO[Fora da allow-list de impacto do blast radius]
```

O ChangeEvent possui identidade estável baseada em `application + revision + occurred_at`. Reobservar o mesmo deploy em outro incidente aumenta o contador de observações em vez de criar um evento duplicado.

Change Events e suas relações usam `temporal_role=event` e reconciliam como `historical`, não como `fresh` ou `stale`. A distinção é intencional: topologia pode ficar stale, enquanto um deploy ocorrido no passado continua sendo um fato histórico válido.

API temporal bounded:

```text
GET /api/v1/awareness/changes?cluster=<cluster>&service=<service>&namespace=<namespace>&since=<iso8601>&until=<iso8601>&limit=100
```

O endpoint mantém isolamento por tenant/cluster e retorna no máximo 200 eventos. Proximidade temporal é somente contexto e nunca transforma deploy/source change em causa raiz sem evidência causal de runtime.


## Validação em ambiente real: rc.1, rc.2 e rc.3 em desenvolvimento

O gate GitHub-first preserva o baseline Kubernetes/inventário da rc.1 e os caminhos Prometheus, Loki e Tempo da rc.2. A etapa atual de PostgreSQL da rc.3 adiciona uma dependência real PostgreSQL 17, postgres_exporter e um gerador limitado de conexões.

O Prometheus ajusta o label instance do exporter para `orders-db:5432`, a mesma identidade da dependência nos traces de falha controlados. A telemetria atual do exporter precisa confirmar a pressão de conexões antes que a origem do trace possa virar uma causa PostgreSQL.

```mermaid
flowchart TD
    KIND["Cluster Kind descartável"] --> BASE["Baseline Kubernetes, topologia e traces"]
    KIND --> DB["PostgreSQL 17 e Redis com cargas limitadas"]
    DB --> EXP["postgres_exporter e redis_exporter"]
    EXP --> PROM["Prometheus: identidades das dependências"]
    PROM --> FAIL["Checks de pressão e traces de falha"]
    FAIL --> RCA["Avaliação stateful clássica e agentic"]
    RCA --> ROOT["Exigir causa tipada para a instância"]
    ROOT --> REC["Liberar conexões e chaves do teste"]
    REC --> FRESH["Novos logs, métricas e traces HTTP 200"]
    FRESH --> VERIFY["Bloquear causas antigas de trace ou stateful"]
    BASE --> REPORT["Relatório JSON e Markdown para o SHA exato"]
    VERIFY --> REPORT
```

As avaliações clássica e agentic precisam convergir em `postgres-connection-exhaustion:...`, com o especialista database concluído e a integridade das evidências verificada. A recuperação exige utilização de conexões abaixo do limite de recuperação e uma investigação restrita aos novos traces pós-recuperação. Nenhuma causa anterior de trace ou anomalia PostgreSQL/Kafka/Redis pode ser promovida nessa janela.

O relatório usa o schema v3 e inclui saúde das fontes, contagem de telemetria, paridade de vereditos, saturação stateful, recuperação, integridade das evidências e checks de grafo/tombstones autoritativos. Credenciais Kubernetes temporárias ficam fora dos artefatos enviados.

A etapa Redis usa `cache-frontend` como serviço OTel e `cache:6379` como identidade do exporter. Uma escrita real rejeitada por maxmemory precisa confirmar `redis-memory-pressure` nos dois caminhos de investigação. A recuperação remove apenas chaves do teste, aguarda memória abaixo de 30% e executa novos SET/GET; a janela de traces saudáveis não pode promover a causa antiga. Essas escritas pertencem somente ao workload descartável.

O cenário de falha/recuperação de Kafka/kafka_exporter ainda está pendente. A versão continua **v0.8.0-rc.3 em desenvolvimento** até esses gates passarem e a promoção da release ser concluída.

Veja [../validation/README_PT-BR.md](../validation/README_PT-BR.md) para o contrato executável e [test-environment.pt-BR.md](test-environment.pt-BR.md) para implantar as imagens validadas.


## Diagnóstico por incidente

`InvestigationView` apresenta resultados clássicos e diagnósticos agentic avaliados no mesmo workspace nas demos e em `/incidents/{incident_id}`. A rota persistida usa a API autorizada existente e encaminha os headers de autenticação no servidor. Falhas de acesso e incidentes ausentes não acionam fallback para demo.

A UI mantém o vínculo `root_cause.hypothesis_id` com a hipótese confirmada, mostra evidências favoráveis e contrárias e recolhe hipóteses descartadas usando controles nativos `details/summary`. A exibição de fatos, consultas e hashes preserva a proveniência sem afirmar uma verificação de integridade adicional. Datas são exibidas em UTC de forma consistente. Recomendações não executam ações.

Os testes em Chromium exercitam demos, resultados persistidos, autenticação direta/proxy, ausência de dados, proposta LLM e resultado inconclusivo. Capturas desktop/mobile e o relatório ficam no artefato `diagnosis-ui-<run_id>`. Elas documentam dados demo renderizados pela aplicação.


### Persistência agentic

`/planner/investigate` materializa somente hipóteses avaliadas no contrato `InvestigationResult`. O campo opcional `agentic` guarda plano/origem, integridade, horários, resumo dos especialistas, decisões e explanation graph. Evidências, entidades e relações vêm do shared envelope; o contexto congelado do Awareness Graph não é reingerido como observação atual. O armazenamento JSON existente dispensa migração e mantém compatibilidade com incidentes antigos.

Uma hipótese bloqueada por precedência, contexto incompleto ou evidência inválida fica inconclusiva na projeção; sua decisão e motivos originais são preservados. A UI exige a decisão `selected` e integridade válida para destacar uma causa agentic. O resultado inconclusivo recebe uma conclusão explícita sem hipótese raiz. A API preserva execution/evaluation e acrescenta ID e link da investigação salva. A auditoria é vinculada ao ID; esse fluxo não envia notificações automaticamente nem cria remediações.


### Histórico e início manual

`GET /api/v1/incidents` aplica tenant e conjunto de clusters permitidos no repositório antes da paginação. A API aceita título/ID (`q`, até 120 caracteres), cluster, status, limite de 1 a 100 e offset de 0 a 10000; retorna summaries e `next_offset`. Ordena por horário de criação e ID decrescentes. A paginação por offset pode mudar quando novas investigações são salvas. O PostgreSQL seleciona apenas campos de resumo, incluindo modo e severidade do JSON, sem retornar o payload completo. A implementação em memória mantém os mesmos filtros e ordenação.

`/incidents` lista 25 resultados por página. `/incidents/new` consulta `/system/me` para permissões e feature flags. A Server Action revalida identidade, cluster, tenant e campos antes de chamar a API autorizada. Credenciais não são enviadas como props ao formulário. O backend permanece a autoridade de RBAC e escopo. A investigação concluída redireciona para `/incidents/{id}`; erros ficam no formulário e os campos controlados mantêm os valores. Sessões globais usam tenant concreto também no caminho clássico.
