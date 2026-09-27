# RCA de dependências stateful — v0.5

**Português do Brasil** · [English](stateful-dependencies.md)

A v0.5 estende o grafo causal baseado em traces para RCA determinístico e específico de dependências. Um nó PostgreSQL, Kafka ou Redis detectado no caminho causal pode deixar de ser apenas uma dependência virtual e virar um investigador tipado que valida evidências atuais de runtime através de consultas Prometheus limitadas e somente leitura.

O desenho preserva a hierarquia causal existente: evidência direta de infraestrutura continua mais forte, o trace mostra **de onde a falha se propagou**, e a telemetria stateful explica **por que aquela dependência falhou**.

## Fluxo ponta a ponta

```mermaid
flowchart TD
    I[Incidente] --> K8S[Playbooks Determinísticos de Infraestrutura]
    K8S --> TRACE[Reconstrução Causal Tempo / TraceQL]
    TRACE --> DEP[Origem TraceDependency]

    DEP --> META[Metadados da Dependência<br/>db.system / db.namespace<br/>messaging.system / destination]
    META --> TYPE{Classificar}

    TYPE -->|PostgreSQL| PG[Investigador PostgreSQL]
    TYPE -->|Kafka| KF[Investigador Kafka]
    TYPE -->|Redis / Valkey| RD[Investigador Redis]
    TYPE -->|Desconhecido| CTX[Manter como Contexto de Trace]

    PG --> PGE[Conexões / Locks / Deadlocks<br/>Replication Lag / Transações Longas]
    KF --> KFE[Consumer Lag / Replicação<br/>Líder / Membros do Group]
    RD --> RDE[Memória / Evictions / Clientes Bloqueados<br/>Capacidade de Clientes / Latência]

    PGE --> SCORE[Scorer de Evidências]
    KFE --> SCORE
    RDE --> SCORE

    SCORE --> CONF{Hipótese específica confirmada?}
    CONF -->|Não| SUPPORT[Apenas Contexto da Dependência]
    CONF -->|Sim| ORIGIN{Dependência é a origem do trace?}

    ORIGIN -->|Não| SUPPORT
    ORIGIN -->|Sim| PRECEDENCE{Já existe uma causa mais forte?}
    PRECEDENCE -->|Kubernetes / infraestrutura| KEEP[Manter Causa Existente]
    PRECEDENCE -->|Trace-only / inconclusivo| PROMOTE[Promover Causa Stateful]
```

## Hierarquia causal

```mermaid
flowchart TB
    INFRA[1. Sinal causal explícito de infraestrutura<br/>kubelet / scheduler / controller]
    STATEFUL[2. Anomalia stateful + ancestralidade como origem do trace]
    TRACE_MULTI[3. Origem repetida em vários traces]
    TRACE_ONE[4. Ancestralidade de um único trace]
    RED[5. RED / correlação de dependência]
    CHANGE[6. Correlação de mudança]
    HISTORY[7. Similaridade histórica]
    LLM[8. Proposta de modelo não verificada]

    INFRA --> STATEFUL --> TRACE_MULTI --> TRACE_ONE --> RED --> CHANGE --> HISTORY --> LLM
```

A evidência stateful pode refinar uma causa raiz baseada somente em trace, mas não substitui silenciosamente um RCA Kubernetes mais forte já confirmado.

## Descoberta da dependência

O parser de traces preserva apenas uma allow-list estreita de atributos semânticos:

```text
db.system
db.namespace
db.name
server.address
server.port
peer.service
messaging.system
messaging.destination.name
messaging.destination
messaging.consumer.group.name
messaging.kafka.consumer.group
```

O classificador atual reconhece:

```mermaid
flowchart LR
    ATTR[Atributos da Dependência no Trace] --> DB{db.system}
    DB -->|postgres / postgresql| PG[postgresql/database]
    DB -->|redis / valkey| RD[redis/cache]

    ATTR --> MSG{messaging.system}
    MSG -->|kafka| KF[kafka/topic]

    ATTR --> OTHER[Desconhecido / Externo]
    OTHER --> CONTEXT[Sem Promoção Stateful]
```

Tipos desconhecidos continuam válidos como contexto no Awareness Graph e não são forçados para um dos adapters suportados.

## Investigador PostgreSQL

```mermaid
flowchart TD
    PG[PostgreSQL como Origem] --> CONN[Conexões / max_connections]
    PG --> LOCKS[Locks / AccessExclusiveLock]
    PG --> DEAD[Deadlocks na Janela]
    PG --> LAG[Replication Lag em Bytes]
    PG --> TX[Idade da Transação Mais Longa]

    CONN --> H1[Esgotamento de Conexões]
    LOCKS --> H2[Contenção por Lock]
    DEAD --> H3[Deadlocks]
    LAG --> H4[Replication Lag]
    TX --> H5[Transação Longa]

    H1 --> SCORE[Scoring Determinístico]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
    H5 --> SCORE
```

