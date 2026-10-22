from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.awareness.kubernetes_inventory import (
    KubernetesInventoryCollector,
    reconcile_kubernetes_inventory,
)
from app.config import Settings
from app.db.repository import MemoryRepository
from app.integrations.http_tools import ReadOnlyHTTPTools


def _obj(
    name: str,
    namespace: str | None = "payments",
    *,
    labels: dict[str, str] | None = None,
    owner: tuple[str, str] | None = None,
    node: str | None = None,
    selector: dict[str, str] | None = None,
    template_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": name,
        "uid": f"uid-{name}",
        "resourceVersion": "42",
    }
    if namespace is not None:
        metadata["namespace"] = namespace
    if labels:
        metadata["labels"] = labels
    if owner:
        metadata["ownerReferences"] = [{
            "apiVersion": "apps/v1",
            "kind": owner[0],
            "name": owner[1],
            "uid": f"uid-{owner[1]}",
            "controller": True,
        }]
    spec: dict[str, Any] = {}
    if node:
        spec["nodeName"] = node
    if selector:
        spec["selector"] = selector
    if template_labels:
        spec["template"] = {
            "metadata": {"labels": dict(template_labels)}
        }
    return {
        "apiVersion": "v1",
        "metadata": metadata,
        "spec": spec,
        "status": {},
    }


class FakeInventoryTools:
    def __init__(self):
        self.current_cluster: str | None = None
        self.fail_kind: str | None = None
        self.data: dict[str, list[dict[str, Any]]] = {
            "Service": [
                _obj(
                    "checkout",
                    labels={"app": "checkout"},
                    selector={"app": "checkout"},
                )
            ],
            "Deployment": [
                _obj(
                    "checkout",
                    template_labels={"app": "checkout"},
                )
            ],
            "StatefulSet": [],
            "DaemonSet": [],
            "ReplicaSet": [
                _obj(
                    "checkout-abc",
                    owner=("Deployment", "checkout"),
                )
            ],
            "Pod": [
                _obj(
                    "checkout-abc-1",
                    labels={"app": "checkout"},
                    owner=("ReplicaSet", "checkout-abc"),
                    node="worker-1",
                )
            ],
            "Node": [_obj("worker-1", None)],
        }

    def set_cluster(self, cluster: str):
        previous = self.current_cluster
        self.current_cluster = cluster
        return previous

    def reset_cluster(self, token) -> None:
        self.current_cluster = token

    async def _list(
        self,
        kind: str,
        namespace: str | None,
    ) -> list[dict[str, Any]]:
        if self.fail_kind == kind:
            raise RuntimeError(f"failed listing {kind}")
        items = self.data[kind]
        if namespace is None or kind == "Node":
            return list(items)
        return [
            item
            for item in items
            if item.get("metadata", {}).get("namespace") == namespace
        ]

    async def list_services(self, namespace=None, *, page_size=500):
        return await self._list("Service", namespace)

    async def list_deployments(self, namespace=None, *, page_size=500):
        return await self._list("Deployment", namespace)

    async def list_statefulsets(self, namespace=None, *, page_size=500):
        return await self._list("StatefulSet", namespace)

    async def list_daemonsets(self, namespace=None, *, page_size=500):
        return await self._list("DaemonSet", namespace)

    async def list_replicasets(self, namespace=None, *, page_size=500):
        return await self._list("ReplicaSet", namespace)

    async def list_pods(self, namespace=None, *, page_size=500):
        return await self._list("Pod", namespace)

    async def list_nodes(self, *, page_size=500):
        return await self._list("Node", None)


@pytest.mark.asyncio
async def test_clusterwide_inventory_builds_deterministic_topology():
    tools = FakeInventoryTools()
    tools.data["Service"][0]["metadata"]["annotations"] = {"ai-sre.io/telemetry-service": "checkout-api"}
    collector = KubernetesInventoryCollector(tools)
    now = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)

    collection = await collector.collect_cluster(
        tenant_id="tenant-a",
        cluster="prod",
        kinds=[
            "Service",
            "Deployment",
            "StatefulSet",
            "DaemonSet",
            "ReplicaSet",
            "Pod",
            "Node",
        ],
        namespaces=None,
        observed_at=now,
    )

    assert len(collection.snapshots) == 1
    snapshot = collection.snapshots[0]
    assert snapshot.complete is True
    service = next(item for item in snapshot.resources if item.kind == "Service")
    assert service.metadata["telemetry_service"] == "checkout-api"
    assert service.metadata["kubernetes_uid"]
    assert snapshot.coverage.namespaces is None
    assert set(snapshot.coverage.relationship_types) == {
        "owned_by",
        "serves",
        "scheduled_on",
    }

    resources = {
        (item.kind, item.namespace, item.name)
        for item in snapshot.resources
    }
    assert ("Service", "payments", "checkout") in resources
    assert ("Deployment", "payments", "checkout") in resources
    assert ("ReplicaSet", "payments", "checkout-abc") in resources
    assert ("Pod", "payments", "checkout-abc-1") in resources
    assert ("Node", None, "worker-1") in resources

    relationships = {
        (
            item.source.kind,
            item.source.name,
            item.relationship,
            item.target.kind,
            item.target.name,
        )
        for item in snapshot.relationships
    }
    assert (
        "Pod",
        "checkout-abc-1",
        "owned_by",
        "ReplicaSet",
        "checkout-abc",
    ) in relationships
    assert (
        "ReplicaSet",
        "checkout-abc",
        "owned_by",
        "Deployment",
        "checkout",
    ) in relationships
    assert (
        "Pod",
        "checkout-abc-1",
        "scheduled_on",
        "Node",
        "worker-1",
    ) in relationships
    assert (
        "Deployment",
        "checkout",
        "serves",
        "Service",
        "checkout",
    ) in relationships


