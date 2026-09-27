# Arquitetura — v0.5

**Português do Brasil** · [English](architecture.md)

## Visão geral do sistema

```mermaid
flowchart TD
    A[Alertmanager / REST API] --> N[Normalizador de Incidente]
    N --> I[Investigation Engine]

    I --> K[Adapter Kubernetes]
    I --> P[Adapter Prometheus]
    I --> L[Adapter Loki]
    I --> O[Service Graph OpenTelemetry]
    I --> T[Tempo / TraceQL<br/>Causalidade por Traces]
    I --> AR[Adapter Argo CD]
    I --> GH[Adapter GitHub]
    I --> KA[Adapter Karpenter]

    K --> G[Awareness Graph]
    P --> G
    L --> G
    O --> G
    T --> G
    AR --> G
    GH --> G
    KA --> G

    G --> SD[Investigadores Stateful<br/>PostgreSQL / Kafka / Redis]
    SD --> EV[Store / Avaliador de Evidências]
    EV --> H[Hipóteses Determinísticas]
    EV --> MEM[Memória de Incidentes<br/>PostgreSQL + pgvector]
    EV --> LLM[LLM Opcional<br/>Propositor de Hipóteses]

    MEM --> H
    LLM --> H
    H --> RC[Seletor de Causa Raiz]
    RC --> RCA[RCA Estruturado]
    RCA --> API[REST API]
    RCA --> UI[UI de Investigação Next.js]
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
    RCA --> REC[Remediação Recomendada]
    REC --> AP{Aprovação Humana Explícita}
    AP -->|Aprovado| RID[Identidade Separada de Remediação]
    RID --> POL[Validação de Política / Allow-list]
    POL --> DRY[Dry-run quando suportado]
    DRY --> ACT[Executar Ação Restrita]
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
- Identidade separada e aprovação humana explícita para qualquer futuro executor de remediação.
