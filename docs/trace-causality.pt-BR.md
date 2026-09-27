# Propagação causal trace-aware — v0.4

**Português do Brasil** · [English](trace-causality.md)

A v0.4 adiciona um caminho de investigação Tempo limitado e somente leitura que transforma traces distribuídos em evidência tipada. O objetivo não é deixar um modelo “raciocinar livremente sobre traces”; a engine reconstrói deterministicamente a ancestralidade dos spans, identifica a operação downstream mais profunda que falhou, compara traces representativos independentes e só então promove a evidência de trace para candidata a causa raiz.

## Fluxo ponta a ponta

```mermaid
flowchart TD
    A[Alertmanager / Incidente Manual] --> S[Resolver Identidade do Serviço]
    S --> Q1[Busca TraceQL<br/>service + status=error]
    Q1 --> Q2[Busca TraceQL Fallback<br/>service apenas]
    Q1 --> IDS[Trace IDs Representativos<br/>com Limite]
    Q2 --> IDS

    IDS --> FETCH[Tempo v2 Trace Lookup<br/>OTLP JSON]
    FETCH --> PARSE[Normalizar ResourceSpans / ScopeSpans]
    PARSE --> TREE[Construir Árvore parentSpanId]
    TREE --> ERR[Classificar Spans com Erro]
    ERR --> LEAF[Selecionar Deepest Failing Span<br/>sem descendente com erro]

    LEAF --> PEER{Client/Producer com Peer?}
    PEER -->|Sim| VN[Criar Nó Virtual<br/>DB / Mensageria / Externo]
    PEER -->|Não| ORIGIN[Usar Serviço Instrumentado como Origem]
    VN --> PATH[Montar Caminho Causal Raiz até Origem]
    ORIGIN --> PATH

    PATH --> GROUP[Agrupar Origem Candidata<br/>service + operation]
    GROUP --> REPEAT{Repete em >=2<br/>traces representativos?}
    REPEAT -->|Sim| CONF[Hipótese trace-causal Confirmada]
    REPEAT -->|Não| TEST[Apenas Testing]

    CONF --> RCA{Já existe RCA mais forte?}
    RCA -->|Não| ROOT[Promover Causa Raiz de Trace]
    RCA -->|Sim| ENRICH[Manter RCA Existente<br/>anexar evidência de trace]
    TEST --> ENRICH
```

## Sequência da investigação

```mermaid
sequenceDiagram
    autonumber
    participant ENG as Investigation Engine
    participant TEMPO as Tempo Query Frontend
    participant PROM as Prometheus / Service Graph Metrics
    participant TRACE as Trace Analyzer
    participant GRAPH as Awareness Graph
    participant RCA as Root Cause Selector

    ENG->>TEMPO: GET /api/search?q={ service && status=error }
    TEMPO-->>ENG: trace IDs
    loop limitado por TRACE_FETCH_LIMIT
        ENG->>TEMPO: GET /api/v2/traces/:traceID
        TEMPO-->>ENG: OTLP JSON padrão
        ENG->>TRACE: parse resource spans + árvore de spans
        TRACE->>TRACE: selecionar deepest failing span
        TRACE-->>ENG: cadeia causal + origem candidata
    end

    ENG->>TEMPO: TraceQL metrics rate / errors / p95
    TEMPO-->>ENG: séries RED
    ENG->>PROM: métricas de request/failure/latência do service graph
    PROM-->>ENG: RED por aresta

    ENG->>GRAPH: adicionar trace_calls + runs_as + nós de dependência
    ENG->>RCA: pontuar repetição da origem
    RCA-->>ENG: confirmed / testing
```

## Seleção da origem causal

O seletor determinístico prefere um span com erro que não tenha descendente também com erro. Se houver vários candidatos, escolhe o span mais profundo na árvore e, depois, o que começou antes. Isso aproxima a primeira falha downstream em vez do sintoma upstream mais visível.

```mermaid
flowchart LR
    F[frontend<br/>HTTP 500] --> C[checkout<br/>ERROR]
    C --> DB[Span client PostgreSQL<br/>ERROR]
    DB --> V[(postgresql/orders)]

    DB -. folha de erro mais profunda .-> O[Origem Candidata]
    O --> P[Caminho de Propagação]
    P --> F
```

