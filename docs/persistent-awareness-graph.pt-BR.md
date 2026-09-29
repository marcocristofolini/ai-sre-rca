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
- `awareness_observations`: observações imutáveis por incidente para histórico de entidade ou relação.

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
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
GET /api/v1/awareness/topology?cluster=<cluster>
GET /api/v1/awareness/services/<service_name>?cluster=<cluster>&namespace=<ns>&depth=2
GET /api/v1/awareness/blast-radius/<target_id>?cluster=<cluster>&depth=3
POST /api/v1/awareness/inventory/reconcile?cluster=<cluster>
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
- métricas e política de retention do grafo;
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
