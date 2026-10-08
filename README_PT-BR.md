# AI SRE RCA

[![GitHub-first gate](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml/badge.svg)](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml)
[![Release](https://img.shields.io/github/v/release/marcocristofolini/ai-sre-rca?include_prereleases)](https://github.com/marcocristofolini/ai-sre-rca/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Português do Brasil** · [English](README.md)

**AI SRE RCA** é uma plataforma de investigação de incidentes SRE para Kubernetes, observabilidade distribuída e dependências stateful. Ela coleta evidências reais, reconstrói relações causais, mantém contexto operacional persistente e produz um RCA estruturado sem permitir que um LLM invente a causa raiz.

> **Agentic para decidir onde investigar. Evidence-first para coletar fatos. Determinístico para decidir o que pode ser afirmado.**

Status atual: **v0.8.0-rc.3 em desenvolvimento**.

---

## O problema que a aplicação resolve

Durante um incidente, um engenheiro normalmente precisa consultar Kubernetes, métricas, logs, traces, mudanças recentes, dependências e incidentes anteriores em ferramentas diferentes. O problema não é apenas encontrar dados. É separar correlação de causalidade e evitar conclusões baseadas em coincidência temporal.

O AI SRE RCA automatiza esse trabalho mantendo uma fronteira explícita entre **contexto** e **evidência causal**.

Ele pode responder perguntas como:

- o Pod caiu por OOM, CrashLoop, eviction ou problema no Node?
- a falha começou dentro do serviço ou em uma dependência downstream?
- PostgreSQL, Kafka ou Redis apresentam telemetria atual que confirma a causa?
- um deployment recente está apenas próximo no tempo ou há evidência real de regressão?
- qual o blast radius provável dentro da topologia conhecida?
- o problema já ocorreu antes?
- a causa continua válida depois que o sistema se recupera?
- existe uma ação de remediação segura e explicitamente permitida?

---

## Histórico e investigação pela interface

Abra `/incidents` para listar diagnósticos autorizados, buscar por título ou ID e filtrar por cluster e estado. A lista usa paginação de 25 itens e não carrega evidências completas. `resolved` significa diagnóstico concluído, não recuperação confirmada.

Em `/incidents/new`, usuários com permissão de investigador informam título, cluster, alerta, severidade e alvo conhecido. O tenant vem da sessão; uma sessão global precisa informar um tenant concreto. O modo agentic aparece somente quando habilitado no servidor. O envio usa Server Actions com autenticação encaminhada no servidor, validação de campos e bloqueio enquanto a chamada está em andamento. Ao concluir, abre o diagnóstico salvo.

O fluxo é síncrono e ainda não mostra progresso por especialista em tempo real. Se a chamada falhar sem confirmação, consulte o histórico antes de reenviar. No modo demo, listagem e formulário informam que a coleta usa adapters de demonstração.

## Tela de diagnóstico do incidente

A tela de diagnóstico usa tema escuro e um grafo SVG com setas gerado pelas relações retornadas pelo motor. O grafo mostra até 48 entidades conectadas e 96 relações, informa cortes e mantém todas as relações em uma lista textual expansível. A topologia isolada não confirma causalidade.

A interface apresenta o alerta, o diagnóstico resumido, entidades e relações, hipóteses expansíveis, telemetria, evidências, linha do tempo, RCA final e recomendações de remediação. Hipóteses descartadas começam recolhidas; cada hipótese mantém seus vínculos com evidências de suporte e contrárias. É possível consultar fatos, consulta de origem, horário, referência e hash registrado de cada evidência.

- As rotas `/`, `/trace` e `/stateful/{postgresql|kafka|redis}` usam dados dos adapters de demonstração e exibem essa origem.
- `/incidents/{incident_id}` consulta uma investigação clássica persistida por `GET /api/v1/incidents/{incident_id}`, encaminhando a autenticação no servidor. O link **Abrir investigação salva** nas demos abre o resultado persistido. Um incidente recebido via webhook ou API pode ser acessado diretamente pelo seu ID nessa rota.
- “Causa confirmada” exige um resultado concluído com a hipótese raiz confirmada por playbook. Resultados inconclusivos e propostas LLM não recebem esse rótulo. `resolved` descreve a conclusão da investigação, não comprova a recuperação do serviço.
- As recomendações são informativas nesta tela. As contagens descrevem contexto observado, sem afirmar blast radius ou indisponibilidade. O hash exibido não representa uma nova verificação de integridade.

O CI verifica a navegação, a expansão das hipóteses e evidências, os estados inconclusivo e de acesso restrito, o encaminhamento de autenticação e o layout mobile. As capturas reais renderizadas em Chromium e o relatório ficam no artefato `diagnosis-ui-<run_id>` do [GitHub-first gate](https://github.com/marcocristofolini/ai-sre-rca/actions/workflows/github-first-gate.yml). As capturas usam o motor em modo demo, não telemetria de produção. A tela agentic inclui execução e decisões do avaliador. A lista e o formulário estão disponíveis; o progresso em tempo real continua pendente.


## O que a aplicação faz

| Capacidade | O que entrega |
| --- | --- |
| **RCA Kubernetes** | Investiga OOMKilled, CrashLoopBackOff, eviction, FailedScheduling, NodeNotReady, HPA, KEDA, Karpenter e sinais de capacidade. |
| **RCA por traces** | Reconstrói árvores de spans e encontra a origem downstream repetida em traces independentes. |
| **Dependências stateful** | Promove PostgreSQL, Kafka e Redis para investigadores determinísticos quando identidade causal e telemetria atual concordam. |
| **Métricas e logs** | Consulta Prometheus e Loki por adapters read-only e scopes controlados pelo servidor. |
| **Mudanças** | Correlaciona Argo CD e GitHub sem transformar proximidade temporal em prova automática. |
| **Persistent Awareness Graph** | Mantém entidades, relações, histórico, tombstones, inventário autoritativo, mudanças e contexto operacional entre incidentes. |
| **Blast radius** | Percorre somente relações causais permitidas e atuais. |
| **Investigação agentic** | Planeja especialistas Kubernetes, metrics, logs, traces, database e changes com budgets e dependências tipadas. |
| **Memória de incidentes** | Recupera incidentes semelhantes em PostgreSQL/pgvector como contexto, nunca como substituto de evidência atual. |
| **Auditoria e integridade** | Usa hashes SHA-256, tenant/cluster isolation, export estruturado e audit trail. |
| **Remediação guardada** | Mantém mutação separada do investigador, com allow-list, dry-run, aprovação humana, drift protection e rollback. |
| **Validação real** | Executa Kind, PostgreSQL/pgvector, Prometheus, Loki, Tempo/OpenTelemetry e cenários controlados no GitHub Actions. |

---

## Como uma investigação funciona

`POST /api/v1/incidents/investigate` usa o engine clássico e persiste o incidente, atualizando o grafo. O caminho agentic em `/api/v1/planner/investigate` é experimental e exige `AGENTIC_EXECUTION_ENABLED=true`. Ele retorna execução, avaliação, `incident_id` e `investigation_url`, persiste o diagnóstico avaliado e registra auditoria vinculada ao incidente. Abra o link retornado para consultar especialistas, budgets, decisões e evidências na mesma tela. `/plan` e `/execute` continuam sem persistir um diagnóstico.

~~~mermaid
flowchart TD
    I["Incidente com tenant e cluster definidos"] --> MODE{"Rota da API"}
    MODE -->|"/incidents/investigate"| CORE["Engine determinístico clássico"]
    CORE --> SAVE["Persistir incidente e observações do grafo"]
    SAVE --> UI["API e interface de investigação"]
    MODE -->|"/planner/investigate"| CTX["Congelar contexto operacional"]
    CTX --> PLAN["Compilar plano limitado de especialistas"]
    PLAN --> READ["Especialistas read-only"]
    READ -->|"Traces fornecem identidade da dependência"| DEP["Especialista database"]
    READ --> ENV["Shared Evidence Envelope"]
    DEP --> ENV
    ENV --> VERIFY{"Integridade e gates causais passam?"}
    VERIFY -->|Sim| RESULT["Veredito determinístico e explicação"]
    VERIFY -->|Não| INC["Avaliação inconclusiva"]
    RESULT --> PERSIST["Persistir diagnóstico avaliado"]
    INC --> PERSIST
    PERSIST --> RESP["API, auditoria e tela do incidente"]
~~~

O LLM opcional pode ajudar a **priorizar ramos de investigação**, mas não pode criar evidência, escolher ferramentas arbitrárias, alterar budgets, escrever no grafo ou confirmar uma causa raiz.

---

## Arquitetura

A plataforma é dividida em quatro planos para manter investigação, contexto, mutação e validação separados.

~~~mermaid
flowchart TD
    IN["REST API ou Alertmanager"] --> CORE["Investigação clássica"]
    IN --> PLAN["Investigação agentic opcional"]
    SRC["Fontes tipadas de evidência read-only"] --> CORE
    SRC --> PLAN
    subgraph READ["1. Investigação read-only"]
      CORE --> RCA["RCA clássico"]
      PLAN --> ENV["Especialistas e shared envelope"]
      ENV --> EVAL["Avaliador determinístico"]
      EVAL --> AR["Diagnóstico agentic persistido e auditado"]
      AR --> STORE
    end
    subgraph AWARE["2. Awareness persistente"]
      RCA --> STORE["Persistência do incidente"]
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
~~~

Arquitetura detalhada: [docs/architecture.pt-BR.md](docs/architecture.pt-BR.md).

---

## Aplicação em execução

As capturas abaixo mostram a **v0.8.0-rc.1 executando localmente com `DEMO_MODE=true`**, usando os adapters de demonstração. São telas renderizadas pela aplicação. A validação com fontes reais está registrada nos relatórios do gate em Kind.

### Investigação Kubernetes

![RCA Kubernetes mostrando causa raiz baseada em evidências](docs/images/investigator-k8s.png)

### Causalidade distribuída por traces

![RCA trace-aware mostrando propagação de falha downstream](docs/images/trace-aware-rca.png)

### Dependência stateful PostgreSQL

![RCA de PostgreSQL mostrando evidência de esgotamento de conexões](docs/images/stateful-postgresql.png)

### Persistent Awareness Graph

![Explorer do Persistent Awareness Graph](docs/images/awareness-explorer.png)

### Saúde das integrações

![Operations UI mostrando capabilities e fontes operacionais](docs/images/operations.png)

---

## Principais componentes

### Investigation Engine

O core determinístico normaliza o incidente, coleta evidências tipadas, aplica playbooks e calcula suporte/contradição. Uma hipótese só pode se tornar causa raiz quando suas referências de evidência são válidas e os gates causais necessários passam.

### Investigation Planner

Compila um plano bounded com especialistas permitidos:

- Kubernetes
- metrics
- logs
- traces
- database
- changes

O plano é recompilado no servidor antes da execução. O cliente nunca executa um task graph arbitrário.

### Trace-aware RCA

Busca traces representativos no Tempo, reconstrói relações parent/child e identifica a span de falha mais profunda. Um único trace não é suficiente para confirmar causalidade. A promoção exige repetição da mesma origem em traces independentes.

### Stateful Dependency RCA

Dependências inferidas pelos traces podem ser investigadas com telemetria atual:

- **PostgreSQL:** utilização de conexões, locks, deadlocks, replication lag e long transactions;
- **Kafka:** consumer lag, under-replicated partitions, offline partitions e members;
- **Redis:** memory pressure, evictions, blocked clients, clients saturation e command latency.

Uma causa stateful só é promovida quando a dependência é causalmente relevante **e** a telemetria atual confirma a anomalia.

### Persistent Awareness Graph

Mantém topologia entre incidentes com identidade estável por tenant/cluster, histórico append-only e reconciliação autoritativa.

Inclui:

- current, stale, historical e tombstoned state;
- entidades e relações Kubernetes;
- ChangeEvents;
- inventory reconciliation;
- graph health;
- retention;
- blast radius;
- change timeline;
- Awareness Explorer;
- Operational Context Snapshot usado pelo planner.

Mais detalhes: [docs/persistent-awareness-graph.pt-BR.md](docs/persistent-awareness-graph.pt-BR.md).

### Remediação guardada

O investigador permanece read-only. A mutação existe em um serviço e identidade separados.

O fluxo exige:

~~~text
RCA confirmado
→ proposta tipada
→ policy gates
→ Kubernetes dry-run
→ aprovação humana
→ drift check
→ mutação allow-listed
→ audit
→ rollback explícito
~~~

A primeira ação suportada é <code>scale_deployment</code>. Não existe shell, <code>kubectl exec</code>, patch genérico ou mutação arbitrária.

Mais detalhes: [docs/remediation.pt-BR.md](docs/remediation.pt-BR.md).

---

## Integrações

| Sistema | Uso |
| --- | --- |
| Kubernetes | runtime state, ownership, events, scheduling, inventory |
| Prometheus | métricas de serviço, Kubernetes e dependências |
| Loki | logs bounded por namespace, Pod ou service |
| Tempo / OpenTelemetry | traces, causal path e service graph |
| Argo CD | estado de deployment e revisão aplicada |
| GitHub | metadados de commit/revisão |
| Karpenter | NodeClaim, NodePool e capacidade |
| PostgreSQL + pgvector | incidentes, awareness graph, audit e memória vetorial |
| Alertmanager | entrada automática de incidentes |
| Slack / PagerDuty | notificação opcional após persistência |

---

## Modelo de segurança

A plataforma foi desenhada para **falhar fechado**.

- o investigador usa somente GET/LIST/WATCH no Kubernetes;
- não existe <code>pods/exec</code>;
- não existe shell no investigator;
- evidências recebem hash SHA-256;
- falha de integridade bloqueia seleção de causa raiz;
- contexto histórico não conta como prova causal;
- mudança recente não é automaticamente culpada;
- incidentes similares são apenas pistas;
- branch incompleta, budget esgotado ou source indisponível não produz “healthy” artificial;
- LLM não confirma RCA;
- remediação usa credenciais separadas e aprovação explícita;
- tenant e cluster fazem parte das fronteiras de identidade e acesso.

---

## Modos de execução

| Modo | Objetivo |
| --- | --- |
| <code>DEMO_MODE=true</code> | Explorar UI e fluxos sem dependências externas. |
| Docker Compose | Executar frontend, backend e PostgreSQL/pgvector localmente. |
| Test environment | Implantar imagens validadas pelo GitHub em um cluster Kubernetes de testes. |
| Helm / produção | Integrar fontes reais, OIDC, RBAC, Secrets, NetworkPolicy e multi-cluster. |

---

## Início rápido

~~~bash
cp .env.example .env
docker compose up --build
~~~

Abra:

~~~text
UI:       http://localhost:3000
Swagger:  http://localhost:8080/docs
Health:   http://localhost:8080/healthz
~~~

Telas de demonstração:

~~~text
/                         RCA Kubernetes
/trace                    RCA baseado em traces
/stateful/postgresql      PostgreSQL
/stateful/kafka           Kafka
/stateful/redis           Redis
/awareness                Persistent Awareness Graph
/ops                      Saúde das integrações
~~~

O Compose inicia em modo demo e define o ambiente do backend diretamente em `docker-compose.yml`. Alterar apenas `.env` não ativa as integrações reais; configure o Compose ou use os overrides Helm descritos no runbook.

Backend isolado:

~~~bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
DEMO_MODE=true uvicorn app.main:app --reload --port 8080
~~~

Testes, a partir da raiz do repositório:

~~~bash
(cd backend && pytest -q)
~~~

---

## Ambiente de testes

O repositório possui uma cadeia GitHub-first para gerar e implantar imagens sem depender de uma workstation.

Após o gate passar em `main`, `Publish test images` publica as imagens no GHCR. O deploy é iniciado manualmente em **Actions → Deploy test environment** e exige as credenciais do cluster configuradas no GitHub Environment `test`. O workflow aguarda os rollouts e executa o smoke de backend e frontend.

Imagens publicadas:

~~~text
ghcr.io/marcocristofolini/ai-sre-rca-backend:edge
ghcr.io/marcocristofolini/ai-sre-rca-backend:sha-<sha-de-12-caracteres>
ghcr.io/marcocristofolini/ai-sre-rca-frontend:edge
ghcr.io/marcocristofolini/ai-sre-rca-frontend:sha-<sha-de-12-caracteres>
~~~

Runbook: [docs/test-environment.md](docs/test-environment.pt-BR.md).

---

## APIs principais

### Incidentes

~~~text
GET  /healthz
GET  /api/v1/incidents?q=&cluster=&status=&limit=25&offset=0
GET  /api/v1/incidents/{id}
POST /api/v1/incidents/investigate
POST /api/v1/webhooks/alertmanager
~~~

### Persistent Awareness Graph

~~~text
GET  /api/v1/awareness/graph
GET  /api/v1/awareness/health
GET  /api/v1/awareness/history/{target_id}
GET  /api/v1/awareness/topology
GET  /api/v1/awareness/services/{service_name}
GET  /api/v1/awareness/blast-radius/{target_id}
GET  /api/v1/awareness/changes
POST /api/v1/awareness/inventory/reconcile
POST /api/v1/awareness/retention/purge
~~~

### Agentic investigation

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
~~~

`/plan` somente compila o plano. `/execute` coleta evidências e não retorna causa raiz. `/investigate` também executa a avaliação determinística. Os dois últimos retornam 404 enquanto `AGENTIC_EXECUTION_ENABLED=false`, que é o padrão. A escolha assistida por modelo exige também `AGENTIC_MODEL_PLANNER_ENABLED=true` e `LLM_ENABLED=true`.

O Swagger completo fica em <code>/docs</code>.

---

## Validação de ambiente real

A release gate usa um cluster Kind descartável e executa cenários reais/controlados.

Atualmente valida:

- OOMKilled;
- restart backoff / CrashLoop safety;
- FailedScheduling;
- inventário Kubernetes autoritativo;
- tombstones;
- Persistent Awareness Graph;
- Prometheus;
- Loki;
- Tempo/OpenTelemetry;
- service graph;
- causalidade por traces;
- PostgreSQL e Redis stateful com falha e recuperação;
- paridade classic/agentic;
- integridade SHA-256.

Os relatórios são emitidos em JSON e Markdown e associados ao SHA validado.

Contrato completo: [validation/README_PT-BR.md](validation/README_PT-BR.md).

---

## GitHub-first delivery

O GitHub é a autoridade de merge e release.

~~~text
branch
→ Pull Request
→ GitHub-first gate
   → CI
   → CodeQL
   → Real Environment Validation
→ gate
→ merge em main
→ Publish test images
→ Deploy test environment (manual)
~~~

A promoção de release valida o SHA, o <code>VERSION</code> e o relatório de ambiente real antes de criar a tag.

Detalhes: [docs/github-first.pt-BR.md](docs/github-first.pt-BR.md).

---

## Estado atual

**v0.8.0-rc.3** está focada em telemetria stateful real e recovery validation.

O trabalho atual inclui:

- PostgreSQL 17 + postgres_exporter no ambiente descartável;
- pressão controlada de conexões;
- promoção determinística de causa PostgreSQL;
- recuperação da dependência;
- novas traces pós-recuperação;
- comprovação de que uma causa antiga deixa de ser promovida;
- Redis com maxmemory de 16 MiB e redis_exporter com a identidade `cache:6379`;
- rejeição real de escrita por OOM, RCA `redis-memory-pressure` e recuperação com novas operações bem-sucedidas.

Próximos alvos da rc.3:

- Kafka + kafka_exporter;
- ciclo de falha e recuperação do Kafka;
- fechamento da release candidate.

Roadmap completo: [docs/roadmap.md](docs/roadmap.md).

---

## Estrutura do repositório

~~~text
backend/
  app/core/             engine, causalidade, playbooks e scoring
  app/agentic/          planner, specialists e evaluator
  app/integrations/     adapters read-only
  app/remediation/      plano de remediação separado
  app/db/               PostgreSQL/pgvector
  tests/                regressões e safety invariants

frontend/               Next.js UI
helm/ai-sre-rca/        chart, RBAC e deployment
validation/             release gate em ambiente real
docs/                   arquitetura, operação e runbooks
~~~

---

## Documentação

- [Arquitetura](docs/architecture.pt-BR.md)
- [Investigação agentic](docs/agentic-investigation.pt-BR.md)
- [Persistent Awareness Graph](docs/persistent-awareness-graph.pt-BR.md)
- [Remediação guardada](docs/remediation.pt-BR.md)
- [Operação e segurança](docs/operations.pt-BR.md)
- [Ambiente de testes](docs/test-environment.pt-BR.md)
- [GitHub-first delivery](docs/github-first.pt-BR.md)
- [Roadmap](docs/roadmap.md)
- [Validation harness](validation/README_PT-BR.md)
- [Changelog](CHANGELOG.md)

---

## Princípio central

**Contexto orienta. Evidência prova. O evaluator determinístico decide.**

Esse princípio vale para Kubernetes, traces, dependências stateful, mudanças, memória de incidentes e investigação agentic.