Sinais consultados atualmente:

| Sinal | Objetivo |
| --- | --- |
| sessions / max_connections | detectar esgotamento da capacidade de conexões |
| conexões ativas | contexto de runtime |
| idle-in-transaction | contexto de pool/transação vazando |
| total de locks | pressão ampla por locks |
| AccessExclusiveLock | pressão disruptiva típica de DDL/schema |
| delta de deadlocks | evidência direta de deadlock |
| lag WAL em bytes | evidência de saúde de replicação |
| idade da transação longa | contexto de duração/bloqueio |

Uma métrica opcional indisponível fica em `unknown`; ela não conta como evidência saudável.

## Investigador Kafka

```mermaid
flowchart TD
    K[Kafka como Origem] --> LAG[Consumer Lag]
    K --> URP[Under-replicated Partitions]
    K --> OFF[Partições Offline / sem Líder]
    K --> MEM[Membros do Consumer Group]

    LAG --> H1[Backlog de Consumo]
    URP --> H2[Degradação de Replicação]
    OFF --> H3[Indisponibilidade de Líder]
    MEM --> H4[Sem Consumers Ativos]

    H1 --> SCORE[Scoring Determinístico]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
```

Topic e consumer group são derivados dos atributos de messaging no trace quando presentes. Um consumer group ausente não é inventado pela engine.

## Investigador Redis

```mermaid
flowchart TD
    R[Redis como Origem] --> MEM[used_memory / maxmemory]
    R --> EVICT[Delta de Chaves Evicted]
    R --> BLOCK[Clientes Bloqueados]
    R --> CLIENTS[connected_clients / maxclients]
    R --> LAT[Duração de Comandos / Rate]

    MEM --> H1[Pressão de Memória]
    EVICT --> H1
    BLOCK --> H2[Clientes Bloqueados]
    CLIENTS --> H3[Saturação de Clientes]
    LAT --> H4[Latência de Comandos]

    H1 --> SCORE[Scoring Determinístico]
    H2 --> SCORE
    H3 --> SCORE
    H4 --> SCORE
```

Pressão de memória e evictions são avaliadas em conjunto porque eviction isolada pode ser esperada dependendo da política de cache.

## Promoção de evidência

Uma hipótese específica de dependência precisa passar por dois testes diferentes antes de virar a causa raiz:

```mermaid
flowchart LR
    T[Evidência de Trace<br/>dependência é a origem causal] --> GATE1{É origem?}
    M[Métricas Stateful<br/>threshold ultrapassado] --> GATE2{Anomalia específica confirmada?}

    GATE1 --> BOTH{As duas condições são verdadeiras?}
    GATE2 --> BOTH

    BOTH -->|Não| CONTEXT[Contexto de Suporte]
    BOTH -->|Sim| PRE{Já existe RCA mais forte?}
    PRE -->|Sim| KEEP[Manter RCA Existente]
    PRE -->|Não| PROMOTE[Promover RCA Stateful Específico]
```

Isso impede que um banco ou broker globalmente degradado seja culpado por um incidente não relacionado apenas porque apareceu em algum ponto do grafo de serviços.

## Budgets de consulta e alvos

A análise stateful é limitada por:

```text
STATEFUL_DEPENDENCY_ANALYSIS_ENABLED=true
STATEFUL_DEPENDENCY_MAX_TARGETS=4
DEPENDENCY_WINDOW_MINUTES=15
```

Somente um conjunto pequeno de consultas Prometheus tipadas é executado por target suportado. Não existe um planejador PromQL genérico exposto ao LLM.

## Thresholds

Os thresholds padrão são pontos de partida, não verdades universais de produção. Devem ser ajustados ao ambiente, à semântica dos exporters e aos SLOs.

```text
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
```

## Boundary read-only

```mermaid
flowchart LR
    RCA[AI SRE RCA] --> PROM[Prometheus GET /api/v1/query]
    PROM --> PG[Métricas postgres_exporter]
    PROM --> KF[Métricas kafka_exporter]
    PROM --> RD[Métricas redis_exporter]

    RCA -. sem sessão SQL .-> PGDB[(PostgreSQL)]
    RCA -. sem API admin do broker .-> KFB[(Kafka)]
    RCA -. sem comandos Redis .-> RDB[(Redis)]
```

O investigador não abre sessões SQL, não altera brokers, não executa comandos Redis e não modifica estado das dependências. A remediação continua sendo apenas recomendação e exige um futuro plano separado com aprovação.
