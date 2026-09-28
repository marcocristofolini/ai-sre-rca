from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.repository import MemoryRepository
from app.remediation.executor import (
    KubernetesScaleExecutor,
    RemediationClusterError,
    RemediationDriftError,
    ScaleState,
)
from app.remediation.service import (
    GuardedRemediationService,
    RemediationApprovalError,
    RemediationPlanStateError,
)
from app.schemas import (
    Entity,
    Hypothesis,
    HypothesisStatus,
    IncidentInput,
    InvestigationResult,
    Remediation,
    RemediationPlanStatus,
    RootCause,
    ScaleDeploymentPlanRequest,
)


class FakeScaleExecutor:
    def __init__(self, replicas: int = 3, resource_version: str = "rv-1"):
        self.replicas = replicas
        self.resource_version = resource_version
        self.inspect_calls = 0
        self.dry_run_calls = 0
        self.execute_calls = 0
        self.rollback_calls = 0

    async def inspect_scale(self, cluster, namespace, deployment):
        self.inspect_calls += 1
        return ScaleState(self.replicas, self.resource_version)

    async def dry_run_scale(
        self,
        cluster,
        namespace,
        deployment,
        desired_replicas,
        current,
    ):
        self.dry_run_calls += 1
        return {
            "current_replicas": current.replicas,
            "desired_replicas": desired_replicas,
            "resource_version": current.resource_version,
            "dry_run": "All",
            "returned_replicas": desired_replicas,
        }

    async def execute_scale(
        self,
        cluster,
        namespace,
        deployment,
        desired_replicas,
        *,
        expected_replicas,
        expected_resource_version,
    ):
        self.execute_calls += 1
        if (
            self.replicas != expected_replicas
            or self.resource_version != expected_resource_version
        ):
            raise RemediationDriftError("scale target changed after dry-run")
        previous = self.replicas
        self.replicas = desired_replicas
        self.resource_version = "rv-executed"
        return {
            "previous_replicas": previous,
            "desired_replicas": desired_replicas,
            "previous_resource_version": expected_resource_version,
            "resource_version": self.resource_version,
            "returned_replicas": desired_replicas,
        }

    async def rollback_scale(
        self,
        cluster,
        namespace,
        deployment,
        previous_replicas,
        *,
        expected_replicas,
        expected_resource_version,
    ):
        self.rollback_calls += 1
        if (
            self.replicas != expected_replicas
            or self.resource_version != expected_resource_version
        ):
            raise RemediationDriftError(
                "scale target changed after execution; rollback is blocked"
            )
        current = self.replicas
        self.replicas = previous_replicas
        self.resource_version = "rv-rollback"
        return {
            "from_replicas": current,
            "restored_replicas": previous_replicas,
            "resource_version": self.resource_version,
            "returned_replicas": previous_replicas,
        }


def settings(**overrides):
    values = {
        "auth_enabled": True,
        "remediation_enabled": True,
        "remediation_allowed_actions": "scale_deployment",
        "remediation_allowed_namespaces": "payments",
        "remediation_scale_hypothesis_allowlist": "capacity-saturation",
        "remediation_cluster_config_json": '{"prod-a":{}}',
        "remediation_min_replicas": 1,
        "remediation_max_replicas": 10,
        "remediation_max_replica_delta": 3,
        "remediation_plan_ttl_minutes": 30,
        "remediation_require_distinct_approver": True,
    }
    values.update(overrides)
    return Settings(**values)


