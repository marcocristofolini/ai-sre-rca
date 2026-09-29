# Investigação Agentic — fundação v0.8

**Português do Brasil** · [English](agentic-investigation.md)

A v0.8 introduz um Investigation Planner bounded e um executor paralelo experimental de especialistas. O planejamento fica disponível por padrão; a execução dos especialistas está implementada, porém **desabilitada por padrão**, exigindo `AGENTIC_EXECUTION_ENABLED=true`. As duas camadas são read-only e nenhuma delas confirma causa raiz.

O princípio é:

> A IA decide onde investigar. Ferramentas tipadas coletam os fatos. Evidência determinística decide o que pode ser afirmado.

## Planner atual

O planner recebe:

- o incidente normalizado;
- configurações e feature flags do servidor;
- uma projeção bounded de um salto do Persistent Awareness Graph quando existe identidade de serviço.

Ele pode emitir somente estes tipos de tarefa:

- `kubernetes`
- `metrics`
- `logs`
- `traces`
- `database`
- `changes`

Não existe no schema do plano nome genérico de ferramenta, comando, shell, URL arbitrária ou ação de mutação.
## Fluxo de planejamento

~~~mermaid
flowchart TD
    I[IncidentInput] --> P[Investigation Planner Bounded]
    G[Persistent Awareness Graph] --> C[Contexto Bounded do Serviço]
    C --> P
    S[Budgets no Servidor] --> P

    P --> K[Tarefa Kubernetes]
    P --> M[Tarefa de Métricas]
    P --> L[Tarefa de Logs]
    P --> T[Tarefa de Traces]
    T --> D[Tarefa Database / Stateful]
    P --> CH[Tarefa de Mudanças]

    K --> OUT[InvestigationPlan]
    M --> OUT
    L --> OUT
    T --> OUT
    D --> OUT
    CH --> OUT

    OUT -->|execute opt-in| ORCH[Orquestrador Paralelo de Especialistas]
    ORCH --> SE[Resultados dos Especialistas]
    SE --> EV[Shared Evidence Envelope]
    EV --> HASH{Integridade OK?}
    HASH -->|Não| FAIL[Inconclusivo / Evidência Inválida]
    HASH -->|Sim| SCORE[Recalcular Hipóteses Playbook]
    SCORE --> CAUSAL[Gates Causais Trace / Stateful]
    CAUSAL --> PRE[Gate de Precedência por Fonte]
    PRE --> DET[Verdict Determinístico]
    DET --> EX[Explanation Graph de Hipóteses Descartadas]
~~~

O executor recompila o plano no servidor imediatamente antes da execução. O cliente não pode enviar listas arbitrárias de tarefas, budgets, dependências ou nomes de ferramentas. O envelope compartilhado de evidências propositalmente não possui campo `root_cause`. O evaluator separado só pode produzir um verdict depois de passar pelos gates de integridade, causalidade e precedência.
## Invariantes de segurança

- A saída do planner fica restrita a uma allow-list fixa de especialistas.
- Toda tarefa recebe `read_only=true`.
- O servidor limita quantidade de tarefas, paralelismo, tamanho do contexto do grafo e budget de queries por especialista.
- O specialist Kubernetes delega para a mesma etapa determinística Kubernetes usada pelo core engine; ele não mantém uma segunda implementação simplificada de RCA.
- A prioridade de especificidade da causa raiz Kubernetes é compartilhada com o seletor clássico de playbooks, impedindo que uma hipótese de mecanismo supere uma causa específica confirmada apenas por score bruto maior.
- Qualquer hipótese emitida por um specialist cujo status final não seja `completed` é bloqueada pelo gate de contexto; evidência parcial de ramos `failed`, `skipped` ou `budget_exhausted` nunca pode ser selecionada.
- Kubernetes só é planejado quando o incidente possui identidade de runtime Kubernetes.
- Logs exigem namespace e identidade de pod/serviço.
- Traces exigem identidade de serviço e trace analysis habilitada.
- Database/stateful só é planejado quando existe o ramo de traces; a tarefa database depende explicitamente de `task-traces`.
- O planner não confirma hipóteses nem causa raiz.
- Hipóteses branch-local produzidas pelos especialistas determinísticos são marcadas como advisory-only.
- O executor verifica os hashes das evidências antes de incluí-las no envelope compartilhado.
- Um especialista que esgota seu budget local vira `budget_exhausted`; ramos dependentes são pulados.
- O query budget global do incidente continua ativo além do budget lógico de cada especialista.
- A API de execução recompila o plano no servidor; planos escritos pelo caller nunca são executados.
- Planner e executor não solicitam remediação nem podem contornar o remediator separado.
- O evaluator recalcula as hipóteses a partir do shared envelope em vez de confiar no status local dos especialistas.
- Qualquer falha de integridade ou referência de suporte/contradição ausente bloqueia a seleção de causa raiz.
- RCA puramente trace-aware continua exigindo origem repetida em traces independentes representativos.
- Promoção stateful continua exigindo identidade de origem no trace mais telemetria atual específica da dependência.
- Sinais Kubernetes diretos bloqueiam promoção downstream de trace/stateful até que um playbook de maior precedência possa ser selecionado.
- Se o specialist Kubernetes foi planejado mas falhou, foi pulado inesperadamente ou não concluiu seu contexto, a promoção downstream de trace/stateful é bloqueada; ausência de evidência de infraestrutura nunca é interpretada como infraestrutura saudável.

