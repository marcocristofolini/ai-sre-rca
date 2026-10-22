import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.api.copilot_investigations import InvestigationConfirmation, InvestigationRequest, propose_investigation, confirm_investigation
from app.awareness.inventory import InventoryResource
from app.db.models import AuditEventRecord, InvestigationRecord, OrchestrationJobRecord, OrchestrationProgressEventRecord
from app.db.repository import SQLRepository
from test_copilot_v1 import make_result
from test_copilot_v2 import interactive_api
from test_copilot_scope_v21 import scope, snapshot


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL required for concurrent scope persistence')
async def test_postgres_concurrent_scope_confirmation_is_idempotent_and_survives_restart():
    app, principal = interactive_api()
    tenant = 'scope-' + uuid4().hex
    source = make_result(tenant=tenant); source.incident_id = 'inc-' + uuid4().hex[:12]
    owner = replace(principal.value, tenant_id=tenant)
    request = SimpleNamespace(app=app)
    repo = SQLRepository(os.environ['TEST_DATABASE_URL']); await repo.initialize(); app.state.repo = repo
    try:
        await repo.save(source)
        inventory = snapshot(tenant=tenant)
        inventory.resources.append(InventoryResource(kind='Pod', name='worker', namespace='apps', metadata={'kubernetes_uid': 'worker-uid'}))
        await repo.reconcile_authoritative_inventory(inventory)
        a = InvestigationRequest(question='Investigate selected target', scope=scope())
        b_scope = scope(name='worker'); b_scope['starts_at'] = (datetime.fromisoformat(b_scope['ends_at']) - timedelta(minutes=45)).isoformat()
        b = InvestigationRequest(question=a.question, scope=b_scope)
        pa, pb = await asyncio.gather(propose_investigation(source.incident_id, a, request, owner), propose_investigation(source.incident_id, b, request, owner))
        ca = InvestigationConfirmation(**a.model_dump(), proposal_id=pa.proposal_id, confirmed=True)
        cb = InvestigationConfirmation(**b.model_dump(), proposal_id=pb.proposal_id, confirmed=True)
        jobs = await asyncio.gather(*(confirm_investigation(source.incident_id, body, request, owner) for body in (ca, ca, cb, cb)))
        assert jobs[0].id == jobs[1].id and jobs[2].id == jobs[3].id and jobs[0].id != jobs[2].id
        await repo.close(); repo = SQLRepository(os.environ['TEST_DATABASE_URL']); await repo.initialize(); app.state.repo = repo
        retry = await confirm_investigation(source.incident_id, ca, request, owner)
        assert retry.id == jobs[0].id
        for job, expected in ((jobs[0], 'web'), (jobs[2], 'worker')):
            stored = await repo.get_orchestration_job(job.id, tenant)
            assert stored['payload']['incident']['pod'] == expected
            assert stored['payload']['incident']['ends_at']
            assert await repo.get_orchestration_job(job.id, 'another-tenant') is None
    finally:
        async with repo.sessions() as session:
            for model in (OrchestrationProgressEventRecord, OrchestrationJobRecord, AuditEventRecord, InvestigationRecord):
                await session.execute(delete(model).where(model.tenant_id == tenant))
            await session.commit()
        await repo.close()
