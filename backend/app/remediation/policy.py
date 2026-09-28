from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.schemas import InvestigationResult, ScaleDeploymentPlanRequest


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    checks: list[dict[str, Any]]


class RemediationPolicy:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _check(name: str, allowed: bool, detail: str) -> dict[str, Any]:
        return {"check": name, "allowed": allowed, "detail": detail}

    def evaluate_static(
        self,
        incident: InvestigationResult,
        request: ScaleDeploymentPlanRequest,
    ) -> PolicyDecision:
        deployment_matches = any(
            entity.kind.lower() == "deployment"
            and entity.name == request.deployment
            and (entity.namespace or incident.incident.namespace) == request.namespace
            for entity in incident.entities
        )
        checks = [
            self._check(
                "remediation_enabled",
                self.settings.remediation_enabled,
                "guarded remediation plane is enabled",
            ),
            self._check(
                "incident_resolved",
                incident.status == "resolved",
                f"incident status is {incident.status}",
            ),
            self._check(
                "evidence_strength",
                incident.root_cause.evidence_strength in {"medium", "strong"},
                f"root-cause evidence is {incident.root_cause.evidence_strength}",
            ),
            self._check(
                "action_allowlist",
                request.action_type in self.settings.remediation_allowed_action_set,
                f"action={request.action_type}",
            ),
            self._check(
                "namespace_allowlist",
                request.namespace in self.settings.remediation_allowed_namespace_set,
                f"namespace={request.namespace}",
            ),
            self._check(
                "incident_namespace",
                incident.incident.namespace == request.namespace,
                f"incident namespace={incident.incident.namespace!r}",
            ),
            self._check(
                "investigated_deployment",
                deployment_matches,
                f"deployment={request.deployment}",
            ),
            self._check(
                "replica_bounds",
                self.settings.remediation_min_replicas
                <= request.desired_replicas
                <= self.settings.remediation_max_replicas,
                (
                    f"desired={request.desired_replicas}; "
                    f"allowed={self.settings.remediation_min_replicas}.."
                    f"{self.settings.remediation_max_replicas}"
                ),
            ),
            self._check(
                "remediation_cluster_registry",
                incident.incident.cluster in self.settings.remediation_cluster_config,
                f"cluster={incident.incident.cluster}",
            ),
        ]
        return PolicyDecision(
            allowed=all(item["allowed"] for item in checks),
            checks=checks,
        )

    def evaluate_runtime(
        self,
        request: ScaleDeploymentPlanRequest,
        current_replicas: int,
    ) -> PolicyDecision:
        delta = abs(request.desired_replicas - current_replicas)
        checks = [
            self._check(
                "not_noop",
                request.desired_replicas != current_replicas,
                f"current={current_replicas}; desired={request.desired_replicas}",
            ),
            self._check(
                "replica_delta",
                delta <= self.settings.remediation_max_replica_delta,
                (
                    f"delta={delta}; "
                    f"max={self.settings.remediation_max_replica_delta}"
                ),
            ),
        ]
        return PolicyDecision(
            allowed=all(item["allowed"] for item in checks),
            checks=checks,
        )