@pytest.mark.asyncio
async def test_owner_relationship_requires_matching_controller_uid():
    tools = FakeInventoryTools()
    tools.data["ReplicaSet"][0]["metadata"]["ownerReferences"][0]["uid"] = (
        "uid-deleted-checkout"
    )
    collector = KubernetesInventoryCollector(tools)

    collection = await collector.collect_cluster(
        tenant_id="tenant-a",
        cluster="prod",
        kinds=["Deployment", "ReplicaSet"],
        namespaces=["payments"],
        observed_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
    )

    snapshot = collection.snapshots[0]
    assert snapshot.coverage.relationship_types == ["owned_by"]
    assert all(
        item.relationship != "owned_by"
        for item in snapshot.relationships
    )


@pytest.mark.asyncio
async def test_namespace_scoped_inventory_does_not_assert_cross_scope_edges():
    tools = FakeInventoryTools()
    collector = KubernetesInventoryCollector(tools)
    now = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)

    collection = await collector.collect_cluster(
        tenant_id="tenant-a",
        cluster="prod",
        kinds=["Service", "Deployment", "ReplicaSet", "Pod", "Node"],
        namespaces=["payments"],
        observed_at=now,
    )

    assert len(collection.snapshots) == 2
    namespaced = next(
        item for item in collection.snapshots
        if item.coverage.namespaces is not None
    )
    nodes = next(
        item for item in collection.snapshots
        if item.coverage.kinds == ["Node"]
    )

    assert namespaced.coverage.namespaces == ["payments"]
    assert "scheduled_on" not in namespaced.coverage.relationship_types
    assert all(
        item.relationship != "scheduled_on"
        for item in namespaced.relationships
    )
    assert nodes.coverage.namespaces is None
    assert len(nodes.resources) == 1


@pytest.mark.asyncio
async def test_partial_collection_never_reconciles_or_tombstones():
    tools = FakeInventoryTools()
    tools.fail_kind = "Deployment"
    repo = MemoryRepository()
    settings = Settings(
        awareness_inventory_clusters="prod",
        awareness_inventory_tenant_id="tenant-a",
        awareness_inventory_kinds="Service,Deployment,ReplicaSet,Pod,Node",
    )

    with pytest.raises(RuntimeError, match="failed listing Deployment"):
        await reconcile_kubernetes_inventory(
            repo,
            tools,
            settings,
            observed_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
        )

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    assert graph.entities == []
    assert graph.relationships == []
    assert repo._audit == []
    assert tools.current_cluster is None


@pytest.mark.asyncio
async def test_automatic_inventory_tombstones_missing_covered_resource():
    tools = FakeInventoryTools()
    repo = MemoryRepository()
    settings = Settings(
        awareness_inventory_clusters="prod",
        awareness_inventory_tenant_id="tenant-a",
        awareness_inventory_kinds="Service,Deployment,ReplicaSet,Pod,Node",
    )
    first_at = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)

    await reconcile_kubernetes_inventory(
        repo,
        tools,
        settings,
        observed_at=first_at,
    )
    current = await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="Deployment",
        name="checkout",
    )
    assert len(current) == 1

    tools.data["Deployment"] = []
    tools.data["ReplicaSet"] = []
    tools.data["Pod"] = []

    second = await reconcile_kubernetes_inventory(
        repo,
        tools,
        settings,
        observed_at=first_at + timedelta(minutes=5),
    )

    assert sum(
        result.entities_tombstoned
        for cluster in second
        for result in cluster.results
    ) >= 1
    assert await repo.find_awareness_entities(
        "tenant-a",
        "prod",
        kind="Deployment",
        name="checkout",
    ) == []

    graph = await repo.get_awareness_graph("tenant-a", "prod")
    deployment = next(
        item for item in graph.entities if item.kind == "Deployment"
    )
    assert deployment.tombstone_source == "kubernetes"
    assert any(
        event.action == "awareness.inventory.reconcile"
        for event in repo._audit
    )


