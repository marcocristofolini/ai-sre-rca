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
    EV -. fronteira separada de autoridade .-> DET[Avaliador Determinístico]
~~~

O executor recompila o plano no servidor imediatamente antes da execução. O cliente não pode enviar listas arbitrárias de tarefas, budgets, dependências ou nomes de ferramentas. O envelope compartilhado de evidências propositalmente não possui campo `root_cause`.
## Invariantes de segurança

- A saída do planner fica restrita a uma allow-list fixa de especialistas.
- Toda tarefa recebe `read_only=true`.
- O servidor limita quantidade de tarefas, paralelismo, tamanho do contexto do grafo e budget de queries por especialista.
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

## API

~~~text
POST /api/v1/planner/plan
POST /api/v1/planner/execute
~~~

Os dois endpoints exigem papel investigator e a autorização existente de cluster. Callers comuns têm o tenant substituído pelo tenant do principal autenticado. Um principal global ainda precisa informar um tenant concreto. `/execute` retorna 404 enquanto `AGENTIC_EXECUTION_ENABLED` não estiver habilitado.

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

São limites rígidos do servidor, não sugestões fornecidas pelo cliente.

## Execução paralela

O executor roda tarefas independentes em paralelo até `AGENTIC_MAX_PARALLEL_TASKS`. Tarefas com dependências entram em waves posteriores. Hoje isso significa que o ramo database/stateful só pode executar depois de o ramo de traces terminar com análises tipadas.

As saídas dos especialistas incluem evidências, entidades, arestas do grafo e estruturas de análise tipadas. Tudo é mesclado em um `SharedEvidenceEnvelope`; evidências cujo hash de integridade falhe são excluídas do envelope e fazem a integridade global ficar falsa.

A camada atual preserva:

- sem shell/SSH/kubectl exec;
- sem ferramenta HTTP arbitrária;
- apenas queries determinísticas e scoped;
- budgets de query por ramo mais o query budget global já existente;
- isolamento tenant/cluster;
- hashes e identidade de origem das evidências;
- sem saída de causa raiz e sem ação de remediação.

## Próxima fase

O próximo passo é alimentar o envelope compartilhado em uma etapa dedicada de agregação/avaliação determinística e construir o rejected-hypothesis explanation graph. Essa etapa decidirá quais hipóteses branch-local continuam suportadas, ruled out ou inconclusivas sem entregar autoridade de causa raiz ao planner ou aos especialistas.
