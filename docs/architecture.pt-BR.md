# Arquitetura — v0.8 em desenvolvimento

**Português do Brasil** · [English](architecture.md)

## Visão geral do sistema

```mermaid
flowchart TD
    A[Alertmanager / REST API] --> N[Normalizador de Incidente]
    N --> PLAN[Investigation Planner Bounded<br/>plan-only na v0.8 atual]
    N --> I[Investigation Engine]

    PAG[(Persistent Awareness Graph)] --> CTX[Contexto Bounded do Serviço]
    CTX --> PLAN
    PLAN --> PT[Tarefas Allow-listed<br/>K8s / Métricas / Logs / Traces / DB / Mudanças]

    I --> K[Adapter Kubernetes]
    I --> P[Adapter Prometheus]
    I --> L[Adapter Loki]
    I --> T[Tempo / TraceQL]
    I --> AR[Argo CD / GitHub]
    I --> KA[Karpenter]

    K --> LG[Grafo Local do Incidente]
    P --> LG
    L --> LG
    T --> LG
    AR --> LG
    KA --> LG

    LG --> EV[Avaliador de Evidências]
    LG --> PAG
    PAG --> REC[Reconciliação Fresh / Stale]
    PAG --> SV[Projeções por Serviço]
    PAG --> BR[Blast Radius]

    EV --> H[Hipóteses Determinísticas]
    EV --> MEM[Memória de Incidentes<br/>PostgreSQL + pgvector]
    EV --> LLM[LLM Opcional<br/>Somente Propositor]
    MEM --> H
    LLM --> H
    H --> RC[Seletor de Causa Raiz]
    RC --> RCA[RCA Estruturado]
    RCA --> API[REST API]
    RCA --> UI[UI Next.js]

    PT -. próxima fase .-> ORCH[Orquestrador Paralelo de Especialistas]
    ORCH -. próxima fase .-> EV
```

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

### Reconciliação não inventa deleção

```mermaid
flowchart TD
    LS[last_seen] --> CMP{Mais antigo que o threshold de stale?}
    CMP -->|Não| F[Fresh]
    CMP -->|Sim| S[Stale]
    S --> KEEP[Preservar registro atual + histórico]
    KEEP --> WAIT[Aguardar fonte autoritativa de inventário]
    WAIT -. futuro .-> T[Tombstone / deleção]
```

Observações de incidentes são parciais. Portanto, um recurso ausente em um RCA posterior não é tratado como deletado.

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

O planner atual é determinístico e plan-only. Ele usa a identidade do incidente, feature flags e um neighborhood bounded do serviço para decidir quais ramos tipados de especialistas são relevantes.

```mermaid
flowchart TD
    INC[IncidentInput] --> PLAN[Planner Determinístico]
    PAG[Persistent Awareness Graph] --> CTX[Contexto Bounded de 1 Salto]
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

    IP -. ainda não implementado .-> ORCH[Execução Paralela de Especialistas]
    ORCH -. futuros envelopes de evidência .-> EV[Avaliador Determinístico]
```

Propriedades de segurança do planner:

- existem somente seis tipos allow-listed de tarefas especialistas;
- toda tarefa é read-only;
- quantidade de tarefas, contexto do grafo, paralelismo e budget de queries por especialista têm limites no servidor;
- database/stateful depende da tarefa de traces para que a identidade da dependência seja tipada antes de consultar telemetria específica;
- o planner não confirma hipóteses, não anexa evidências, não executa mutações e não contorna o plano de remediação separado.

Detalhes: [persistent-awareness-graph.pt-BR.md](persistent-awareness-graph.pt-BR.md) e [agentic-investigation.pt-BR.md](agentic-investigation.pt-BR.md).