def resolved_incident() -> InvestigationResult:
    return InvestigationResult(
        incident_id="inc-v07-test",
        status="resolved",
        incident=IncidentInput(
            title="Checkout saturation",
            tenant_id="tenant-a",
            cluster="prod-a",
            namespace="payments",
            service="checkout",
            alert_name="HighErrorRate",
        ),
        entities=[
            Entity(
                id="deployment",
                kind="Deployment",
                name="checkout",
                namespace="payments",
            )
        ],
        evidence=[],
        hypotheses=[
            Hypothesis(
                id="capacity-saturation",
                statement="Checkout deployment capacity is saturated.",
                status=HypothesisStatus.CONFIRMED,
                origin="playbook",
                score=0.95,
                rationale="Deterministic capacity evidence confirmed.",
            )
        ],
        timeline=[],
        root_cause=RootCause(
            title="Checkout capacity saturation",
            summary="Current evidence confirms capacity saturation.",
            hypothesis_id="capacity-saturation",
            evidence_strength="strong",
            supporting_signals=3,
            contradicting_signals=0,
        ),
        remediation=[
            Remediation(
                priority="P1",
                action="Scale checkout after approval.",
                reason="Restore capacity while preserving evidence.",
            )
        ],
    )


def request(namespace: str = "payments", replicas: int = 5):
    return ScaleDeploymentPlanRequest(
        incident_id="inc-v07-test",
        namespace=namespace,
        deployment="checkout",
        desired_replicas=replicas,
        rationale="Temporarily restore service capacity.",
    )


@pytest.mark.asyncio
async def test_guarded_scale_requires_distinct_approval_and_supports_rollback():
    repo = MemoryRepository()
    incident = resolved_incident()
    await repo.save(incident)
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(
        request(),
        incident,
        actor="investigator-a",
    )

    assert plan.status == RemediationPlanStatus.AWAITING_APPROVAL
    assert plan.dry_run["dry_run"] == "All"
    assert plan.rollback["previous_replicas"] == 3
    assert executor.execute_calls == 0

    with pytest.raises(RemediationApprovalError):
        await service.approve(plan, actor="investigator-a", note=None)

    plan = await service.approve(
        plan,
        actor="remediator-b",
        note="Reviewed evidence and dry-run.",
    )
    assert plan.status == RemediationPlanStatus.APPROVED

    plan = await service.execute(plan, incident, actor="remediator-b")
    assert plan.status == RemediationPlanStatus.EXECUTED
    assert executor.replicas == 5

    plan = await service.rollback(
        plan,
        actor="remediator-c",
        note="Rollback requested after validation.",
    )
    assert plan.status == RemediationPlanStatus.ROLLED_BACK
    assert executor.replicas == 3

    audit = await repo.list_audit(incident.incident_id, "tenant-a")
    actions = {item.action for item in audit}
    assert "remediation.plan_created" in actions
    assert "remediation.approved" in actions
    assert "remediation.executed" in actions
    assert "remediation.rolled_back" in actions


@pytest.mark.asyncio
async def test_policy_denial_never_touches_kubernetes():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(
        request(namespace="other"),
        incident,
        actor="investigator-a",
    )

    assert plan.status == RemediationPlanStatus.POLICY_DENIED
    assert executor.inspect_calls == 0
    assert executor.dry_run_calls == 0
    assert executor.execute_calls == 0


@pytest.mark.asyncio
async def test_execute_is_impossible_before_approval():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")

    with pytest.raises(RemediationPlanStateError):
        await service.execute(plan, incident, actor="remediator-b")

    assert executor.execute_calls == 0


@pytest.mark.asyncio
async def test_drift_after_approval_blocks_execution():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")
    plan = await service.approve(plan, actor="remediator-b", note=None)

    executor.replicas = 4
    executor.resource_version = "rv-drift"

    plan = await service.execute(plan, incident, actor="remediator-b")

    assert plan.status == RemediationPlanStatus.EXECUTION_FAILED
    assert "changed after dry-run" in (plan.failure or "")
    assert executor.replicas == 4


@pytest.mark.asyncio
async def test_replica_delta_policy_blocks_large_change_before_dry_run():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor(replicas=2)
    service = GuardedRemediationService(
        repo,
        executor,
        settings(remediation_max_replica_delta=2),
    )

    plan = await service.create_plan(
        request(replicas=8),
        incident,
        actor="investigator-a",
    )

    assert plan.status == RemediationPlanStatus.POLICY_DENIED
    assert executor.inspect_calls == 1
    assert executor.dry_run_calls == 0


