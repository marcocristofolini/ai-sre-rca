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
- incidente resolvido;
- evidência da causa raiz medium ou strong;
- ação presente na allow-list;
- namespace presente na allow-list e igual ao namespace do incidente;
- Deployment alvo presente no Awareness Graph;
- quantidade de réplicas dentro dos limites configurados;
- cluster presente no registry separado de remediação.

Depois o serviço lê as réplicas atuais. A mudança não pode ser no-op e o delta deve respeitar REMEDIATION_MAX_REPLICA_DELTA. Só então é enviado o dry-run server-side ao Kubernetes.

## Aprovação e regra de quatro olhos

Planos aprovados pela política e pelo dry-run entram em **awaiting_approval**. A aprovação exige exatamente o papel OIDC remediator configurado. Por padrão REMEDIATION_REQUIRE_DISTINCT_APPROVER=true, então quem solicitou o plano não pode aprová-lo.

Aprovar ainda não executa nada. A execução exige outra chamada explícita da API por um remediator.

## Proteção contra drift

O preflight guarda a quantidade atual de réplicas e o resourceVersion do Kubernetes. Na execução, o Scale subresource é lido novamente. Se qualquer um mudou depois do dry-run e da aprovação, a execução é bloqueada e um novo plano precisa ser criado.

O PATCH real também envia o resourceVersion armazenado para o Kubernetes poder rejeitar mudanças concorrentes.

## Rollback

Todo plano aceito guarda a quantidade anterior de réplicas antes da aprovação. O rollback é outra ação explícita do remediator e restaura somente esse valor usando o mesmo subresource deployments/scale.

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

O remediator do Helm vem desabilitado por padrão. targetNamespaces também começa vazio, então nenhum Role de escrita é criado até os namespaces serem declarados explicitamente.

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
REMEDIATION_ALLOWED_ACTIONS=scale_deployment
REMEDIATION_ALLOWED_NAMESPACES=payments,checkout
REMEDIATION_MIN_REPLICAS=1
REMEDIATION_MAX_REPLICAS=20
REMEDIATION_MAX_REPLICA_DELTA=3
REMEDIATION_CLUSTER_CONFIG_JSON={"prod-us":{}}
REMEDIATOR_ROLE=remediator
~~~

DATABASE_URL deve apontar para o mesmo banco operacional usado pelo investigator. O compartilhamento é do estado do incidente e da auditoria, não da credencial Kubernetes de mutação.

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
    approved --> executed
    approved --> execution_failed
    executed --> rolled_back
    executed --> rollback_failed
~~~

A v0.7 não adiciona execução autônoma. Uma recomendação do RCA, sozinha, nunca pode alterar infraestrutura.
