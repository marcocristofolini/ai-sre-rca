# Investigação Agentic — v0.8.0-rc.2

**Português do Brasil** · [English](agentic-investigation.md)

A v0.8.0-rc.1 inclui um Investigation Planner bounded e um executor paralelo experimental de especialistas. O planejamento fica disponível por padrão; a execução dos especialistas está implementada, porém **desabilitada por padrão**, exigindo `AGENTIC_EXECUTION_ENABLED=true`. Um planner assistido por modelo opcional também fica desabilitado por padrão e exige `LLM_ENABLED=true` mais `AGENTIC_MODEL_PLANNER_ENABLED=true`. Todas as camadas permanecem read-only e somente o evaluator determinístico pode confirmar causa raiz.

O princípio é:

> A IA decide onde investigar. Ferramentas tipadas coletam os fatos. Evidência determinística decide o que pode ser afirmado.

## Planner atual

O planner recebe:

- o incidente normalizado;
- configurações e feature flags do servidor;
- um neighborhood bounded do serviço no Persistent Awareness Graph;
- estado reconciliado current, stale, historical e tombstoned;
- histórico append-only bounded dos alvos selecionados do grafo;
- ChangeEvents recentes do serviço na janela do incidente;
- contagens de Graph Health e freshness das fontes autoritativas de inventário.

Ele pode emitir somente estes tipos de tarefa:

- `kubernetes`
- `metrics`
- `logs`
- `traces`
- `database`
- `changes`

Não existe no schema do plano nome genérico de ferramenta, comando, shell, URL arbitrária ou ação de mutação.

## Operational Context Snapshot congelado

O planner congela o contexto persistente dentro do `InvestigationPlan` antes da execução. O executor entrega o mesmo snapshot para todos os especialistas e o copia para o Shared Evidence Envelope, impedindo que uma investigação troque silenciosamente de visão do grafo no meio da execução.

~~~mermaid
flowchart LR
    PAG[(Persistent Awareness Graph)] --> TOPO[Topologia bounded<br/>fresh / stale / historical / tombstoned]
    PAG --> HIST[Histórico bounded por alvo]
    PAG --> CH[ChangeEvents recentes]
    GH[Graph Health + freshness de inventário] --> SNAP[Operational Context Snapshot congelado]
    TOPO --> SNAP
    HIST --> SNAP
    CH --> SNAP
    SNAP --> PLAN[InvestigationPlan]
    PLAN --> K[Kubernetes]
    PLAN --> M[Métricas]
    PLAN --> L[Logs]
    PLAN --> T[Traces]
    PLAN --> D[Database]
    PLAN --> C[Mudanças]
    SNAP --> ENV[Shared Evidence Envelope]
~~~

O snapshot é contexto, não autoridade de causa raiz. Entidades tombstoned permanecem visíveis para explicação, porém ficam fora de `required_entity_ids`. ChangeEvents persistidos entram como evidência `context_only` quando consumidos pelo specialist de mudanças. Evidência context-only é removida explicitamente do scoring de hipóteses, portanto deploy histórico, tombstone ou fato stale do grafo não pode confirmar causalidade sozinho.

O snapshot é bounded por `AGENTIC_MAX_CONTEXT_NODES`, `AGENTIC_CONTEXT_DEPTH`, `AGENTIC_CONTEXT_MAX_HISTORY_TARGETS`, `AGENTIC_CONTEXT_HISTORY_PER_TARGET` e `AGENTIC_CONTEXT_CHANGE_LIMIT`.

## Scoping determinístico dos especialistas

Um especialista só pode herdar identidade de consulta ausente do contexto congelado do grafo sob regras determinísticas. A identidade explícita do incidente sempre tem precedência. Um Pod ou uma aplicação Argo CD derivados do grafo só são aceitos quando existe exatamente uma entidade correspondente `fresh` e a projeção de topologia não foi truncada.

~~~mermaid
flowchart TD
    INC[Identidade explícita do incidente] --> HAS{Alvo já conhecido?}
    HAS -->|Sim| SCOPE[SpecialistScope tipado]
    HAS -->|Não| GRAPH[Candidatos fresh do grafo]
    GRAPH --> COMPLETE{Topologia completa dentro do budget?}
    COMPLETE -->|Não| BLOCK[Marcar ambiguidade / não inferir]
    COMPLETE -->|Sim| UNIQUE{Exatamente um candidato?}
    UNIQUE -->|Não| BLOCK
    UNIQUE -->|Sim| SCOPE
    SCOPE --> LIVE[Verificação na fonte live<br/>Kubernetes / Loki / Argo CD]
    LIVE --> EVIDENCE[Evidência atual tipada]