@pytest.mark.asyncio
async def test_remediation_plan_repository_is_tenant_scoped():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")

    assert await repo.get_remediation_plan(plan.id, tenant_id="tenant-b") is None
    assert await repo.get_remediation_plan(plan.id, tenant_id="tenant-a") is not None



@pytest.mark.asyncio
async def test_atomic_execution_claim_blocks_stale_second_execution():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")
    plan = await service.approve(plan, actor="remediator-b", note=None)
    stale = plan.model_copy(deep=True)

    executed = await service.execute(plan, incident, actor="remediator-b")
    assert executed.status == RemediationPlanStatus.EXECUTED
    assert executor.execute_calls == 1

    with pytest.raises(RemediationPlanStateError, match="changed concurrently"):
        await service.execute(stale, incident, actor="remediator-c")

    assert executor.execute_calls == 1


@pytest.mark.asyncio
async def test_atomic_approval_blocks_stale_rejection():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")
    stale = plan.model_copy(deep=True)

    approved = await service.approve(plan, actor="remediator-b", note=None)
    assert approved.status == RemediationPlanStatus.APPROVED

    with pytest.raises(RemediationPlanStateError, match="changed concurrently"):
        await service.reject(
            stale,
            actor="remediator-c",
            note="stale decision",
        )



def test_investigator_api_does_not_expose_remediation_routes():
    from app.main import app as investigator_app

    paths = set(investigator_app.openapi()["paths"])
    assert not any(path.startswith("/api/v1/remediation") for path in paths)


def test_remediator_is_a_separate_fastapi_application():
    from app.remediation.main import app as remediator_app

    paths = set(remediator_app.openapi()["paths"])
    assert "/api/v1/remediation/plans" in paths
    assert "/api/v1/incidents/investigate" not in paths
    assert remediator_app.version == "0.7.0"



def test_remediation_cluster_registry_fails_closed():
    with pytest.raises(ValidationError, match="remediation_cluster_config_json"):
        Settings(remediation_cluster_config_json="{")

    with pytest.raises(ValidationError, match="remediation_cluster_config_json"):
        Settings(remediation_cluster_config_json='{"prod-a":"token"}')


def test_remediation_executor_rejects_insecure_kubernetes_api(tmp_path):
    token = tmp_path / "token"
    ca = tmp_path / "ca.crt"
    token.write_text("secret")
    ca.write_text("ca")

    executor = KubernetesScaleExecutor(Settings(
        remediation_cluster_config_json=(
            '{"prod-a":{'
            f'"kubernetes_api_url":"http://k8s.example",'
            f'"kubernetes_token_file":"{token}",'
            f'"kubernetes_ca_file":"{ca}"'
            '}}'
        )
    ))

    with pytest.raises(RemediationClusterError, match="must use HTTPS"):
        executor._api_url("prod-a")


@pytest.mark.asyncio
async def test_scale_executor_uses_only_scale_subresource_and_server_dry_run(
    tmp_path,
    monkeypatch,
):
    token = tmp_path / "token"
    ca = tmp_path / "ca.crt"
    token.write_text("secret-token")
    ca.write_text("fake-ca")

    calls = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path):
            calls.append(("get", path))
            return FakeResponse({
                "metadata": {"resourceVersion": "rv-1"},
                "spec": {"replicas": 3},
            })

        async def patch(self, path, *, params, json):
            calls.append(("patch", path, params, json))
            return FakeResponse({
                "metadata": {"resourceVersion": "rv-2"},
                "spec": {"replicas": json["spec"]["replicas"]},
            })

    monkeypatch.setattr(
        "app.remediation.executor.httpx.AsyncClient",
        FakeClient,
    )

    executor = KubernetesScaleExecutor(Settings(
        remediation_cluster_config_json=(
            '{"prod-a":{'
            f'"kubernetes_api_url":"https://k8s.example",'
            f'"kubernetes_token_file":"{token}",'
            f'"kubernetes_ca_file":"{ca}"'
            '}}'
        )
    ))

    current = await executor.inspect_scale("prod-a", "payments", "checkout")
    result = await executor.dry_run_scale(
        "prod-a",
        "payments",
        "checkout",
        5,
        current,
    )

    get_call = next(item for item in calls if item[0] == "get")
    patch_call = next(item for item in calls if item[0] == "patch")

    assert get_call[1].endswith(
        "/namespaces/payments/deployments/checkout/scale"
    )
    assert patch_call[1] == get_call[1]
    assert patch_call[2]["dryRun"] == "All"
    assert patch_call[3]["metadata"]["resourceVersion"] == "rv-1"
    assert patch_call[3]["spec"]["replicas"] == 5
    assert result["dry_run"] == "All"

    execution = await executor.execute_scale(
        "prod-a",
        "payments",
        "checkout",
        5,
        expected_replicas=3,
        expected_resource_version="rv-1",
    )
    patch_calls = [item for item in calls if item[0] == "patch"]
    assert len(patch_calls) == 3
    assert patch_calls[-2][2]["dryRun"] == "All"
    assert "dryRun" not in patch_calls[-1][2]
    assert execution["pre_execute_dry_run_replicas"] == 5
    assert execution["returned_replicas"] == 5



