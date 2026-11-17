from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.core.query_budget import consume_query
from app.core.query_safety import path_segment, quoted_string
from app.core.trace_ids import canonical_trace_id
from app.integrations.telemetry import (
    KafkaDependency,
    PostgresDependency,
    RedisDependency,
    ServiceGraph,
    UnavailableSource,
    UnsupportedSignal,
    build_source,
    unsupported_dependency_result,
)
from app.integrations.telemetry.prometheus import finite_scalar, instance_matcher
from app.integrations.telemetry.registry import CONFIG_KEYS, DEFAULT_BACKENDS


class UnknownClusterError(ValueError):
    pass


class ReadOnlyHTTPTools:
    """Narrow read-only clients used by the investigator.

    This class intentionally exposes only GET-style infrastructure operations.
    There is no generic shell, kubectl exec, patch, update or delete primitive.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cluster_ctx: ContextVar[str | None] = ContextVar(
            f"ai_sre_cluster_{id(self)}",
            default=None,
        )

    def set_cluster(self, cluster: str):
        registry = self.settings.cluster_config
        if registry and cluster not in registry:
            raise UnknownClusterError(f"cluster is not configured: {cluster}")
        return self._cluster_ctx.set(cluster)

    def reset_cluster(self, token) -> None:
        self._cluster_ctx.reset(token)

    @property
    def current_cluster(self) -> str | None:
        return self._cluster_ctx.get()

    def _cluster_value(self, key: str, default: Any) -> Any:
        cluster = self.current_cluster
        if not cluster:
            return default
        return self.settings.cluster_config.get(cluster, {}).get(key, default)

    def _allow(self, source: str) -> bool:
        return consume_query(source)

    def _k8s_headers(self) -> dict[str, str]:
        token_path = Path(self._cluster_value("kubernetes_token_file", self.settings.kubernetes_token_file))
        if not token_path.exists():
            return {}
        return {"Authorization": f"Bearer {token_path.read_text().strip()}"}

    def _k8s_verify(self) -> str | bool:
        ca = Path(self._cluster_value("kubernetes_ca_file", self.settings.kubernetes_ca_file))
        return str(ca) if ca.exists() else True

    async def _k8s_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._allow("kubernetes"):
            return {}
        async with httpx.AsyncClient(
            base_url=self._cluster_value("kubernetes_api_url", self.settings.kubernetes_api_url),
            headers=self._k8s_headers(),
            verify=self._k8s_verify(),
            timeout=10,
        ) as client:
            response = await client.get(path, params=params)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def _k8s_list(
        self,
        path: str,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        continue_token: str | None = None
        seen_continue_tokens: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": page_size}
            if continue_token:
                params["continue"] = continue_token
            payload = await self._k8s_get(path, params)
            page = payload.get("items")
            metadata = payload.get("metadata")
            if (
                not isinstance(page, list)
                or not isinstance(metadata, dict)
                or any(not isinstance(item, dict) for item in page)
            ):
                raise RuntimeError(
                    f"Kubernetes inventory list was incomplete or invalid: {path}"
                )
            items.extend(page)
            raw_continue = metadata.get("continue")
            continue_token = (
                str(raw_continue).strip()
                if raw_continue is not None
                else ""
            )
            if not continue_token:
                return items
            if continue_token in seen_continue_tokens:
                raise RuntimeError(
                    "Kubernetes inventory pagination repeated continue token: "
                    f"{path}"
                )
            seen_continue_tokens.add(continue_token)

    async def list_services(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/api/v1/namespaces/{path_segment(namespace)}/services"
            if namespace
            else "/api/v1/services"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def list_pods(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/api/v1/namespaces/{path_segment(namespace)}/pods"
            if namespace
            else "/api/v1/pods"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def list_nodes(
        self,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        return await self._k8s_list("/api/v1/nodes", page_size=page_size)

    async def list_deployments(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/apis/apps/v1/namespaces/{path_segment(namespace)}/deployments"
            if namespace
            else "/apis/apps/v1/deployments"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def list_statefulsets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/apis/apps/v1/namespaces/{path_segment(namespace)}/statefulsets"
            if namespace
            else "/apis/apps/v1/statefulsets"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def list_daemonsets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/apis/apps/v1/namespaces/{path_segment(namespace)}/daemonsets"
            if namespace
            else "/apis/apps/v1/daemonsets"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def list_replicasets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        path = (
            f"/apis/apps/v1/namespaces/{path_segment(namespace)}/replicasets"
            if namespace
            else "/apis/apps/v1/replicasets"
        )
        return await self._k8s_list(path, page_size=page_size)

    async def get_pod(self, namespace: str, pod: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/namespaces/{path_segment(namespace)}/pods/{path_segment(pod)}")

    async def get_node(self, node: str) -> dict[str, Any]:
        return await self._k8s_get(f"/api/v1/nodes/{path_segment(node)}")

    async def get_events(self, namespace: str, object_name: str) -> list[dict[str, Any]]:
        data = await self._k8s_get(
            f"/api/v1/namespaces/{path_segment(namespace)}/events",
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
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{path_segment(namespace)}/replicasets/{path_segment(name)}")

    async def get_deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{path_segment(namespace)}/deployments/{path_segment(name)}")

    async def get_statefulset(self, namespace: str, name: str) -> dict[str, Any]:
        return await self._k8s_get(f"/apis/apps/v1/namespaces/{path_segment(namespace)}/statefulsets/{path_segment(name)}")

    async def list_pods_on_node(self, node: str) -> list[dict[str, Any]]:
        data = await self._k8s_get("/api/v1/pods", {"fieldSelector": f"spec.nodeName={node}"})
        return data.get("items", [])

    async def get_hpa_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/autoscaling/v2/namespaces/{path_segment(namespace)}/horizontalpodautoscalers"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            if target.get("kind", "").lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def get_scaledobject_for_target(self, namespace: str, kind: str, name: str) -> dict[str, Any]:
        data = await self._k8s_get(
            f"/apis/keda.sh/v1alpha1/namespaces/{path_segment(namespace)}/scaledobjects"
        )
        for item in data.get("items", []):
            target = item.get("spec", {}).get("scaleTargetRef", {})
            target_kind = target.get("kind") or "Deployment"
            if str(target_kind).lower() == kind.lower() and target.get("name") == name:
                return item
        return {}

    async def prometheus_query(self, query: str, at: datetime | None = None) -> dict[str, Any]:
        if not self._allow("prometheus"):
            return {"status": "success", "data": {"result": []}, "_budget_denied": True}
        params: dict[str, Any] = {"query": query}
        if at:
            params["time"] = at.isoformat()
        async with httpx.AsyncClient(
            base_url=self._cluster_value("prometheus_url", self.settings.prometheus_url),
            timeout=10,
        ) as client:
            response = await client.get("/api/v1/query", params=params)
            response.raise_for_status()
            return response.json()

    async def prometheus_query_range(self, query: str, start: datetime, end: datetime, step: str = "60s") -> dict[str, Any]:
        if not self._allow("prometheus"):
            return {"status": "success", "data": {"result": []}, "_budget_denied": True}
        async with httpx.AsyncClient(
            base_url=self._cluster_value("prometheus_url", self.settings.prometheus_url),
            timeout=15,
        ) as client:
            response = await client.get(
                "/api/v1/query_range",
                params={"query": query, "start": start.isoformat(), "end": end.isoformat(), "step": step},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _prometheus_scalar(payload: dict[str, Any]) -> float | None:
        return finite_scalar(payload)

    @staticmethod
    def _prom_instance_matcher(value: str) -> str:
        return instance_matcher(value)

    def _backend(self, capability: str) -> str:
        return str(self._cluster_value(CONFIG_KEYS[capability], DEFAULT_BACKENDS[capability]))

    @property
    def metrics_source(self) -> Any:
        return build_source("metrics", self, self._backend("metrics"))

    @property
    def logs_source(self) -> Any:
        return build_source("logs", self, self._backend("logs"))

    @property
    def traces_source(self) -> Any:
        return build_source("traces", self, self._backend("traces"))

    async def _dependency(self, request: Any, at: datetime) -> dict[str, Any]:
        source = self.metrics_source
        try:
            return await source.dependency(request, at)
        except UnsupportedSignal:
            return unsupported_dependency_result(request, source.source)

    async def postgres_dependency_metrics(
        self,
        database: str,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ) -> dict[str, Any]:
        return await self._dependency(PostgresDependency(database, instance_hint, window_minutes), at)

    async def kafka_dependency_metrics(
        self,
        topic: str | None,
        consumer_group: str | None,
        at: datetime,
        window_minutes: int = 15,
        instance_hint: str | None = None,
    ) -> dict[str, Any]:
        return await self._dependency(KafkaDependency(topic, consumer_group, instance_hint, window_minutes), at)

    async def redis_dependency_metrics(
        self,
        instance_hint: str | None,
        at: datetime,
        window_minutes: int = 15,
    ) -> dict[str, Any]:
        return await self._dependency(RedisDependency(instance_hint, window_minutes), at)

    async def service_graph(self, service: str, at: datetime) -> list[dict[str, Any]]:
        request = ServiceGraph(service, self.settings.service_graph_window_minutes)
        try:
            return await self.metrics_source.service_graph(request, at)
        except UnsupportedSignal:
            return []

    def _tempo_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        tenant_id = self._cluster_value("tempo_tenant_id", self.settings.tempo_tenant_id)
        if tenant_id:
            headers["X-Scope-OrgID"] = str(tenant_id)
        return headers

    async def tempo_search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        escaped = quoted_string(service)
        queries = [
            f'{{ resource.service.name = "{escaped}" && status = error }}',
            f'{{ resource.service.name = "{escaped}" }}',
        ]
        traces: list[dict[str, Any]] = []
        seen: set[str] = set()
        if not self._allow("tempo"):
            return traces
        async with httpx.AsyncClient(
            base_url=self._cluster_value("tempo_url", self.settings.tempo_url),
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            for query in queries:
                if len(traces) >= limit:
                    break
                response = await client.get(
                    "/api/search",
                    params={
                        "q": query,
                        "start": int(start.timestamp()),
                        "end": int(end.timestamp()),
                        "limit": max(1, limit - len(traces)),
                        "spss": 5,
                    },
                )
                if response.status_code in {404, 501}:
                    return traces
                response.raise_for_status()
                for item in response.json().get("traces", []):
                    trace_id = canonical_trace_id(item.get("traceID"))
                    if not trace_id or trace_id in seen:
                        continue
                    seen.add(trace_id)
                    copy = dict(item)
                    copy['traceID'] = trace_id
                    copy["_source_query"] = query
                    traces.append(copy)
                    if len(traces) >= limit:
                        break
        # Tempo search results are not ordered. Preserve error-first selection,
        # then use the newest verified timestamps within the bounded response.
        traces.sort(key=lambda item: (
            'status = error' not in item.get('_source_query', ''),
            -int(item['startTimeUnixNano']) if str(item.get('startTimeUnixNano', '')).isdigit() else 0,
            item.get('traceID', ''),
        ))
        return traces

    async def tempo_get_trace(
        self,
        trace_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if start:
            params["start"] = int(start.timestamp())
        if end:
            params["end"] = int(end.timestamp())
        if not self._allow("tempo"):
            return {}
        async with httpx.AsyncClient(
            base_url=self._cluster_value("tempo_url", self.settings.tempo_url),
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            response = await client.get(f"/api/v2/traces/{path_segment(trace_id)}", params=params)
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def tempo_metrics_query(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ) -> dict[str, Any]:
        if not self._allow("tempo"):
            return {}
        async with httpx.AsyncClient(
            base_url=self._cluster_value("tempo_url", self.settings.tempo_url),
            headers=self._tempo_headers(),
            timeout=20,
        ) as client:
            response = await client.get(
                "/api/metrics/query_range",
                params={
                    "q": query,
                    "start": int(start.timestamp()),
                    "end": int(end.timestamp()),
                    "step": step,
                },
            )
            if response.status_code in {404, 501}:
                return {}
            response.raise_for_status()
            return response.json()

    async def loki_query_range(self, query: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
        if not self._allow("loki"):
            return {"data": {"result": []}, "_budget_denied": True}
        async with httpx.AsyncClient(
            base_url=self._cluster_value("loki_url", self.settings.loki_url),
            timeout=15,
        ) as client:
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
        if not self._allow("argocd"):
            return {}
        headers = {}
        if self.settings.argocd_token:
            headers["Authorization"] = f"Bearer {self.settings.argocd_token}"
        async with httpx.AsyncClient(
            base_url=self._cluster_value("argocd_url", self.settings.argocd_url),
            headers=headers,
            timeout=10,
        ) as client:
            response = await client.get(f"/api/v1/applications/{path_segment(app_name)}")
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            return response.json()

    async def github_commit(self, repo_url: str, revision: str) -> dict[str, Any]:
        if not self._allow("github"):
            return {}
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
            response = await client.get(f"/repos/{path_segment(owner)}/{path_segment(repo)}/commits/{path_segment(revision)}")
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

    async def capability_report(self, cluster: str) -> list[dict[str, Any]]:
        token = self.set_cluster(cluster)
        try:
            async def probe(
                source: str,
                base_url: str,
                path: str,
                *,
                headers: dict[str, str] | None = None,
                verify: str | bool = True,
            ) -> dict[str, Any]:
                started = time.monotonic()
                try:
                    async with httpx.AsyncClient(
                        base_url=base_url,
                        headers=headers,
                        verify=verify,
                        timeout=5,
                    ) as client:
                        response = await client.get(path)
                    latency = round((time.monotonic() - started) * 1000, 2)
                    if response.status_code < 400:
                        return {
                            "source": source,
                            "status": "healthy",
                            "detail": f"HTTP {response.status_code}",
                            "cluster": cluster,
                            "latency_ms": latency,
                        }
                    return {
                        "source": source,
                        "status": "degraded",
                        "detail": f"HTTP {response.status_code}",
                        "cluster": cluster,
                        "latency_ms": latency,
                    }
                except Exception as exc:
                    return {
                        "source": source,
                        "status": "unavailable",
                        "detail": exc.__class__.__name__,
                        "cluster": cluster,
                        "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    }

            argocd_headers: dict[str, str] = {}
            if self.settings.argocd_token:
                argocd_headers["Authorization"] = f"Bearer {self.settings.argocd_token}"

            probes = [
                probe(
                    "kubernetes",
                    self._cluster_value("kubernetes_api_url", self.settings.kubernetes_api_url),
                    "/version",
                    headers=self._k8s_headers(),
                    verify=self._k8s_verify(),
                ),
                probe(
                    "prometheus",
                    self._cluster_value("prometheus_url", self.settings.prometheus_url),
                    "/-/ready",
                ),
                probe(
                    "loki",
                    self._cluster_value("loki_url", self.settings.loki_url),
                    "/ready",
                ),
                probe(
                    "tempo",
                    self._cluster_value("tempo_url", self.settings.tempo_url),
                    "/ready",
                    headers=self._tempo_headers(),
                ),
                probe(
                    "argocd",
                    self._cluster_value("argocd_url", self.settings.argocd_url),
                    "/api/v1/version",
                    headers=argocd_headers,
                ),
            ]
            github_headers = {"Accept": "application/vnd.github+json"}
            if self.settings.github_token:
                github_headers["Authorization"] = f"Bearer {self.settings.github_token}"
            probes.append(
                probe(
                    "github",
                    self.settings.github_api_url,
                    "/rate_limit",
                    headers=github_headers,
                )
            )
            reports = list(await asyncio.gather(*probes))
            reports.extend(await self._alternate_backend_reports(cluster))
            return reports
        finally:
            self.reset_cluster(token)

    async def _alternate_backend_reports(self, cluster: str) -> list[dict[str, Any]]:
        """Report telemetry capabilities served by a non-default backend (ADR-0001)."""
        reports: list[dict[str, Any]] = []
        for capability, default in DEFAULT_BACKENDS.items():
            backend = self._backend(capability)
            if backend == default:
                continue
            source = build_source(capability, self, backend)
            entry: dict[str, Any] = {"source": backend, "capability": capability, "cluster": cluster}
            health = getattr(source, "health", None)
            if isinstance(source, UnavailableSource):
                entry.update(status="unsupported_backend", detail="backend is not registered")
            elif health is None:
                entry.update(status="unknown", detail="backend exposes no health check")
            else:
                try:
                    entry.update(await health())
                except Exception as exc:  # noqa: BLE001 - report, never raise
                    entry.update(status="unavailable", detail=exc.__class__.__name__)
            reports.append(entry)
        return reports