~~~

O valor derivado do grafo apenas restringe a consulta, não vira evidência. Kubernetes ainda lê o Pod resolvido na fonte live, Logs ainda consulta o Loki para aquele Pod exato e Changes ainda verifica a aplicação resolvida no Argo CD. Se existirem múltiplos Pods/aplicações fresh ou se o neighborhood tiver sido truncado, o resolver registra ambiguidade e não faz escolha arbitrária.

## Compilação opcional assistida por modelo

O modelo nunca cria um `SpecialistTask`. Primeiro, o planner determinístico constrói o conjunto completo de tarefas elegíveis a partir da identidade do incidente, feature flags e contexto do grafo. O modelo recebe apenas contexto sanitizado mais as descrições desses especialistas elegíveis e pode retornar nomes de especialistas com justificativas curtas.

~~~mermaid
flowchart LR
    I[Incidente + Operational Context Congelado] --> EL[Conjunto Determinístico Elegível]
    EL --> ADV{Model Planner Habilitado?}
    ADV -->|Não| DP[Plano Determinístico]
    ADV -->|Sim| LLM[LLM Advisory<br/>somente nomes de especialistas]
    LLM --> COMP[Compilador no Servidor]
    EL --> COMP
    COMP --> AL[Allow-list de Especialistas]
    COMP --> BASE[Baseline Obrigatório<br/>Kubernetes / Métricas / Traces quando elegíveis]
    COMP --> DEP[Dependências Tipadas<br/>traces antes de database]
    COMP --> BUD[Budgets Controlados pelo Servidor]
    AL --> OUT[InvestigationPlan]
    BASE --> OUT
    DEP --> OUT
    BUD --> OUT
    COMP --> TRACE[Advisory Trace<br/>requested / accepted / rejected / forced]
~~~

Nomes desconhecidos ou inelegíveis são rejeitados. Texto produzido pelo modelo nunca substitui objetivos das tarefas, limites de query ou dependências. Se a chamada ao modelo falhar, retornar JSON inválido ou não propor nenhum especialista elegível, o plano determinístico completo é preservado.

## Fluxo de planejamento

~~~mermaid
flowchart TD
    I[IncidentInput] --> P[Investigation Planner Bounded]
    G[Persistent Awareness Graph] --> C[Operational Context Congelado]
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
| `AGENTIC_MODEL_PLANNER_ENABLED` | false |
| `AGENTIC_MODEL_PLANNER_TIMEOUT_SECONDS` | 10 |
| `AGENTIC_MODEL_PLANNER_MAX_SPECIALISTS` | 6 |
| `AGENTIC_MAX_TASKS` | 6 |
| `AGENTIC_MAX_PARALLEL_TASKS` | 4 |
| `AGENTIC_MAX_CONTEXT_NODES` | 50 |
| `AGENTIC_CONTEXT_DEPTH` | 3 |
| `AGENTIC_CONTEXT_MAX_HISTORY_TARGETS` | 12 |
| `AGENTIC_CONTEXT_HISTORY_PER_TARGET` | 2 |
| `AGENTIC_CONTEXT_CHANGE_LIMIT` | 20 |
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

## Status da v0.8.0-rc.1 e próxima validação

A release candidate já inclui ChangeEvents first-class, o explorer read-only do Persistent Awareness Graph, reconciliação autoritativa com tombstones, Operational Context Snapshot congelado, compilação assistida por modelo atrás de gates determinísticos no servidor, scoping determinístico dos especialistas, execução paralela read-only atrás de feature flag opt-in e avaliação do shared envelope.

O gate de ambiente real da v0.8.0-rc.1 valida a paridade do specialist Kubernetes e a reconciliação do grafo persistente contra um cluster real descartável. O próximo alvo, v0.8.0-rc.2, deve executar planner e executor contra dados reais de Prometheus, Loki e Tempo/OpenTelemetry produzidos por um workload instrumentado. O modelo de autoridade continua igual: modelo/planner escolhem o trabalho de investigação, enquanto evidência live tipada e gates determinísticos decidem o verdict.
