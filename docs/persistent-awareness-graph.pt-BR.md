# Awareness Graph Persistente — fundação v0.8

**Português do Brasil** · [English](persistent-awareness-graph.md)

A v0.8 inicia a transição de uma topologia válida apenas durante o incidente para conhecimento operacional persistente. O grafo recebe somente fatos que já foram coletados como evidência por uma investigação. O LLM não ganha uma operação genérica de escrita no grafo.

## Objetivos

- identidade lógica estável entre incidentes independentes;
- isolamento estrito por tenant e cluster;
- estado atual do grafo mais observações temporais append-only;
- relações ligadas às evidências;
- ingestão idempotente quando um incidente é persistido novamente;
- superfície de consulta para blast radius, memória e futuros agentes.

## Identidade canônica

IDs locais de incidente como `pod` e `deployment` não são tratados como identidade global. O ID persistente de uma entidade é derivado de:

`tenant + cluster + kind + namespace + name`

O ID de uma relação é derivado de:

`tenant + cluster + origem canônica + relação + destino canônico`
Os dois usam identificadores estáveis derivados de SHA-256. Tenant e cluster fazem parte da fronteira de identidade, evitando colapso de recursos homônimos entre ambientes.

## Modelo temporal

O grafo armazena três famílias de registros:

- `awareness_entities`: estado lógico atual, first/last seen e contador de observações;
- `awareness_relationships`: relação atual, referências de evidência da observação mais recente, first/last seen e contador;
- `awareness_observations`: histórico imutável de entidades/relações mais receipts de snapshots de inventário usados para garantir identidade de replay.

O ID das observações de incidente é determinístico a partir de `incidente + alvo`. Observações de inventário autoritativo usam `fonte + snapshot + alvo + ação`. Reprocessar a mesma evidência é idempotente e não aumenta os contadores. Metadata e referências de evidência antigas permanecem em `awareness_observations`; o registro atual da entidade/relação reflete a observação válida mais recente. Entidades e relações também carregam timestamp, fonte e snapshot do tombstone quando um inventário completo e autoritativo comprova que um fato conhecido deixou de ser atual.

~~~mermaid
flowchart LR
    RCA[Investigação Persistida] --> CAN[Canonicalizar grafo local]
    CAN --> E[Entidades Persistentes]
    CAN --> R[Relações Persistentes]
    E --> O[Observações Temporais]
    R --> O
    E --> Q[Consulta do Grafo]
    R --> Q
    O --> H[Histórico]
~~~
## Fronteira de persistência

A ingestão ocorre no limite de persistência do repositório. Com PostgreSQL, o payload da investigação e as atualizações do Awareness Graph são confirmados na mesma transação do repositório. O repositório em memória implementa o mesmo contrato para testes e demo.

## Reconciliação de topologia

A v0.8 classifica entidades e relações de topologia como `fresh`, `stale` ou `tombstoned`. Fresh/stale vem de `last_seen` e de um threshold controlado pelo servidor. Fatos temporais imutáveis, como nós `ChangeEvent`, reconciliam como `historical`.

~~~mermaid
flowchart LR
    INC[Observação Parcial de Incidente] --> CUR[Estado Atual do Grafo]
    CUR --> AGE[Comparar last_seen com threshold]
    AGE -->|dentro do threshold| F[Fresh]
    AGE -->|mais antigo| S[Stale]
    S --> KEEP[Preservar fato sem inferir deleção]

    INV[Inventário Autoritativo Completo] --> SCOPE[Validar fonte + cobertura declarada]
    SCOPE --> PRESENT[Atualizar fatos presentes]
    SCOPE --> ABSENT[Fato coberto ausente do snapshot]
    ABSENT -->|last_seen <= horário do snapshot| T[Tombstoned]
    ABSENT -->|há observação mais nova| SKIP[Preservar fato mais novo]
~~~

Ausência em um incidente nunca é evidência de deleção. Um tombstone só pode nascer de `POST /api/v1/awareness/inventory/reconcile`, que exige papel admin, fonte presente em `AWARENESS_AUTHORITATIVE_INVENTORY_SOURCES`, `complete=true` e cobertura explícita de kinds/namespaces/relações. Recursos fora dessa cobertura não são alterados. Nós ChangeEvent e relações de evento ficam fora da reconciliação de tombstones.

