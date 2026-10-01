# Harness de Validação em Ambiente Real

O harness de validação agora possui duas camadas:

- **baseline da v0.8.0-rc.1:** falhas reais de Kubernetes mais reconciliação autoritativa do Persistent Awareness Graph.
- **release candidate full-stack v0.8.0-rc.2:** Prometheus, Loki e Tempo/OpenTelemetry reais, alimentados por um workload instrumentado e consultados pelos adapters read-only de produção.

## Gate da release

O harness cria um cluster Kind descartável com dois nós, aplica a ServiceAccount de validação read-only, inicia PostgreSQL/pgvector, sobe os workloads de validação e executa o código real de investigação contra APIs live.

Para o caminho rc2 ele também sobe:

- Prometheus, fazendo scrape do workload instrumentado a cada dois segundos;
- Loki, recebendo logs estruturados pela API HTTP de push;
- Tempo, recebendo traces OTLP/HTTP gerados pelo SDK oficial do OpenTelemetry;
- um workload `frontend` instrumentado que expõe métricas Prometheus e emite logs e traces correlacionados.

As imagens do stack de observabilidade e do nó Kind ficam presas por digest. A imagem do workload fixa a imagem base Python e as versões dos pacotes OpenTelemetry.

## O que o gate comprova

O baseline Kubernetes continua exigindo:

- `OOMKilled` real, resolvido como `container-oom`;
- restart backoff real com exit code 42, preservando paridade clássico/agentic e nunca promovendo `application-exit` sem evidência de logs;
- `FailedScheduling` real por requests impossíveis, resolvido como `insufficient-resources`;
- ingestão autoritativa do inventário Kubernetes;
- relações `owned_by`, `serves` e `scheduled_on` no grafo;
- tombstones autoritativos depois da remoção de recursos reais;
- verificação SHA-256 da integridade das evidências.

O caminho full-stack da rc2 gera adicionalmente três requests de checkout com falha. Cada request produz:

- contador HTTP 500 visível no Prometheus;
- log estruturado no Loki com labels de Pod/serviço e o trace ID;
- trace OTLP com o caminho causal `frontend -> postgresql/orders@orders-db:5432`;
- métricas RED do service graph expostas pelo Prometheus.

O gate exige que Prometheus, Loki e Tempo apareçam como healthy nos checks de capability do adapter de produção. Pelo menos dois traces independentes precisam convergir para a mesma origem downstream antes de `trace-causal-propagation` poder ser selecionada. A execução agentic precisa concluir os specialists de métricas, logs e traces, e as avaliações clássica e agentic precisam concordar com a causa raiz determinística.

Uma falha de fonte opcional nunca pode fabricar evidência. O invariant original da rc1 para crash loop continua valendo: sem a evidência exigida para diagnosticar uma saída específica da aplicação, o engine pode ficar inconclusivo ou selecionar o mecanismo genérico de crash loop, mas não pode afirmar uma especificidade sem suporte.

## Slice PostgreSQL de recuperação da v0.8.0-rc.3

O primeiro slice da rc3 exercita o adapter Prometheus de PostgreSQL de produção contra dados live no Prometheus usando exatamente os nomes de métricas e labels esperados por telemetria compatível com postgres_exporter.

O workload de validação expõe dois estados:

- **pressão:** 19 de 20 conexões PostgreSQL aparecem em uso, gerando 95% de utilização e exigindo que `postgres-connection-exhaustion:*` substitua a causa apenas por trace;
- **recuperado:** a mesma dependência volta para 4 de 20 conexões enquanto novos traces com falha continuam sendo gerados, exigindo que o engine deixe de promover a causa PostgreSQL antiga e volte para `trace-causal-propagation`.

Os caminhos clássico e agentic precisam concordar. O specialist de database precisa concluir nas duas fases, e a integridade das evidências continua obrigatória.

Este slice valida o contrato do adapter e a semântica de recuperação. Um próximo slice da rc3 substituirá as métricas compatíveis emitidas pelo workload por PostgreSQL + postgres_exporter reais antes de fecharmos a release candidate.

## Fluxo

```mermaid
flowchart LR
    CI[GitHub Actions ou execução local] --> H[Validation Harness]
    H --> K[Cluster Kind descartável com 2 nós]
    H --> PG[(PostgreSQL + pgvector)]

    K --> KF[Workloads de falha Kubernetes]
    K --> OBS[Observabilidade full-stack]
    OBS --> PROM[Prometheus]
    OBS --> LOKI[Loki]
    OBS --> TEMPO[Tempo]
    OBS --> APP[Frontend instrumentado]

    APP -->|scrape| PROM
    APP -->|logs estruturados| LOKI
    APP -->|OTLP/HTTP| TEMPO

    K --> SA[ServiceAccount read-only]
    SA --> T[ReadOnlyHTTPTools]
    PROM --> T
    LOKI --> T
    TEMPO --> T

    T --> CLASSIC[Investigation Engine clássico]
    T --> AGENT[Specialists agentic]
    T --> INV[Coletor autoritativo de inventário]

    INV --> G[(Persistent Awareness Graph)]
    CLASSIC --> V[Verdict determinístico]
    AGENT --> E[Avaliador do shared envelope]
    G --> R[Topologia + tombstones]

    V --> REP[Evidência JSON + Markdown]
    E --> REP
    R --> REP
```

## Executar localmente

É necessário ter Docker, Kind, kubectl e as dependências Python do backend.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cd ..
make validate-harness
make validate-real
```

O harness constrói o pequeno workload de validação localmente, carrega a imagem no Kind, inicia o stack de telemetria e remove tudo ao terminar.

## Evidências

Cada execução gera apenas estes artefatos compartilháveis:

- `validation-artifacts/validation-report.json`
- `validation-artifacts/validation-report.md`

O relatório v3 preserva todas as seções da rc2 e adiciona evidências de pressão/recuperação stateful de PostgreSQL. Na fase de pressão o RCA específico de PostgreSQL precisa ser promovido; após a recuperação, com telemetria atual saudável, o engine precisa voltar para a causalidade por traces.

O bearer token temporário do Kubernetes e o arquivo de CA ficam em `validation-artifacts/runtime/` somente durante a execução e nunca são enviados como artefatos pelo CI.

O processo termina com código diferente de zero se qualquer sinal de runtime, fonte de telemetria, verdict determinístico, verdict agentic, relação do grafo, verificação de integridade ou invariant de tombstone falhar.

## Limpeza

Por padrão o harness encerra os port-forwards, remove o cluster Kind descartável e remove o container PostgreSQL pelo caminho de `finally`. Use `--keep` apenas para depuração local.
