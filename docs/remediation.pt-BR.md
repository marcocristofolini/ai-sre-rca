# Plano de remediação guardada — v0.7

**Português do Brasil** · [English](remediation.md)

A v0.7 adiciona um plano de controle de remediação sem transformar o investigador RCA em um serviço com escrita. O investigator mantém o ServiceAccount read-only. A remediação roda em outro processo FastAPI, outro Deployment e outro ServiceAccount Kubernetes.

## Boundary

~~~mermaid
flowchart LR
    INV[Investigator GET LIST WATCH] --> RCA[RCA baseado em evidências]
    RCA --> PLAN[Plano de remediação proposto]
    PLAN --> POL[Validação estática de política]
    POL --> INSPECT[Ler estado atual de Scale]
    INSPECT --> DYN[Validação dinâmica de política]
    DYN --> DRY[Kubernetes dryRun=All]
    DRY --> WAIT[Aguardar aprovação humana]
    WAIT --> APPROVE[Aprovação com papel remediator]
    APPROVE --> DRIFT{Alvo permaneceu igual?}
    DRIFT -->|Não| BLOCK[Bloquear execução]
    DRIFT -->|Sim| EXEC[PATCH deployments/scale]
    EXEC --> AUDIT[Auditoria + metadata de rollback]
    AUDIT --> RB[Endpoint explícito de rollback]
    INV -. sem credencial de escrita .-> EXEC
~~~

Os dois serviços podem compartilhar o PostgreSQL para o remediator carregar o incidente estruturado e gravar auditoria. Eles não compartilham a identidade Kubernetes usada para mutação.

## Allow-list atual

A v0.7 entrega propositalmente uma única ação mutável: **scale_deployment**.

O executor só chama o subresource de Scale de Deployment usando GET e PATCH. Não existe patch genérico de Kubernetes, shell, kubectl, pods/exec, create/delete de objetos, Argo sync, mutação em cloud ou primitive de URL arbitrária.

## Gates de política

Antes do dry-run, todos estes checks precisam passar:

- remediação explicitamente habilitada;
- incidente resolvido e com idade máxima de `REMEDIATION_MAX_INCIDENT_AGE_MINUTES`;

- evidência da causa raiz medium ou strong e com sinais de suporte;
- hipótese raiz presente no incidente como hipótese determinística de playbook confirmada;
- ID exato da hipótese raiz presente em REMEDIATION_SCALE_HYPOTHESIS_ALLOWLIST;
- ação presente na allow-list;
- namespace presente na allow-list e igual ao namespace do incidente;
- Deployment alvo presente no Awareness Graph;
- quantidade de réplicas dentro dos limites configurados;
- cluster presente no registry separado de remediação.

Depois o serviço lê as réplicas atuais. A mudança não pode ser no-op e o delta deve respeitar REMEDIATION_MAX_REPLICA_DELTA. Só então é enviado o dry-run server-side ao Kubernetes.

## Aprovação e regra de quatro olhos

Planos aprovados pela política e pelo dry-run entram em **awaiting_approval**. A aprovação exige exatamente o papel OIDC remediator configurado. Por padrão REMEDIATION_REQUIRE_DISTINCT_APPROVER=true, então quem solicitou o plano não pode aprová-lo.

Aprovar ainda não executa nada. A execução exige outra chamada explícita da API por um remediator. Aprovação, claim de execução e claim de rollback usam transições atômicas compare-and-save, impedindo que requisições concorrentes antigas sobrescrevam um estado mais novo do plano.

## Proteção contra drift

O preflight guarda a quantidade atual de réplicas e o resourceVersion do Kubernetes. Na execução, o Scale subresource é lido novamente. Se qualquer um mudou depois do dry-run e da aprovação, a execução é bloqueada e um novo plano precisa ser criado.

Imediatamente antes da mutação real, o remediator repete o dry-run server-side contra o alvo ainda inalterado. O PATCH real envia o resourceVersion armazenado para o Kubernetes rejeitar uma mudança concorrente que aconteça entre esse dry-run e a execução.