@pytest.mark.asyncio
async def test_inventory_rejects_unsupported_kind_before_reconciliation():
    tools = FakeInventoryTools()
    collector = KubernetesInventoryCollector(tools)

    with pytest.raises(
        ValueError,
        match="unsupported Kubernetes inventory kind",
    ):
        await collector.collect_cluster(
            tenant_id="tenant-a",
            cluster="prod",
            kinds=["Secret"],
            namespaces=None,
        )



@pytest.mark.asyncio
async def test_http_inventory_list_follows_kubernetes_continue_tokens(monkeypatch):
    tools = ReadOnlyHTTPTools(Settings())
    calls: list[dict[str, Any]] = []

    async def fake_get(path: str, params=None):
        calls.append({"path": path, "params": dict(params or {})})
        if not params or not params.get("continue"):
            return {
                "items": [{"metadata": {"name": "pod-a"}}],
                "metadata": {"continue": "next-page"},
            }
        return {
            "items": [{"metadata": {"name": "pod-b"}}],
            "metadata": {"continue": ""},
        }

    monkeypatch.setattr(tools, "_k8s_get", fake_get)

    items = await tools.list_pods(page_size=1)

    assert [item["metadata"]["name"] for item in items] == [
        "pod-a",
        "pod-b",
    ]
    assert calls == [
        {
            "path": "/api/v1/pods",
            "params": {"limit": 1},
        },
        {
            "path": "/api/v1/pods",
            "params": {"limit": 1, "continue": "next-page"},
        },
    ]


@pytest.mark.asyncio
async def test_http_inventory_list_fails_closed_on_repeated_continue_token(
    monkeypatch,
):
    tools = ReadOnlyHTTPTools(Settings())
    calls = 0

    async def fake_get(path: str, params=None):
        nonlocal calls
        calls += 1
        return {
            "items": [{"metadata": {"name": f"pod-{calls}"}}],
            "metadata": {"continue": "loop-token"},
        }

    monkeypatch.setattr(tools, "_k8s_get", fake_get)

    with pytest.raises(
        RuntimeError,
        match="pagination repeated continue token",
    ):
        await tools.list_pods(page_size=1)

    assert calls == 2


@pytest.mark.asyncio
async def test_http_inventory_list_fails_closed_on_malformed_item(monkeypatch):
    tools = ReadOnlyHTTPTools(Settings())

    async def fake_get(path: str, params=None):
        return {
            "items": [
                {"metadata": {"name": "pod-a"}},
                "not-a-kubernetes-object",
            ],
            "metadata": {"continue": ""},
        }

    monkeypatch.setattr(tools, "_k8s_get", fake_get)

    with pytest.raises(RuntimeError, match="inventory list was incomplete"):
        await tools.list_pods()


@pytest.mark.asyncio
async def test_http_inventory_list_fails_closed_on_incomplete_payload(monkeypatch):
    tools = ReadOnlyHTTPTools(Settings())

    async def fake_get(path: str, params=None):
        return {}

    monkeypatch.setattr(tools, "_k8s_get", fake_get)

    with pytest.raises(RuntimeError, match="inventory list was incomplete"):
        await tools.list_deployments()



@pytest.mark.asyncio
async def test_service_relationship_survives_scale_to_zero_without_pods():
    tools = FakeInventoryTools()
    tools.data["Pod"] = []
    collector = KubernetesInventoryCollector(tools)

    collection = await collector.collect_cluster(
        tenant_id="tenant-a",
        cluster="prod",
        kinds=["Service", "Deployment"],
        namespaces=["payments"],
        observed_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc),
    )

    snapshot = collection.snapshots[0]
    assert snapshot.coverage.relationship_types == ["serves"]
    serves = [
        item
        for item in snapshot.relationships
        if item.relationship == "serves"
    ]
    assert len(serves) == 1
    assert serves[0].source.kind == "Deployment"
    assert serves[0].source.name == "checkout"
    assert serves[0].target.kind == "Service"
    assert serves[0].target.name == "checkout"


@pytest.mark.asyncio
async def test_multiple_inventory_clusters_require_explicit_cluster_registry():
    tools = FakeInventoryTools()
    repo = MemoryRepository()
    settings = Settings(
        awareness_inventory_clusters="prod-a,prod-b",
        awareness_inventory_tenant_id="tenant-a",
        awareness_inventory_kinds="Service,Deployment",
        cluster_config_json="{}",
    )

    with pytest.raises(
        RuntimeError,
        match="multiple AWARENESS_INVENTORY_CLUSTERS require",
    ):
        await reconcile_kubernetes_inventory(
            repo,
            tools,
            settings,
        )

    assert repo._audit == []
