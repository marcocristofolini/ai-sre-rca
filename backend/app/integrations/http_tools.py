from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings


class ReadOnlyHTTPTools:
    """Narrow, read-only clients for the infrastructure APIs used during RCA."""

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
            r = await client.get(path, params=params)
            r.raise_for_status()
            return r.json()

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

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        data = await self._k8s_get("/api/v1/pods", {"fieldSelector": f"spec.nodeName={node}"})
        return data.get("items", [])

    async def prometheus_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query}
        if at:
            params["time"] = at.isoformat()
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=10) as client:
            r = await client.get("/api/v1/query", params=params)
            r.raise_for_status()
            return r.json()

    async def prometheus_query_range(self, query: str, start: datetime, end: datetime, step: str = "60s") -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.prometheus_url, timeout=15) as client:
            r = await client.get(
                "/api/v1/query_range",
                params={"query": query, "start": start.isoformat(), "end": end.isoformat(), "step": step},
            )
            r.raise_for_status()
            return r.json()

    async def loki_query_range(self, query: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.settings.loki_url, timeout=15) as client:
            r = await client.get(
                "/loki/api/v1/query_range",
                params={
                    "query": query,
                    "start": str(int(start.timestamp() * 1_000_000_000)),
                    "end": str(int(end.timestamp() * 1_000_000_000)),
                    "limit": limit,
                    "direction": "backward",
                },
            )
            r.raise_for_status()
            return r.json()

    async def argocd_application(self, app_name: str) -> dict[str, Any]:
        headers = {}
        if self.settings.argocd_token:
            headers["Authorization"] = f"Bearer {self.settings.argocd_token}"
        async with httpx.AsyncClient(base_url=self.settings.argocd_url, headers=headers, timeout=10) as client:
            r = await client.get(f"/api/v1/applications/{quote(app_name)}")
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            return r.json()
