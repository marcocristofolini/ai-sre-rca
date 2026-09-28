from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.db.repository import InvestigationRepository
from app.remediation.executor import (
    KubernetesScaleExecutor,
    RemediationDriftError,
    RemediationExecutionError,
)
from app.remediation.policy import RemediationPolicy
from app.schemas import (
    AuditEvent,
    InvestigationResult,
    RemediationPlan,
    RemediationPlanStatus,
    ScaleDeploymentPlanRequest,
)


class RemediationPlanNotFound(LookupError):
    pass


class RemediationPlanStateError(RuntimeError):
    pass


class RemediationApprovalError(PermissionError):
    pass


class GuardedRemediationService:
    def __init__(
        self,
        repo: InvestigationRepository,
        executor: KubernetesScaleExecutor,
        settings: Settings,
    ):
        self.repo = repo
        self.executor = executor
        self.settings = settings
        self.policy = RemediationPolicy(settings)

    async def _audit(
        self,
        plan: RemediationPlan,
        *,
        actor: str,
        action: str,
        detail: dict | None = None,
    ) -> None:
        await self.repo.append_audit(
            AuditEvent(
                incident_id=plan.incident_id,
                tenant_id=plan.tenant_id,
                actor=actor,
                action=action,
                detail={
                    "remediation_plan_id": plan.id,
                    "cluster": plan.cluster,
                    "namespace": plan.namespace,
                    "deployment": plan.deployment,
                    **(detail or {}),
                },
            )
        )

    async def _save(self, plan: RemediationPlan) -> RemediationPlan:
        plan.updated_at = datetime.now(timezone.utc)
        await self.repo.save_remediation_plan(plan)
        return plan

    async def _save_if_status(
        self,
        plan: RemediationPlan,
        *,
        expected_status: RemediationPlanStatus,
    ) -> RemediationPlan:
        candidate = plan.model_copy(deep=True)
        candidate.updated_at = datetime.now(timezone.utc)
        saved = await self.repo.save_remediation_plan_if_status(
            candidate,
            expected_status.value,
        )
        if saved:
            return candidate
        current = await self.repo.get_remediation_plan(
            plan.id,
            tenant_id=plan.tenant_id,
        )
        current_status = current.status.value if current else "missing"
        raise RemediationPlanStateError(
            f"plan state changed concurrently; current state is {current_status}"
        )

    async def create_plan(
        self,
        request: ScaleDeploymentPlanRequest,
        incident: InvestigationResult,
        *,
        actor: str,
    ) -> RemediationPlan:
        now = datetime.now(timezone.utc)
        plan = RemediationPlan(
            incident_id=incident.incident_id,
            tenant_id=incident.incident.tenant_id,
            cluster=incident.incident.cluster,
            action_type=request.action_type,
            namespace=request.namespace,
            deployment=request.deployment,
            desired_replicas=request.desired_replicas,
            rationale=request.rationale,
            requested_by=actor,
            status=RemediationPlanStatus.POLICY_DENIED,
            expires_at=now + timedelta(
                minutes=max(1, self.settings.remediation_plan_ttl_minutes)
            ),
        )

        static = self.policy.evaluate_static(incident, request)
        plan.policy_checks.extend(static.checks)
        if not static.allowed:
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.policy_denied",
                detail={"checks": static.checks},
            )
            return plan

        try:
            current = await self.executor.inspect_scale(
                plan.cluster,
                plan.namespace,
                plan.deployment,
            )
        except Exception as exc:
            plan.status = RemediationPlanStatus.DRY_RUN_FAILED
            plan.failure = f"preflight inspection failed: {exc.__class__.__name__}"
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.preflight_failed",
                detail={"error": plan.failure},
            )
            return plan

        runtime = self.policy.evaluate_runtime(request, current.replicas)
        plan.policy_checks.extend(runtime.checks)
        if not runtime.allowed:
            plan.status = RemediationPlanStatus.POLICY_DENIED
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.policy_denied",
                detail={"checks": runtime.checks},
            )
            return plan

        plan.rollback = {
            "action_type": "scale_deployment",
            "previous_replicas": current.replicas,
            "resource_version": current.resource_version,
        }
        try:
            plan.dry_run = await self.executor.dry_run_scale(
                plan.cluster,
                plan.namespace,
                plan.deployment,
                plan.desired_replicas,
                current,
            )
        except Exception as exc:
            plan.status = RemediationPlanStatus.DRY_RUN_FAILED
            plan.failure = f"dry-run failed: {exc.__class__.__name__}"
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.dry_run_failed",
                detail={"error": plan.failure},
            )
            return plan

        plan.status = RemediationPlanStatus.AWAITING_APPROVAL
        await self._save(plan)
        await self._audit(
            plan,
            actor=actor,
            action="remediation.plan_created",
            detail={
                "desired_replicas": plan.desired_replicas,
                "dry_run": plan.dry_run,
                "rollback": plan.rollback,
            },
        )
        return plan

    async def get_plan(
        self,
        plan_id: str,
        *,
        tenant_id: str | None,
    ) -> RemediationPlan:
        plan = await self.repo.get_remediation_plan(plan_id, tenant_id=tenant_id)
        if plan is None:
            raise RemediationPlanNotFound(plan_id)
        return await self._expire_if_needed(plan)

    async def list_plans(
        self,
        *,
        tenant_id: str,
        incident_id: str | None = None,
        limit: int = 50,
    ) -> list[RemediationPlan]:
        plans = await self.repo.list_remediation_plans(
            tenant_id,
            incident_id=incident_id,
            limit=limit,
        )
        return [await self._expire_if_needed(plan) for plan in plans]

    async def _expire_if_needed(self, plan: RemediationPlan) -> RemediationPlan:
        if (
            plan.status
            in {
                RemediationPlanStatus.AWAITING_APPROVAL,
                RemediationPlanStatus.APPROVED,
            }
            and datetime.now(timezone.utc) > plan.expires_at
        ):
            expected = plan.status
            candidate = plan.model_copy(deep=True)
            candidate.status = RemediationPlanStatus.EXPIRED
            try:
                plan = await self._save_if_status(
                    candidate,
                    expected_status=expected,
                )
            except RemediationPlanStateError:
                current = await self.repo.get_remediation_plan(
                    plan.id,
                    tenant_id=plan.tenant_id,
                )
                if current is None:
                    raise
                return current
            await self._audit(
                plan,
                actor="remediation-service",
                action="remediation.plan_expired",
            )
        return plan

    async def approve(
        self,
        plan: RemediationPlan,
        *,
        actor: str,
        note: str | None,
    ) -> RemediationPlan:
        plan = await self._expire_if_needed(plan)
        if plan.status != RemediationPlanStatus.AWAITING_APPROVAL:
            raise RemediationPlanStateError(
                f"plan cannot be approved from state {plan.status.value}"
            )
        if (
            self.settings.remediation_require_distinct_approver
            and plan.requested_by == actor
        ):
            raise RemediationApprovalError(
                "requester cannot approve their own remediation plan"
            )
        candidate = plan.model_copy(deep=True)
        candidate.status = RemediationPlanStatus.APPROVED
        candidate.approved_by = actor
        candidate.approved_at = datetime.now(timezone.utc)
        candidate.approval_note = note
        plan = await self._save_if_status(
            candidate,
            expected_status=RemediationPlanStatus.AWAITING_APPROVAL,
        )
        await self._audit(
            plan,
            actor=actor,
            action="remediation.approved",
            detail={"note": note},
        )
        return plan

    async def reject(
        self,
        plan: RemediationPlan,
        *,
        actor: str,
        note: str | None,
    ) -> RemediationPlan:
        plan = await self._expire_if_needed(plan)
        if plan.status not in {
            RemediationPlanStatus.AWAITING_APPROVAL,
            RemediationPlanStatus.APPROVED,
        }:
            raise RemediationPlanStateError(
                f"plan cannot be rejected from state {plan.status.value}"
            )
        expected = plan.status
        candidate = plan.model_copy(deep=True)
        candidate.status = RemediationPlanStatus.REJECTED
        candidate.rejected_by = actor
        candidate.rejected_at = datetime.now(timezone.utc)
        candidate.approval_note = note
        plan = await self._save_if_status(
            candidate,
            expected_status=expected,
        )
        await self._audit(
            plan,
            actor=actor,
            action="remediation.rejected",
            detail={"note": note},
        )
        return plan

    async def execute(
        self,
        plan: RemediationPlan,
        incident: InvestigationResult,
        *,
        actor: str,
    ) -> RemediationPlan:
        plan = await self._expire_if_needed(plan)
        if plan.status != RemediationPlanStatus.APPROVED:
            raise RemediationPlanStateError(
                f"plan cannot execute from state {plan.status.value}"
            )

        claimed = plan.model_copy(deep=True)
        claimed.status = RemediationPlanStatus.EXECUTING
        plan = await self._save_if_status(
            claimed,
            expected_status=RemediationPlanStatus.APPROVED,
        )

        request = ScaleDeploymentPlanRequest(
            incident_id=plan.incident_id,
            namespace=plan.namespace,
            deployment=plan.deployment,
            desired_replicas=plan.desired_replicas,
            rationale=plan.rationale,
        )
        static = self.policy.evaluate_static(incident, request)
        runtime = self.policy.evaluate_runtime(
            request,
            int(plan.rollback["previous_replicas"]),
        )
        if not static.allowed or not runtime.allowed:
            plan.status = RemediationPlanStatus.EXECUTION_FAILED
            plan.failure = "policy changed after approval"
            plan.policy_checks.extend(static.checks)
            plan.policy_checks.extend(runtime.checks)
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.execution_blocked",
                detail={"checks": [*static.checks, *runtime.checks]},
            )
            return plan

        try:
            plan.execution = await self.executor.execute_scale(
                plan.cluster,
                plan.namespace,
                plan.deployment,
                plan.desired_replicas,
                expected_replicas=int(plan.rollback["previous_replicas"]),
                expected_resource_version=str(plan.rollback["resource_version"]),
            )
        except RemediationDriftError as exc:
            plan.status = RemediationPlanStatus.EXECUTION_FAILED
            plan.failure = str(exc)
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.execution_blocked",
                detail={"reason": "drift", "error": plan.failure},
            )
            return plan
        except RemediationExecutionError as exc:
            plan.status = RemediationPlanStatus.EXECUTION_FAILED
            plan.failure = str(exc)
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.execution_failed",
                detail={"error": plan.failure},
            )
            return plan

        plan.status = RemediationPlanStatus.EXECUTED
        plan.executed_at = datetime.now(timezone.utc)
        plan.failure = None
        await self._save(plan)
        await self._audit(
            plan,
            actor=actor,
            action="remediation.executed",
            detail={"execution": plan.execution},
        )
        return plan

    async def rollback(
        self,
        plan: RemediationPlan,
        *,
        actor: str,
        note: str | None,
    ) -> RemediationPlan:
        if plan.status != RemediationPlanStatus.EXECUTED:
            raise RemediationPlanStateError(
                f"plan cannot roll back from state {plan.status.value}"
            )
        claimed = plan.model_copy(deep=True)
        claimed.status = RemediationPlanStatus.ROLLING_BACK
        plan = await self._save_if_status(
            claimed,
            expected_status=RemediationPlanStatus.EXECUTED,
        )
        previous_replicas = int(plan.rollback["previous_replicas"])
        try:
            execution_resource_version = plan.execution.get("resource_version")
            if not execution_resource_version:
                raise RemediationExecutionError(
                    "execution resourceVersion is unavailable; rollback is blocked"
                )
            result = await self.executor.rollback_scale(
                plan.cluster,
                plan.namespace,
                plan.deployment,
                previous_replicas,
                expected_replicas=plan.desired_replicas,
                expected_resource_version=str(execution_resource_version),
            )
        except RemediationExecutionError as exc:
            plan.status = RemediationPlanStatus.ROLLBACK_FAILED
            plan.failure = str(exc)
            plan.rollback_execution = {
                "requested_by": actor,
                "note": note,
                "error": str(exc),
            }
            await self._save(plan)
            await self._audit(
                plan,
                actor=actor,
                action="remediation.rollback_failed",
                detail=plan.rollback_execution,
            )
            return plan

        plan.status = RemediationPlanStatus.ROLLED_BACK
        plan.rolled_back_at = datetime.now(timezone.utc)
        plan.failure = None
        plan.rollback_execution = {
            "requested_by": actor,
            "note": note,
            **result,
        }
        await self._save(plan)
        await self._audit(
            plan,
            actor=actor,
            action="remediation.rolled_back",
            detail=plan.rollback_execution,
        )
        return plan
