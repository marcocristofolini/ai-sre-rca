from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.awareness.inventory import (
    AuthoritativeInventorySnapshot,
    InventoryCoverage,
    InventoryReconciliationResult,
    InventoryRelationship,
    InventoryResource,
    InventoryResourceRef,
)
from app.config import Settings
from app.db.repository import InvestigationRepository


KUBERNETES_INVENTORY_SOURCE = "kubernetes"
KUBERNETES_INVENTORY_ACTOR = "system:kubernetes-inventory-collector"

SUPPORTED_KINDS: tuple[str, ...] = (
    "Service",
    "Deployment",
    "StatefulSet",
    "DaemonSet",
    "ReplicaSet",
    "Pod",
    "Node",
)
NAMESPACED_KINDS = frozenset(kind for kind in SUPPORTED_KINDS if kind != "Node")
SERVICE_TARGET_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet"})


class KubernetesInventoryTools(Protocol):
    def set_cluster(self, cluster: str): ...
    def reset_cluster(self, token) -> None: ...

    async def list_services(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_deployments(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_statefulsets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_daemonsets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_replicasets(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_pods(
        self,
        namespace: str | None = None,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_nodes(
        self,
        *,
        page_size: int = 500,
    ) -> list[dict[str, Any]]: ...


class KubernetesInventoryCollection(BaseModel):
    tenant_id: str
    cluster: str
    observed_at: datetime
    snapshots: list[AuthoritativeInventorySnapshot] = Field(default_factory=list)


class KubernetesInventoryReconciliation(BaseModel):
    tenant_id: str
    cluster: str
    observed_at: datetime
    results: list[InventoryReconciliationResult] = Field(default_factory=list)


def _identity(kind: str, namespace: str | None, name: str) -> tuple[str, str, str]:
    return (kind.casefold(), namespace or "", name)


def _metadata(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") or {}
    status = item.get("status") or {}
    spec = item.get("spec") or {}
    output: dict[str, Any] = {
        "source": KUBERNETES_INVENTORY_SOURCE,
    }
    for key, target in (
        ("uid", "kubernetes_uid"),
        ("resourceVersion", "resource_version"),
        ("creationTimestamp", "creation_timestamp"),
    ):
        value = metadata.get(key)
        if value is not None:
            output[target] = value
    generation = metadata.get("generation")
    if generation is not None:
        output["generation"] = generation
    labels = metadata.get("labels")
    if isinstance(labels, dict) and labels:
        output["labels"] = dict(labels)

    replicas = spec.get("replicas")
    if replicas is not None:
        output["desired_replicas"] = replicas
    available = status.get("availableReplicas")
    if available is not None:
        output["available_replicas"] = available
    ready = status.get("readyReplicas")
    if ready is not None:
        output["ready_replicas"] = ready
    phase = status.get("phase")
    if phase:
        output["phase"] = phase
    return output


def _resource(kind: str, item: dict[str, Any]) -> InventoryResource:
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError(f"Kubernetes {kind} item is missing metadata")
    name = str(metadata.get("name") or "").strip()
    if not name:
        raise RuntimeError(f"Kubernetes {kind} item is missing metadata.name")
    namespace = metadata.get("namespace")
    if kind != "Node":
        namespace = str(namespace or "").strip()
        if not namespace:
            raise RuntimeError(
                f"Kubernetes {kind}/{name} is missing metadata.namespace"
            )
    else:
        namespace = None
    return InventoryResource(
        kind=kind,
        name=name,
        namespace=namespace,
        metadata=_metadata(item),
    )


def _controller_owner(
    item: dict[str, Any],
) -> tuple[str, str, str] | None:
    metadata = item.get("metadata") or {}
    raw_refs = metadata.get("ownerReferences") or []
    refs = [ref for ref in raw_refs if isinstance(ref, dict)]
    controllers = [ref for ref in refs if ref.get("controller") is True]
    if len(controllers) != 1:
        return None
    controller = controllers[0]
    kind = str(controller.get("kind") or "").strip()
    name = str(controller.get("name") or "").strip()
    uid = str(controller.get("uid") or "").strip()
    if not kind or not name or not uid:
        return None
    return kind, name, uid


def _selector_matches(
    selector: dict[str, Any],
    labels: dict[str, Any],
) -> bool:
    if not selector:
        return False
    return all(str(labels.get(key)) == str(value) for key, value in selector.items())


def _snapshot_id(
    *,
    tenant_id: str,
    cluster: str,
    observed_at: datetime,
    scope: str,
) -> str:
    stamp = observed_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = sha256(
        "\x1f".join((tenant_id, cluster, scope)).encode("utf-8")
    ).hexdigest()[:12]
    return f"k8s-{stamp}-{digest}"


class KubernetesInventoryCollector:
    def __init__(
        self,
        tools: KubernetesInventoryTools,
        *,
        page_size: int = 500,
    ):
        self.tools = tools
        self.page_size = page_size

    async def _list_kind(
        self,
        kind: str,
        namespace: str | None,
    ) -> list[dict[str, Any]]:
        methods = {
            "Service": self.tools.list_services,
            "Deployment": self.tools.list_deployments,
            "StatefulSet": self.tools.list_statefulsets,
            "DaemonSet": self.tools.list_daemonsets,
            "ReplicaSet": self.tools.list_replicasets,
            "Pod": self.tools.list_pods,
        }
        if kind == "Node":
            if namespace is not None:
                return []
            return await self.tools.list_nodes(page_size=self.page_size)
        method = methods[kind]
        return await method(namespace, page_size=self.page_size)

    async def _collect_objects(
        self,
        *,
        kinds: list[str],
        namespaces: list[str] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        tasks: list[tuple[str, asyncio.Task[list[dict[str, Any]]]]] = []
        for kind in kinds:
            if kind == "Node":
                tasks.append((
                    kind,
                    asyncio.create_task(self._list_kind(kind, None)),
                ))
                continue
            if namespaces is None:
                tasks.append((
                    kind,
                    asyncio.create_task(self._list_kind(kind, None)),
                ))
            else:
                for namespace in namespaces:
                    tasks.append((
                        kind,
                        asyncio.create_task(
                            self._list_kind(kind, namespace)
                        ),
                    ))

        output = {kind: [] for kind in kinds}
        try:
            for kind, task in tasks:
                output[kind].extend(await task)
        except Exception:
            for _, task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for _, task in tasks),
                return_exceptions=True,
            )
            raise
        return output

    @staticmethod
    def _relationships(
        resources: list[InventoryResource],
        objects: dict[str, list[dict[str, Any]]],
        *,
        allow_scheduled_on: bool,
    ) -> list[InventoryRelationship]:
        resources_by_key = {
            _identity(item.kind, item.namespace, item.name): item
            for item in resources
        }
        raw_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for kind, items in objects.items():
            for item in items:
                metadata = item.get("metadata") or {}
                name = str(metadata.get("name") or "").strip()
                if not name:
                    continue
                namespace = (
                    None
                    if kind == "Node"
                    else str(metadata.get("namespace") or "").strip() or None
                )
                raw_by_key[_identity(kind, namespace, name)] = item

        relationships: dict[
            tuple[tuple[str, str, str], str, tuple[str, str, str]],
            InventoryRelationship,
        ] = {}
        for child_key, item in raw_by_key.items():
            child_resource = resources_by_key.get(child_key)
            if child_resource is None:
                continue
            owner = _controller_owner(item)
            if owner is None:
                continue
            owner_kind, owner_name, owner_uid = owner
            owner_key = _identity(
                owner_kind,
                child_resource.namespace,
                owner_name,
            )
            owner_resource = resources_by_key.get(owner_key)
            owner_item = raw_by_key.get(owner_key)
            if owner_resource is None or owner_item is None:
                continue
            observed_owner_uid = str(
                (owner_item.get("metadata") or {}).get("uid") or ""
            ).strip()
            if not observed_owner_uid or observed_owner_uid != owner_uid:
                continue
            key = (child_key, "owned_by", owner_key)
            relationships[key] = InventoryRelationship(
                source=InventoryResourceRef(
                    kind=child_resource.kind,
                    name=child_resource.name,
                    namespace=child_resource.namespace,
                ),
                target=InventoryResourceRef(
                    kind=owner_resource.kind,
                    name=owner_resource.name,
                    namespace=owner_resource.namespace,
                ),
                relationship="owned_by",
                metadata={
                    "basis": "ownerReference.uid",
                    "owner_uid": owner_uid,
                },
            )

        if allow_scheduled_on:
            for pod_key, item in raw_by_key.items():
                if pod_key[0] != "pod":
                    continue
                pod = resources_by_key.get(pod_key)
                if pod is None:
                    continue
                node_name = str(
                    (item.get("spec") or {}).get("nodeName") or ""
                ).strip()
                if not node_name:
                    continue
                node_key = _identity("Node", None, node_name)
                node = resources_by_key.get(node_key)
                if node is None:
                    continue
                key = (pod_key, "scheduled_on", node_key)
                relationships[key] = InventoryRelationship(
                    source=InventoryResourceRef(
                        kind=pod.kind,
                        name=pod.name,
                        namespace=pod.namespace,
                    ),
                    target=InventoryResourceRef(
                        kind=node.kind,
                        name=node.name,
                        namespace=node.namespace,
                    ),
                    relationship="scheduled_on",
                    metadata={"basis": "spec.nodeName"},
                )

        workloads = [
            (key, item)
            for key, item in raw_by_key.items()
            if (
                key in resources_by_key
                and resources_by_key[key].kind in SERVICE_TARGET_KINDS
            )
        ]
        for service_key, item in raw_by_key.items():
            if service_key[0] != "service":
                continue
            service = resources_by_key.get(service_key)
            if service is None:
                continue
            selector = (item.get("spec") or {}).get("selector")
            if not isinstance(selector, dict) or not selector:
                continue
            for workload_key, workload_item in workloads:
                workload = resources_by_key[workload_key]
                if workload.namespace != service.namespace:
                    continue
                template = (workload_item.get("spec") or {}).get("template") or {}
                labels = (template.get("metadata") or {}).get("labels") or {}
                if not isinstance(labels, dict):
                    continue
                if not _selector_matches(selector, labels):
                    continue
                key = (workload_key, "serves", service_key)
                relationships[key] = InventoryRelationship(
                    source=InventoryResourceRef(
                        kind=workload.kind,
                        name=workload.name,
                        namespace=workload.namespace,
                    ),
                    target=InventoryResourceRef(
                        kind=service.kind,
                        name=service.name,
                        namespace=service.namespace,
                    ),
                    relationship="serves",
                    metadata={
                        "basis": "service.selector+workload.podTemplate",
                        "selector": dict(selector),
                    },
                )

        return list(relationships.values())

    @staticmethod
    def _relationship_types(
        kinds: set[str],
        *,
        allow_scheduled_on: bool,
    ) -> list[str]:
        output: list[str] = []
        if (
            ("Pod" in kinds and bool(kinds & {"ReplicaSet", "StatefulSet", "DaemonSet"}))
            or ("ReplicaSet" in kinds and "Deployment" in kinds)
        ):
            output.append("owned_by")
        if "Service" in kinds and bool(kinds & SERVICE_TARGET_KINDS):
            output.append("serves")
        if allow_scheduled_on and {"Pod", "Node"}.issubset(kinds):
            output.append("scheduled_on")
        return output

    async def collect_cluster(
        self,
        *,
        tenant_id: str,
        cluster: str,
        kinds: list[str],
        namespaces: list[str] | None,
        observed_at: datetime | None = None,
    ) -> KubernetesInventoryCollection:
        canonical_by_fold = {
            item.casefold(): item for item in SUPPORTED_KINDS
        }
        normalized_kinds: list[str] = []
        for raw in kinds:
            canonical = canonical_by_fold.get(raw.casefold())
            if canonical is None:
                raise ValueError(
                    f"unsupported Kubernetes inventory kind: {raw}"
                )
            if canonical not in normalized_kinds:
                normalized_kinds.append(canonical)
        if not normalized_kinds:
            raise ValueError(
                "Kubernetes inventory requires at least one supported kind"
            )

        now = (observed_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        )
        token = self.tools.set_cluster(cluster)
        try:
            objects = await self._collect_objects(
                kinds=normalized_kinds,
                namespaces=namespaces,
            )
        finally:
            self.tools.reset_cluster(token)

        resources_by_kind = {
            kind: [_resource(kind, item) for item in items]
            for kind, items in objects.items()
        }

        snapshots: list[AuthoritativeInventorySnapshot] = []
        namespaced_kinds = [
            kind for kind in normalized_kinds if kind in NAMESPACED_KINDS
        ]
        if namespaced_kinds:
            resources = [
                resource
                for kind in namespaced_kinds
                for resource in resources_by_kind[kind]
            ]
            relationships = self._relationships(
                resources,
                {kind: objects[kind] for kind in namespaced_kinds},
                allow_scheduled_on=False,
            )
            kind_set = set(namespaced_kinds)
            snapshots.append(AuthoritativeInventorySnapshot(
                snapshot_id=_snapshot_id(
                    tenant_id=tenant_id,
                    cluster=cluster,
                    observed_at=now,
                    scope="namespaced",
                ),
                source=KUBERNETES_INVENTORY_SOURCE,
                observed_at=now,
                complete=True,
                coverage=InventoryCoverage(
                    kinds=namespaced_kinds,
                    namespaces=namespaces,
                    relationship_types=self._relationship_types(
                        kind_set,
                        allow_scheduled_on=False,
                    ),
                ),
                resources=resources,
                relationships=relationships,
                tenant_id=tenant_id,
                cluster=cluster,
            ))

        if "Node" in normalized_kinds:
            node_resources = resources_by_kind["Node"]
            snapshots.append(AuthoritativeInventorySnapshot(
                snapshot_id=_snapshot_id(
                    tenant_id=tenant_id,
                    cluster=cluster,
                    observed_at=now,
                    scope="nodes",
                ),
                source=KUBERNETES_INVENTORY_SOURCE,
                observed_at=now,
                complete=True,
                coverage=InventoryCoverage(
                    kinds=["Node"],
                    namespaces=None,
                    relationship_types=[],
                ),
                resources=node_resources,
                relationships=[],
                tenant_id=tenant_id,
                cluster=cluster,
            ))

        # Cluster-wide collection can safely assert cross-scope Pod -> Node
        # scheduling edges because namespaced and cluster-scoped endpoints were
        # observed in the same complete API pass.
        if namespaces is None and "Node" in normalized_kinds and namespaced_kinds:
            all_resources = [
                resource
                for kind in normalized_kinds
                for resource in resources_by_kind[kind]
            ]
            relationships = self._relationships(
                all_resources,
                objects,
                allow_scheduled_on=True,
            )
            snapshots = [AuthoritativeInventorySnapshot(
                snapshot_id=_snapshot_id(
                    tenant_id=tenant_id,
                    cluster=cluster,
                    observed_at=now,
                    scope="cluster",
                ),
                source=KUBERNETES_INVENTORY_SOURCE,
                observed_at=now,
                complete=True,
                coverage=InventoryCoverage(
                    kinds=normalized_kinds,
                    namespaces=None,
                    relationship_types=self._relationship_types(
                        set(normalized_kinds),
                        allow_scheduled_on=True,
                    ),
                ),
                resources=all_resources,
                relationships=relationships,
                tenant_id=tenant_id,
                cluster=cluster,
            )]

        return KubernetesInventoryCollection(
            tenant_id=tenant_id,
            cluster=cluster,
            observed_at=now,
            snapshots=snapshots,
        )


async def reconcile_kubernetes_inventory(
    repo: InvestigationRepository,
    tools: KubernetesInventoryTools,
    settings: Settings,
    *,
    observed_at: datetime | None = None,
) -> list[KubernetesInventoryReconciliation]:
    clusters = settings.awareness_inventory_cluster_list
    if not clusters:
        raise RuntimeError(
            "AWARENESS_INVENTORY_CLUSTERS must be configured"
        )
    if (
        KUBERNETES_INVENTORY_SOURCE
        not in settings.awareness_authoritative_inventory_source_set
    ):
        raise RuntimeError(
            "kubernetes must be allow-listed in "
            "AWARENESS_AUTHORITATIVE_INVENTORY_SOURCES"
        )
    registry = settings.cluster_config
    if len(clusters) > 1 and not registry:
        raise RuntimeError(
            "multiple AWARENESS_INVENTORY_CLUSTERS require "
            "CLUSTER_CONFIG_JSON entries for explicit routing"
        )
    if registry:
        missing = sorted(set(clusters) - set(registry))
        if missing:
            raise RuntimeError(
                "inventory clusters are missing from CLUSTER_CONFIG_JSON: "
                + ", ".join(missing)
            )

    collector = KubernetesInventoryCollector(
        tools,
        page_size=settings.awareness_inventory_page_size,
    )
    output: list[KubernetesInventoryReconciliation] = []
    for cluster in clusters:
        collection = await collector.collect_cluster(
            tenant_id=settings.awareness_inventory_tenant_id,
            cluster=cluster,
            kinds=settings.awareness_inventory_kind_list,
            namespaces=settings.awareness_inventory_namespace_list,
            observed_at=observed_at,
        )
        results = [
            await repo.reconcile_authoritative_inventory(
                snapshot,
                actor=KUBERNETES_INVENTORY_ACTOR,
            )
            for snapshot in collection.snapshots
        ]
        output.append(KubernetesInventoryReconciliation(
            tenant_id=collection.tenant_id,
            cluster=collection.cluster,
            observed_at=collection.observed_at,
            results=results,
        ))
    return output