## Rollback

Todo plano aceito guarda a quantidade anterior de réplicas antes da aprovação. O rollback é outra ação explícita do remediator e restaura somente esse valor usando o mesmo subresource deployments/scale. O rollback também confere réplicas e resourceVersion deixados pela execução; se outro ator alterou o alvo depois, ele é bloqueado em vez de sobrescrever o estado mais novo.

## API

A API separada do remediator expõe:

~~~text
GET  /healthz
POST /api/v1/remediation/plans
GET  /api/v1/remediation/plans
GET  /api/v1/remediation/plans/{plan_id}
POST /api/v1/remediation/plans/{plan_id}/approve
POST /api/v1/remediation/plans/{plan_id}/reject
POST /api/v1/remediation/plans/{plan_id}/execute
POST /api/v1/remediation/plans/{plan_id}/rollback
~~~

A API do investigator não expõe essas rotas.

Exemplo de plano:

~~~json
{
  "incident_id": "inc-123",
  "action_type": "scale_deployment",
  "namespace": "payments",
  "deployment": "checkout",
  "desired_replicas": 5,
  "rationale": "Restaurar capacidade temporariamente enquanto a causa de saturação é corrigida."
}
~~~

## Credenciais separadas e RBAC Helm

REMEDIATION_CLUSTER_CONFIG_JSON é separado de CLUSTER_CONFIG_JSON. Em cluster local, um cluster lógico configurado pode usar o token do ServiceAccount do próprio Pod do remediator. Clusters remotos devem usar credenciais distintas montadas somente nesse serviço.

O remediator do Helm vem desabilitado por padrão. targetNamespaces também começa vazio, então nenhum Role de escrita é criado até os namespaces serem declarados explicitamente. O Helm rejeita configurações que reutilizem o nome do ServiceAccount do investigator no remediator.

Para cada namespace alvo, o chart cria somente:

~~~yaml
apiGroups: ["apps"]
resources: ["deployments/scale"]
verbs: ["get", "patch"]
~~~

## Configuração operacional

~~~text
REMEDIATION_ENABLED=true
REMEDIATION_REQUIRE_DISTINCT_APPROVER=true
REMEDIATION_PLAN_TTL_MINUTES=30
REMEDIATION_MAX_INCIDENT_AGE_MINUTES=120
REMEDIATION_ALLOWED_ACTIONS=scale_deployment
REMEDIATION_ALLOWED_NAMESPACES=payments,checkout
REMEDIATION_SCALE_HYPOTHESIS_ALLOWLIST=capacity-saturation
REMEDIATION_MIN_REPLICAS=1
REMEDIATION_MAX_REPLICAS=20
REMEDIATION_MAX_REPLICA_DELTA=3
REMEDIATION_CLUSTER_CONFIG_JSON={"prod-us":{}}
REMEDIATOR_ROLE=remediator
~~~

Com REMEDIATION_ENABLED=true, o remediator recusa o startup se AUTH_ENABLED não estiver true, se DATABASE_URL não for persistente/compartilhado, se issuer/audience/JWKS do OIDC estiverem incompletos ou se REMEDIATOR_ROLE reutilizar viewer/investigator/admin. DATABASE_URL deve apontar para o mesmo banco operacional usado pelo investigator. O compartilhamento é do estado do incidente e da auditoria, não da credencial Kubernetes de mutação.

## Máquina de estados

~~~mermaid
stateDiagram-v2
    [*] --> policy_denied
    [*] --> dry_run_failed
    [*] --> awaiting_approval
    awaiting_approval --> approved
    awaiting_approval --> rejected
    awaiting_approval --> expired
    approved --> rejected
    approved --> expired
    approved --> executing
    executing --> executed
    executing --> execution_failed
    executed --> rolling_back
    rolling_back --> rolled_back
    rolling_back --> rollback_failed
~~~

A v0.7 não adiciona execução autônoma. Uma recomendação do RCA, sozinha, nunca pode alterar infraestrutura.