Quando um span client ou producer com erro contém atributos de peer, banco ou mensageria, o peer vira um nó virtual de dependência. O span instrumentado continua no caminho como o serviço upstream que propagou a falha.

## Regra de confirmação entre traces

Um único trace é evidência útil, mas não basta para confirmar uma causa raiz baseada apenas em tracing.

```mermaid
flowchart TD
    T1[Trace A<br/>postgresql/orders : SELECT orders] --> G[Agrupar por service + operation]
    T2[Trace B<br/>postgresql/orders : SELECT orders] --> G
    T3[Trace C<br/>redis : GET cart] --> G

    G --> C1[postgresql/orders + SELECT orders<br/>2 sinais fortes]
    G --> C2[redis + GET cart<br/>1 sinal]

    C1 --> SCORE[Scoring de Evidências]
    SCORE --> CONF[Confirmed]
    C2 --> TEST[Testing]
```

O scorer de evidências existente continua valendo. Duas evidências fortes independentes de cadeia causal atingem o limiar de confirmação; um único trace forte permanece em testing.

## Sinais RED

A v0.4 coleta duas visões RED complementares:

1. **TraceQL metrics** para o serviço investigado:
   - rate de spans/requests
   - rate de spans com erro
   - p95 de duração
2. **Métricas por aresta do service graph**:
   - requests
   - failures
   - error ratio
   - p95 de latência server-side

```mermaid
flowchart LR
    TR[Tempo TraceQL Metrics] --> SVC[RED do Serviço]
    SG[Tempo/Alloy Service Graph Metrics] --> EDGE[RED por Aresta]
    SVC --> E[Evidência]
    EDGE --> E
    E --> G[Awareness Graph]
```

RED fortalece contexto e entendimento de blast radius. Sozinho, não substitui a ancestralidade causal pai/filho.

## Correlação Trace → Deployment

Os atributos de recurso OTLP são preservados por serviço. Quando presentes, a v0.4 correlaciona:

- `k8s.deployment.name`
- `k8s.namespace.name`
- `k8s.pod.name`
- `k8s.node.name`
- `service.version`
- `deployment.environment.name`

```mermaid
flowchart LR
    SPAN[Atributos de Recurso do Trace] --> SVC[Serviço]
    SPAN --> DEP[k8s.deployment.name]
    SPAN --> VER[service.version]
    DEP --> EDGE[Service runs_as Deployment]
    VER --> REV{Combina com revisão Argo?}
    REV -->|Sim| STRONG[Correlação de Mudança Mais Forte]
    REV -->|Não / desconhecido| CONTEXT[Apenas Contexto de Runtime]
```

## Hierarquia de segurança causal

```mermaid
flowchart TB
    K8S[1. Sinal causal explícito de infraestrutura<br/>ex.: reason de eviction do kubelet]
    MULTI[2. Ancestralidade repetida em traces<br/>mesma falha profunda em vários traces]
    SINGLE[3. Ancestralidade de um único trace]
    RED[4. Anomalia RED / correlação de service graph]
    CHANGE[5. Correlação temporal de deploy]
    HISTORY[6. Similaridade histórica]
    LLM[7. Sugestão do LLM]

    K8S --> MULTI --> SINGLE --> RED --> CHANGE --> HISTORY --> LLM
```

Um playbook Kubernetes já confirmado nunca é substituído silenciosamente por evidência de trace. Traces podem enriquecer Awareness Graph, hipóteses, timeline e contexto de remediação.

## Consultas limitadas e read-only

O adapter de trace emite apenas GETs:

- `GET /api/search`
- `GET /api/v2/traces/<traceID>`
- `GET /api/metrics/query_range`

Os limites são controlados por:

```text
TRACE_ANALYSIS_ENABLED=true
TRACE_WINDOW_MINUTES=15
TRACE_SEARCH_LIMIT=8
TRACE_FETCH_LIMIT=4
TRACE_SLOW_SPAN_MIN_MS=500
TRACE_SLOW_SPAN_ROOT_FRACTION=0.5
```

Não existe endpoint de escrita no Tempo, acesso a shell ou interface genérica de execução de comandos dentro do investigador.