Uma observação posterior, vinda de incidente ou inventário autoritativo, pode reativar um fato tombstoned. Snapshots antigos nunca sobrescrevem nem removem observações mais novas. A tupla `tenant + cluster + source + snapshot_id` é imutável: repetir o mesmo conteúdo é idempotente, enquanto reutilizar essa identidade com conteúdo diferente retorna conflito. Fatos tombstoned continuam consultáveis no grafo/histórico para explicação, mas ficam fora da resolução corrente de entidades e da travessia de blast radius.

## Fonte automática de inventário Kubernetes

A primeira fonte autoritativa automatizada é um worker one-shot de inventário Kubernetes, implantado pelo Helm como um `CronJob` opcional. Ele não roda dentro do processo da API: escalar a API horizontalmente não multiplica loops de inventário, e `concurrencyPolicy: Forbid` impede jobs sobrepostos da mesma release.

O worker executa somente operações GET/LIST no Kubernetes usando o ServiceAccount read-only do investigador. Os kinds iniciais suportados são `Service`, `Deployment`, `StatefulSet`, `DaemonSet`, `ReplicaSet`, `Pod` e `Node`. A paginação segue o token `metadata.continue` da API Kubernetes até terminar e falha de forma fechada se uma página trouxer itens malformados ou repetir um token de continuação. Qualquer falha HTTP, de listagem ou de payload aborta a coleta antes que um snapshot chegue à reconciliação.

As relações são emitidas somente a partir de estado explícito do Kubernetes:

- `owned_by` a partir de um único `ownerReference` controlador, somente quando seu UID corresponde ao objeto owner observado;
- `scheduled_on` a partir de `Pod.spec.nodeName`, apenas quando o snapshot é cluster-wide e inclui Nodes;
- `serves` a partir de um selector não vazio de Service que corresponde aos labels do Pod template de um Deployment, StatefulSet ou DaemonSet, inclusive quando o workload está escalado para zero.

Uma coleta limitada por namespace propositalmente não afirma semântica de deleção Pod-to-Node, porque Node possui escopo de cluster. Nesse modo o worker emite escopos autoritativos separados para recursos namespaced e Nodes. Uma coleta cluster-wide pode reconciliar arestas de scheduling porque os dois lados foram observados na mesma coleta completa.

~~~mermaid
flowchart LR
    CRON[Helm CronJob<br/>concurrencyPolicy: Forbid] --> GET[Kubernetes somente GET/LIST]
    GET --> PAGE[Paginar até metadata.continue ficar vazio]
    PAGE --> VALID{Objetos válidos + token progride?}
    VALID -->|Não| ABORT[Abortar execução<br/>sem reconciliação]
    VALID -->|Sim| COMPLETE{Todas as listas configuradas completas?}
    COMPLETE -->|Não| ABORT
    COMPLETE -->|Sim| BUILD[Construir recursos + relações explícitas]
    BUILD --> SNAP[Snapshot autoritativo completo]
    SNAP --> REC[Contrato existente de reconciliação]
    REC --> PAG[(Persistent Awareness Graph)]
    REC --> AUDIT[(Histórico de auditoria)]
~~~

## Graph Health

`GET /api/v1/awareness/health?cluster=<cluster>` expõe uma projeção factual do grafo persistido com escopo por tenant/cluster. Ela informa separadamente contagens current, stale, historical e tombstoned para entidades e relações, o total/horário da observação de awareness mais recente e a freshness de cada fonte autoritativa configurada.

A freshness da fonte de inventário usa o último receipt imutável do tipo `inventory_snapshot`. `AWARENESS_INVENTORY_MAX_AGE_MINUTES` tem padrão de 15 minutos e é propositalmente independente de `AWARENESS_STALE_AFTER_MINUTES`, que controla a freshness dos fatos do grafo. Uma source aparece como `missing` quando nunca recebeu snapshot autoritativo, `current` enquanto o último receipt está dentro da idade configurada e `stale` depois desse limite.

O endpoint exige papel viewer e preserva o isolamento existente por tenant/cluster. Ele propositalmente não reduz essas dimensões a um score composto de saúde do grafo: o consumidor recebe o estado observável e pode alertar sobre as dimensões relevantes ao seu ambiente.

~~~mermaid
flowchart LR
    E[Entidades / Relações] --> C[Contagens de Estado]
    O[Observações Append-only] --> L[Observação Mais Recente]
    O --> R[Último Receipt de Inventário por Source]
    C --> H[API de Graph Health]
    L --> H
    R --> H
