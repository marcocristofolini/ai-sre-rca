"""Explicit demo-only API entry point for existing browser regression fixtures.

Kubernetes responses mirror the inventory seeded by those tests. Production
adapters and the separate live Kind browser runner never use this adapter.
"""
from contextvars import ContextVar
from fastapi import HTTPException

from app.main import app
from app.integrations.demo import DemoTools
from app.core.copilot_scope import verified_identity

if not app.state.settings.demo_mode:
    raise RuntimeError('Browser fixtures require DEMO_MODE=true')


class BrowserFixtureTools(DemoTools):
    cluster = ContextVar('browser_fixture_cluster', default=None)

    def set_cluster(self, cluster):
        return self.cluster.set(cluster)

    def reset_cluster(self, token):
        self.cluster.reset(token)

    async def resource(self, kind, namespace, name):
        repo, settings = app.state.repo, app.state.settings
        cluster = self.cluster.get()
        found = await repo.find_awareness_entities(settings.default_tenant_id,cluster,kind=kind,name=name,namespace=namespace,limit=2)
        if len(found) != 1:
            return None
        entity = found[0]
        try:
            identity = await verified_identity(repo,settings,entity)
        except HTTPException as error:
            if error.status_code == 409:
                return None  # Legacy demo entities have their original fixtures.
            raise
        neighbors, edges = await repo.get_awareness_neighbors({entity.id},entity.tenant_id,cluster,limit=100,include_tombstoned=False)
        parents = {e.id:e for e in neighbors}
        owners = []
        for edge in edges:
            if edge.relationship == 'owned_by' and edge.source_id == entity.id:
                parent = parents[edge.target_id]
                parent_identity = await verified_identity(repo,settings,parent)
                owners.append({'kind':parent.kind,'name':parent.name,'uid':parent_identity['uid'],'controller':True})
        return {'metadata':{'name':name,'namespace':namespace,'uid':identity['uid'],'ownerReferences':owners},
            'spec':{'nodeName':'browser-fixture-node'} if kind=='Pod' else {},
            'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[]}}

    async def get_pod(self, namespace, name):
        return await self.resource('Pod',namespace,name) or await super().get_pod(namespace,name)

    async def get_replicaset(self, namespace, name):
        return await self.resource('ReplicaSet',namespace,name) or await super().get_replicaset(namespace,name)

    async def get_deployment(self, namespace, name):
        return await self.resource('Deployment',namespace,name) or await super().get_deployment(namespace,name)

    async def get_statefulset(self, namespace, name):
        return await self.resource('StatefulSet',namespace,name) or {}

    async def get_node(self, name):
        if name == 'browser-fixture-node':
            return {'metadata':{'name':name,'uid':'fixture-node-uid'},'status':{'conditions':[{'type':'Ready','status':'True'}]}}
        return await super().get_node(name)


app.state.tools = BrowserFixtureTools()
