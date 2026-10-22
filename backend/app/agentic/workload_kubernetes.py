"""UID-bound read surface for the existing deterministic Kubernetes stage."""
from app.core.query_safety import quoted_string


class WorkloadKubernetesTools:
    def __init__(self, tools, budget, incident):
        self.tools, self.budget, self.incident = tools, budget, incident
        self.members = {(m.kind, m.name): m.uid for m in incident.workload.members}
        self.members[(incident.workload.kind, incident.workload.name)] = incident.workload.uid
        self.pods = {}
        self.active = None
        self.root_verified = False

    async def _resource(self, kind, namespace, name, operation):
        if namespace != self.incident.namespace or (kind, name) not in self.members:
            raise ValueError('resource is outside confirmed workload scope')
        self.budget.consume(operation)
        item = await getattr(self.tools, operation)(namespace, name)
        meta = item.get('metadata', {})
        if meta.get('uid') != self.members[(kind, name)] or meta.get('name') != name or meta.get('namespace') != namespace:
            raise ValueError('live Kubernetes UID changed after confirmation')
        owners = [ref for ref in meta.get('ownerReferences', []) if ref.get('controller') is True]
        if kind in ('Pod', 'ReplicaSet'):
            parent_kind = 'Deployment' if kind == 'ReplicaSet' else ('ReplicaSet' if self.incident.workload.kind == 'Deployment' else 'StatefulSet')
            if len(owners) != 1 or owners[0].get('kind') != parent_kind or self.members.get((parent_kind, owners[0].get('name'))) != owners[0].get('uid'):
                raise ValueError('live Kubernetes controller ownership changed')
        return item

    async def get_pod(self, namespace, name):
        if name not in self.pods:
            if not self.root_verified:
                root = self.incident.workload
                operation = 'get_deployment' if root.kind == 'Deployment' else 'get_statefulset'
                await self._resource(root.kind, namespace, root.name, operation)
                self.root_verified = True
            self.pods[name] = await self._resource('Pod', namespace, name, 'get_pod')
            if self.incident.workload.kind == 'Deployment':
                owner = next(r for r in self.pods[name]['metadata']['ownerReferences'] if r.get('controller') is True)
                await self.get_replicaset(namespace, owner['name'])
        self.active = name
        return self.pods[name]

    async def get_replicaset(self, namespace, name):
        return await self._resource('ReplicaSet', namespace, name, 'get_replicaset')

    async def get_deployment(self, namespace, name):
        return await self._resource('Deployment', namespace, name, 'get_deployment')

    async def get_node(self, name):
        if name not in {p.get('spec', {}).get('nodeName') for p in self.pods.values()}:
            raise ValueError('node is not assigned to a verified Pod')
        self.budget.consume('get_node')
        node = await self.tools.get_node(name)
        if node.get('metadata', {}).get('name') != name or not node.get('metadata', {}).get('uid') or not any(c.get('type') == 'Ready' for c in node.get('status', {}).get('conditions', [])):
            raise ValueError('assigned-node runtime context is incomplete')
        return node

    async def get_events(self, namespace, name):
        if namespace != self.incident.namespace or ('Pod', name) not in self.members:
            raise ValueError('events are outside confirmed workload scope')
        self.budget.consume('get_events')
        events = await self.tools.get_events(namespace, name)
        return [e for e in events if e.get('involvedObject', {}).get('uid') == self.members[('Pod', name)]]

    async def get_cluster_events(self, name):
        return await self.get_events(self.incident.namespace, name) if ('Pod', name) in self.members else []

    async def loki_query_range(self, query, start, end, limit=100):
        pod = self.active
        uid = self.members[('Pod', pod)]
        query = f'{{namespace="{quoted_string(self.incident.namespace)}",pod="{quoted_string(pod)}",pod_uid="{quoted_string(uid)}"}}'
        self.budget.consume('loki_query_range')
        payload = await self.tools.loki_query_range(query, start, end, limit)
        for stream in payload.get('data', {}).get('result', []):
            labels = stream.get('stream', {})
            if (labels.get('namespace'), labels.get('pod'), labels.get('pod_uid')) != (self.incident.namespace, pod, uid):
                raise ValueError('log stream is outside confirmed Pod UID scope')
        return payload

    # Telemetry and change specialists own explicit service identities. The
    # Kubernetes stage must not infer these identities from Pod labels/names.
    async def prometheus_query(self, *args, **kwargs):
        return {}

    async def prometheus_query_range(self, *args, **kwargs):
        return {}

    async def service_graph(self, *args, **kwargs):
        return []

    async def argocd_application(self, *args, **kwargs):
        return {}

    async def github_commit(self, *args, **kwargs):
        return {}

    async def get_hpa_for_target(self, *args, **kwargs):
        return {}

    async def get_scaledobject_for_target(self, *args, **kwargs):
        return {}

    async def karpenter_nodeclaim_for_node(self, *args, **kwargs):
        return {}

    async def list_pods_on_node(self, node):
        return [p for p in self.pods.values() if p.get('spec', {}).get('nodeName') == node]