@pytest.mark.asyncio
async def test_scale_policy_requires_explicit_causal_hypothesis_allowlist():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(
        repo,
        executor,
        settings(remediation_scale_hypothesis_allowlist="other-hypothesis"),
    )

    plan = await service.create_plan(request(), incident, actor="investigator-a")

    assert plan.status == RemediationPlanStatus.POLICY_DENIED
    assert executor.inspect_calls == 0
    causal_check = next(
        item
        for item in plan.policy_checks
        if item["check"] == "causal_hypothesis_allowlist"
    )
    assert causal_check["allowed"] is False


@pytest.mark.asyncio
async def test_rollback_is_blocked_if_target_drifted_after_execution():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")
    plan = await service.approve(plan, actor="remediator-b", note=None)
    plan = await service.execute(plan, incident, actor="remediator-b")
    assert plan.status == RemediationPlanStatus.EXECUTED

    executor.replicas = 6
    executor.resource_version = "rv-after-external-change"

    plan = await service.rollback(
        plan,
        actor="remediator-c",
        note="Attempt rollback after external scale.",
    )

    assert plan.status == RemediationPlanStatus.ROLLBACK_FAILED
    assert "rollback is blocked" in (plan.failure or "")
    assert executor.replicas == 6



@pytest.mark.asyncio
async def test_scale_policy_rejects_unverified_or_llm_root_hypothesis():
    repo = MemoryRepository()
    incident = resolved_incident()
    incident.hypotheses[0].origin = "llm"
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(repo, executor, settings())

    plan = await service.create_plan(request(), incident, actor="investigator-a")

    assert plan.status == RemediationPlanStatus.POLICY_DENIED
    assert executor.inspect_calls == 0
    verified = next(
        item
        for item in plan.policy_checks
        if item["check"] == "verified_deterministic_root_hypothesis"
    )
    assert verified["allowed"] is False



@pytest.mark.asyncio
async def test_remediation_policy_denies_when_authentication_is_disabled():
    repo = MemoryRepository()
    incident = resolved_incident()
    executor = FakeScaleExecutor()
    service = GuardedRemediationService(
        repo,
        executor,
        settings(auth_enabled=False),
    )

    plan = await service.create_plan(request(), incident, actor="investigator-a")

    assert plan.status == RemediationPlanStatus.POLICY_DENIED
    assert executor.inspect_calls == 0
    auth_check = next(
        item
        for item in plan.policy_checks
        if item["check"] == "authentication_enabled"
    )
    assert auth_check["allowed"] is False


def test_remediator_runtime_requires_auth_and_shared_database():
    from app.remediation.main import validate_remediator_runtime

    with pytest.raises(RuntimeError, match="AUTH_ENABLED=true"):
        validate_remediator_runtime(settings(auth_enabled=False))

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        validate_remediator_runtime(settings(database_url=None))

    validate_remediator_runtime(
        settings(database_url="postgresql+asyncpg://user:pass@db/remediation")
    )
