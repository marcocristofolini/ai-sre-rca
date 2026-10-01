# Harness de Validação em Ambiente Real

O harness da v0.8.0-rc.1 comprova o caminho de RCA Kubernetes contra um control plane Kubernetes real e descartável, sem depender de mocks ou fixtures preparadas.

## Gate da release candidate

O harness cria um cluster Kind com dois nós, aplica uma ServiceAccount de validação somente leitura, inicia PostgreSQL/pgvector, injeta falhas controladas e executa tanto o engine determinístico clássico quanto o caminho agentic da v0.8 usando a API real do Kubernetes.

O primeiro gate da RC cobre:

- estado real `OOMKilled`, com causa esperada `container-oom` quando o Prometheus está propositalmente indisponível;
- backoff real de restart com exit code 42 enquanto o Loki está indisponível; o gate aceita o mecanismo genérico `container-crash-loop` ou resultado inconclusivo, exige paridade entre clássico e agentic e proíbe a promoção para `application-exit` sem evidência de logs;
- `FailedScheduling` real causado por requests impossíveis de recursos, com causa esperada `insufficient-resources`;
- coleta autoritativa do inventário Kubernetes para o Persistent Awareness Graph;
- relações reais `owned_by`, `serves` e `scheduled_on`;
- tombstone autoritativo depois que um recurso é removido do cluster real;
- verificação de integridade SHA-256 das evidências;
- paridade de causa raiz entre o caminho clássico e a avaliação agentic Kubernetes.

Prometheus, Loki, Tempo e Argo CD ficam propositalmente indisponíveis neste primeiro gate. OOMKilled e FailedScheduling ainda precisam resolver com evidência direta do Kubernetes. Para crash loops, o gate valida um invariant de segurança em vez de exigir um verdict exato sensível ao instante da coleta: clássico e agentic precisam concordar, o resultado pode ser o mecanismo genérico `container-crash-loop` ou inconclusivo, e `application-exit` fica proibido enquanto não houver evidência de logs. Isso comprova que uma fonte opcional indisponível não cria evidência fictícia nem faz o engine afirmar uma especificidade sem suporte suficiente.

## Evidência publicada da v0.8.0-rc.1

A prerelease publicada aponta para o commit `4c3429f8ee26be7bf42279c40186b50da045e9d1`. O workflow pós-merge de ambiente real passou nesse mesmo commit, e os relatórios JSON/Markdown estão anexados à release no GitHub.

Observado nessa execução:

- 34 recursos Kubernetes observados pelo inventário autoritativo;
- 13 entidades do namespace de validação no grafo;
- relações `owned_by`, `scheduled_on` e `serves` presentes;
- 3 tombstones autoritativos após remoção real de recursos;
- OOMKilled resolvido como `container-oom`;
- FailedScheduling resolvido como `insufficient-resources`;
- o cenário de restart backoff preservou paridade clássico/agentic e não promoveu `application-exit` sem evidência de logs;
- todas as verificações de integridade das evidências passaram.

## Fluxo

```mermaid
flowchart LR
    CI[GitHub Actions ou execução local] --> H[Validation Harness]
    H --> K[Cluster Kind descartável com 2 nós]
    H --> PG[(PostgreSQL + pgvector)]
    K --> F[Workloads com falhas controladas]
    K --> SA[ServiceAccount read-only]
    SA --> T[ReadOnlyHTTPTools]
    T --> I[Investigation Engine clássico]
    T --> A[Especialistas agentic]
    T --> INV[Coletor autoritativo de inventário]
    INV --> G[(Persistent Awareness Graph)]
    I --> V[Validação do verdict determinístico]
    A --> E[Avaliador do shared envelope]
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

A imagem de nó do Kind fica presa por digest. No CI, o binário do Kind também fica em versão fixa e tem o SHA-256 validado antes da execução.

## Evidências

Cada execução gera apenas estes artefatos compartilháveis:

- `validation-artifacts/validation-report.json`
- `validation-artifacts/validation-report.md`

O bearer token temporário do Kubernetes e o arquivo de CA ficam em `validation-artifacts/runtime/` somente durante a execução e nunca são enviados como artefatos pelo CI.

O processo termina com código diferente de zero se qualquer sinal obrigatório, verdict determinístico, verdict agentic, relação do grafo, verificação de integridade ou tombstone não corresponder ao esperado.

## Limpeza

Por padrão o harness remove o cluster Kind descartável e o container PostgreSQL dentro de um bloco `finally`. Use `--keep` apenas para depuração local.
