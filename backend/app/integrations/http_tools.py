from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.config import Settings


class ReadOnlyHTTPTools:
    """Narrow read-only clients used by the investigator.

    This class intentionally exposes only GET-style infrastructure operations.
    There is no generic shell, kubectl exec, patch, update or delete primitive.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def _k8s_headers(self) -> dict[str, str]:
        token_path = Path(self.settings.kubernetes_token_file)
        if not token_path.exists():
            return {}
        return {"Authorization": f"Bearer {token_path.read_text().strip()}"}

    def _k8s_verify(self) -> str | bool:
        ca = Path(self.settings.kubernetes_ca_file)
        return str(ca) if ca.exists() else True

    async def _k8s_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.settings.kubernetes_api_url,
            headers=self._k8s_headers(),
            verify=self._k8s_verify(),
            timeout=10,
        ) as client:
            response = await client.get(path, params=params)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def get_pod(self, namespace: str, pod: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/namespaces/{quote(namespace)}/pods/{quote(pod)}")

    async def get_node(self, node: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/nodes/{quote(node)}")

    async def get_events(self, namespace: str, object_name: str) -> list[dict[str, Any]]:
        data = await self._k8s_get(
            f"/api/v1/namespaces/{quote(namespace)}/events",
            {"fieldSelector": f"involvedObject.name={object_name}"},
        )
        return data.get("items", [])

    async def get_cluster_events(self, object_name: str) -> list[dict[str, Any]]:
        data = await self._k8s_get(
            "/api/v1/events",
            {"fieldSelector": f"involvedObject.name={object_name}"},
        )
        return data.get("items", [])

    async def get_replicaset(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{quote(namespace)}/replicasets/{quote(name)}")

    async def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{quote(namespace)}/deployments/{quote(name)}")

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        data = await self._k8s_get("/api/v1/pods", {"fieldSelector": f"spec.nodeName={node}"})
        return data.get("items", [])

    async def get_hpa_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/autoscaling/v2/namespaces/{quote(namespace)}/horizontalpodautoscalers"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            if target.get("kind", "").lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def get_scaledobject_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/keda.sh/v1alpha1/namespaces/{quote(namespace)}/scaledobjects"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            target_kind = target.get("kind") or "Deployment"
            if str(target_kind).lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def prometheus_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query}
        if at:
            params["time"] = at.isoformat()
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=10) as client:
            response = await client.get("/api/v1/query", params=params)
            response.raise_for_status()
            return response.json()

    async def prometheus_query_range(self, query: str, start: datetime, end: datetime, step: str = "60s") -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=15) as client:
            response = await client.get(
                "/api/v1/query_range",
                params={"query": query, "start": start.isoformat(), "end": end.isoformat(), "step": step},
            )
            response.raise_for_status()
            return response.json()

    async def service_graph(self, service: str, at: datetime) -> list[dict[str, Any]]:
        window = self.settings.service_graph_window_minutes
        escaped = service.replace('"', '\\"')
        query = (
            f'sum(increase(traces_service_graph_request_total{{server="{escaped}"}}[{window}m])) by (client, server, connection_type) > 0 '
            f'or sum(increase(traces_service_graph_request_total{{client="{escaped}"}}[{window}m])) by (client, server, connection_type) > 0'
        )
        payload = await self.prometheus_query(query, at)
        edges: list[dict[str, Any]] = []
        for row in payload.get("data", {}).get("result", []):
            metric = row.get("metric", {})
            try:
                value = float(row.get("value", [0, "0"])[1])
            except (TypeError, ValueError, IndexError):
                value = 0.0
            client = metric.get("client")
            server = metric.get("server")
            if client and server:
                edges.append({
                    "client": client,
                    "server": server,
                    "requests": value,
                    "connection_type": metric.get("connection_type", "unset"),
                    "query": query,
                })
        return edges

    async def loki_query_range(self, query: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.loki_url, timeout=15) as client:
            response = await client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(int(start.timestamp() * 1_000_000_000)),
                    "end": str(int(end.timestamp() * 1_000_000_000)),
                    "limit": limit,
                    "direction": "backward",
                },
            )
            response.raise_for_status()
            return response.json()

    async def argocd_application(self, app_name: str) -> dict[str, Any]:
        headers = {}
        if self.settings.argocd_token:
            headers["Authorization"] = f"Bearer {self.settings.argocd_token}"
        async with httpx.AsyncClient(base_url=self.settings.argocd_url, headers=headers, timeout=10) as client:
            response = await client.get(f"/api/v1/applications/{quote(app_name)}")
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def github_commit(self, repo_url: str, revision: str) -> dict[str, Any]:
        parsed = urlparse(repo_url)
        if parsed.hostname not in {"github.com", "www.github.com"}:
            return {}
        parts = [part for part in parsed.path.removesuffix(".git").split("/") if part]
        if len(parts) < 2:
            return {}
        owner, repo = parts[0], parts[1]
        headers = {"Accept": "application/vnd.github+json"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        async with httpx.AsyncClient(base_url=self.settings.github_api_url, headers=headers, timeout=10) as client:
            response = await client.get(f"/repos/{quote(owner)}/{quote(repo)}/commits/{quote(revision)}")
            if response.status_code in {401, 403, 404}:
                return {}
            response.raise_for_status()
            return response.json()

    async def karpenter_nodeclaim_for_node(self, node: str) -> dict[str, Any]:
        data = await self._k8s_get("/apis/karpenter.sh/v1/nodeclaims")
        for item in data.get("items", []):
            if item.get("status", {}).get("nodeName") == node:
                return item
        return {}
