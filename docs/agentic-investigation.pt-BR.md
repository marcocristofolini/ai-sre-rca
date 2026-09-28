# Investigação Agentic — fundação v0.8

**Português do Brasil** · [English](agentic-investigation.md)

A v0.8 introduz um Investigation Planner bounded como primeira camada agentic. A implementação atual está propositalmente em modo **plan-only**: ela escolhe ramos de investigação tipados e read-only, além das dependências entre eles, mas ainda não executa specialist agents em paralelo.

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

    OUT -. próxima fase .-> ORCH[Orquestrador Paralelo de Especialistas]
    ORCH -. próxima fase .-> EV[Evidência Compartilhada]
    EV -. autoridade existente .-> DET[Avaliador Determinístico]
~~~

O caminho pontilhado de execução ainda não está implementado. A API atual retorna o `InvestigationPlan` tipado.
## Invariantes de segurança

- A saída do planner fica restrita a uma allow-list fixa de especialistas.
- Toda tarefa recebe `read_only=true`.
- O servidor limita quantidade de tarefas, paralelismo, tamanho do contexto do grafo e budget de queries por especialista.
- Kubernetes só é planejado quando o incidente possui identidade de runtime Kubernetes.
- Logs exigem namespace e identidade de pod/serviço.
- Traces exigem identidade de serviço e trace analysis habilitada.
- Database/stateful só é planejado quando existe o ramo de traces; a tarefa database depende explicitamente de `task-traces`.
- O planner não confirma hipóteses nem causa raiz.
- O planner não anexa IDs de evidência.
- O planner não solicita remediação.
- O planner não pode contornar o serviço remediator separado.

## API

~~~text
POST /api/v1/planner/plan
~~~

O caller precisa ter papel investigator. O tenant enviado pelo caller é substituído pelo tenant do principal autenticado, e a autorização existente de cluster continua obrigatória.

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
| `AGENTIC_MAX_TASKS` | 6 |
| `AGENTIC_MAX_PARALLEL_TASKS` | 4 |
| `AGENTIC_MAX_CONTEXT_NODES` | 50 |
| `AGENTIC_SPECIALIST_MAX_QUERIES` | 12 |

São limites rígidos do planner, não sugestões fornecidas pelo cliente.

## Próxima fase

O próximo passo é um orquestrador tipado que execute ramos elegíveis em paralelo e grave todos os resultados em um envelope compartilhado de evidências. O avaliador determinístico existente continuará sendo o único componente autorizado a promover uma hipótese para causa raiz confirmada.

A execução futura dos especialistas deve preservar:

- sem shell/SSH/kubectl exec;
- sem PromQL/LogQL genérico produzido por um modelo irrestrito;
- sem ferramenta HTTP arbitrária;
- budgets de query por ramo;
- isolamento tenant/cluster;
- hashes e identidade de origem das evidências;
- explicação explícita de hipóteses descartadas.