## API

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
POST /api/v1/planner/investigate
~~~

Os três endpoints exigem papel investigator e a autorização existente de cluster. Callers comuns têm o tenant substituído pelo tenant do principal autenticado. Um principal global ainda precisa informar um tenant concreto. `/execute` e `/investigate` retornam 404 enquanto `AGENTIC_EXECUTION_ENABLED` não estiver habilitado.

Exemplo lógico:

~~~json
{
  "planner_origin": "deterministic",
  "tasks": [
    {"specialist": "kubernetes", "read_only": true},
    {"specialist": "metrics", "read_only": true},
    {"specialist": "logs", "read_only": true},
    {"specialist": "traces", "read_only": true},
    {
      "specialist": "database",
      "depends_on": ["task-traces"],
      "read_only": true
    },
    {"specialist": "changes", "read_only": true}
  ]
}
~~~
## Budgets

Defaults atuais no servidor:

| Configuração | Default |
| --- | ---: |
| `AGENTIC_EXECUTION_ENABLED` | false |
| `AGENTIC_MAX_TASKS` | 6 |
| `AGENTIC_MAX_PARALLEL_TASKS` | 4 |
| `AGENTIC_MAX_CONTEXT_NODES` | 50 |
| `AGENTIC_SPECIALIST_MAX_QUERIES` | 12 |
| `AGENTIC_KUBERNETES_MAX_QUERIES` | 30 |

São limites rígidos do servidor, não sugestões fornecidas pelo cliente.

## Execução paralela

O executor roda tarefas independentes em paralelo até `AGENTIC_MAX_PARALLEL_TASKS`. Tarefas com dependências entram em waves posteriores. Hoje isso significa que o ramo database/stateful só pode executar depois de o ramo de traces terminar com análises tipadas.

O ramo Kubernetes usa um budget read-only próprio porque a etapa determinística completa pode consultar legitimamente estado de Pod/Node, eventos, ownership ReplicaSet/Deployment, HPA/KEDA, Prometheus, Loki, Argo CD, GitHub e contexto do Karpenter. O default é 30 queries no specialist Kubernetes, ainda subordinadas ao query budget global do incidente.

As saídas dos especialistas incluem evidências, entidades, arestas do grafo e estruturas de análise tipadas. Tudo é mesclado em um `SharedEvidenceEnvelope`; evidências cujo hash de integridade falhe são excluídas do envelope e fazem a integridade global ficar falsa.

A camada atual preserva:

- sem shell/SSH/kubectl exec;
- sem ferramenta HTTP arbitrária;
- apenas queries determinísticas e scoped;
- budgets de query por ramo mais o query budget global já existente;
- isolamento tenant/cluster;
- hashes e identidade de origem das evidências;
- sem saída de causa raiz e sem ação de remediação.

## Avaliação determinística do shared envelope

`POST /api/v1/planner/investigate` compõe as três etapas atuais:

1. construir o plano bounded no servidor;
2. executar especialistas read-only allow-listed em waves com dependências;
3. avaliar deterministicamente o shared envelope.

O evaluator **não** confia em `confirmed` produzido localmente pelo specialist. Ele filtra apenas evidências com integridade verificada, remove referências inválidas, recalcula o score ponderado existente e então aplica gates causais:

- hipóteses puramente trace-aware precisam da mesma origem em pelo menos dois trace IDs representativos;
- hipóteses stateful precisam de análise tipada com `causal_role=origin`, evidência atual da origem no trace e telemetria específica da dependência;
- sinais Kubernetes diretos como eviction, FailedScheduling, node NotReady ou pressão ativa no node bloqueiam promoção downstream;
- hipóteses stateful têm precedência sobre o pai trace-only menos específico quando ambas estão confirmadas.

O resultado inclui `root_cause` determinística somente quando todos esses gates passam. Também retorna um explanation graph com nós de hipótese, evidência, gate e verdict conectados por `supports`, `contradicts`, `blocks`, `selected` e `considered`.

## Próxima fase

O próximo passo é aproximar o specialist Kubernetes dos playbooks completos da engine principal, adicionar eventos first-class de deployment/change e uma UI para explorar tanto topologia quanto explanation graphs.
