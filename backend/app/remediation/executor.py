from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.core.query_safety import path_segment


class RemediationClusterError(ValueError):
    pass


class RemediationExecutionError(RuntimeError):
    pass


class RemediationDriftError(RemediationExecutionError):
    pass


@dataclass(frozen=True)
class ScaleState:
    replicas: int
    resource_version: str


class KubernetesScaleExecutor:
    """Single-purpose Kubernetes Deployment scale executor.

    This class exposes no generic HTTP, shell or object-patch primitive. Its only
    mutation is PATCH against the apps/v1 deployments/scale subresource.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _cluster(self, cluster: str) -> dict[str, Any]:
        config = self.settings.remediation_cluster_config.get(cluster)
        if config is None:
            raise RemediationClusterError(
                f"remediation cluster is not configured: {cluster}"
            )
        return config

    def _api_url(self, cluster: str) -> str:
        value = str(
            self._cluster(cluster).get(
                "kubernetes_api_url",
                self.settings.kubernetes_api_url,
            )
        )
        if urlparse(value).scheme != "https":
            raise RemediationClusterError(
                "remediation Kubernetes API must use HTTPS"
            )
        return value

    def _token_file(self, cluster: str) -> Path:
        return Path(
            str(
                self._cluster(cluster).get(
                    "kubernetes_token_file",
                    self.settings.kubernetes_token_file,
                )
            )
        )

    def _ca_file(self, cluster: str) -> Path:
        return Path(
            str(
                self._cluster(cluster).get(
                    "kubernetes_ca_file",
                    self.settings.kubernetes_ca_file,
                )
            )
        )

    def _headers(self, cluster: str) -> dict[str, str]:
        token_file = self._token_file(cluster)
        if not token_file.exists():
            raise RemediationExecutionError(
                f"remediation service-account token is unavailable: {token_file}"
            )
        token = token_file.read_text().strip()
        if not token:
            raise RemediationExecutionError(
                f"remediation service-account token is empty: {token_file}"
            )
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
            "Accept": "application/json",
        }

    def _verify(self, cluster: str) -> str:
        ca_file = self._ca_file(cluster)
        if not ca_file.exists():
            raise RemediationExecutionError(
                f"remediation Kubernetes CA is unavailable: {ca_file}"
            )
        return str(ca_file)

    @staticmethod
    def _path(namespace: str, deployment: str) -> str:
        return (
            f"/apis/apps/v1/namespaces/{path_segment(namespace)}"
            f"/deployments/{path_segment(deployment)}/scale"
        )

    async def inspect_scale(
        self,
        cluster: str,
        namespace: str,
        deployment: str,
    ) -> ScaleState:
        try:
            async with httpx.AsyncClient(
                base_url=self._api_url(cluster),
                headers=self._headers(cluster),
                verify=self._verify(cluster),
                timeout=10,
            ) as client:
                response = await client.get(self._path(namespace, deployment))
                if response.status_code == 404:
                    raise RemediationExecutionError(
                        f"deployment scale target not found: {namespace}/{deployment}"
                    )
                response.raise_for_status()
                payload = response.json()
        except RemediationExecutionError:
            raise
        except httpx.HTTPError as exc:
            raise RemediationExecutionError(
                f"Kubernetes scale inspection failed: {exc.__class__.__name__}"
            ) from exc
        try:
            replicas = int(payload["spec"]["replicas"])
            resource_version = str(payload["metadata"]["resourceVersion"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RemediationExecutionError(
                "Kubernetes Scale response is missing replicas/resourceVersion"
            ) from exc
        return ScaleState(replicas=replicas, resource_version=resource_version)

    async def _patch_scale(
        self,
        cluster: str,
        namespace: str,
        deployment: str,
        desired_replicas: int,
        resource_version: str,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        params: dict[str, str] = {
            "fieldManager": "ai-sre-rca-remediator",
        }
        if dry_run:
            params["dryRun"] = "All"
        payload = {
            "metadata": {"resourceVersion": resource_version},
            "spec": {"replicas": desired_replicas},
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._api_url(cluster),
                headers=self._headers(cluster),
                verify=self._verify(cluster),
                timeout=10,
            ) as client:
                response = await client.patch(
                    self._path(namespace, deployment),
                    params=params,
                    json=payload,
                )
                if response.status_code == 409:
                    raise RemediationDriftError(
                        "Kubernetes rejected the scale because resourceVersion changed"
                    )
                response.raise_for_status()
                return response.json()
        except RemediationDriftError:
            raise
        except httpx.HTTPError as exc:
            raise RemediationExecutionError(
                f"Kubernetes scale mutation failed: {exc.__class__.__name__}"
            ) from exc

    async def dry_run_scale(
        self,
        cluster: str,
        namespace: str,
        deployment: str,
        desired_replicas: int,
        current: ScaleState,
    ) -> dict[str, Any]:
        payload = await self._patch_scale(
            cluster,
            namespace,
            deployment,
            desired_replicas,
            current.resource_version,
            dry_run=True,
        )
        return {
            "current_replicas": current.replicas,
            "desired_replicas": desired_replicas,
            "resource_version": current.resource_version,
            "dry_run": "All",
            "returned_replicas": payload.get("spec", {}).get("replicas"),
        }

    async def execute_scale(
        self,
        cluster: str,
        namespace: str,
        deployment: str,
        desired_replicas: int,
        *,
        expected_replicas: int,
        expected_resource_version: str,
    ) -> dict[str, Any]:
        current = await self.inspect_scale(cluster, namespace, deployment)
        if (
            current.replicas != expected_replicas
            or current.resource_version != expected_resource_version
        ):
            raise RemediationDriftError(
                "scale target changed after dry-run; create a new remediation plan"
            )
        pre_execute_dry_run = await self._patch_scale(
            cluster,
            namespace,
            deployment,
            desired_replicas,
            current.resource_version,
            dry_run=True,
        )
        payload = await self._patch_scale(
            cluster,
            namespace,
            deployment,
            desired_replicas,
            current.resource_version,
            dry_run=False,
        )
        return {
            "pre_execute_dry_run_replicas": pre_execute_dry_run.get("spec", {}).get("replicas"),
            "previous_replicas": current.replicas,
            "desired_replicas": desired_replicas,
            "previous_resource_version": current.resource_version,
            "resource_version": payload.get("metadata", {}).get("resourceVersion"),
            "returned_replicas": payload.get("spec", {}).get("replicas"),
        }

    async def rollback_scale(
        self,
        cluster: str,
        namespace: str,
        deployment: str,
        previous_replicas: int,
        *,
        expected_replicas: int,
        expected_resource_version: str,
    ) -> dict[str, Any]:
        current = await self.inspect_scale(cluster, namespace, deployment)
        if (
            current.replicas != expected_replicas
            or current.resource_version != expected_resource_version
        ):
            raise RemediationDriftError(
                "scale target changed after execution; rollback is blocked"
            )
        pre_rollback_dry_run = await self._patch_scale(
            cluster,
            namespace,
            deployment,
            previous_replicas,
            current.resource_version,
            dry_run=True,
        )
        payload = await self._patch_scale(
            cluster,
            namespace,
            deployment,
            previous_replicas,
            current.resource_version,
            dry_run=False,
        )
        return {
            "pre_rollback_dry_run_replicas": pre_rollback_dry_run.get("spec", {}).get("replicas"),
            "from_replicas": current.replicas,
            "restored_replicas": previous_replicas,
            "previous_resource_version": current.resource_version,
            "resource_version": payload.get("metadata", {}).get("resourceVersion"),
            "returned_replicas": payload.get("spec", {}).get("replicas"),
        }
