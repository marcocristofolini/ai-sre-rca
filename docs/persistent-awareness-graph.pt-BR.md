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

O ID de cada observação é determinístico a partir de `incidente + alvo`. Salvar novamente o mesmo incidente é idempotente e não aumenta os contadores. Metadata e referências de evidência antigas permanecem em `awareness_observations`; o registro atual da entidade/relação reflete a observação mais recente.

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

## API

~~~text
GET /api/v1/awareness/graph?cluster=<cluster>
GET /api/v1/awareness/graph?cluster=<cluster>&namespace=<ns>&kind=<kind>
GET /api/v1/awareness/history/<target_id>?cluster=<cluster>
~~~

O tenant vem do principal autenticado. Um principal global com `tenant_id="*"` precisa informar explicitamente `tenant_id`; principals comuns não podem consultar outro tenant. A autorização de cluster existente continua obrigatória.

## Invariantes de segurança

- nenhuma mutação de infraestrutura é adicionada;
- nenhum LLM grava fatos diretamente no grafo;
- somente entidades e relações produzidas pela investigação entram no grafo;
- endpoints de aresta desconhecidos são ignorados, nunca inventados;
- tenant e cluster participam da identidade e de toda consulta;
- observações históricas permanecem preservadas quando metadata atual muda.

## Próximas fases da v0.8

- reconciliação de topologia e semântica de stale/tombstone;
- projeções centradas em serviço;
- blast radius por travessia do grafo;
- mudanças/deploys como eventos temporais first-class;
- contexto bounded do grafo para o futuro Investigation Planner;
- métricas/retention do grafo;
- UI de exploração do grafo.