~~~

## Retention segura para explicabilidade

A retention do Awareness Graph é separada da retention de incidentes/auditoria porque receipts de inventário e observações históricas do grafo possuem requisitos mais fortes de identidade e explicação.

`POST /api/v1/awareness/retention/purge?cluster=<cluster>` exige papel admin e usa `dry_run=true` por padrão. Duas janelas independentes controlam a elegibilidade:

- `AWARENESS_OBSERVATION_RETENTION_DAYS=90` para observações redundantes de entidades/relações;
- `AWARENESS_TOMBSTONE_RETENTION_DAYS=30` para linhas tombstoned do estado corrente.

As regras de segurança são mais rígidas que um delete apenas por idade:

- todo receipt `inventory_snapshot` é preservado, mantendo replay/idempotência e detecção de conteúdo conflitante para snapshots;
- a observação mais recente de cada entidade/relação é preservada mesmo quando mais antiga que a janela;
- relações tombstoned expiradas são removidas antes das entidades tombstoned expiradas;
- uma entidade tombstoned só é removida quando nenhuma relação sobrevivente ainda a referencia;
- dry-run não remove dados e não gera evento de auditoria de purge;
- um purge executado é auditado na mesma transação do repositório que realiza as deleções;
- o worker agendado opcional usa o mesmo contrato de purge, escopo explícito de tenant/cluster, Secret dedicado de banco e nenhum token de ServiceAccount Kubernetes montado.

~~~mermaid
flowchart TD
    AGE[Linhas mais antigas que o cutoff] --> TYPE{Tipo de registro}
    TYPE -->|inventory_snapshot| KEEP1[Sempre preservar]
    TYPE -->|observação de entidade/relação| LAST{É a última do alvo?}
    LAST -->|Sim| KEEP2[Preservar mínimo explicável]
    LAST -->|Não| DEL[Elegível para remoção]

    TREL[Relação tombstoned expirada] --> DELREL[Remover relação]
    TENT[Entidade tombstoned expirada] --> REF{Alguma relação sobrevivente?}
    REF -->|Sim| BLOCK[Preservar entidade]
    REF -->|Não| DELENT[Remover entidade]
~~~

## Projeções centradas em serviço

A projeção resolve a entidade canônica exata do tipo `Service` e percorre um neighborhood topológico sob budgets rígidos de profundidade, nós e arestas. Se o mesmo nome de serviço existir em mais de um namespace, o caller precisa informar o namespace em vez de receber uma correspondência arbitrária.

## Blast radius

Blast radius não é uma travessia genérica do grafo. Cada tipo de relação possui direção explícita de impacto:

| Relação | Direção armazenada | Direção de impacto |
| --- | --- | --- |
| `calls` / `depends_on` | caller → dependência | dependência → caller |
| `scheduled_on` | workload → node | node → workload |
| `owned_by` | filho → owner | owner → filho |
| `managed_by` | workload → manager | manager → workload |
| `syncs_from` | app → revisão/fonte | fonte → app |
| `represented_by` | recurso → objeto controller | controller → recurso |
| `runs_as` | serviço → workload | workload → serviço |
| `serves` | workload → serviço | workload → serviço |

Tipos de relação desconhecidos permanecem visíveis nas respostas de topologia, mas nunca são percorridos como caminhos de impacto. Relações `stale` ou `tombstoned` ficam fora da propagação atual. Uma entidade tombstoned não pode ser usada como origem atual do blast radius e alvos tombstoned não são promovidos como impactos atuais.

## API

~~~text
GET /api/v1/awareness/graph?cluster=<cluster>
GET /api/v1/awareness/health?cluster=<cluster>
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
GET /api/v1/awareness/topology?cluster=<cluster>
GET /api/v1/awareness/services/<service_name>?cluster=<cluster>&namespace=<ns>&depth=2
GET /api/v1/awareness/blast-radius/<target_id>?cluster=<cluster>&depth=3
POST /api/v1/awareness/inventory/reconcile?cluster=<cluster>
POST /api/v1/awareness/retention/purge?cluster=<cluster>&dry_run=true
~~~

O tenant vem do principal autenticado. Um principal global com `tenant_id="*"` precisa informar explicitamente `tenant_id`; principals comuns não podem consultar outro tenant. A autorização de cluster existente continua obrigatória. A reconciliação de inventário também exige papel admin e uma fonte autoritativa allow-listed. Toda reconciliação aceita gera registro de auditoria com escopo e contagem de tombstones.

