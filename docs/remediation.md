# Guarded remediation plane — v0.7

[Português do Brasil](remediation.pt-BR.md) · **English**

v0.7 introduces a remediation control plane without turning the RCA investigator into a write-capable service. The investigator keeps its read-only ServiceAccount. Remediation runs as a separate FastAPI process, Deployment and Kubernetes ServiceAccount.

## Boundary

~~~mermaid
flowchart LR
    INV[Investigator GET LIST WATCH] --> RCA[Evidence-backed RCA]
    RCA --> PLAN[Proposed remediation plan]
    PLAN --> POL[Static policy validation]
    POL --> INSPECT[Read current Scale state]
    INSPECT --> DYN[Runtime policy validation]
    DYN --> DRY[Kubernetes dryRun=All]
    DRY --> WAIT[Await human approval]
    WAIT --> APPROVE[Remediator role approval]
    APPROVE --> DRIFT{Target unchanged?}
    DRIFT -->|No| BLOCK[Block execution]
    DRIFT -->|Yes| EXEC[PATCH deployments/scale]
    EXEC --> AUDIT[Audit + rollback metadata]
    AUDIT --> RB[Explicit rollback endpoint]
    INV -. no write credentials .-> EXEC
~~~

The two services may share PostgreSQL so the remediator can load the structured incident and append audit events. They do not share the Kubernetes identity used for mutation.

## Current action allow-list

v0.7 intentionally ships one mutating action: **scale_deployment**.

The executor can only call the Deployment scale subresource with GET and PATCH. There is no generic Kubernetes patch method, shell, kubectl, pods/exec, object delete/create, Argo sync, cloud mutation or arbitrary URL primitive in the remediator.

## Policy gates

Before a dry-run is attempted, all of these checks must pass:

- remediation is explicitly enabled;
- the incident is resolved;
- root-cause evidence is medium or strong and has supporting signals;
- the selected root hypothesis exists in the incident as a confirmed deterministic playbook hypothesis;
- the exact root hypothesis ID is present in REMEDIATION_SCALE_HYPOTHESIS_ALLOWLIST;
- the action is allow-listed;
- the namespace is allow-listed and matches the incident;
- the target Deployment is present in the incident Awareness Graph;
- requested replicas remain inside configured bounds;
- the cluster is present in the separate remediation cluster registry.

The service then reads the current replica count. The change cannot be a no-op and the replica delta must remain within REMEDIATION_MAX_REPLICA_DELTA. Only then is Kubernetes server-side dry-run invoked.

## Approval and four-eyes control

Plans that pass policy and dry-run enter **awaiting_approval**. Approval requires the exact configured remediator OIDC role. By default REMEDIATION_REQUIRE_DISTINCT_APPROVER=true, so the requester cannot approve their own plan.

Approval still does not execute anything. Execution is another explicit API call by a remediator. Approval, execution claim and rollback claim use atomic compare-and-save transitions so stale concurrent requests cannot overwrite a newer plan state.

## Drift protection

Preflight stores the current replica count and Kubernetes resourceVersion. Execution re-reads the Scale subresource. If either value changed after dry-run and approval, execution is blocked and a fresh plan is required.

Immediately before the real mutation, the remediator repeats a server-side dry-run against the unchanged target. The real PATCH then carries the stored resourceVersion so Kubernetes can reject a concurrent change that lands between that dry-run and execution.

## Rollback

Every accepted plan stores the previous replica count before approval. Rollback is another explicit remediator action and restores only that replica count through the same deployments/scale subresource. Rollback also re-checks replica count and resourceVersion from the completed execution; if another actor changed the target afterward, rollback is blocked rather than overwriting the newer state.

## API

The separate remediator API exposes:

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

The investigator API does not expose these routes.

Example plan:

~~~json
{
  "incident_id": "inc-123",
  "action_type": "scale_deployment",
  "namespace": "payments",
  "deployment": "checkout",
  "desired_replicas": 5,
  "rationale": "Temporarily restore capacity while the underlying saturation issue is corrected."
}
~~~

## Separate credentials and Helm RBAC

REMEDIATION_CLUSTER_CONFIG_JSON is deliberately separate from CLUSTER_CONFIG_JSON. For an in-cluster remediator, a configured logical cluster can use the remediator Pod's own ServiceAccount token. Remote clusters must use separate mounted credentials.

The Helm remediator is disabled by default. targetNamespaces is also empty by default, so no write Role is created until namespaces are explicitly listed. Helm rejects configurations that reuse the investigator ServiceAccount name for the remediator.

For each target namespace the chart creates only:

~~~yaml
apiGroups: ["apps"]
resources: ["deployments/scale"]
verbs: ["get", "patch"]
~~~

## Operational settings

~~~text
REMEDIATION_ENABLED=true
REMEDIATION_REQUIRE_DISTINCT_APPROVER=true
REMEDIATION_PLAN_TTL_MINUTES=30
REMEDIATION_ALLOWED_ACTIONS=scale_deployment
REMEDIATION_ALLOWED_NAMESPACES=payments,checkout
REMEDIATION_SCALE_HYPOTHESIS_ALLOWLIST=capacity-saturation
REMEDIATION_MIN_REPLICAS=1
REMEDIATION_MAX_REPLICAS=20
REMEDIATION_MAX_REPLICA_DELTA=3
REMEDIATION_CLUSTER_CONFIG_JSON={"prod-us":{}}
REMEDIATOR_ROLE=remediator
~~~

When REMEDIATION_ENABLED=true, the remediator refuses startup unless AUTH_ENABLED=true, DATABASE_URL is persistent/shared, OIDC issuer/audience/JWKS are all configured, and REMEDIATOR_ROLE is distinct from viewer/investigator/admin. DATABASE_URL should point to the same operational database used by the investigator. This shares incident state and audit storage, not Kubernetes mutation credentials.

## State machine

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

v0.7 does not add autonomous execution. An RCA recommendation alone can never mutate infrastructure.