Os limites padrão no servidor são `depth=4`, `nodes=200`, `edges=800`, com threshold de stale em 24 horas. Todos podem ser configurados por Helm/variáveis de ambiente.

## Invariantes de segurança

- nenhuma mutação de infraestrutura é adicionada;
- nenhum LLM grava fatos diretamente no grafo;
- evidência de incidente pode adicionar observações, mas o estado de deleção só muda com snapshot completo de uma fonte autoritativa allow-listed;
- endpoints de aresta desconhecidos são ignorados, nunca inventados;
- tenant e cluster participam da identidade e de toda consulta;
- observações históricas permanecem preservadas quando metadata atual muda;
- ausência em incidente nunca cria tombstone;
- a cobertura do snapshot é explícita e fail-closed;
- snapshots antigos não podem tombstonar observações mais novas;
- ChangeEvents são fatos históricos e nunca recebem tombstone de inventário.

## Próximas fases da v0.8

Concluído na branch atual:

- reconciliação de freshness sem inferência insegura de tombstone;
- projeções bounded centradas em serviço;
- blast radius com direção causal explícita;
- contexto bounded do grafo para o Investigation Planner;
- avaliação determinística do shared envelope e rejected-hypothesis explanation graph;
- Awareness Explorer read-only para topologia reconciliada, projeções por serviço e histórico de ChangeEvents;
- reconciliação autoritativa de inventário completo com tombstones first-class, auditoria e semântica de reativação.

Ainda planejado:

- enriquecimento autoritativo de Change Events a partir de outros sistemas de delivery além de Argo CD/GitHub;
- execução agendada de retention do Awareness Graph independente do startup da API;
- contexto mais rico do grafo para paridade do specialist Kubernetes e raciocínio sobre eventos de deployment/change.


## Awareness Explorer read-only

A rota Next.js `/awareness` consome somente APIs de viewer. Ela permite escopo por cluster, namespace e serviço, renderiza o estado reconciliado `fresh` / `stale` / `historical` / `tombstoned` das entidades, mostra relações persistidas e posiciona ChangeEvents em uma timeline histórica bounded. Quando um serviço é informado, a leitura de topologia usa o endpoint existente de projeção bounded por serviço em vez de implementar travessia do grafo no browser.

O explorer encaminha os mesmos headers de autorização da UI existente e expõe override opcional de tenant apenas para principals que já possuem escopo global de tenant. Não há controles de mutação nem endpoint de escrita no grafo.

~~~mermaid
flowchart LR
    UI[Awareness Explorer] --> TOP[GET /awareness/topology]
    UI --> SVC[GET /awareness/services/service]
    UI --> CH[GET /awareness/changes]
    TOP --> PAG[(Persistent Awareness Graph)]
    SVC --> PAG
    CH --> PAG
~~~

## Change Events first-class

Evidências coletadas de deployment no Argo CD são projetadas em nós estáveis `ChangeEvent`. O mesmo evento é identificado por aplicação, revisão e horário do evento, então reobservações em incidentes diferentes aumentam o histórico sem duplicar o deploy.

A metadata do evento inclui horário, aplicação, revisão, repositório, serviço/workload alvo e IDs das evidências. Evidência correspondente do GitHub adiciona SHA, mensagem, autor e link do commit.

As relações do evento são propositalmente contextuais:

- `observed_on` → ArgoCDApplication
- `targets` → Deployment
- `targets_service` → Service
- `revision_from` → GitRepository

Essas relações não entram na allow-list de direção de impacto do blast radius. Um deploy recente continua sendo correlação/contexto até que evidência de runtime satisfaça uma hipótese causal determinística.

A materialização do evento é fail-closed. `application`, `revision` e um timestamp autoritativo da operação do Argo CD são obrigatórios; valores ausentes ou malformados continuam somente como evidência de correlação. Os vínculos com Deployment, Service e GitRepository são adicionados apenas por relações explícitas e não ambíguas `managed_by`, `serves` e `syncs_from` já existentes no grafo da investigação. A implementação nunca usa o primeiro recurso do namespace como evidência de identidade.

Consulta temporal:

```text
GET /api/v1/awareness/changes
```

Filtros: `cluster` obrigatório, `service`, `namespace`, `since`, `until`, `tenant_id` para principal global e `limit` bounded.
